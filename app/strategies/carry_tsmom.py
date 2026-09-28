"""Carry + zaman serisi momentumu (TSMOM) — VORTEX'in yeni karar cekirdegi.

NEDEN BU, GOSTERGE SKORU DEGIL
------------------------------
Eski motor bes gostergeyi oy olarak topluyordu. Sorun gostergelerin "kotu"
olmasi degil: hepsi AYNI fiyat serisinin turevi. Bes gosterge bes bagimsiz
kanit gibi gorunuyor ama aslinda tek bir seyin bes kez sayilmasi. 27.08
olcumu bunu dogruladi — skor 5, skor 4'ten KOTU cikti; yani skor guven
olcmuyordu.

Buradaki iki sinyalin dayanagi farkli:

  1. TSMOM — "bir varlik uzun suredir yukseliyorsa yukselmeye devam etme
     egilimindedir." Hisse, emtia, tahvil, FX ve kriptoda bagimsiz olarak
     olculmus en eski anomalilerden biri. Kriptoda kesitsel momentumdan
     (XSMOM) daha saglam cikiyor; XSMOM'un short bacagi sicrama riskinden
     patliyor.

  2. FONLAMA (carry) — bu bir gosterge degil, SOZLESME. Fonlama pozitifken
     long tutan taraf her 8 saatte bir odeme YAPAR. Yani hem gercek bir
     maliyet, hem de kalabalikligin dogrudan olcusu. Tahmin degil aritmetik.

TASARIMIN UC KURALI
-------------------
A) OY YOK. Tek bir yon sinyali var (harmanlanmis momentum). Ikinci bir
   gosterge "onaylamiyor" — cunku ayni seriden turemis bir onay, onay
   degil, ayni bilginin ikinci kez sayilmasidir.

B) MALIYET KARARIN ICINDE. Eski motorda maliyet backtest raporunda, yani
   is bittikten SONRA goruluyordu. Burada maliyet_R kurulum reddedilirken
   hesaplaniyor: masrafi tavani asan kurulum hic acilmiyor.

C) OLCEK GURULTUYE GORE. Ham getiri karsilastirilamaz — %10 yukselen
   sakin bir coin ile %10 yukselen carpik bir coin ayni sey degil. Momentum
   kendi oynakligina bolunuyor (t degeri), boylece butun evren ayni
   cetvelle olculuyor.

BU MODUL AG ERISIMI YAPMAZ. Girdi olarak mumlari ve fonlamayi alir, karari
dondurur. Boylece tek basina, sentetik veriyle test edilebiliyor.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

# Momentum bu ufuklarin ORTALAMASI. Neden ortalama, neden "hepsi ayni yonu
# gostermeli" degil: uc ufuk birbiriyle yuksek korelasyonlu; "ucu de ayni
# yonde" sarti bagimsiz kanit toplamaz, sadece esigi gizlice yukseltir.
# Ortalama almak varyansi dusurur ve ne yaptigi acikca gorunur.
LOOKBACKS = (21, 63, 126)          # ~1 ay, ~3 ay, ~6 ay (gunluk bar)
VOL_WINDOW = 60                    # oynaklik penceresi (gun)
MIN_BARS = max(LOOKBACKS) + VOL_WINDOW + 5

DEFAULTS = {
    # OLCULDU (3000 sentetik rastgele yuruyus, gunluk %4 oynaklik):
    #   esik 0.55 -> yuruyuslerin %53'u geciyor  (filtre degil)
    #   esik 1.00 -> %24
    #   esik 1.50 -> %8
    # 1.0 bir ANLAMLILIK TESTI degil, taban. TSMOM'un iddiasi zaten "isaret
    # bilgi tasir"; esik cok yukselirse hareketin sonunda girilir. Asil
    # frekans kontrolu motorun |t|'ye gore siralayip en iyi N'i almasi.
    "min_abs_t": 1.0,
    "stop_atr_mult": 2.5,          # gunluk ATR carpani
    "target_r": 4.0,               # uzak hedef; asil cikis sinyal donusu
    "horizon_days": 14,
    # Maliyet tavani. 0.05R cok siki: 14 gunluk tutusta normal fonlama
    # (1bp) tek basina ~0.04R eder ve her kurulumu elerdi. 0.10R, kazanani
    # 3-4R kosan bir stratejide kabul edilebilir bir asinma.
    # NOT: SHORT tarafi pozitif fonlamada TAHSIL eder, yani maliyeti duser —
    # carry sinyalinin karara dogrudan girdigi yer burasi.
    "max_cost_r": 0.10,
    "taker_fee": 0.00045,
    "maker_fee": 0.00020,
    "slippage": 0.0002,
    "use_maker_entry": True,
    "funding_extreme_bp": 5.0,     # 8 saatlik fonlama, baz puan (%0.05)
    # Fiyatin sinyal barindan izin verilen maks. sapmasi (ATR cinsinden).
    # 0.75 ATR: hareketin dortte ucu olmussa artik geç kalinmistir.
    # Cikis tabani: pozisyon yonundeki momentum bunun altina duserse cikilir.
    # Giris 1.0, cikis 0.30 — girdigin sebebin ucte birine inmesi cikmak icin
    # yeterli. 0.15 denendi, cok pasifti: momentum yariya inmis pozisyonlar
    # 14 gunluk sure stopunu bekliyor ve yeni kurulumlara yer birakmiyordu.
    "exit_t_floor": 0.30,
    "max_chase_atr": 0.75,
    # Stop mesafesinin fiyata orani icin UST SINIR.
    #
    # NEDEN GEREKLI: stop = ATR x carpan. ATR yuzdesi serbest birakilirsa
    # stop da serbest kalir. max_atr_pct 15 iken stop %37.5'e cikabiliyordu —
    # ve 3x kaldiracta likidasyon %33'te. Yani stop TETIKLENMEDEN pozisyon
    # likide olurdu; kagit uzerinde duran ama hicbir zaman calismayan bir
    # stop, stop degildir.
    #
    # Gercek sinir kaldiractan gelir: likidasyon ~ %100/kaldirac. Stop bunun
    # guvenli bir kesrinin altinda kalmali. Asagidaki sabit ust tavan,
    # kaldirac hesabiyla birlikte hangisi kucukse o baglar.
    "max_stop_pct": 15.0,
    "leverage": 3,
    "liq_safety": 0.65,            # stopun likidasyon mesafesine orani, en fazla
    "min_atr_pct": 0.8,            # gunluk ATR fiyatin en az %'si (cok sakin = maliyet yer)
    "max_atr_pct": 15.0,           # cok carpik = stop mesafesi anlamsizlasir
}


# --------------------------------------------------------------------------- #
# Yardimcilar
# --------------------------------------------------------------------------- #
def _closes(rows: Sequence[Sequence]) -> np.ndarray:
    return np.array([float(r[4]) for r in rows], dtype=float)


def daily_atr(rows: Sequence[Sequence], period: int = 14) -> Optional[float]:
    """Wilder ATR — gunluk barlarda. Stop mesafesinin temeli."""
    if len(rows) < period + 1:
        return None
    high = np.array([float(r[2]) for r in rows], float)
    low = np.array([float(r[3]) for r in rows], float)
    close = np.array([float(r[4]) for r in rows], float)
    prev = close[:-1]
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - prev), np.abs(low[1:] - prev)))
    if len(tr) < period:
        return None
    atr = float(tr[:period].mean())
    for x in tr[period:]:                      # Wilder yumusatmasi
        atr = (atr * (period - 1) + float(x)) / period
    return atr


def momentum_t(closes: np.ndarray, lookbacks: Sequence[int] = LOOKBACKS,
               vol_window: int = VOL_WINDOW) -> Optional[Dict[str, Any]]:
    """Oynakliga bolunmus momentum.

    Her ufuk icin:  t_L = log(P_t / P_{t-L}) / (sigma_gunluk * sqrt(L))

    Payda, L gun boyunca SANSLA birikebilecek hareketin buyuklugu. Yani t,
    "bu hareket kendi gurultusunun kac kati" sorusunun cevabi. Bu olceklemeyi
    yapmadan sakin bir coin ile carpik bir coin ayni cetvelle olculemez.
    """
    n = len(closes)
    if n < max(lookbacks) + vol_window + 2:
        return None
    logret = np.diff(np.log(closes))
    sigma = float(np.std(logret[-vol_window:], ddof=1))
    # Sigma tabani. Neredeyse duz bir seride sigma sifira gider ve t degeri
    # patlar (olculdu: mukemmel duz us serisinde t = 2.7e14). Boyle bir coin
    # evrenin EN GUCLU sinyali gibi gorunur, oysa tamamen sayisal gurultudur.
    # Gunluk %0.2'nin altinda oynaklik olan bir perp ya olmustur ya da verisi
    # bozuktur; ikisinde de islem acilmamali.
    if not np.isfinite(sigma) or sigma < 0.002:
        return None

    per: Dict[int, float] = {}
    for L in lookbacks:
        if n <= L:
            continue
        r = float(np.log(closes[-1] / closes[-1 - L]))
        per[L] = r / (sigma * math.sqrt(L))
    if not per:
        return None
    blended = float(np.mean(list(per.values())))
    return {
        "t": blended,
        "per_lookback": {str(k): round(v, 3) for k, v in per.items()},
        "sigma_daily": sigma,
        "sigma_annual": sigma * math.sqrt(365),
        # Ufuklar ayni yonu gosteriyor mu? KARARA GIRMEZ — sadece raporlanir,
        # cunku bunu sarta baglamak korelasyonlu kaniti iki kez saymaktir.
        "agree": len({1 if v > 0 else -1 for v in per.values()}) == 1,
    }


def funding_context(current_rate: Optional[float],
                    history: Optional[Sequence[float]] = None,
                    recent_periods: int = 21) -> Dict[str, Any]:
    """Fonlamayi hem seviye hem de KENDI gecmisine gore konumlandirir.

    Mutlak esik tek basina yaniltir: bazi coinlerde %0.05 normaldir, bazisinda
    asiridir. Yuzdelik dilim bunu duzeltir.
    """
    rate = float(current_rate) if current_rate is not None else None
    out: Dict[str, Any] = {
        "rate": rate, "bp": rate * 10_000 if rate is not None else None,
        # forward_rate: 14 gunluk tutus icin ILERI TAHMIN. Varsayilan olarak
        # guncel oran, gecmis varsa yakin gecmisin medyani (asagida).
        "forward_rate": rate, "forward_bp": rate * 10_000 if rate is not None else None,
        "percentile": None, "n_history": 0,
    }
    if not history:
        return out
    hist = np.array([float(x) for x in history if x is not None], float)
    if len(hist) < 10:
        return out
    out["n_history"] = int(len(hist))
    # Son 21 donem ~ 7 gun. Fonlama ortalamaya donduğu icin ileri tahminde
    # sivri son degeri degil bu medyani kullaniyoruz.
    recent = hist[-recent_periods:] if len(hist) >= recent_periods else hist
    fwd = float(np.median(recent))
    out["forward_rate"] = fwd
    out["forward_bp"] = round(fwd * 10_000, 3)
    out["median_bp"] = round(float(np.median(hist)) * 10_000, 3)
    if rate is not None:
        out["percentile"] = round(float((hist < rate).mean() * 100), 1)
    return out


# --------------------------------------------------------------------------- #
# Karar
# --------------------------------------------------------------------------- #
def evaluate(symbol: str, daily_rows: Sequence[Sequence],
             funding_rate: Optional[float] = None,
             funding_history: Optional[Sequence[float]] = None,
             cfg: Optional[Dict[str, Any]] = None,
             live_price: Optional[float] = None) -> Dict[str, Any]:
    """Bir sembol icin karar. Ag erisimi yok, yan etki yok.

    IKI FARKLI FIYAT — 27.08 hata duzeltmesi
    ----------------------------------------
    `daily_rows` KAPANMIS gunluk barlardir; kapanmamis bar bilerek disarida
    birakiliyor, yoksa momentum gun ici oynamayla gidip geliyor. Ama bu,
    serinin son kapanisinin 24 saate kadar ESKI olabilecegi anlamina gelir.

    Onceki surum giris fiyatini da o kapanistan aliyordu. Sonucu: bir gun
    icinde 0,20'den 0,13'e dusen bir coin'de sistem 0,20'den girmis
    gorunuyordu — gercekte var olmayan bir fiyattan. Bu yalnizca kozmetik
    degil; stop, hedef ve maliyet_R'nin hepsi o fiyattan turedigi icin
    kurulumun tamami yanlis oluyordu.

    Artik ayrildi:
      momentum, oynaklik, ATR  -> KAPANMIS barlardan (sinyal)
      giris / stop / hedef     -> CANLI fiyattan (uygulama)

    ATR yuzde olarak tasiniyor: mutlak ATR sinyal barinin fiyatina gore
    olculuyor, sonra canli fiyata uygulaniyor. Boylece fiyat gun icinde
    ne kadar oynarsa oynasin stop mesafesi orantili kaliyor.

    Donen sozlukte 'ok' False ise 'veto' listesi NEDEN reddedildigini soyler.
    """
    c = {**DEFAULTS, **(cfg or {})}
    veto: List[str] = []
    notes: List[str] = []

    if len(daily_rows) < MIN_BARS:
        return {"ok": False, "symbol": symbol, "veto": [f"yetersiz gecmis ({len(daily_rows)} gun, {MIN_BARS} gerekli)"]}

    closes = _closes(daily_rows)
    signal_close = float(closes[-1])          # son KAPANMIS gunluk bar
    if signal_close <= 0:
        return {"ok": False, "symbol": symbol, "veto": ["gecersiz fiyat"]}
    # Giris fiyati CANLI olmali. live_price verilmezse son kapanisa duseriz
    # ama bu yalnizca test/backtest yolu icindir; canli motor her zaman verir.
    price = float(live_price) if live_price and live_price > 0 else signal_close

    mom = momentum_t(closes)
    if mom is None:
        return {"ok": False, "symbol": symbol,
                "veto": ["momentum hesaplanamadi (seri duz ya da oynaklik olculemedi)"]}

    atr_abs = daily_atr(daily_rows)
    if not atr_abs or atr_abs <= 0:
        return {"ok": False, "symbol": symbol, "veto": ["ATR hesaplanamadi"]}
    # ATR'yi yuzdeye cevirip CANLI fiyata uyguluyoruz. Mutlak degeri
    # dogrudan kullanmak, fiyat gun icinde %35 dustugunde stop mesafesini
    # orantisiz genis birakirdi.
    atr_pct = atr_abs / signal_close * 100
    atr = price * atr_pct / 100

    side = "LONG" if mom["t"] > 0 else "SHORT"
    direction = 1 if side == "LONG" else -1

    # --- Kapi 1: momentum gurultuden ayirt edilebilir mi? ---
    if abs(mom["t"]) < c["min_abs_t"]:
        veto.append(f"momentum zayif (|t|={abs(mom['t']):.2f} < {c['min_abs_t']})")

    # --- Kapi 2: oynaklik kullanilabilir aralikta mi? ---
    # Cok sakin: stop mesafesi kucuk -> maliyet R olarak buyur.
    # Cok carpik: ATR tabanli stop anlamsizlasir, tek mumda hem stop hem hedef.
    if atr_pct < c["min_atr_pct"]:
        veto.append(f"oynaklik cok dusuk (ATR %{atr_pct:.2f}) — maliyet edge'i yer")
    elif atr_pct > c["max_atr_pct"]:
        veto.append(f"oynaklik cok yuksek (ATR %{atr_pct:.2f}) — stop anlamsiz")

    # --- Kapi 2b: fiyat sinyal barindan ne kadar uzaklasti? ---
    # Momentum kapanmis bardan hesaplandi. Fiyat o bardan bu yana ATR'nin
    # buyuk bir kismi kadar hareket ettiyse, kurulumun kendisi degismistir:
    # hareketin pesinden kosmus oluruz. ATR cinsinden olculuyor ki esik
    # sakin ve carpik coinlerde ayni anlami tasisin.
    drift_abs = abs(price - signal_close)
    drift_atr = drift_abs / atr_abs if atr_abs else 0.0
    drift_pct = drift_abs / signal_close * 100
    if drift_atr > c["max_chase_atr"]:
        veto.append(f"fiyat sinyal barindan uzaklasti (%{drift_pct:.1f} = {drift_atr:.2f} ATR "
                    f"> {c['max_chase_atr']} ATR)")

    # --- Seviyeler ---
    risk = atr * c["stop_atr_mult"]
    stop_pct = risk / price * 100

    # --- Kapi 2c: stop makul mu? ---
    # 29.08 DUZELTMESI. Burasi eskiden KALDIRACA bagliydi: tavan
    # min(max_stop_pct, 65 x 100/kaldirac) idi. Yani 5x kaldiracta tavan
    # %13'e iniyor ve kurulumlarin cogu "stop cok genis" diye ELENIYORDU.
    # Bu yanlisti: kaldirac bir UYGULAMA parametresi, sinyalin kendisiyle
    # ilgisi yok. Ayni kurulum 3x'te gecerli 5x'te gecersiz olamaz —
    # degisen sey piyasa degil, bizim pozisyonu nasil tasidigimiz.
    # Likidasyon kontrolu artik copy_trade katmaninda: orada gerektiginde
    # KALDIRAC dusuruluyor, kurulum atilmiyor.
    cap_pct = float(c["max_stop_pct"])
    # lev ve liq_pct yalnizca RAPOR icin duruyor — karara girmiyorlar.
    # copy_trade bu sayilara bakip gerekirse kaldiraci dusuruyor.
    lev = max(1.0, float(c.get("leverage", 3) or 3))
    liq_pct = 100.0 / lev
    if stop_pct > cap_pct:
        veto.append(f"stop cok genis (%{stop_pct:.1f} > %{cap_pct:.1f} tavan; "
                    f"ATR %{atr_pct:.1f})")

    stop = price - direction * risk
    target = price + direction * risk * c["target_r"]
    cost_basis = price / risk                      # maliyet_R = yuzde_maliyet x bu

    # --- Maliyet: KARARIN ICINDE, raporun sonunda degil ---
    entry_fee = c["maker_fee"] if c["use_maker_entry"] else c["taker_fee"]
    entry_slip = c["slippage"] * (0.25 if c["use_maker_entry"] else 1.0)
    fee_pct = entry_fee + c["taker_fee"] + entry_slip + c["slippage"]
    fee_cost_r = fee_pct * cost_basis

    fund = funding_context(funding_rate, funding_history)
    funding_cost_r = 0.0
    if fund["forward_rate"] is not None:
        # Fonlama 8 saatte bir odenir -> gunde 3 kez. Pozitif oranda ODEYEN
        # taraf LONG'dur; SHORT tahsil eder (isaret bu yuzden yon carpani ile).
        #
        # ILERI TAHMIN GUNCEL ORAN DEGIL, YAKIN GECMISIN MEDYANI. Fonlama
        # ortalamaya doner; bugunku sivri bir oranin 14 gun surecegini
        # varsaymak, tahsil eden tarafta beklentiyi ciddi sekilde sisirir.
        # Guncel oran yine kullaniliyor ama baska bir is icin: kalabalik
        # taraf kapisi (asagida), cunku o "su an" ile ilgili bir soru.
        periods = c["horizon_days"] * 3
        funding_pct = fund["forward_rate"] * periods * direction
        funding_cost_r = funding_pct * cost_basis
        notes.append(f"fonlama simdi {fund['bp']:+.2f}bp, ileri tahmin "
                     f"{fund['forward_bp']:+.2f}bp/8s -> {c['horizon_days']}g icin "
                     f"{funding_pct*100:+.3f}% ({funding_cost_r:+.4f}R)")

    total_cost_r = fee_cost_r + funding_cost_r
    # KAPI ICIN AYRI HESAP: beklenen fonlama GELIRI sayilmaz.
    # Fonlama geliri belirsizdir (oran her 8 saatte degisir, pozisyon erken
    # kapanabilir). Belirsiz bir gelirin, kesin olan komisyonu "affetmesine"
    # izin verilirse motor pahali kurulumlari carry bahanesiyle iceri alir.
    # Rapor tam sayiyi gosterir, kapi ise sadece odenen tarafi sayar.
    gate_cost_r = fee_cost_r + max(funding_cost_r, 0.0)

    # --- Kapi 3: maliyet tavani ---
    # Islem basina beklenen edge'i BILMIYORUZ (olculmedi). Ama sunu biliyoruz:
    # maliyet 0.05R'yi asiyorsa, makul hicbir edge bunu kaldiramaz. Edge'i
    # tahmin etmek yerine maliyete tavan koymak durustce olan yaklasim.
    if gate_cost_r > c["max_cost_r"]:
        veto.append(f"maliyet tavani asildi ({gate_cost_r:.4f}R > {c['max_cost_r']}R)")

    # --- Kapi 4: kalabalik taraf ---
    # Asiri pozitif fonlama = kalabalik long. O tarafa katilmak hem fonlama
    # oder hem de long tasfiyesi riskini ustlenir.
    extreme = c["funding_extreme_bp"]
    if fund["bp"] is not None:
        if side == "LONG" and fund["bp"] > extreme:
            veto.append(f"kalabalik long (fonlama {fund['bp']:+.2f}bp > {extreme}bp)")
        elif side == "SHORT" and fund["bp"] < -extreme:
            veto.append(f"kalabalik short (fonlama {fund['bp']:+.2f}bp < {-extreme}bp)")
    if fund["percentile"] is not None:
        if side == "LONG" and fund["percentile"] >= 95:
            veto.append(f"fonlama kendi gecmisinin tepesinde (%{fund['percentile']:.0f})")
        elif side == "SHORT" and fund["percentile"] <= 5:
            veto.append(f"fonlama kendi gecmisinin dibinde (%{fund['percentile']:.0f})")

    if not mom["agree"]:
        notes.append("ufuklar ayrisiyor (karara girmiyor, bilgi amacli)")

    return {
        "ok": not veto,
        "symbol": symbol,
        "side": side,
        "entry": round(price, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "risk_distance": round(risk, 8),
        "stop_pct": round(stop_pct, 2),
        "target_pct": round(stop_pct * c["target_r"], 2),
        "stop_cap_pct": round(cap_pct, 2),
        "liq_pct": round(liq_pct, 1),
        "leverage": int(lev),
        "t_stat": round(mom["t"], 3),
        "per_lookback": mom["per_lookback"],
        "lookbacks_agree": mom["agree"],
        "sigma_annual_pct": round(mom["sigma_annual"] * 100, 1),
        "atr_daily": round(atr, 8),
        "atr_pct": round(atr_pct, 2),
        "signal_close": round(signal_close, 8),
        "live_used": bool(live_price and live_price > 0),
        "drift_pct": round(drift_pct, 2),
        "drift_atr": round(drift_atr, 2),
        "cost_basis": round(cost_basis, 2),
        "fee_cost_r": round(fee_cost_r, 4),
        "funding_cost_r": round(funding_cost_r, 4),
        "total_cost_r": round(total_cost_r, 4),
        "gate_cost_r": round(gate_cost_r, 4),
        "funding": fund,
        "horizon_days": c["horizon_days"],
        "target_r": c["target_r"],
        "veto": veto,
        "notes": notes,
    }


def exit_signal(position_side: str, daily_rows: Sequence[Sequence],
                cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pozisyon acikken: onu tutma sebebi hala duruyor mu?

    TSMOM'un asil cikisi sabit hedef DEGIL, sinyalin bitmesidir. Sabit hedef
    kazananlari erken keser; TSMOM'un getirisi uzun kuyruktan gelir. Sabit
    hedef yine de var ama uzakta (4R) ve rolu bir emniyet tavani.

    IKI AYRI CIKIS — 27.08 duzeltmesi
    ---------------------------------
    Onceki surum yalnizca TAM DONUSTE cikiyordu: momentumun ters tarafta
    anlamli hale gelmesi gerekiyordu. Pratikte bu cok nadir oluyor; pozisyon
    trend olmus olsa bile ufuk dolana kadar (14 gun) elde kaliyordu ve yeni
    kurulumlara yer acilmiyordu.

    Simdi pozisyon YONUNDEKI momentum olculuyor (t_dir):
      - t_dir <= exit_t_floor  -> "trend soldu", pozisyonu tutma sebebi bitti
      - t_dir < 0 ve guclu     -> "yon dondu"
    Giris esigi 1.0 iken cikis tabani 0.30: girdigin sebebin ucte birine
    inmesi cikmak icin yeterli.
    """
    c = {**DEFAULTS, **(cfg or {})}
    if len(daily_rows) < MIN_BARS:
        return {"exit": False, "flip": False, "reason": "yetersiz gecmis"}
    mom = momentum_t(_closes(daily_rows))
    if mom is None:
        return {"exit": False, "flip": False, "reason": "momentum hesaplanamadi"}

    t_dir = mom["t"] * (1 if position_side == "LONG" else -1)
    floor = float(c["exit_t_floor"])
    reversed_ = t_dir <= -abs(floor) * 2
    faded = t_dir <= floor
    return {
        "exit": faded,
        "flip": reversed_,                     # geriye donuk uyumluluk
        "t_stat": round(mom["t"], 3),
        "t_dir": round(t_dir, 3),
        "side_now": "LONG" if mom["t"] > 0 else "SHORT",
        "reason": ("yön döndü" if reversed_ else "trend soldu" if faded
                   else "momentum aynı yönde"),
    }
