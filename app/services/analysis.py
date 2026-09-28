"""Bir sembolu tek bir 'anlik goruntu'ye cevirir: indikatorler + yapi + seviyeler.

TASARIM NOTU (onemli):
  Bu modul PUAN URETMEZ. MIHENK'te olculen sifir edge'in en olasi sebebi,
  birbiriyle yuksek korelasyonlu bilesenleri (sweep, CRT, OB mitigasyonu, IFVG
  hepsi buyuk olcude AYNI bar orgusunu olcer) bagimsizmis gibi toplamakti.
  Burada her bulgu AYRI AYRI, kendi guven etiketiyle raporlanir; birlestirme
  karari kullanicinin/strateji katmanininidir.
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

import numpy as np

from .. import runtime
from ..indicators import core, cvd as cvd_mod, levels as lv, liquidity_sweep as ls_hunter, regime as rg, structure as st, vwap as vw
from . import binance

# Kanit gucu etiketleri - arastirmadan gelen olculmus edge degerlendirmesi
EVIDENCE = {
    "cvd": "olculmus",
    "atr": "olculmus",
    "regime": "olculmus",
    "oi": "olculmus",
    "vwap": "guclu-baglam",
    "volume_profile": "guclu-baglam",
    "funding": "kesitsel-anlamli",
    "sweep": "zayif-sinyal-iyi-filtre",
    "structure": "durum-makinesi",
    "bb_squeeze": "volatilite-ongorusu",
    "fvg": "olculmemis",
    "ifvg": "olculmemis",
    "order_block": "olculmemis",
    "crt": "olculmemis",
    "divergence": "olculmemis",
    "premium_discount": "olculmemis",
}


def _last(arr: np.ndarray) -> Optional[float]:
    if arr is None or len(arr) == 0:
        return None
    v = float(arr[-1])
    return None if np.isnan(v) else round(v, 8)


def _macd(close: np.ndarray):
    fast, slow = core.ema(close, 12), core.ema(close, 26)
    macd_line = fast - slow
    valid = np.where(np.isfinite(macd_line))[0]
    signal = np.full(len(close), np.nan)
    if len(valid) >= 9:
        signal[valid] = core.ema(macd_line[valid], 9)
    return macd_line, signal, macd_line - signal


def _stoch_rsi(rsi_values: np.ndarray, period: int = 14):
    raw = np.full(len(rsi_values), np.nan)
    for i in range(period - 1, len(rsi_values)):
        window = rsi_values[i - period + 1:i + 1]
        if not np.all(np.isfinite(window)):
            continue
        lo, hi = float(np.min(window)), float(np.max(window))
        raw[i] = (float(rsi_values[i]) - lo) / (hi - lo) * 100 if hi > lo else 50.0
    valid = np.where(np.isfinite(raw))[0]
    k = np.full(len(raw), np.nan)
    d = np.full(len(raw), np.nan)
    if len(valid) >= 3:
        k[valid] = core.sma(raw[valid], 3)
        valid_k = np.where(np.isfinite(k))[0]
        if len(valid_k) >= 3:
            d[valid_k] = core.sma(k[valid_k], 3)
    return k, d


def _ichimoku(high: np.ndarray, low: np.ndarray):
    def midpoint(period: int):
        out = np.full(len(high), np.nan)
        for i in range(period - 1, len(high)):
            out[i] = (float(np.max(high[i-period+1:i+1])) +
                      float(np.min(low[i-period+1:i+1]))) / 2
        return out
    tenkan, kijun = midpoint(9), midpoint(26)
    span_a = (tenkan + kijun) / 2
    span_b = midpoint(52)
    return tenkan, kijun, span_a, span_b


def build_trade_plan(snap: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """ATR tabanli senaryo plani. Bu bir emir veya karlilik vaadi degildir.

    SKORLAMA TASARIMI (v3 — 26.08.2026 duzeltmesi)
    ----------------------------------------------
    Onceki surumde 5 bilesen vardi ama uc tanesi (EMA20/50, Supertrend, VWAP
    tarafi) ayni soruyu soruyordu; EMA ile Supertrend arasinda olculen
    korelasyon r=0.86 idi. ADX>=20 ise yon-bagimsiz oldugu icin LONG ve SHORT'ta
    esit siklikta geciyordu — bedava puan. Sonuc: adaylarin %87'si 4/5 esigini
    geciyordu, yani filtre filtrelemiyordu.

    Bu surumde bilesenler birbirinden BAGIMSIZ eksenlere ayrildi:
      1) Trend       — EMA20/50 + Supertrend + VWAP'in UCU BIRDEN uyumlu olmali
                       (uc korelasyonlu sinyal tek puana indirildi)
      2) Momentum    — RSI giris bandinda ve StochRSI tukenmis degil
      3) Rejim       — rejim siniflandirici yonu onayliyor (daha once
                       hesaplaniyor ama hicbir seyi engellemiyordu)
      4) Order flow  — CVD / taker delta yonu uyumlu (fiyattan bagimsiz bilgi)
      5) Yapi        — BOS/CHoCH bias veya yonu destekleyen likidite supurmesi

    ADX artik puan degil VETO: trend gucu yoksa kurulum tamamen elenir.
    Rejim 'sikisik' ise de veto uygulanir.
    """
    from .. import runtime as _runtime
    cfg = cfg or _runtime.get()
    reward_r = float(cfg["risk"]["reward_r"])

    price = float(snap.get("price") or 0)
    ind = snap.get("indicators") or {}
    regime = (snap.get("regime") or {}).get("regime", "bilinmiyor")
    struct_bias = (snap.get("structure") or {}).get("bias", "neutral")
    findings = snap.get("findings") or {}

    bias = struct_bias
    if bias not in ("bull", "bear"):
        e20, e50 = ind.get("ema20"), ind.get("ema50")
        bias = "bull" if (e20 and e50 and e20 > e50) else "bear"
    side = "LONG" if bias == "bull" else "SHORT"
    long_side = side == "LONG"
    direction = 1 if long_side else -1

    atr = float(ind.get("atr14") or price * .005)
    stop_mult = float(cfg["risk"].get("stop_atr_mult", 1.5))
    risk = max(atr * stop_mult, price * .0035)
    stop = price - direction * risk
    # Hedef merdiveni 1R'den baslar. Onceki surumde ilk hedef 0.6R idi ve
    # trades tablosuna take_profit olarak O yaziliyordu: 1R stop karsisinda
    # 0.6R hedef, %62.5 uzeri isabet olmadan matematiksel olarak kaybettirir.
    primary_r = float(cfg["risk"].get("target_r", 1.5))
    span = max(reward_r - primary_r, 0.0)
    ladder = [primary_r + span * i / 4.0 for i in range(5)]
    targets = [price + direction * risk * m for m in ladder]

    score = 0
    reasons: List[str] = []
    misses: List[str] = []
    veto: List[str] = []
    # Her bilesenin 0/1 sonucu ayri tutuluyor. Sebep: toplam skor, hangi
    # bilesenin gercekten para kazandirdigini gizliyor. Bunlar kaydedilmeden
    # "skor 5, skor 4'ten kotu" gibi bir bulgunun sebebi bulunamaz.
    components: Dict[str, int] = {}

    def check(ok: bool, hit: str, miss: str, key: str = "") -> None:
        nonlocal score
        if key:
            components[key] = 1 if ok else 0
        if ok:
            score += 1
            reasons.append(hit)
        else:
            misses.append(miss)

    # --- VETO kapilari (puan degil, gecis sarti) ---
    adx_now = ind.get("adx14")
    if adx_now is not None and adx_now < 18:
        veto.append(f"ADX {adx_now:.0f} — trend gücü yok")
    if regime == "sikisik":
        veto.append("Rejim sıkışık/range")

    # --- 1) Trend: uc gostergenin UCU BIRDEN uyumlu olmali ---
    e20, e50 = ind.get("ema20"), ind.get("ema50")
    vwap_ref = ind.get("vwap") or price
    ema_ok = bool(e20 and e50 and ((e20 > e50) if long_side else (e20 < e50)))
    st_ok = ind.get("supertrend_dir") == (1 if long_side else -1)
    vwap_ok = (price >= vwap_ref) if long_side else (price <= vwap_ref)
    check(ema_ok and st_ok and vwap_ok,
          "Trend uyumlu (EMA+Supertrend+VWAP)",
          "Trend bileşenleri ayrışıyor", "trend")

    # --- 2) Momentum ---
    rsi_now = ind.get("rsi14")
    stoch = ind.get("stoch_rsi_k")
    rsi_ok = rsi_now is not None and ((45 <= rsi_now <= 68) if long_side else (32 <= rsi_now <= 55))
    stoch_ok = stoch is None or ((stoch < 88) if long_side else (stoch > 12))
    check(rsi_ok and stoch_ok, "Momentum giriş bandında", "Momentum bandın dışında",
          "momentum")

    # --- 3) Rejim onayi ---
    check(regime == ("trend_yukari" if long_side else "trend_asagi"),
          "Rejim yönü onaylıyor", "Rejim yönü onaylamıyor", "rejim")

    # --- 4) Order flow (CVD / taker delta) ---
    delta20 = ind.get("order_flow_delta_20")
    cvd20 = ind.get("cvd_change_20")
    flow = delta20 if delta20 is not None else cvd20
    check(flow is not None and ((flow > 0) if long_side else (flow < 0)),
          "Order flow yönü uyumlu", "Order flow ters tarafta", "akis")

    # --- 5) Piyasa yapisi ---
    sweeps = findings.get("sweeps") or []
    recent_sweep = any(x.get("direction") == ("bull" if long_side else "bear")
                       for x in sweeps[-3:])
    check(struct_bias == bias or recent_sweep,
          "Yapı yönü destekliyor (BOS/CHoCH veya sweep)",
          "Yapı yönü desteklemiyor", "yapi")

    raw_score = score
    if veto:
        score = 0

    auto_open_score = int(cfg["engine"].get("auto_open_score", 0) or 0)
    return {
        "side": side, "entry": round(price, 8), "stop": round(stop, 8),
        "targets": [round(x, 8) for x in targets],
        "target_r": [round(m, 2) for m in ladder],
        "primary_target": round(targets[0], 8),
        "risk_distance": round(risk, 8),
        "score": score, "raw_score": raw_score, "max_score": 5,
        "qualified": score >= int(cfg["engine"]["min_score"]),
        "high_confidence": bool(auto_open_score and score >= auto_open_score),
        "reasons": reasons, "misses": misses, "veto": veto,
        "components": components,
        "note": "ATR tabanlı kâğıt senaryodur; gerçek emir değildir.",
    }


def _level_text(supports, resistances, price: float, atr: float) -> tuple:
    """(destek metni, direnc metni) — biri yoksa ATR projeksiyonu uretir.

    Eskiden liste bosken rapora "Dirençler: — / —" dusuyordu. Fiyat bakilan
    pencerede en tepedeyse bu DOGRU bir bilgidir ama boyle yazilinca hicbir
    sey ifade etmez: okuyan "hesap bozuk" saniyor. Artik durum acikca soylenir
    ve yerine olculebilir bir projeksiyon konur.
    """
    def fmt(items):
        return " / ".join(f"{x['price']:.8g}" for x in items[:2])

    if supports:
        sup_txt = fmt(supports)
    elif atr:
        sup_txt = f"yapısal destek yok — ATR projeksiyonu {price - 1.5 * atr:.8g}"
    else:
        sup_txt = "yapısal destek yok"

    if resistances:
        res_txt = fmt(resistances)
    elif atr:
        res_txt = (f"fiyat aralığın tepesinde, üstte yapısal direnç yok — "
                   f"ATR projeksiyonu {price + 1.5 * atr:.8g}")
    else:
        res_txt = "fiyat aralığın tepesinde, üstte yapısal direnç yok"
    return sup_txt, res_txt


def telegram_report(snap: Dict[str, Any]) -> str:
    """Emir uretmeden, mevcut bulgulari aciklayan Telegram piyasa raporu."""
    e = lambda value: escape(str(value), quote=False)
    ind = snap.get("indicators") or {}
    structure = snap.get("structure") or {}
    levels = snap.get("levels") or {}
    context = snap.get("context") or {}
    price = float(snap.get("price") or 0)
    bull, bear, conflicts = [], [], []

    def side(condition: Optional[bool], positive: str, negative: str) -> None:
        if condition is True:
            bull.append(positive)
        elif condition is False:
            bear.append(negative)

    bias = structure.get("bias", "neutral")
    if bias == "bull":
        bull.append("Piyasa yapısı boğa yönünde")
    elif bias == "bear":
        bear.append("Piyasa yapısı ayı yönünde")
    e20, e50, e200 = ind.get("ema20"), ind.get("ema50"), ind.get("ema200")
    if e20 is not None and e50 is not None:
        side(e20 > e50, "EMA20, EMA50 üzerinde", "EMA20, EMA50 altında")
    if e200 is not None:
        side(price > e200, "Fiyat EMA200 üzerinde", "Fiyat EMA200 altında")
    if ind.get("vwap") is not None:
        side(price >= ind["vwap"], "Fiyat VWAP üzerinde", "Fiyat VWAP altında")
    if ind.get("supertrend_dir") in (1, -1):
        side(ind["supertrend_dir"] == 1, "Supertrend yukarı", "Supertrend aşağı")
    if ind.get("macd_histogram") is not None:
        side(ind["macd_histogram"] >= 0, "MACD histogram pozitif", "MACD histogram negatif")
    if ind.get("order_flow_delta_20") is not None:
        side(ind["order_flow_delta_20"] >= 0, "20 mumluk agresif akış alıcı", "20 mumluk agresif akış satıcı")
    if ind.get("cvd_divergence") and ind["cvd_divergence"] != "none":
        conflicts.append(f"CVD uyumsuzluğu: {ind['cvd_divergence']}")

    rsi = ind.get("rsi14")
    if rsi is not None:
        momentum = "aşırı alım" if rsi >= 70 else "aşırı satım" if rsi <= 30 else "nötr bant"
    else:
        momentum = "hesaplanamadı"
    adx = ind.get("adx14")
    trend_power = "güçlü" if adx is not None and adx >= 25 else "zayıf/kararsız"
    if bull and bear:
        conflicts.append("Trend filtreleri aynı yönde değil")
    verdict = "BOĞA AĞIRLIKLI" if len(bull) >= len(bear) + 2 else \
        "AYI AĞIRLIKLI" if len(bear) >= len(bull) + 2 else "KARIŞIK / NÖTR"

    sr = levels.get("support_resistance") or []
    supports = sorted((x for x in sr if x.get("price", 0) < price), key=lambda x: price - x["price"])
    resistances = sorted((x for x in sr if x.get("price", 0) > price), key=lambda x: x["price"] - price)
    liq = structure.get("liquidity_hunter") or {}
    oi = context.get("open_interest") or {}
    funding = context.get("funding") or {}
    interp = oi.get("interpretation") or {}

    lines = [
        f"<b>VORTEX · {e(snap.get('symbol'))} PİYASA ANALİZİ</b>",
        f"<code>{e(snap.get('interval'))}</code> · Fiyat <code>{e(round(price, 8))}</code>", "",
        f"<b>Genel okuma: {verdict}</b>",
        f"Yapı: <b>{e(str(bias).upper())}</b> · Rejim: {e((snap.get('regime') or {}).get('label', '—'))}",
        f"Momentum: RSI {e(round(rsi, 2) if rsi is not None else '—')} ({momentum}) · ADX {e(round(adx, 2) if adx is not None else '—')} ({trend_power})", "",
        "<b>Boğa lehine bulgular</b>",
        *(f"• {e(x)}" for x in bull or ["Belirgin üstünlük yok"]), "",
        "<b>Ayı lehine bulgular</b>",
        *(f"• {e(x)}" for x in bear or ["Belirgin üstünlük yok"]), "",
        "<b>Akış ve likidite</b>",
        f"• Son mum delta %{e(ind.get('order_flow_delta_pct', '—'))}; alıcı oranı %{e(ind.get('order_flow_buy_ratio', '—'))}",
        f"• 20 mum delta {e(ind.get('order_flow_delta_20', '—'))}; CVD değişimi {e(ind.get('cvd_change_20', '—'))}",
        f"• Yakın destek: {e(_level_text(supports, resistances, price, float(ind.get('atr14') or 0))[0])}",
        f"• Yakın direnç: {e(_level_text(supports, resistances, price, float(ind.get('atr14') or 0))[1])}",
        f"• Likidite avı sinyali {e(len(liq.get('signals', [])) if isinstance(liq, dict) else 0)} adet", "",
        "<b>Türev bağlamı</b>",
        f"• Funding %{e(funding.get('rate_pct', '—'))}",
        f"• OI 24s %{e(oi.get('change_24h_pct', '—'))}; fiyat 24s %{e(oi.get('price_change_24h_pct', '—'))}",
        f"• {e(interp.get('tr', 'Belirgin değil'))}: {e(interp.get('note', ''))}",
    ]
    if conflicts:
        lines += ["", "<b>Çelişki / risk</b>", *(f"• {e(x)}" for x in conflicts)]
    lines += ["", "<i>Bu metin piyasa bağlamı analizidir; giriş emri veya kâr vaadi değildir.</i>"]
    return "\n".join(lines)


def x_post(snap: Dict[str, Any]) -> str:
    """X/Premium veya flood icin ayrintili, veriye dayali piyasa analizi."""
    symbol = str(snap.get("symbol") or "")
    base = symbol.removesuffix("USDT") or symbol
    interval = str(snap.get("interval") or "")
    price = float(snap.get("price") or 0)
    ind = snap.get("indicators") or {}
    structure = snap.get("structure") or {}
    bias = structure.get("bias", "neutral")
    regime = snap.get("regime") or {}
    context = snap.get("context") or {}
    sr = (snap.get("levels") or {}).get("support_resistance") or []
    supports = sorted((x for x in sr if x.get("price", 0) < price), key=lambda x: price - x["price"])
    resistances = sorted((x for x in sr if x.get("price", 0) > price), key=lambda x: x["price"] - price)

    def p(value: Any) -> str:
        if value is None:
            return "—"
        value = float(value)
        return f"{value:.2f}" if abs(value) >= 10 else f"{value:.5f}".rstrip("0").rstrip(".")

    support1 = p(supports[0]["price"]) if supports else "—"
    support2 = p(supports[1]["price"]) if len(supports) > 1 else "—"
    resistance1 = p(resistances[0]["price"]) if resistances else "—"
    resistance2 = p(resistances[1]["price"]) if len(resistances) > 1 else "—"
    rsi_value, adx_value = ind.get("rsi14"), ind.get("adx14")
    ema20, ema50, ema200 = ind.get("ema20"), ind.get("ema50"), ind.get("ema200")
    ema = "EMA20 > EMA50" if (ema20 or 0) > (ema50 or 0) else "EMA20 < EMA50"
    long_filter = "EMA200 üzerinde" if ema200 is not None and price >= ema200 else "EMA200 altında"
    vwap_side = "VWAP üstü" if price >= (ind.get("vwap") or price) else "VWAP altı"
    flow = float(ind.get("order_flow_delta_20") or 0); flow_sign = "+" if flow >= 0 else ""
    last_delta = ind.get("order_flow_delta_pct"); buy_ratio = ind.get("order_flow_buy_ratio")
    buy_ratio20 = ind.get("order_flow_buy_ratio_20"); cvd20 = ind.get("cvd_change_20")
    bias_text = {"bull": "BULL", "bear": "BEAR"}.get(bias, "NÖTR")
    last_event = structure.get("last_event") or {}
    event_text = f"{last_event.get('kind', '—')} / {str(last_event.get('direction', '—')).upper()}"
    liquidity = structure.get("liquidity_hunter") or {}
    upper_liq = (liquidity.get("upper_levels") or [{}])[0].get("price")
    lower_liq = (liquidity.get("lower_levels") or [{}])[0].get("price")
    recent_sweeps = liquidity.get("signals") or []
    sweep_text = "yok"
    if recent_sweeps:
        last_sweep = recent_sweeps[-1]
        sweep_text = f"{str(last_sweep.get('direction', '—')).upper()} reclaim {p(last_sweep.get('reclaim_level'))}"
    funding = context.get("funding") or {}; oi = context.get("open_interest") or {}
    interpretation = oi.get("interpretation") or {}
    adx_comment = "trend güçlü" if adx_value is not None and adx_value >= 25 else "trend gücü zayıf; teyit gerekli"
    flow_comment = "alıcı baskısı" if flow > 0 and (buy_ratio20 or 0) >= 50 else "satıcı baskısı" if flow < 0 and (buy_ratio20 or 100) < 50 else "akış karışık"
    # Seviye listesi bos kalabilir: fiyat pencerenin tepesinde/dibindeyse bu
    # DOGRU bir bilgidir, ama "—" yazmak okuyana hesap bozuk hissi veriyordu.
    # Bos tarafi acikca soyleyip yerine ATR projeksiyonu koyuyoruz.
    atr_now = float(ind.get("atr14") or 0)
    sup_line, res_line = _level_text(supports, resistances, price, atr_now)

    # Referans seviye yoksa ATR projeksiyonunu kullan; metinde "—" gorunmesin.
    sup_ref = support1 if supports else (p(price - 1.5 * atr_now) if atr_now else "—")
    res_ref = resistance1 if resistances else (p(price + 1.5 * atr_now) if atr_now else "—")
    sup_kind = "destek" if supports else "ATR projeksiyonu"
    res_kind = "direnç" if resistances else "ATR projeksiyonu"

    if not resistances and atr_now:
        thesis = (f"Fiyat bakılan pencerenin tepesinde; üstte yapısal direnç yok. "
                  f"Bu boğa lehine ama referanssız bir bölge — ilk ölçülebilir hedef "
                  f"ATR projeksiyonu {p(price + 1.5 * atr_now)}. "
                  f"Aşağıda {sup_ref} ({sup_kind}) kaybedilirse yapı bozulur.")
    elif not supports and atr_now:
        thesis = (f"Fiyat pencerenin dibinde; altta yapısal destek yok. "
                  f"İlk ölçülebilir bölge ATR projeksiyonu {p(price - 1.5 * atr_now)}. "
                  f"Yukarıda {res_ref} ({res_kind}) geri alınmadan tez değişmez.")
    elif bias == "bull":
        thesis = f"{support1} üzerinde yapı korunuyor. {resistance1} hacimli aşılırsa boğa devamlılığı güçlenir; {support1} altı kapanışta tez zayıflar ve {support2} gündeme gelir."
    elif bias == "bear":
        thesis = f"{resistance1} altında ayı yapısı korunuyor. {support1} kaybedilirse satış baskısı {support2} bölgesine genişleyebilir; {resistance1} üstü kapanış ayı tezini zayıflatır."
    else:
        thesis = f"Fiyat {support1}–{resistance1} karar aralığında. Yön iddiası için seviyelerden birinin hacim ve order flow teyidiyle kırılması gerekiyor."
    return "\n".join([
        f"📊 ${base} | {interval} Piyasa Analizi",
        f"Anlık fiyat: {p(price)} · Yapı: {bias_text} · Rejim: {regime.get('label', '—')}", "",
        "🔎 Yapı ve trend",
        f"Son yapı olayı {event_text}. {ema}; fiyat {long_filter} ve {vwap_side}. Supertrend {'yukarı' if ind.get('supertrend_dir') == 1 else 'aşağı'} tarafta.",
        f"RSI {p(rsi_value)}, ADX {p(adx_value)} ({adx_comment}). MACD histogram {p(ind.get('macd_histogram'))}; +DI {p(ind.get('plus_di'))}, -DI {p(ind.get('minus_di'))}.", "",
        "📈 Order Flow",
        f"Son mum delta %{p(last_delta)}, agresif alıcı oranı %{p(buy_ratio)}. Son 20 mum delta {flow_sign}{flow:.2f} ve alıcı oranı %{p(buy_ratio20)}: {flow_comment}.",
        f"CVD 20 mum değişimi {p(cvd20)}; uyumsuzluk: {ind.get('cvd_divergence') or 'yok'}.", "",
        "📍 Kritik bölgeler",
        f"Destekler: {sup_line}",
        f"Dirençler: {res_line}",
        f"Yakın alt likidite: {p(lower_liq)} · üst likidite: {p(upper_liq)} · son sweep: {sweep_text}.", "",
        "🧭 Senaryo",
        thesis, "",
        "⚙️ Türev bağlamı",
        f"Funding %{p(funding.get('rate_pct'))}; OI 24s %{p(oi.get('change_24h_pct'))}, fiyat 24s %{p(oi.get('price_change_24h_pct'))}. {interpretation.get('tr', 'Belirgin değil')}: {interpretation.get('note', '')}", "",
        "Bu paylaşım piyasa bağlamıdır; tek başına işlem emri değildir.",
        f"#{base} #Kripto #TeknikAnaliz #OrderFlow",
    ])


def detailed_analysis(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Bagimsiz veri ailelerinden okunabilir ana tez ve karsi tez uretir.

    Bu fonksiyon FVG/OB/sweep gibi ayni fiyat orgusunden tureyen bulgulari
    bagimsiz oylar gibi ust uste toplamaz. Yon kalitesi yalnizca yapi/trend,
    momentum, order flow ve turev baglami arasindaki uyuma dayanir.
    """
    symbol = str(snap.get("symbol") or "")
    interval = str(snap.get("interval") or "")
    price = float(snap.get("price") or 0)
    ind = snap.get("indicators") or {}
    structure = snap.get("structure") or {}
    regime = snap.get("regime") or {}
    context = snap.get("context") or {}
    levels = (snap.get("levels") or {}).get("support_resistance") or []

    def num(key: str, default: float = 0.0) -> float:
        try:
            value = ind.get(key)
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def fmt(value: Any) -> str:
        if value is None:
            return "—"
        try:
            n = float(value)
            if abs(n) >= 1000:
                return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if abs(n) >= 10:
                return f"{n:.2f}".replace(".", ",")
            return f"{n:.6f}".rstrip("0").rstrip(".").replace(".", ",")
        except (TypeError, ValueError):
            return str(value)

    supports = sorted(
        (x for x in levels if float(x.get("price", 0) or 0) < price),
        key=lambda x: price - float(x.get("price", 0) or 0),
    )
    resistances = sorted(
        (x for x in levels if float(x.get("price", 0) or 0) > price),
        key=lambda x: float(x.get("price", 0) or 0) - price,
    )
    atr = num("atr14")
    support = float(supports[0]["price"]) if supports else (price - 1.5 * atr if atr else None)
    resistance = float(resistances[0]["price"]) if resistances else (price + 1.5 * atr if atr else None)

    bias = str(structure.get("bias") or "neutral")
    ema20, ema50, ema200 = num("ema20"), num("ema50"), num("ema200")
    rsi, adx = num("rsi14", 50), num("adx14")
    macd_hist = num("macd_histogram")
    supertrend_dir = int(num("supertrend_dir"))
    flow_delta = num("order_flow_delta_20")
    buy_ratio = num("order_flow_buy_ratio_20", 50)
    cvd_change = num("cvd_change_20")

    votes: Dict[str, int] = {}
    votes["structure"] = 1 if bias == "bull" else -1 if bias == "bear" else 0
    trend_parts = [
        1 if ema20 > ema50 else -1 if ema20 < ema50 else 0,
        1 if supertrend_dir == 1 else -1 if supertrend_dir == -1 else 0,
        1 if ema200 and price > ema200 else -1 if ema200 and price < ema200 else 0,
    ]
    trend_sum = sum(trend_parts)
    votes["trend"] = 1 if trend_sum > 0 else -1 if trend_sum < 0 else 0
    momentum_sum = (1 if macd_hist > 0 else -1 if macd_hist < 0 else 0) + (1 if rsi > 55 else -1 if rsi < 45 else 0)
    votes["momentum"] = 1 if momentum_sum > 0 else -1 if momentum_sum < 0 else 0
    flow_sum = (1 if flow_delta > 0 else -1 if flow_delta < 0 else 0) + (1 if buy_ratio > 52 else -1 if buy_ratio < 48 else 0) + (1 if cvd_change > 0 else -1 if cvd_change < 0 else 0)
    votes["order_flow"] = 1 if flow_sum > 0 else -1 if flow_sum < 0 else 0

    oi = context.get("open_interest") or {}
    oi_code = ((oi.get("interpretation") or {}).get("code") or "neutral")
    votes["derivatives"] = 1 if oi_code in ("new_longs", "short_covering") else -1 if oi_code in ("new_shorts", "long_flush") else 0
    total = sum(votes.values())
    direction = "Yukarı eğilim" if total >= 2 else "Aşağı eğilim" if total <= -2 else "Kararsız / yatay"
    direction_code = "bull" if total >= 2 else "bear" if total <= -2 else "neutral"
    aligned = max(sum(1 for value in votes.values() if value > 0), sum(1 for value in votes.values() if value < 0))
    quality = "güçlü uyum" if aligned >= 4 and adx >= 25 else "orta uyum" if aligned >= 3 else "zayıf / çelişkili"

    evidence_for: List[str] = []
    evidence_against: List[str] = []
    for key, label in (("structure", "Piyasa yapısı"), ("trend", "EMA/Supertrend"), ("momentum", "RSI/MACD"), ("order_flow", "Order flow/CVD"), ("derivatives", "OI/funding bağlamı")):
        value = votes[key]
        supports_thesis = value > 0 if direction_code == "bull" else value < 0 if direction_code == "bear" else False
        if supports_thesis:
            evidence_for.append(f"{label} ana yönü destekliyor")
        elif value:
            evidence_against.append(f"{label} ana yönle çelişiyor")

    if direction_code == "bull":
        summary = f"{symbol} {interval} görünümünde bağımsız veri ailelerinin çoğu yukarı yönü destekliyor; fakat {fmt(resistance)} aşılmadan devam teyidi tamamlanmış sayılmaz."
        confirmation = f"{fmt(resistance)} üzerinde kapanış, pozitif 20 mum delta ve %52 üzeri agresif alıcı oranı birlikte görülmeli."
        invalidation = f"{fmt(support)} altında kapanış ana tezi zayıflatır; EMA20/50 aşağı kesişimi ve negatif CVD bunu teyit eder."
    elif direction_code == "bear":
        summary = f"{symbol} {interval} görünümünde yapı, momentum ve akış aşağı baskıya işaret ediyor; fakat {fmt(support)} kırılmadan satış devamı teyit edilmiş sayılmaz."
        confirmation = f"{fmt(support)} altında kapanış, negatif 20 mum delta ve %48 altı agresif alıcı oranı birlikte görülmeli."
        invalidation = f"{fmt(resistance)} üzerinde kapanış ayı tezini zayıflatır; EMA20/50 yukarı kesişimi ve pozitif CVD bunu teyit eder."
    else:
        summary = f"{symbol} {interval} görünümünde veri aileleri aynı yönde hizalanmıyor. {fmt(support)}–{fmt(resistance)} aralığında yön iddiası kurmak zayıf risk yönetimidir."
        confirmation = f"Yukarıda {fmt(resistance)}, aşağıda {fmt(support)} kapanışla kırılmalı; kırılım order flow ve hacimle teyit edilmelidir."
        invalidation = "Yatay senaryo, karar aralığının dışında kapanış ve devam mumu geldiğinde biter."

    blind_spots: List[str] = []
    if snap.get("last_bar_closed") is False:
        blind_spots.append("Son mum açık; kapanmadan görülen sinyal geri boyanabilir.")
    if adx and adx < 20:
        blind_spots.append(f"ADX {fmt(adx)}: trend gücü düşük, kesişimlerin yanlış sinyal riski yüksek.")
    if rsi >= 70 or rsi <= 30:
        blind_spots.append(f"RSI {fmt(rsi)} aşırı bölgede; bu tek başına dönüş sinyali değildir.")
    funding = context.get("funding") or {}
    funding_pct = funding.get("rate_pct")
    if funding_pct is not None and abs(float(funding_pct)) >= 0.03:
        blind_spots.append(f"Funding %{fmt(funding_pct)}: kaldıraç tek tarafa yığılmış olabilir.")
    if not oi or oi.get("error"):
        blind_spots.append("Açık pozisyon bağlamı eksik; türev teyidi sınırlı.")
    if evidence_against:
        blind_spots.append("Bağımsız göstergeler tam hizalı değil; tek göstergeye güvenme.")
    if not blind_spots:
        blind_spots.append("Haber, likidite boşluğu ve ani volatilite teknik yapıyı geçersiz kılabilir.")

    return {
        "direction": direction,
        "direction_code": direction_code,
        "quality": quality,
        "summary": summary,
        "evidence_for": evidence_for or ["Ana yön için yeterli bağımsız teyit yok"],
        "evidence_against": evidence_against or ["Belirgin karşı teyit yok; bu kesinlik anlamına gelmez"],
        "confirmation": confirmation,
        "invalidation": invalidation,
        "bull_scenario": f"{fmt(resistance)} üzeri kabul ve pozitif akış, bir sonraki yapısal direnç/ATR projeksiyonunu gündeme getirir.",
        "bear_scenario": f"{fmt(support)} altı kabul ve negatif akış, bir sonraki destek/ATR projeksiyonuna baskıyı artırır.",
        "blind_spots": blind_spots,
        "metrics": {
            "price": fmt(price), "support": fmt(support), "resistance": fmt(resistance),
            "rsi": fmt(rsi), "adx": fmt(adx), "flow_delta_20": fmt(flow_delta),
            "buy_ratio_20": fmt(buy_ratio), "regime": regime.get("label") or regime.get("regime") or "—",
        },
    }


def compute(symbol: str, interval: str, rows: List[list],
            include_series: bool = False) -> Dict[str, Any]:
    """Mumlardan analizi uretir. TAMAMEN SENKRONDUR — ag erisimi yoktur.

    Ayri bir fonksiyon olmasinin sebebi: bu hesap saf numpy'dir ve sembol
    basina ~40-70 ms boyunca olay dongusunu BLOKLAR. 24 sembollu taramada
    onemsizdi; 300 sembollu taramada toplam ~20 saniye eder ve WebSocket
    fiyat akisi her taramada donar. Senkron oldugu icin cagiran taraf bunu
    asyncio.to_thread ile ayri bir is parcacigina atabilir.
    """
    k = binance.parse_klines(rows)
    n = len(k["close"])
    if n < 60:
        return {"symbol": symbol, "interval": interval, "ok": False,
                "error": "yetersiz veri", "bars": n}

    o, h, l, c, v = k["open"], k["high"], k["low"], k["close"], k["volume"]
    tb, ot = k["taker_buy"], k["open_time"]

    # --- son mum kapandi mi? (repaint uyarisi icin) ---
    import time as _t
    last_closed = int(k["close_time"][-1]) < int(_t.time() * 1000)

    rsi14 = core.rsi(c, 14)
    atr14 = core.atr(h, l, c, 14)
    ema20, ema50 = core.ema(c, 20), core.ema(c, 50)
    ema200 = core.ema(c, 200) if n > 200 else np.full(n, np.nan)
    basis, bb_up, bb_low, _ = core.bollinger(c, 20, 2.0)
    bbw = core.bb_width(c, 20, 2.0)
    st_line, st_dir = core.supertrend(h, l, c, 10, 3.0)
    adx14, plus_di, minus_di = core.adx(h, l, c, 14)
    macd_line, macd_signal, macd_hist = _macd(c)
    stoch_k, stoch_d = _stoch_rsi(rsi14)
    ichi_tenkan, ichi_kijun, ichi_a, ichi_b = _ichimoku(h, l)
    session_vwap = vw.session_vwap(h, l, c, v, ot)
    avwap = vw.anchored_vwap(h, l, c, v, anchor=max(n - 120, 0))
    cvd_series = cvd_mod.cvd(v, tb)
    delta_series = cvd_mod.delta(v, tb)
    with np.errstate(divide="ignore", invalid="ignore"):
        delta_pct = np.divide(delta_series, v, out=np.zeros_like(delta_series), where=v != 0) * 100.0
        buy_ratio = np.divide(tb, v, out=np.full_like(tb, .5), where=v != 0) * 100.0
    price = float(c[-1])
    atr_now = _last(atr14) or 0.0

    # --- yapi katmani ---
    events = st.structure_events(h, l, c)
    sweeps = st.liquidity_sweeps(h, l, c, o)
    gaps = st.fair_value_gaps(h, l, c)
    obs = st.order_blocks(o, h, l, c)
    crts = st.crt_setups(o, h, l, c)
    eq = st.equal_levels(h, l)
    divs = st.rsi_divergences(h, l, rsi14)
    liquidity_hunter = ls_hunter.detect(o, h, l, c, v)
    pd_zones = st.premium_discount(h, l, min(120, n))

    sr = lv.pivot_levels(h, l, c)
    near = lv.nearest_levels(sr, price)
    vp = lv.volume_profile(h, l, c, v, bins=40)
    regime = rg.classify(h, l, c)

    recent = max(n - 30, 0)   # "yakin gecmis" penceresi

    def _recent(items, attr="index"):
        return [x.dict() for x in items if getattr(x, attr) >= recent]

    bbw_series = bbw[~np.isnan(bbw)]
    squeeze = None
    if len(bbw_series) >= 60:
        pct = float(np.mean(bbw_series[-1] <= bbw_series[-120:]))
        squeeze = {"width": round(float(bbw_series[-1]), 3),
                   "percentile": round(1 - pct, 2),
                   "is_squeeze": bool(pct >= 0.85)}

    snap: Dict[str, Any] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "ok": True,
        "bars": n,
        "last_bar_closed": last_closed,
        "price": round(price, 8),
        "time": int(ot[-1]),
        "demo": binance.is_demo(),
        "indicators": {
            "rsi14": _last(rsi14),
            "atr14": atr_now,
            "atr_pct": round(atr_now / price * 100, 3) if price else None,
            "ema20": _last(ema20), "ema50": _last(ema50), "ema200": _last(ema200),
            "bb_upper": _last(bb_up), "bb_basis": _last(basis), "bb_lower": _last(bb_low),
            "bb_squeeze": squeeze,
            "supertrend": _last(st_line),
            "supertrend_dir": int(st_dir[-1]) if len(st_dir) else 0,
            "adx14": _last(adx14), "plus_di": _last(plus_di), "minus_di": _last(minus_di),
            "macd": _last(macd_line), "macd_signal": _last(macd_signal),
            "macd_histogram": _last(macd_hist),
            "stoch_rsi_k": _last(stoch_k), "stoch_rsi_d": _last(stoch_d),
            "ichimoku_tenkan": _last(ichi_tenkan), "ichimoku_kijun": _last(ichi_kijun),
            "ichimoku_span_a": _last(ichi_a), "ichimoku_span_b": _last(ichi_b),
            "vwap": _last(session_vwap),
            "avwap": _last(avwap["vwap"]),
            "avwap_upper1": _last(avwap["upper_1"]),
            "avwap_lower1": _last(avwap["lower_1"]),
            "avwap_upper2": _last(avwap["upper_2"]),
            "avwap_lower2": _last(avwap["lower_2"]),
            "cvd": round(float(cvd_series[-1]), 4),
            "cvd_change_20": round(float(cvd_series[-1] - cvd_series[-21]), 4) if n > 21 else None,
            "cvd_divergence": cvd_mod.cvd_divergence(c, cvd_series),
            "order_flow_delta": round(float(delta_series[-1]), 4),
            "order_flow_delta_pct": round(float(delta_pct[-1]), 3),
            "order_flow_delta_20": round(float(np.sum(delta_series[-20:])), 4),
            "order_flow_buy_ratio": round(float(buy_ratio[-1]), 2),
            "order_flow_buy_ratio_20": round(float(np.sum(tb[-20:]) / np.sum(v[-20:]) * 100), 2)
            if float(np.sum(v[-20:])) else 50.0,
        },
        "regime": {**regime, "label": rg.REGIME_LABEL.get(regime["regime"], regime["regime"])},
        "structure": {
            "bias": events[-1].direction if events else "neutral",
            "last_event": events[-1].dict() if events else None,
            "events": [e.dict() for e in events[-8:]],
        },
        "findings": {
            "sweeps": _recent(sweeps),
            "crt": _recent(crts),
            "divergences": _recent(divs),
            "fvg_active": [g.dict() for g in st.active_fvgs(gaps)][-6:],
            "fvg_inversed": [g.dict() for g in st.inversed_fvgs(gaps)][-6:],
            "order_blocks": [b.dict() for b in obs if b.mitigated_at is None][-6:],
            "breakers": [b.dict() for b in obs if b.is_breaker][-4:],
            "liquidity_hunter": liquidity_hunter,
        },
        "levels": {
            "support_resistance": sr,
            "nearest": near,
            "equal_highs": eq["eqh"][-5:],
            "equal_lows": eq["eql"][-5:],
            "volume_profile": {kk: vv for kk, vv in vp.items() if kk != "bins"},
            "premium_discount": {kk: round(vv, 8) for kk, vv in pd_zones.items()},
        },
        "evidence": EVIDENCE,
    }
    snap["trade_plan"] = build_trade_plan(snap)

    if include_series:
        snap["series"] = {
            "candles": [{"time": int(ot[i] // 1000), "open": float(o[i]), "high": float(h[i]),
                         "low": float(l[i]), "close": float(c[i]), "volume": float(v[i])}
                        for i in range(n)],
            "rsi": [None if np.isnan(x) else round(float(x), 3) for x in rsi14],
            "atr": [None if np.isnan(x) else round(float(x), 8) for x in atr14],
            "ema20": [None if np.isnan(x) else round(float(x), 8) for x in ema20],
            "ema50": [None if np.isnan(x) else round(float(x), 8) for x in ema50],
            "ema200": [None if np.isnan(x) else round(float(x), 8) for x in ema200],
            "vwap": [None if np.isnan(x) else round(float(x), 8) for x in session_vwap],
            "bb_upper": [None if np.isnan(x) else round(float(x), 8) for x in bb_up],
            "bb_lower": [None if np.isnan(x) else round(float(x), 8) for x in bb_low],
            "supertrend": [None if np.isnan(x) else round(float(x), 8) for x in st_line],
            "macd": [None if np.isnan(x) else round(float(x), 8) for x in macd_line],
            "macd_signal": [None if np.isnan(x) else round(float(x), 8) for x in macd_signal],
            "macd_histogram": [None if np.isnan(x) else round(float(x), 8) for x in macd_hist],
            "stoch_rsi_k": [None if np.isnan(x) else round(float(x), 3) for x in stoch_k],
            "stoch_rsi_d": [None if np.isnan(x) else round(float(x), 3) for x in stoch_d],
            "adx": [None if np.isnan(x) else round(float(x), 3) for x in adx14],
            "plus_di": [None if np.isnan(x) else round(float(x), 3) for x in plus_di],
            "minus_di": [None if np.isnan(x) else round(float(x), 3) for x in minus_di],
            "ichimoku_tenkan": [None if np.isnan(x) else round(float(x), 8) for x in ichi_tenkan],
            "ichimoku_kijun": [None if np.isnan(x) else round(float(x), 8) for x in ichi_kijun],
            "ichimoku_span_a": [None if np.isnan(x) else round(float(x), 8) for x in ichi_a],
            "ichimoku_span_b": [None if np.isnan(x) else round(float(x), 8) for x in ichi_b],
            "cvd": [round(float(x), 4) for x in cvd_series],
            "order_flow_delta": [round(float(x), 4) for x in delta_series],
            "order_flow_delta_pct": [round(float(x), 3) for x in delta_pct],
            "volume_profile_bins": vp.get("bins", []),
        }
    return snap


async def snapshot(symbol: str, interval: str = "15m", limit: int = 500,
                   include_series: bool = False,
                   rows: Optional[List[list]] = None) -> Dict[str, Any]:
    """Mumlari getirir ve compute()'a devreder.

    rows verilirse Binance'e hic gidilmez. Backtest bunu kullanir: her barda
    yalnizca O BARA KADAR olan mumlari gecerek look-ahead'i imkansiz kilar ve
    canli sistemle BIREBIR ayni karar zincirini calistirir.
    """
    if rows is None:
        rows = await binance.klines(symbol, interval, limit)
    return compute(symbol, interval, rows, include_series)


async def market_context(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Turev piyasa baglami: funding + OI degisimi + OI/fiyat matrisi."""
    out: Dict[str, Any] = {"symbol": symbol.upper()}
    try:
        pi = await binance.premium_index(symbol)
        rate = float(pi.get("lastFundingRate", 0) or 0)
        out["funding"] = {
            "rate": rate,
            "rate_pct": round(rate * 100, 5),
            "annualized_pct": round(rate * 3 * 365 * 100, 2),
            "next_time": pi.get("nextFundingTime"),
            "mark_price": float(pi.get("markPrice", 0) or 0),
        }
    except Exception as exc:  # noqa: BLE001
        out["funding"] = {"error": str(exc)[:100]}

    try:
        oi = await binance.open_interest_hist(symbol, "1h", 25)
        if len(oi) >= 2:
            first = float(oi[0]["sumOpenInterestValue"])
            last = float(oi[-1]["sumOpenInterestValue"])
            change = (last - first) / first * 100 if first else 0.0
            klines = await binance.klines(symbol, "1h", 25)
            pc = ((float(klines[-1][4]) - float(klines[0][4])) / float(klines[0][4]) * 100
                  if klines else 0.0)
            out["open_interest"] = {
                "value_usd": last,
                "change_24h_pct": round(change, 2),
                "price_change_24h_pct": round(pc, 2),
                "interpretation": _oi_matrix(pc, change),
                "series": [{"t": int(x["timestamp"]), "v": float(x["sumOpenInterestValue"])}
                           for x in oi],
            }
    except Exception as exc:  # noqa: BLE001
        out["open_interest"] = {"error": str(exc)[:100]}
    return out


def _oi_matrix(price_change: float, oi_change: float) -> Dict[str, str]:
    """Fiyat/OI dortlusu — fiyat serisinden turetilemeyen bir bilgi."""
    up_p, up_oi = price_change > 0.15, oi_change > 0.5
    dn_p, dn_oi = price_change < -0.15, oi_change < -0.5
    if up_p and up_oi:
        return {"code": "new_longs", "tr": "Yeni long girişi",
                "note": "Fiyat ve OI birlikte artıyor — taze kaldıraçlı alım."}
    if up_p and dn_oi:
        return {"code": "short_covering", "tr": "Short kapanışı",
                "note": "Fiyat artarken OI düşüyor — yükseliş kapanışlardan besleniyor, zayıf."}
    if dn_p and up_oi:
        return {"code": "new_shorts", "tr": "Yeni short girişi",
                "note": "Fiyat düşerken OI artıyor — taze satış baskısı."}
    if dn_p and dn_oi:
        return {"code": "long_flush", "tr": "Long tasfiyesi",
                "note": "Fiyat ve OI birlikte düşüyor — zorunlu kapanışlar, sıklıkla dip bölgesi."}
    return {"code": "neutral", "tr": "Belirgin değil", "note": "OI ve fiyat net bir yön vermiyor."}
