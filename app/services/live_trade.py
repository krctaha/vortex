"""TSMOM kararlarini gercek Binance USD-M pozisyonlarina aynalar.

Bu dosyanin degismez kurallari:
  * Eski sinyaller geriye donuk acilmaz; yalnizca yeni research_id gelir.
  * Ayni sembolde kullanici pozisyonu varsa sistem emir vermez.
  * Giristen hemen sonra borsada STOP_MARKET kurulamazsa pozisyon marketten
    kapatilir. Stopsuz pozisyon basarili sayilmaz.
  * Tum emirler benzersiz client id ve DB kaydiyla izlenir.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .. import db, runtime
from . import binance_trade

log = logging.getLogger("vortex.live")
SETTING = "live_execution_enabled"
ENGINE_TAG = "tsmom"


def enabled() -> bool:
    return bool(db.get_setting(SETTING, False))


def set_enabled(value: bool) -> None:
    db.set_setting(SETTING, bool(value))
    db.set_setting("live_execution_changed_at", db.now_ms())


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(row.get("meta") or "{}")
    except (TypeError, ValueError):
        return {}


def open_rows() -> List[Dict[str, Any]]:
    return [dict(x) for x in db.query(
        "SELECT * FROM trades WHERE mode='live' AND status IN ('candidate','open') "
        "ORDER BY opened_at")]


def _row_for_research(research_id: int) -> Optional[Dict[str, Any]]:
    for row in open_rows():
        if int(_meta(row).get("research_id") or 0) == int(research_id):
            return row
    return None


def _client(kind: str, research_id: int, symbol: str) -> str:
    safe = re.sub(r"[^A-Z0-9]", "", symbol.upper())[:8]
    return f"vx-{kind}-{research_id}-{safe}"[:36]


def _position_amount(rows: List[Dict[str, Any]], position_side: str) -> float:
    for row in rows:
        if str(row.get("positionSide") or "BOTH") == position_side:
            return float(row.get("positionAmt", 0) or 0)
    return 0.0


def _algo_id(payload: Dict[str, Any]) -> Optional[int]:
    for key in ("algoId", "orderId"):
        try:
            value = int(payload.get(key) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            pass
    return None


def _has_managed_stop(algos: List[Dict[str, Any]], meta: Dict[str, Any]) -> bool:
    """DB'deki VORTEX stopunun borsada gercekten acik olup olmadigi."""
    wanted_id = int(meta.get("stop_algo_id") or 0)
    wanted_client = str(meta.get("stop_client_id") or "")
    for algo in algos:
        try:
            algo_id = int(algo.get("algoId") or algo.get("orderId") or 0)
        except (TypeError, ValueError):
            algo_id = 0
        client_id = str(algo.get("clientAlgoId") or algo.get("clientOrderId") or "")
        order_type = str(algo.get("orderType") or algo.get("type") or "").upper()
        if order_type != "STOP_MARKET":
            continue
        if (wanted_id and algo_id == wanted_id) or (wanted_client and client_id == wanted_client):
            return True
    return False


async def _risk_alert(symbol: str, event: str, body: str) -> None:
    """Kritik canlı risk olayını spam üretmeden Telegram + push'a yollar."""
    key = f"live_risk_alert:{event}:{symbol}"
    now = db.now_ms()
    if now - int(db.get_setting(key, 0) or 0) < 300_000:
        return
    db.set_setting(key, now)
    admin = db.query_one(
        "SELECT id,telegram_token,telegram_chat_id FROM users "
        "WHERE role='admin' AND is_active=1 ORDER BY id LIMIT 1")
    if not admin:
        return
    try:
        from . import telegram
        await telegram.send(
            f"⚠️ <b>VORTEX CANLI RİSK</b>\n<b>{telegram.esc(symbol)}</b>\n"
            f"{telegram.esc(body)}",
            token=admin.get("telegram_token", ""),
            chat_id=admin.get("telegram_chat_id", ""), silent=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram risk alarmi gonderilemedi: %s", exc)
    try:
        from . import webpush
        await webpush.send("VORTEX canlı risk", f"{symbol}: {body}",
                           url="/ayarlar#trading-settings",
                           tag=f"live-risk-{symbol}", user_id=int(admin["id"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("Push risk alarmi gonderilemedi: %s", exc)


async def preflight(include_order_test: bool = False) -> Dict[str, Any]:
    """Canli anahtari acmadan once tum sert kapilari kontrol et."""
    out: Dict[str, Any] = {"ok": False, "checks": []}

    def add(name: str, ok: bool, detail: str, level: str = "ok") -> None:
        out["checks"].append({"name": name, "ok": ok, "detail": detail,
                              "level": level if ok else "err"})

    if not binance_trade.has_keys():
        add("API anahtarı", False, "Kayıtlı Binance API anahtarı yok")
        return out
    try:
        account = await binance_trade.account_v3()
        permission = await binance_trade.account_v2()
        hedge = await binance_trade.position_mode()
        restrictions = await binance_trade.api_restrictions()
    except Exception as exc:  # noqa: BLE001
        add("Binance hesabı", False, f"Hesap okunamadı: {str(exc)[:180]}")
        return out

    can_trade = bool(permission.get("canTrade"))
    available = float(account.get("availableBalance", 0) or 0)
    positions = [p for p in account.get("positions", [])
                 if abs(float(p.get("positionAmt", 0) or 0)) > 0]
    add("Futures emir yetkisi", can_trade,
        "Açık" if can_trade else "Kapalı; Binance API ayarından Enable Futures açılmalı")
    min_cash = max(10.0, float(runtime.get()["risk"]["max_position_margin"]) * 1.20)
    add("Kullanılabilir bakiye", available >= min_cash,
        f"{available:.2f} USDT · gerekli güvenlik payı {min_cash:.2f} USDT")
    add("Pozisyon modu", True, "Hedge" if hedge else "Tek yön")
    withdrawals = bool(restrictions.get("enableWithdrawals"))
    ip_locked = bool(restrictions.get("ipRestrict"))
    add("Para çekme yetkisi", not withdrawals,
        "Kapalı — doğru" if not withdrawals else "AÇIK; canlı sistem başlatılamaz")
    add("API IP kısıtlaması", ip_locked,
        "Açık — yalnızca izinli sunucu" if ip_locked else
        "KAPALI; Binance API ayarına 77.42.28.224 eklenmeden canlı sistem başlatılmaz")
    out.update(available=round(available, 2), wallet=round(float(
        account.get("totalWalletBalance", 0) or 0), 2), hedge_mode=hedge,
        external_positions=[{"symbol": p.get("symbol"),
                             "amount": float(p.get("positionAmt", 0) or 0)}
                            for p in positions])
    if include_order_test and can_trade:
        try:
            await binance_trade.test_order_permission()
            add("Deneme emri", True, "Binance test endpointi kabul etti; gerçek emir oluşmadı")
            out["order_test"] = True
        except Exception as exc:  # noqa: BLE001
            add("Deneme emri", False, f"Emir katmanı reddetti: {str(exc)[:180]}")
            out["order_test"] = False
    out["ok"] = all(x["ok"] for x in out["checks"] if x["name"] != "Pozisyon modu")
    return out


async def _close_exchange_position(symbol: str, side: str, quantity: float,
                                   position_side: str, client_id: str) -> Dict[str, Any]:
    close_side = "SELL" if side == "LONG" else "BUY"
    return await binance_trade.place_market(
        symbol, close_side, quantity, position_side, client_id,
        reduce_only=(position_side == "BOTH"))


async def open_from_signal(decision: Dict[str, Any], research_id: int,
                           paper: Dict[str, Any]) -> Dict[str, Any]:
    """Yeni, para kapisini gecmis bir karari gercek hesaba uygula."""
    if not enabled():
        return {"ok": False, "skipped": True, "reason": "canlı yürütme kapalı"}
    if _row_for_research(research_id):
        return {"ok": False, "skipped": True, "reason": "bu sinyal daha önce işlendi"}
    if not paper.get("ok"):
        return {"ok": False, "skipped": True, "reason": "paper para kapısı geçilmedi"}

    symbol, side = str(decision["symbol"]), str(decision["side"])
    rcfg, ccfg = runtime.get()["risk"], runtime.get()["copy"]
    if len([r for r in open_rows() if r["status"] == "open"]) >= int(ccfg["max_open_trades"]):
        return {"ok": False, "reason": "canlı açık pozisyon sınırı dolu"}

    try:
        account = await binance_trade.account_v3()
        permission = await binance_trade.account_v2()
        if not bool(permission.get("canTrade")):
            return {"ok": False, "reason": "Binance Futures emir yetkisi kapalı"}
        hedge = await binance_trade.position_mode()
        position_side = side if hedge else "BOTH"
        positions = await binance_trade.position_rows(symbol)
        # En onemli izolasyon: CAKE/DOGE gibi kullanicinin kendi pozisyonuna
        # miktar eklemek, stopla onu da kapatmak veya ortalama fiyati bozmak yok.
        if any(abs(float(p.get("positionAmt", 0) or 0)) > 0 for p in positions):
            return {"ok": False, "reason": f"{symbol} hesabında zaten açık pozisyon var; dokunulmadı"}

        # V4 finansal kanıt kapısı Telegram'daki manuel bypass'tan bağımsızdır.
        # Kullanıcının mevcut pozisyon izolasyonu önce kontrol edilir; bundan
        # sonra hiçbir borsa yazımı yapılmadan katı kanıt kapısı uygulanır.
        from . import decision_v4
        strict = decision_v4.strict_live_gate(side, decision.get("v4"))
        if not strict.get("allowed"):
            return {"ok": False, "skipped": True,
                    "reason": "V4 canlı emir doğrulaması: "
                              + " · ".join(strict.get("reasons") or [])}

        rules = await binance_trade.symbol_rules(symbol)
        if rules.get("status") != "TRADING":
            return {"ok": False, "reason": f"{symbol} işlem durumunda değil"}
        ticker = await binance_trade.public("/fapi/v2/ticker/price", {"symbol": symbol})
        price = float(ticker["price"])
        stop_raw, target_raw = float(decision["stop"]), float(decision["target"])
        if (side == "LONG" and stop_raw >= price) or (side == "SHORT" and stop_raw <= price):
            return {"ok": False, "reason": "fiyat stop seviyesini geçti; geç giriş yapılmadı"}
        if (side == "LONG" and target_raw <= price) or (side == "SHORT" and target_raw >= price):
            return {"ok": False, "reason": "fiyat hedef seviyesini geçti; geç giriş yapılmadı"}

        leverage = int(paper.get("leverage") or rcfg["default_leverage"])
        # Kâğıt katmanı düşük güvenli kurulumu keşif riskiyle boyutladıysa
        # canlı emir bunu tekrar tam riske şişiremez.
        paper_risk = float(paper.get("risk") or rcfg["max_risk_per_trade"])
        risk_cap = min(float(rcfg["max_risk_per_trade"]), paper_risk)
        margin_cap = float(rcfg["max_position_margin"])
        by_risk = risk_cap / abs(price - stop_raw)
        by_margin = margin_cap * leverage / price
        qty = binance_trade.floor_qty(min(by_risk, by_margin), rules)
        if qty < float(rules["min_qty"]) or qty <= 0:
            return {"ok": False, "reason": "borsanın minimum miktarı risk sınırını aşıyor"}
        notional = qty * price
        if notional < float(rules["min_notional"]):
            return {"ok": False, "reason":
                    f"notional {notional:.2f}$ borsa minimumu {rules['min_notional']:.2f}$ altında"}
        margin = notional / leverage
        available = float(account.get("availableBalance", 0) or 0)
        if available < margin * 1.20:
            return {"ok": False, "reason":
                    f"yetersiz güvenli bakiye ({available:.2f}$ < {margin * 1.20:.2f}$)"}

        # Stop yuvarlamasi daima pozisyona yaklasir; risk tavani yuvarlamayla asilmaz.
        stop = binance_trade.trigger_price(stop_raw, rules, upward=(side == "LONG"))
        target = binance_trade.trigger_price(target_raw, rules, upward=(side == "SHORT"))
        admin_id = int(paper["user_id"])
        meta = {
            "engine": ENGINE_TAG, "research_id": research_id, "state": "preparing",
            "position_side": position_side, "planned_entry": price,
            "risk_tier": str(paper.get("risk_tier") or "standard"),
            "risk_cap_usdt": round(risk_cap, 4),
            "entry_client_id": _client("entry", research_id, symbol),
            "stop_client_id": _client("stop", research_id, symbol),
            "tp_client_id": _client("tp", research_id, symbol),
        }
        trade_id = db.execute(
            "INSERT INTO trades(user_id,symbol,side,status,mode,entry,stop,take_profit,"
            "margin_usdt,leverage,margin_type,qty,risk_usdt,opened_at,interval,note,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (admin_id, symbol, side, "candidate", "live", price, stop, target, margin,
             leverage, "ISOLATED", qty, abs(price - stop) * qty, db.now_ms(), "1d",
             f"TSMOM CANLI · research #{research_id}", json.dumps(meta, ensure_ascii=False)))

        await binance_trade.set_margin_type(symbol, "ISOLATED")
        await binance_trade.set_leverage(symbol, leverage)
        entry_side = "BUY" if side == "LONG" else "SELL"
        try:
            entry = await binance_trade.place_market(
                symbol, entry_side, qty, position_side, meta["entry_client_id"])
        except Exception as entry_exc:  # noqa: BLE001
            # HTTP 503 gibi belirsiz cevaplarda tekrar emir atmak duplicate
            # pozisyon acabilir. Ayni client id'yi sorgula; bulunursa devam et.
            try:
                entry = await binance_trade.query_order(symbol, meta["entry_client_id"])
                if str(entry.get("status")) not in ("FILLED", "PARTIALLY_FILLED"):
                    raise entry_exc
            except Exception:
                raise entry_exc
        actual_qty = float(entry.get("executedQty", 0) or qty)
        actual_entry = float(entry.get("avgPrice", 0) or price)
        meta.update(state="entry_filled", entry_order_id=entry.get("orderId"),
                    actual_entry=actual_entry, actual_qty=actual_qty)
        close_side = "SELL" if side == "LONG" else "BUY"
        try:
            stop_order = await binance_trade.place_algo(
                symbol, close_side, "STOP_MARKET", actual_qty, stop,
                position_side, meta["stop_client_id"])
        except Exception as stop_exc:  # noqa: BLE001
            # Stopsuz bir saniye bile "basarili canli pozisyon" sayilmaz.
            panic_ok = False
            try:
                emergency = await _close_exchange_position(
                    symbol, side, actual_qty, position_side,
                    _client("panic", research_id, symbol))
                meta.update(state="emergency_closed", stop_error=str(stop_exc)[:250],
                            emergency_order_id=emergency.get("orderId"))
                db.execute("UPDATE trades SET status='cancelled',closed_at=?,meta=? WHERE id=?",
                           (db.now_ms(), json.dumps(meta, ensure_ascii=False), trade_id))
                panic_ok = True
            except Exception as panic_exc:  # noqa: BLE001
                # Kapatma cevabi belirsiz olabilir; borsadaki gercek miktar
                # sifirsa kapatma basarili kabul edilir.
                try:
                    check_rows = await binance_trade.position_rows(symbol)
                    panic_ok = not any(abs(float(p.get("positionAmt", 0) or 0)) > 0
                                       for p in check_rows)
                except Exception:
                    panic_ok = False
                meta.update(state=("emergency_closed" if panic_ok else "UNPROTECTED_EMERGENCY"),
                            stop_error=str(stop_exc)[:250], emergency_error=str(panic_exc)[:250])
                db.execute("UPDATE trades SET status=?,closed_at=?,meta=? WHERE id=?",
                           ("cancelled" if panic_ok else "open", db.now_ms() if panic_ok else None,
                            json.dumps(meta, ensure_ascii=False), trade_id))
                if not panic_ok:
                    log.critical("%s STOPSuz ve acil kapatma basarisiz: %s", symbol, panic_exc)
            return {"ok": False, "reason":
                    (f"koruyucu stop kurulamadı; pozisyon acil kapatıldı: {stop_exc}"
                     if panic_ok else f"ACİL: stop ve acil kapatma başarısız: {stop_exc}")}

        meta.update(state="protected", stop_algo_id=_algo_id(stop_order))
        tp_warning = ""
        try:
            tp_order = await binance_trade.place_algo(
                symbol, close_side, "TAKE_PROFIT_MARKET", actual_qty, target,
                position_side, meta["tp_client_id"])
            meta["tp_algo_id"] = _algo_id(tp_order)
        except Exception as exc:  # noqa: BLE001
            # Stop varsa pozisyon korumali; hedef basarisizligi pozisyonu acil
            # kapatmayi gerektirmez. Motorun momentum cikisi yine calisir.
            tp_warning = str(exc)[:250]
            meta["tp_error"] = tp_warning
            log.error("%s hedef emri kurulamadı; stop aktif: %s", symbol, exc)
        db.execute("UPDATE trades SET status='open',entry=?,qty=?,margin_usdt=?,risk_usdt=?,meta=? WHERE id=?",
                   (actual_entry, actual_qty, actual_entry * actual_qty / leverage,
                    abs(actual_entry - stop) * actual_qty,
                    json.dumps(meta, ensure_ascii=False), trade_id))
        log.warning("CANLI ACILDI %s %s qty=%s entry=%s stop=%s target=%s",
                    symbol, side, actual_qty, actual_entry, stop, target)
        return {"ok": True, "trade_id": trade_id, "entry": actual_entry,
                "qty": actual_qty, "stop": stop, "target": target,
                "margin": round(actual_entry * actual_qty / leverage, 4),
                "risk": round(abs(actual_entry - stop) * actual_qty, 4),
                "tp_warning": tp_warning}
    except Exception as exc:  # noqa: BLE001
        log.error("Canli acilis reddedildi %s: %s", symbol, exc, exc_info=True)
        # Emirden once/sonra olusan pending satiri varsa operator gorebilsin.
        row = _row_for_research(research_id)
        if row and row["status"] == "candidate":
            meta = _meta(row)
            meta.update(state="failed", error=str(exc)[:300])
            db.execute("UPDATE trades SET status='cancelled',closed_at=?,meta=? WHERE id=?",
                       (db.now_ms(), json.dumps(meta, ensure_ascii=False), row["id"]))
        return {"ok": False, "reason": str(exc)[:300]}


async def close_for_research(research_id: int, planned_price: float,
                             planned_r: float, reason: str) -> Dict[str, Any]:
    row = _row_for_research(research_id)
    if not row or row["status"] != "open":
        return {"ok": True, "closed": False, "reason": "canlı eş yok"}
    meta = _meta(row)
    try:
        position_side = str(meta.get("position_side") or "BOTH")
        positions = await binance_trade.position_rows(row["symbol"])
        amt = _position_amount(positions, position_side)
        qty = min(abs(amt), float(row["qty"] or 0))
        exit_price = float(planned_price)
        if qty > 0:
            order = await _close_exchange_position(
                row["symbol"], row["side"], qty, position_side,
                _client("exit", research_id, row["symbol"]))
            exit_price = float(order.get("avgPrice", 0) or planned_price)
            meta["exit_order_id"] = order.get("orderId")
        for id_key, client_key in (("stop_algo_id", "stop_client_id"),
                                   ("tp_algo_id", "tp_client_id")):
            try:
                await binance_trade.cancel_algo(
                    row["symbol"], meta.get(id_key), meta.get(client_key))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s koruma emri iptal edilemedi: %s", row["symbol"], exc)
        direction = 1 if row["side"] == "LONG" else -1
        pnl = (exit_price - float(row["entry"])) * direction * float(row["qty"] or 0)
        per_unit = abs(float(row["entry"]) - float(row["stop"]))
        actual_r = ((exit_price - float(row["entry"])) * direction / per_unit
                    if per_unit else planned_r)
        meta.update(state="closed", exit_reason=reason)
        db.execute("UPDATE trades SET status='closed',exit_price=?,pnl_usdt=?,r_multiple=?,"
                   "closed_at=?,meta=? WHERE id=?",
                   (exit_price, round(pnl, 6), round(actual_r, 4), db.now_ms(),
                    json.dumps(meta, ensure_ascii=False), row["id"]))
        log.warning("CANLI KAPANDI %s %s pnl=%.4f reason=%s",
                    row["symbol"], row["side"], pnl, reason)
        return {"ok": True, "closed": True, "exit_price": exit_price,
                "pnl": round(pnl, 4), "r": round(actual_r, 4)}
    except Exception as exc:  # noqa: BLE001
        meta.update(close_error=str(exc)[:300], close_retry_at=db.now_ms())
        db.execute("UPDATE trades SET meta=? WHERE id=?",
                   (json.dumps(meta, ensure_ascii=False), row["id"]))
        log.error("Canli kapatma basarisiz %s: %s", row["symbol"], exc, exc_info=True)
        return {"ok": False, "closed": False, "reason": str(exc)[:300]}


async def reconcile() -> Dict[str, int]:
    """Kapanmis research kayitlarini kapatir; tetiklenen stop/hedefi DB'ye yansitir."""
    counts = {"checked": 0, "closed": 0, "retry": 0,
              "protected": 0, "reprotected": 0, "emergency_closed": 0}
    if not binance_trade.has_keys():
        db.set_setting("live_reconcile_last", {**counts, "at": db.now_ms(), "keys": False})
        return counts
    for row in open_rows():
        if row["status"] != "open":
            continue
        counts["checked"] += 1
        meta = _meta(row)
        rid = int(meta.get("research_id") or 0)
        research = db.query_one("SELECT status,exit_price,result_r,exit_reason FROM research_trades WHERE id=?",
                                (rid,)) if rid else None
        if research and research["status"] != "open":
            res = await close_for_research(rid, float(research["exit_price"] or row["entry"]),
                                           float(research["result_r"] or 0),
                                           str(research["exit_reason"] or "motor"))
            counts["closed" if res.get("ok") else "retry"] += 1
            continue
        try:
            positions = await binance_trade.position_rows(row["symbol"])
            position_side = str(meta.get("position_side") or "BOTH")
            position_amt = _position_amount(positions, position_side)
            if abs(position_amt) > 0:
                # DB'de stop kimligi bulunmasi yeterli degil: kullanici veya
                # borsa emri sonradan iptal etmis olabilir. Her uzlasmada
                # VORTEX'in kendi STOP_MARKET emrini borsadan dogrula.
                algos = await binance_trade.open_algo_orders(row["symbol"])
                if _has_managed_stop(algos, meta):
                    counts["protected"] += 1
                    continue
                qty = min(abs(position_amt), abs(float(row.get("qty") or position_amt)))
                close_side = "SELL" if row["side"] == "LONG" else "BUY"
                repair_client = _client(
                    f"guard{str(db.now_ms())[-6:]}", rid or int(row["id"]), row["symbol"])
                try:
                    repaired = await binance_trade.place_algo(
                        row["symbol"], close_side, "STOP_MARKET", qty,
                        float(row["stop"]), position_side, repair_client)
                    meta.update(stop_algo_id=_algo_id(repaired),
                                stop_client_id=repair_client,
                                reprotected_at=db.now_ms(), state="protected")
                    db.execute("UPDATE trades SET meta=? WHERE id=?",
                               (json.dumps(meta, ensure_ascii=False), row["id"]))
                    counts["protected"] += 1
                    counts["reprotected"] += 1
                    log.error("KORUMA YENILENDI %s %s — borsada stop eksikti",
                              row["symbol"], row["side"])
                    await _risk_alert(row["symbol"], "reprotected",
                                      "Borsadaki stop eksikti; VORTEX stopu yeniden kurdu.")
                    continue
                except Exception as stop_exc:  # noqa: BLE001
                    # Stopsuz acik pozisyon kabul edilmez. Yeniden kurma da
                    # basarisizsa VORTEX'in yonettigi miktari reduce-only kapat.
                    try:
                        emergency = await _close_exchange_position(
                            row["symbol"], row["side"], qty, position_side,
                            _client("guardclose", rid or int(row["id"]), row["symbol"]))
                        meta.update(state="emergency_reconcile_closed",
                                    stop_error=str(stop_exc)[:250],
                                    emergency_order_id=emergency.get("orderId"),
                                    exit_reason="koruma yeniden kurulamadi")
                        db.execute("UPDATE trades SET status='cancelled',closed_at=?,meta=? WHERE id=?",
                                   (db.now_ms(), json.dumps(meta, ensure_ascii=False), row["id"]))
                        counts["emergency_closed"] += 1
                        log.critical("KORUMASIZ POZISYON ACIL KAPANDI %s: %s",
                                     row["symbol"], stop_exc)
                        await _risk_alert(
                            row["symbol"], "emergency_closed",
                            "Stop yeniden kurulamadı; VORTEX pozisyonu acil kapattı.")
                        continue
                    except Exception as close_exc:  # noqa: BLE001
                        meta.update(state="UNPROTECTED_EMERGENCY",
                                    stop_error=str(stop_exc)[:250],
                                    emergency_error=str(close_exc)[:250])
                        db.execute("UPDATE trades SET meta=? WHERE id=?",
                                   (json.dumps(meta, ensure_ascii=False), row["id"]))
                        counts["retry"] += 1
                        log.critical("KORUMASIZ POZISYON KAPATILAMADI %s stop=%s close=%s",
                                     row["symbol"], stop_exc, close_exc)
                        await _risk_alert(
                            row["symbol"], "unprotected",
                            "STOP YOK ve acil kapatma başarısız. Binance hesabını hemen kontrol et.")
                        continue
            # Pozisyon borsadaki stop/hedef veya kullanici islemiyle kapanmis.
            # Kalan kardes algo emrini iptal et; fiyat/PnL icin mark fiyati
            # yaklasik kayit olarak kullanilir, Binance gelir gecmisi ayri tutulur.
            ticker = await binance_trade.public("/fapi/v2/ticker/price", {"symbol": row["symbol"]})
            exit_price = float(ticker["price"])
            for id_key, client_key in (("stop_algo_id", "stop_client_id"),
                                       ("tp_algo_id", "tp_client_id")):
                try:
                    await binance_trade.cancel_algo(row["symbol"], meta.get(id_key), meta.get(client_key))
                except Exception:
                    pass
            direction = 1 if row["side"] == "LONG" else -1
            pnl = (exit_price - float(row["entry"])) * direction * float(row["qty"] or 0)
            per_unit = abs(float(row["entry"]) - float(row["stop"]))
            r_mult = (exit_price - float(row["entry"])) * direction / per_unit if per_unit else 0
            meta.update(state="exchange_closed", exit_reason="borsa stop/hedef")
            db.execute("UPDATE trades SET status='closed',exit_price=?,pnl_usdt=?,r_multiple=?,"
                       "closed_at=?,meta=? WHERE id=?",
                       (exit_price, round(pnl, 6), round(r_mult, 4), db.now_ms(),
                        json.dumps(meta, ensure_ascii=False), row["id"]))
            counts["closed"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["retry"] += 1
            log.warning("Canli mutabakat ertelendi %s: %s", row["symbol"], exc)
    db.set_setting("live_reconcile_last", {**counts, "at": db.now_ms(), "keys": True})
    return counts


async def status() -> Dict[str, Any]:
    rows = open_rows()
    open_live = [r for r in rows if r["status"] == "open"]
    account = await binance_trade.account_summary()
    return {
        "enabled": enabled(), "order_layer": True,
        "open_live": len(open_live), "pending": len(rows) - len(open_live),
        "protected": sum(1 for r in open_live if _meta(r).get("stop_algo_id")),
        "last_reconcile": db.get_setting("live_reconcile_last", {}),
        "account": account,
        "changed_at": db.get_setting("live_execution_changed_at"),
    }
