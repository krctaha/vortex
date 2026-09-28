"""MENTOR GUNLUGU — kullanicinin KENDI islemlerini olcen katman.

BU MODUL app/api/mentor.py ICINDEKI review AKISINDAN AYRIDIR:
  review    -> "bu isleme gireyim mi" (karar oncesi tavsiye)
  gunluk    -> "ne yaptim, ise yariyor mu" (karar sonrasi olcum)
Ikisi ayri tablolar kullanir (mentor_reviews / mentor_trades) ve
birbirinin yerine gecmez.

TASARIM GEREKCESI
-----------------
Bu sistemin motor tarafi bir aydir olculuyor ve alti backtest'in hicbirinde
maliyet sonrasi edge cikmadi. Ama bu sure boyunca OLCULMEYEN bir sey vardi:
kullanicinin kendisi. Elle actigi islemlerin hicbir kaydi yok.

Bu modul o boslugu kapatiyor. Motorun karnesi STRATEJIYI olcer; burasi
INSANI olcer. Ikisi ayri tablolarda, ayri sicillerde.

Buradaki hicbir metrik "sinyal calisiyor mu" varsayimi yapmaz. Hepsi
edge'den bagimsiz: R dagilimi, cikis verimliligi, kural uyumu, davranis
bayraklari. Sinyal hic calismasa bile bu olcumler insanin nerede para
kaybettigini soyler.

DURUSTLUK KURALLARI
-------------------
1. initial_stop giriste dondurulur, asla guncellenmez. Stop tasindiginda
   R'yi yeniden hesaplamak, her stop tasimayi istatistiksel olarak
   odullendirir — ev yapimi gunluklerin en sik kendini kandirma bicimi.
2. Hicbir istatistik n < MIN_ORNEK altinda "anlamli" diye sunulmaz.
3. Guven araligi standart hata sifirken hesaplanmaz (varyans sifirsa
   aralik bir noktaya coker ve kod yanlislikla "anlamli" der — bu hatayi
   motor karnesinde bir kez yaptik).
4. Bootstrap araligi sifiri iceriyorsa ekranda ACIKCA "kanitlanmadi" yazar.
"""
from __future__ import annotations

import json
import logging
import math
import random
from typing import Any, Dict, List, Optional, Sequence

from .. import db

log = logging.getLogger("vortex.mentor")

MIN_ORNEK = 20            # bunun altinda hicbir kirilim "bulgu" sayilmaz
MIN_KIRILIM_ORNEK = 10    # etiket/saat kirilimlarinda hucre esigi
BOOTSTRAP_N = 5000

# Giris oncesi kontrol listesi. Binary ve sert: biri bile "hayir" ise
# islem kurallara UYMAYAN sayilir. Amac puan sismesini engellemek.
KONTROL_MADDELERI = [
    ("kurulum", "Kurulum kriterlerime uyuyor"),
    ("boyut", "Pozisyon boyutunu risk kuralımdan hesapladım"),
    ("stop", "Stopu girmeden ÖNCE belirledim"),
    ("limit", "Günlük zarar limitimin içindeyim"),
    ("duygu", "Plandan giriyorum, duygudan değil"),
]


# ------------------------------------------------------------------ #
# Temel hesaplar
# ------------------------------------------------------------------ #
def r_multiple(side: str, entry: float, initial_stop: float,
               exit_price: float) -> Optional[float]:
    """Giristeki riske gore R. initial_stop dondurulmus degerdir."""
    risk = abs(entry - initial_stop)
    if risk <= 0:
        return None
    yon = 1.0 if side.upper() == "LONG" else -1.0
    return (exit_price - entry) * yon / risk


def yol_metrikleri(side: str, entry: float, initial_stop: float,
                   initial_target: Optional[float],
                   bars: Sequence[Sequence]) -> Dict[str, Optional[float]]:
    """Islem suresince fiyatin gittigi en iyi/en kotu yer.

    bars: [ts, open, high, low, close, ...] — islem PENCERESINE ait mumlar.

    NOT: giris ve cikis barinin kendi ic ekstremumlari da dahil ediliyor;
    Tradervue bunlari disliyor cunku o fiili gerceklesmeyecek bir dolum
    olurdu. Burada islem zaten gerceklesmis, biz sadece "fiyat nereye
    kadar gitti" diyoruz — o yuzden dahil birakmak dogru.
    """
    risk = abs(entry - initial_stop)
    if risk <= 0 or not bars:
        return {"mfe_r": None, "mae_r": None, "updraw_pct": None,
                "drawdown_pct": None}
    # AKIL SAGLIGI KAPISI
    # Mumlar islemle ayni fiyat olceginde degilse (yanlis sembol, hatali
    # giris fiyati, bozuk veri) buradan cikan MFE/MAE anlamsiz buyuk sayilar
    # olur — ve o sayilar "erken cikiyorsun" gibi YANLIS ogutlere donusur.
    # Olcemiyorsak sessizce yanlis olcmek yerine None donuyoruz.
    try:
        en_yuksek = max(float(b[2]) for b in bars)
        en_dusuk = min(float(b[3]) for b in bars)
    except (TypeError, ValueError, IndexError):
        return {"mfe_r": None, "mae_r": None, "updraw_pct": None,
                "drawdown_pct": None}
    pay = (en_yuksek - en_dusuk) or (en_yuksek * 0.01) or 1.0
    if not (en_dusuk - pay <= entry <= en_yuksek + pay):
        log.warning("mentor yol metrikleri atlandi: giris %.8g mum araligi "
                    "[%.8g, %.8g] disinda", entry, en_dusuk, en_yuksek)
        return {"mfe_r": None, "mae_r": None, "updraw_pct": None,
                "drawdown_pct": None, "olculemedi": "fiyat araligi uyusmuyor"}

    yon = 1.0 if side.upper() == "LONG" else -1.0
    en_iyi = en_kotu = 0.0
    for b in bars:
        try:
            hi, lo = float(b[2]), float(b[3])
        except (TypeError, ValueError, IndexError):
            continue
        lehte = (hi - entry) * yon / risk if yon > 0 else (entry - lo) / risk
        aleyhte = (lo - entry) * yon / risk if yon > 0 else (entry - hi) / risk
        en_iyi = max(en_iyi, lehte)
        en_kotu = min(en_kotu, aleyhte)

    # Normalize edilmis okumalar sinirli oldugu icin ham R'den daha
    # eyleme donuk: "hedefin yuzde kacina gitti", "stopun yuzde kacina indi".
    hedef_r = None
    if initial_target is not None:
        hedef_r = abs(initial_target - entry) / risk
    updraw = (en_iyi / hedef_r * 100.0) if hedef_r and hedef_r > 0 else None
    drawdown = abs(en_kotu) * 100.0     # 1R dusus = stop = %100

    return {"mfe_r": round(en_iyi, 3), "mae_r": round(en_kotu, 3),
            "updraw_pct": round(updraw, 1) if updraw is not None else None,
            "drawdown_pct": round(drawdown, 1)}


def cikis_verimliligi(side: str, entry: float, initial_stop: float,
                      exit_price: float, mfe_r: Optional[float]) -> Optional[float]:
    """Yakalanan kar / yakalanabilecek en iyi kar. Sadece KAZANANLAR icin.

    Kaybedenlerde anlamsiz: MFE 0'a yakinsa bolen sifirlanir ve sayi
    patlar. Bu yuzden kaybedenlerde None donuyor.
    """
    r = r_multiple(side, entry, initial_stop, exit_price)
    if r is None or mfe_r is None or r <= 0 or mfe_r <= 0:
        return None
    return round(min(r / mfe_r, 1.0) * 100.0, 1)


# ------------------------------------------------------------------ #
# Davranis bayraklari — kullanicidan HICBIR girdi istemez
# ------------------------------------------------------------------ #
def bayraklari_hesapla(yeni: Dict[str, Any], gecmis: List[Dict[str, Any]]) -> List[str]:
    """Zaman damgasi ve boyuttan turetilen tilt gostergeleri.

    Edgewonk'un "Tiltmeter"i kullanicinin kendi yorumuna dayaniyor, yani
    durustlukten ceyrek puan aliyor. Bunlar tamamen mekanik: kullanici
    ne hissettigini soylemek zorunda degil, veri zaten soyluyor.
    """
    bayrak: List[str] = []
    if not gecmis:
        return bayrak

    kapali = [g for g in gecmis if g.get("closed_at") and g.get("result_r") is not None]
    kapali.sort(key=lambda g: g["closed_at"])
    if not kapali:
        return bayrak

    son = kapali[-1]
    ara_dk = (yeni["opened_at"] - int(son["closed_at"])) / 60000.0

    # 1) Zarardan hemen sonra hizli tekrar giris
    if float(son["result_r"]) < 0 and 0 <= ara_dk < 15:
        bayrak.append("zarardan 15 dk icinde tekrar giris")

    # 2) Zarardan sonra boyut buyutme — intikam islemi imzasi
    boyutlar = [float(g["qty"]) for g in kapali[-10:]
                if g.get("qty") and float(g["qty"]) > 0]
    if boyutlar and yeni.get("qty") and float(son["result_r"]) < 0:
        boyutlar_sirali = sorted(boyutlar)
        medyan = boyutlar_sirali[len(boyutlar_sirali) // 2]
        if medyan > 0 and float(yeni["qty"]) > medyan * 1.5:
            bayrak.append("zarardan sonra pozisyon buyutuldu")

    # 3) Ust uste kayip serisi
    seri = 0
    for g in reversed(kapali):
        if float(g["result_r"]) < 0:
            seri += 1
        else:
            break
    if seri >= 3:
        bayrak.append(f"{seri} islemdir kaybediyorsun")

    # 4) Ayni gun yogunlugu
    gun_basi = yeni["opened_at"] - (yeni["opened_at"] % 86_400_000)
    bugun = [g for g in gecmis if int(g["opened_at"]) >= gun_basi]
    if len(bugun) >= 5:
        bayrak.append(f"bugun {len(bugun) + 1}. islem — yogunluk yuksek")

    return bayrak


# ------------------------------------------------------------------ #
# Istatistik
# ------------------------------------------------------------------ #
def _ozet(rs: List[float]) -> Dict[str, Any]:
    n = len(rs)
    if n == 0:
        return {"n": 0}
    ort = sum(rs) / n
    sd = (sum((x - ort) ** 2 for x in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else 0.0
    kazanan = [x for x in rs if x > 0]
    kaybeden = [x for x in rs if x < 0]
    brut_kar = sum(kazanan)
    brut_zarar = abs(sum(kaybeden))
    return {
        "n": n,
        "toplam_r": round(sum(rs), 3),
        "ortalama_r": round(ort, 4),
        "isabet": round(len(kazanan) / n * 100, 1),
        "profit_factor": round(brut_kar / brut_zarar, 3) if brut_zarar > 0 else None,
        "ort_kazanc_r": round(sum(kazanan) / len(kazanan), 3) if kazanan else None,
        "ort_kayip_r": round(sum(kaybeden) / len(kaybeden), 3) if kaybeden else None,
        "sd": round(sd, 4),
        "se": round(se, 4),
    }


def sqn(rs: List[float]) -> Optional[Dict[str, Any]]:
    """Van Tharp System Quality Number.

    N, 100'de KIRPILIR. Sebep: sqrt(N) carpani yuzden fazla islemde skoru
    sadece cok islem yaparak sisirmeye izin verirdi. Kirpma, "daha cok
    islem = daha iyi sistem" yanilsamasini engelliyor.
    """
    n = len(rs)
    if n < 2:
        return None
    ort = sum(rs) / n
    sd = (sum((x - ort) ** 2 for x in rs) / (n - 1)) ** 0.5
    if sd <= 0:
        return None
    deger = (ort / sd) * math.sqrt(min(n, 100))
    if deger < 1.6:
        yorum = "gurultuden ayirt edilemiyor"
    elif deger < 2.0:
        yorum = "ortalamanin altinda"
    elif deger < 2.5:
        yorum = "ortalama"
    elif deger < 3.0:
        yorum = "iyi"
    elif deger < 7.0:
        yorum = "cok iyi"
    else:
        yorum = "supheli — asiri uyum olabilir"
    return {"deger": round(deger, 2), "yorum": yorum, "kirpilan_n": min(n, 100)}


def bootstrap_ci(rs: List[float], tekrar: int = BOOTSTRAP_N,
                 tohum: int = 12345) -> Optional[Dict[str, Any]]:
    """Ortalama R icin yuzde 90 bootstrap araligi.

    Normallik varsaymadigi icin R dagilimina t-testinden daha uygun:
    R dagilimi tanimi geregi carpik (kayiplar -1R'de yigilir, kazanclar
    uzun kuyruk yapar).

    Aralik sifiri iceriyorsa sonuc "kanitlanmadi"dir. Ortalamanin pozitif
    olmasi tek basina bir sey soylemez.
    """
    n = len(rs)
    if n < 5:
        return None
    rng = random.Random(tohum)
    ortalamalar = []
    for _ in range(tekrar):
        ortalamalar.append(sum(rs[rng.randrange(n)] for _ in range(n)) / n)
    ortalamalar.sort()
    alt = ortalamalar[int(tekrar * 0.05)]
    ust = ortalamalar[int(tekrar * 0.95)]
    return {
        "alt": round(alt, 4), "ust": round(ust, 4),
        "sifiri_iceriyor": bool(alt <= 0 <= ust),
        "tekrar": tekrar,
    }


def _kirilim(rows: List[Dict[str, Any]], anahtar) -> List[Dict[str, Any]]:
    """Bir boyuta gore kirilim. Hucre esigin altindaysa ISARETLENIR.

    Az ornekli hucreyi bulgu gibi sunmak, tam olarak alti olu backtest'i
    ureten gurultu madenciliginin ta kendisi. Hucre gosteriliyor ama
    "yetersiz" damgasiyla.
    """
    kova: Dict[str, List[float]] = {}
    for r in rows:
        k = anahtar(r)
        if k is None:
            continue
        kova.setdefault(str(k), []).append(float(r["result_r"]))
    out = []
    for k, rs in kova.items():
        o = _ozet(rs)
        o["anahtar"] = k
        o["yeterli"] = bool(o["n"] >= MIN_KIRILIM_ORNEK)
        out.append(o)
    out.sort(key=lambda x: -x["n"])
    return out


def karne(user_id: int, gun: int = 0) -> Dict[str, Any]:
    """Kullanicinin kendi sicili."""
    sql = ("SELECT * FROM mentor_trades WHERE user_id=? AND status='closed' "
           "AND result_r IS NOT NULL")
    params: List[Any] = [user_id]
    if gun > 0:
        sql += " AND closed_at >= ?"
        params.append(db.now_ms() - gun * 86_400_000)
    sql += " ORDER BY closed_at"
    rows = db.query(sql, params)
    rs = [float(r["result_r"]) for r in rows]

    genel = _ozet(rs)
    ci = bootstrap_ci(rs)
    kalite = sqn(rs)

    # KURAL UYUMU — bu modulun asil isi.
    # Strateji hic calismasa bile "kurallarima uydugumda ne oluyor,
    # uymadigimda ne oluyor" sorusunun cevabi insanin kontrol edebilecegi
    # tek degiskeni gosterir.
    uyan = [float(r["result_r"]) for r in rows if r["kural_uyumu"] == 1]
    uymayan = [float(r["result_r"]) for r in rows if r["kural_uyumu"] == 0]
    uyum_orani = (len(uyan) / len(rows) * 100.0) if rows else None

    # Cikis verimliligi yalniz kazananlarda anlamli
    verim = [float(r["exit_efficiency"]) for r in rows
             if r["exit_efficiency"] is not None]
    updraw = [float(r["updraw_pct"]) for r in rows if r["updraw_pct"] is not None]
    kazanan_mae = [abs(float(r["drawdown_pct"])) for r in rows
                   if r["drawdown_pct"] is not None and float(r["result_r"]) > 0]

    return {
        "genel": genel,
        "guven_araligi": ci,
        "sqn": kalite,
        "yeterli_ornek": bool(genel["n"] >= MIN_ORNEK),
        "min_ornek": MIN_ORNEK,
        "kural": {
            "uyum_orani": round(uyum_orani, 1) if uyum_orani is not None else None,
            "uydugumda": _ozet(uyan),
            "uymadigimda": _ozet(uymayan),
        },
        "kirilim": {
            "etiket": _kirilim(rows, lambda r: r["etiket"] or "etiketsiz"),
            "sembol": _kirilim(rows, lambda r: r["symbol"]),
            "yon": _kirilim(rows, lambda r: r["side"]),
            "saat": _kirilim(rows, lambda r: f"{_saat(r['opened_at']):02d}:00"),
        },
        "verimlilik": {
            "ort_cikis_verimi": round(sum(verim) / len(verim), 1) if verim else None,
            "ort_updraw": round(sum(updraw) / len(updraw), 1) if updraw else None,
            "kazananlarda_ort_drawdown": (round(sum(kazanan_mae) / len(kazanan_mae), 1)
                                          if kazanan_mae else None),
        },
        "bayrak_sayimi": _bayrak_sayimi(rows),
        # DOLAR OZETI — R'si OLMAYAN islemler de dahil.
        # Binance gecmisinden aktarilan islemlerde stop yok, dolayisiyla R
        # yok; yukaridaki butun R metrikleri onlari haric tutuyor (dogrusu
        # bu). Ama dolar sonucu, sure, sembol ve yon bilgisi TAM. Onlari
        # tamamen gormezden gelmek, elde olan tarafsiz sicili israf etmek
        # olurdu. Bu yuzden ayri ve ACIKCA ayri bir bolum.
        "dolar": _dolar_ozeti(user_id, gun),
    }


def _dolar_ozeti(user_id: int, gun: int = 0) -> Dict[str, Any]:
    """TUM kapanmis islemler — R'si olsun olmasin — dolar bazinda.

    R metrikleri stopsuz islemleri disliyor cunku R stopsuz hesaplanamaz.
    Ama "kac islem yaptim, net kac dolar, hangi sembolde ne oldu" sorulari
    stop gerektirmez. Bu bolum onlari cevapliyor ve R bolumunden AYRI
    duruyor ki ikisi karistirilmasin.
    """
    sql = ("SELECT symbol, side, opened_at, closed_at, pnl_usdt, result_r, etiket "
           "FROM mentor_trades WHERE user_id=? AND status='closed' "
           "AND pnl_usdt IS NOT NULL")
    params: List[Any] = [user_id]
    if gun > 0:
        sql += " AND closed_at >= ?"
        params.append(db.now_ms() - gun * 86_400_000)
    rows = db.query(sql, params)
    if not rows:
        return {"n": 0}

    pnl = [float(r["pnl_usdt"]) for r in rows]
    kazanan = [x for x in pnl if x > 0]
    kaybeden = [x for x in pnl if x < 0]
    rsiz = sum(1 for r in rows if r["result_r"] is None)

    def kova(anahtar) -> List[Dict[str, Any]]:
        k: Dict[str, List[float]] = {}
        for r in rows:
            a = anahtar(r)
            if a is None:
                continue
            k.setdefault(str(a), []).append(float(r["pnl_usdt"]))
        out = [{"anahtar": a, "n": len(v), "toplam": round(sum(v), 2),
                "ortalama": round(sum(v) / len(v), 3),
                "isabet": round(len([x for x in v if x > 0]) / len(v) * 100, 1),
                "yeterli": len(v) >= MIN_KIRILIM_ORNEK}
               for a, v in k.items()]
        out.sort(key=lambda x: -x["n"])
        return out

    sureler = [(int(r["closed_at"]) - int(r["opened_at"])) / 3_600_000.0
               for r in rows if r["opened_at"] and r["closed_at"]]

    return {
        "n": len(rows),
        "r_siz": rsiz,                      # stopu olmayan (aktarilmis) islem sayisi
        "toplam_usdt": round(sum(pnl), 2),
        "ortalama_usdt": round(sum(pnl) / len(pnl), 3),
        "isabet": round(len(kazanan) / len(pnl) * 100, 1),
        "ort_kazanc": round(sum(kazanan) / len(kazanan), 2) if kazanan else None,
        "ort_kayip": round(sum(kaybeden) / len(kaybeden), 2) if kaybeden else None,
        "profit_factor": (round(sum(kazanan) / abs(sum(kaybeden)), 3)
                          if kaybeden and sum(kaybeden) != 0 else None),
        "en_iyi": round(max(pnl), 2), "en_kotu": round(min(pnl), 2),
        "ort_sure_saat": round(sum(sureler) / len(sureler), 1) if sureler else None,
        "yon": kova(lambda r: r["side"]),
        "sembol": kova(lambda r: r["symbol"])[:12],
        "saat": kova(lambda r: f"{_saat(int(r['opened_at'])):02d}:00" if r["opened_at"] else None),
    }


def _saat(ms: int) -> int:
    # Europe/Istanbul = UTC+3, yaz saati uygulamasi yok.
    return int(((ms // 3_600_000) + 3) % 24)


def _bayrak_sayimi(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Her davranis bayragi icin: kac kez ve o islemlerin ortalama R'si."""
    kova: Dict[str, List[float]] = {}
    for r in rows:
        try:
            bayraklar = json.loads(r["bayraklar"] or "[]")
        except (TypeError, ValueError):
            bayraklar = []
        for b in bayraklar:
            kova.setdefault(str(b), []).append(float(r["result_r"]))
    return {k: {"n": len(v), "ortalama_r": round(sum(v) / len(v), 3)}
            for k, v in sorted(kova.items(), key=lambda x: -len(x[1]))}


# ------------------------------------------------------------------ #
# Ogutler — SADECE yeterli ornek varsa
# ------------------------------------------------------------------ #
def ogutler(k: Dict[str, Any]) -> List[Dict[str, str]]:
    """Karneden turetilen somut gozlemler.

    Her ogut bir ORNEK SAYISI tasir. Sayisi yetersiz olan hicbir gozlem
    uretilmez — "shortlarin kaybediyor" demek icin 3 short yeterli degil.
    """
    out: List[Dict[str, str]] = []
    g = k["genel"]
    if g["n"] < MIN_ORNEK:
        out.append({
            "tip": "bilgi",
            "metin": f"Henüz {g['n']} kapanmış işlem var. Anlamlı bir şey "
                     f"söyleyebilmek için en az {MIN_ORNEK} gerekiyor. "
                     f"Şu ana kadarki sayılar bilgi amaçlı."})
        return out

    ci = k["guven_araligi"]
    if ci and ci["sifiri_iceriyor"]:
        out.append({
            "tip": "uyari",
            "metin": f"Ortalama {g['ortalama_r']}R, ama %90 aralik "
                     f"[{ci['alt']}, {ci['ust']}] sifiri iceriyor — "
                     f"elinde kanıtlanmış bir avantaj YOK. {g['n']} işlem."})
    elif ci and ci["alt"] > 0:
        out.append({
            "tip": "iyi",
            "metin": f"Ortalama {g['ortalama_r']}R ve %90 aralığın ALT sınırı "
                     f"({ci['alt']}) sıfırın üstünde. {g['n']} işlemde bu "
                     f"anlamlı bir sonuç."})

    ku = k["kural"]
    a, b = ku["uydugumda"], ku["uymadigimda"]
    if a.get("n", 0) >= MIN_KIRILIM_ORNEK and b.get("n", 0) >= MIN_KIRILIM_ORNEK:
        fark = a["ortalama_r"] - b["ortalama_r"]
        if fark > 0.15:
            out.append({
                "tip": "onemli",
                "metin": f"Kurallarına uyduğunda {a['ortalama_r']}R, "
                         f"uymadığında {b['ortalama_r']}R. Fark {fark:.2f}R. "
                         f"Kontrol edebildiğin tek değişken bu."})
        elif fark < -0.15:
            out.append({
                "tip": "uyari",
                "metin": f"Kurallarına UYMADIĞINDA daha iyi sonuç alıyorsun "
                         f"({b['ortalama_r']}R vs {a['ortalama_r']}R). "
                         f"Kuralların yanlış olabilir — gözden geçir."})

    v = k["verimlilik"]
    if v["ort_updraw"] is not None and v["ort_updraw"] < 60:
        out.append({
            "tip": "gozlem",
            "metin": f"Fiyat ortalama hedefinin %{v['ort_updraw']}'ine kadar "
                     f"gidiyor. Hedeflerin fazla uzak olabilir."})
    if v["ort_cikis_verimi"] is not None and v["ort_cikis_verimi"] < 50:
        out.append({
            "tip": "gozlem",
            "metin": f"Kazanan işlemlerde mümkün kârın %{v['ort_cikis_verimi']}'ini "
                     f"alıyorsun. Erken çıkıyorsun."})
    if v["kazananlarda_ort_drawdown"] is not None and v["kazananlarda_ort_drawdown"] > 70:
        out.append({
            "tip": "gozlem",
            "metin": f"Kazanan işlemlerin bile stopun %"
                     f"{v['kazananlarda_ort_drawdown']}'ine kadar iniyor. "
                     f"Ya stopun çok dar ya girişin çok erken."})

    for ad, kir in (("yon", "yön"), ("etiket", "kurulum")):
        for h in k["kirilim"][ad]:
            if h["yeterli"] and h["n"] >= MIN_KIRILIM_ORNEK and h["ortalama_r"] < -0.2:
                out.append({
                    "tip": "uyari",
                    "metin": f"{kir} '{h['anahtar']}': {h['n']} işlemde "
                             f"ortalama {h['ortalama_r']}R. Bu tarafı kes."})

    for bayrak, s in k["bayrak_sayimi"].items():
        if s["n"] >= MIN_KIRILIM_ORNEK and s["ortalama_r"] < -0.2:
            out.append({
                "tip": "uyari",
                "metin": f"'{bayrak}' işaretli {s['n']} işlemin ortalaması "
                         f"{s['ortalama_r']}R. Bu davranış sana pahalıya mal oluyor."})
    return out


# =================================================================== #
# I/O KATMANI — yukarisi saf hesap, burasi veritabani ve borsa
# =================================================================== #
async def baglam_al(symbol: str, interval: str = "1h") -> Dict[str, Any]:
    """Giris anindaki piyasa durumunun OTOMATIK ve AYRINTILI anlik goruntusu.

    Kullaniciya "RSI kacti, funding neydi" diye sormuyoruz. Sorsak ya
    hatirlamaz ya da sonradan rasyonalize eder — ikisi de olcumu bozar.
    Sistem kendisi kaydediyor; kullanicidan istenen tek sey GEREKCE.

    NEDEN AYRINTILI: kayit sadece "RSI 72" derse, aylar sonra karneye
    bakip "bu kurulumda ne goruyordum" sorusunu cevaplayamayiz. Bu yuzden
    tez, karsi tez, gecersizlik noktasi ve kor noktalar da saklaniyor —
    yani karari BESLEYEN bilginin tamami.

    Hicbir hata kaydi engellemez: baglam alinamazsa islem yine kaydedilir,
    baglam kismi kalir. Olcumu kaydin onune gecirmemek onemli.
    """
    from . import binance, rsi_context
    out: Dict[str, Any] = {"interval": interval, "alindi": db.now_ms()}

    # --- 1) RSI baglami (hafif, tek klines cagrisi) -------------------
    try:
        rows = await binance.klines(symbol, interval, 220)
        if rows and len(rows) >= 60:
            fr = None
            try:
                pi = await binance.premium_index(symbol)
                fr = float(pi.get("lastFundingRate", 0) or 0) * 10_000
            except Exception:  # noqa: BLE001
                pass
            c = rsi_context.classify(symbol, rows, fr)
            if c:
                out.update({
                    "rsi": c.get("rsi"), "zone": c.get("zone"),
                    "verdict": c.get("verdict"),
                    "verdict_label": rsi_context.verdict_label(
                        c.get("zone", ""), c.get("verdict", "")),
                    "t_stat": c.get("t_stat"),
                    "stretch_atr": c.get("stretch_atr"),
                    "divergence": bool(c.get("divergence")),
                    "trend_label": c.get("trend_label"),
                })
            out["funding_bp"] = round(fr, 2) if fr is not None else None
            out["fiyat"] = float(rows[-1][4])
    except Exception as exc:  # noqa: BLE001
        out["rsi_hata"] = type(exc).__name__
        log.debug("mentor baglam (rsi) alinamadi %s: %s", symbol, exc, exc_info=True)

    # --- 2) Ayrintili tez / karsi tez ---------------------------------
    # analysis.snapshot agir (500 bar) ama kayit basina BIR KEZ calisiyor.
    # Karsiliginda aylar sonra "o an ne goruyordum" sorusu cevaplanabilir.
    try:
        from . import analysis
        snap = await analysis.snapshot(symbol, interval, 500, include_series=False)
        if snap.get("ok"):
            try:
                snap["context"] = await analysis.market_context(symbol)
            except Exception:  # noqa: BLE001
                pass
            d = analysis.detailed_analysis(snap)
            ind = snap.get("indicators") or {}
            out["ayrinti"] = {
                "ozet": d.get("summary"),
                "teyit": d.get("confirmation"),
                "gecersizlik": d.get("invalidation"),
                "lehte": (d.get("evidence_for") or [])[:6],
                "aleyhte": (d.get("evidence_against") or [])[:6],
                "kor_nokta": (d.get("blind_spots") or [])[:4],
                "boga_senaryo": d.get("bull_scenario"),
                "ayi_senaryo": d.get("bear_scenario"),
            }
            out["gostergeler"] = {
                "atr14": ind.get("atr14"), "ema50": ind.get("ema50"),
                "ema200": ind.get("ema200"), "adx": ind.get("adx"),
                "hacim_orani": ind.get("volume_ratio"),
            }
            rej = snap.get("regime") or {}
            if rej:
                out["rejim"] = rej.get("label") or rej.get("regime")
    except Exception as exc:  # noqa: BLE001
        out["ayrinti_hata"] = type(exc).__name__
        log.debug("mentor baglam (ayrinti) alinamadi %s: %s", symbol, exc, exc_info=True)

    return out


def kapi(user_id: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """DEVRE KESICI — yeni islem acilabilir mi?

    Iki kapi var ve ikisi de sinyalden bagimsiz calisir:

    1. GUNLUK ZARAR DURDURUCUSU. Bugun gerceklesmis zarar limiti astiysa
       gun bitene kadar yeni islem yok. 380$'lik bir hesapta hesabi
       kurtarma ihtimali en yuksek tek ozellik budur.
    2. ZARAR SONRASI BEKLEME. Son islem zararla kapandiysa N dakika
       yeni islem yok — intikam islemine karsi mekanik engel.

    Bunlar tavsiye degil, KAPI: arayuz bunlar kapaliyken kayit formunu
    kilitler. Kendi kendine "bu sefer farkli" demenin onune geciyor.
    """
    now = db.now_ms()
    gun_basi = now - (now % 86_400_000)
    bugun = db.query(
        "SELECT result_r, pnl_usdt, closed_at FROM mentor_trades "
        "WHERE user_id=? AND status='closed' AND closed_at >= ?",
        (user_id, gun_basi))

    gunluk_zarar = sum(float(r["pnl_usdt"] or 0) for r in bugun
                       if float(r["pnl_usdt"] or 0) < 0)
    limit = float(cfg.get("gunluk_zarar_limiti", 0) or 0)
    if limit > 0 and abs(gunluk_zarar) >= limit:
        return {"acik": False, "sebep": "gunluk_zarar",
                "mesaj": f"Bugun {abs(gunluk_zarar):.2f}$ zarardasin, "
                         f"limitin {limit:.2f}$. Yarina kadar yeni islem yok.",
                "gunluk_zarar": round(gunluk_zarar, 2), "limit": limit}

    bekleme_dk = float(cfg.get("zarar_sonrasi_bekleme_dk", 0) or 0)
    if bekleme_dk > 0:
        son = db.query_one(
            "SELECT closed_at, result_r FROM mentor_trades WHERE user_id=? "
            "AND status='closed' AND result_r IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT 1", (user_id,))
        if son and float(son["result_r"]) < 0:
            gecen = (now - int(son["closed_at"])) / 60000.0
            if gecen < bekleme_dk:
                return {"acik": False, "sebep": "bekleme",
                        "mesaj": f"Son islem zararla kapandi. "
                                 f"{bekleme_dk - gecen:.0f} dakika daha bekle.",
                        "kalan_dk": round(bekleme_dk - gecen, 1)}

    return {"acik": True, "gunluk_zarar": round(gunluk_zarar, 2),
            "limit": limit, "bugun_islem": len(bugun)}


async def ac(user_id: int, symbol: str, side: str, entry: float,
             initial_stop: float, initial_target: Optional[float] = None,
             qty: Optional[float] = None, leverage: Optional[int] = None,
             gerekce: str = "", etiket: str = "",
             kontrol: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """Kullanicinin actigi bir islemi kaydeder."""
    if abs(entry - initial_stop) <= 0:
        return {"ok": False, "hata": "stop giris fiyatina esit olamaz"}
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return {"ok": False, "hata": "yon LONG veya SHORT olmali"}
    if side == "LONG" and initial_stop >= entry:
        return {"ok": False, "hata": "LONG'ta stop girisin ALTINDA olmali"}
    if side == "SHORT" and initial_stop <= entry:
        return {"ok": False, "hata": "SHORT'ta stop girisin USTUNDE olmali"}

    now = db.now_ms()
    kontrol = kontrol or {}
    # Sert kural: bes maddenin BIRI bile isaretsizse kural uyumu 0.
    uyum = 1 if all(bool(kontrol.get(k)) for k, _ in KONTROL_MADDELERI) else 0

    gecmis = db.query(
        "SELECT opened_at, closed_at, result_r, qty FROM mentor_trades "
        "WHERE user_id=? ORDER BY opened_at DESC LIMIT 40", (user_id,))
    bayraklar = bayraklari_hesapla(
        {"opened_at": now, "qty": qty}, [dict(g) for g in gecmis])

    baglam = await baglam_al(symbol)

    tid = db.execute(
        "INSERT INTO mentor_trades(user_id,symbol,side,opened_at,entry,"
        "initial_stop,initial_target,qty,leverage,gerekce,etiket,kural_uyumu,"
        "kontrol_listesi,baglam,bayraklar,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, symbol.upper(), side, now, entry, initial_stop,
         initial_target, qty, leverage, gerekce.strip()[:400],
         (etiket or "").strip()[:60], uyum,
         json.dumps(kontrol, ensure_ascii=False),
         json.dumps(baglam, ensure_ascii=False),
         json.dumps(bayraklar, ensure_ascii=False), "open", now))
    return {"ok": True, "id": tid, "bayraklar": bayraklar,
            "baglam": baglam, "kural_uyumu": uyum}


async def kapat(user_id: int, trade_id: int, exit_price: float,
                exit_reason: str = "elle") -> Dict[str, Any]:
    """Islemi kapatir ve TUM yol metriklerini hesaplar."""
    row = db.query_one(
        "SELECT * FROM mentor_trades WHERE id=? AND user_id=? AND status='open'",
        (trade_id, user_id))
    if not row:
        return {"ok": False, "hata": "acik islem bulunamadi"}

    side = row["side"]
    entry = float(row["entry"])
    # STOP NULL OLABILIR. Binance'ten otomatik alinan bir pozisyonda borsada
    # kurulu stop emri yoksa initial_stop bos kalir (uydurmak yerine). O
    # durumda R hesaplanamaz -> None. Dolar sonucu, sure ve yol yuzdeleri
    # yine olculur; yalnizca R tabanli istatistikler bu satiri saymaz.
    stop = float(row["initial_stop"]) if row["initial_stop"] is not None else None
    hedef = float(row["initial_target"]) if row["initial_target"] else None
    now = db.now_ms()

    r = r_multiple(side, entry, stop, exit_price) if stop is not None else None

    # Yol metrikleri icin islem PENCERESINDEKI mumlar. Alinamazsa islem
    # yine kapanir — olcum eksik kalir ama kayit kaybolmaz.
    yol = {"mfe_r": None, "mae_r": None, "updraw_pct": None, "drawdown_pct": None}
    try:
        from . import binance
        sure_ms = max(now - int(row["opened_at"]), 60_000)
        aralik = "5m" if sure_ms <= 12 * 3_600_000 else "1h"
        bars = await binance.klines_range(
            row["symbol"], aralik, int(row["opened_at"]) - 60_000, now + 60_000)
        if bars and stop is not None:
            yol = yol_metrikleri(side, entry, stop, hedef, bars)
    except Exception as exc:  # noqa: BLE001
        log.debug("mentor yol metrikleri alinamadi: %s", exc, exc_info=True)

    verim = (cikis_verimliligi(side, entry, stop, exit_price, yol["mfe_r"])
             if stop is not None else None)
    pnl = None
    if row["qty"]:
        yon = 1.0 if side == "LONG" else -1.0
        pnl = round((exit_price - entry) * yon * float(row["qty"]), 4)

    db.execute(
        "UPDATE mentor_trades SET closed_at=?, exit_price=?, exit_reason=?, "
        "result_r=?, pnl_usdt=?, mfe_r=?, mae_r=?, updraw_pct=?, "
        "drawdown_pct=?, exit_efficiency=?, status='closed' WHERE id=?",
        (now, exit_price, exit_reason[:60],
         round(r, 4) if r is not None else None, pnl,
         yol["mfe_r"], yol["mae_r"], yol["updraw_pct"], yol["drawdown_pct"],
         verim, trade_id))

    return {"ok": True, "id": trade_id, "result_r": round(r, 4) if r else None,
            "pnl_usdt": pnl, **yol, "exit_efficiency": verim}


def acik_islemler(user_id: int) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM mentor_trades WHERE user_id=? AND status='open' "
        "ORDER BY opened_at DESC", (user_id,))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("baglam", "bayraklar", "kontrol_listesi"):
            try:
                d[k] = json.loads(d.get(k) or "null")
            except (TypeError, ValueError):
                d[k] = None
        out.append(d)
    return out


def gecmis(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM mentor_trades WHERE user_id=? AND status='closed' "
        "ORDER BY closed_at DESC LIMIT ?", (user_id, limit))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("baglam", "bayraklar", "kontrol_listesi"):
            try:
                d[k] = json.loads(d.get(k) or "null")
            except (TypeError, ValueError):
                d[k] = None
        out.append(d)
    return out
