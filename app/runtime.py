"""Veritabaninda saklanan, yeniden baslatma gerektirmeyen calisma ayarlari."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from . import db
from .config import settings

DEFAULTS: Dict[str, Any] = {
    "engine": {
        "scan_interval_seconds": 300, "universe_size": 120, "signal_interval": "1h",
        "min_score": 4, "direction": "LONG", "cooldown_minutes": 360,
        "min_listing_days": 30, "min_quote_volume_m": 5,
        "max_signals_per_scan": 3, "max_signals_per_day": 6,
        # 0 = OTOMATIK ACILIS KAPALI. Backtest skor 5'i en kotu kova
        # olarak olctu (-0,0876R vs skor 4'te -0,0189R); "en guclu sinyali
        # otomatik ac" kurali tam tersini yapiyordu.
        "auto_open_score": 0,
        "research_enabled": True, "research_min_score": 2,
        "research_max_per_day": 12, "research_horizon_hours": 4,
    },
    # TSMOM + carry motoru. Eski skor motorundan TAMAMEN AYRI ayarlar:
    # ikisi ayni sayilari paylassaydi birini ayarlamak digerini bozardi ve
    # karsilastirma anlamsizlasirdi.
    "tsmom": {
        "enabled": True,
        # TAM TARAMA: tum evren, gunluk momentum yeniden hesaplanir.
        # 3600 -> 300. Gunluk barlar onbellekte tutuldugu icin tekrar
        # taramanin API maliyeti dusuk.
        "scan_interval_seconds": 300,
        # HIZLI GOZCU: tam tarama arasinda surekli calisir. Iki isi var:
        #   1) cikislari kontrol eder (stop/hedef gecikmesi 1 saatten
        #      30 saniyeye iner — en buyuk kazanc bu)
        #   2) izleme listesindeki kurulumlari CANLI fiyata gore yeniden
        #      degerlendirir; "fiyat sinyal barindan uzaklasti" diye elenmis
        #      bir kurulum geri cekilince acilabilir hale gelir
        "watch_interval_seconds": 30,
        "watchlist_size": 12,
        # RSI RADAR BESLEMESI. Saatlik radarın en yüksek/en düşük RSI
        # listeleri doğrudan işlem değildir; bunlar ana motora aday taşır.
        # Pozisyon ancak günlük TSMOM yönü, oynaklık, maliyet, fonlama ve
        # canlı fiyat kapıları da aynı yönde geçerse açılır.
        "rsi_leads_enabled": True,
        "rsi_leads_universe_size": 80,
        "rsi_leads_limit": 12,
        "rsi_leads_min_t": 1.5,
        # 01.09: 150 -> 250. Hacim + liste yasi filtrelerinden gecen 182
        # kontratin 32'si sirf ilk-150 kirpmasi yuzunden taranmiyordu. Canli
        # kuru provada bu dis grupta tum para kapilarini gecen CHZ SHORT
        # bulundu. Kalite esikleri gevsetilmedi; yalnizca uygun evrenin
        # tamamini tarama engeli kaldirildi.
        "universe_size": 250,
        "min_listing_days": 220,           # 126 gun geri bakis + 60 gun oynaklik + pay
        # 30.08: 10 -> 4. DIKKAT — bu esik cogu zaman ZATEN BAGLAYICI DEGIL:
        # evren "esigi gecenler -> hacme gore sirala -> ilk universe_size" diye
        # secildigi icin, esigi gecen sembol sayisi universe_size'dan fazlaysa
        # esik hicbir seyi elemez. Gercekte evreni belirleyen universe_size'dir.
        # Tarama teshisinde ikisi de artik ayri ayri gosteriliyor.
        "min_quote_volume_m": 4,
        "min_abs_t": 1.0,
        "stop_atr_mult": 2.5,
        "target_r": 4.0,
        "horizon_days": 14,
        "max_cost_r": 0.10,
        "use_maker_entry": True,
        "funding_extreme_bp": 5.0,
        "min_atr_pct": 0.8,
        "max_atr_pct": 15.0,
        "max_chase_atr": 0.75,             # fiyat sinyal barindan bu kadar uzaklastiysa girme
        "exit_t_floor": 0.30,              # yondeki momentum bunun altina duserse cik
        # Stop mesafesi tavani. 29.08'de 15 -> 25: likidasyon kontrolu
        # copy_trade katmanina tasindi, burasi artik yalnizca "bu kurulum
        # tutarli mi" sorusunu soruyor. 14 gunluk ufukta oynak bir altcoinde
        # 2.5xATR'nin %20'yi asmasi normaldir; %15 tavan kurulumlarin
        # cogunu sebepsiz eliyordu.
        "max_stop_pct": 25.0,
        "liq_safety": 0.65,                # stop, likidasyon mesafesinin en fazla bu kadari
        # MOTORUN KENDI SINIRLARI. Bunlar strateji sinirlari; paranin bununla
        # ilgisi yok. 29.08'de genisletildi: kullanici motorun surekli kurulum
        # kovalamasini istiyor, dar tavanlar bunu engelliyordu (5 LONG kaydi
        # dolunca motor duruyor gibi gorunuyordu — sebep para degil buydu).
        "max_open": 20,
        # Korelasyon ~0.48; ayni yonde yiginmak riski cesitlendirmez buyutur.
        # Ama bu RISK argumani ve risk artik para katmaninin isi. Motor
        # tarafinda yalnizca olcumun tek yone saplanmamasi icin duruyor.
        "max_per_side": 10,
        "max_new_per_scan": 4,
        # PORTFOY ROTASYONU. Gunluk TSMOM'u 4 saatlik stratejiye cevirmeden
        # her 4 saatte bir aciklar ile yeni adaylari karsilastirir. Yeni aday
        # belirgin gucluyse zayif, genc olmayan ve buyuk kazanca gecmemis
        # kayit kapatilarak slot acilir.
        "rotation_enabled": True,
        "rotation_review_hours": 4.0,
        "rotation_min_age_hours": 4.0,
        "rotation_min_candidate_t": 1.25,
        "rotation_min_t_improvement": 0.30,
        "rotation_protect_winner_r": 0.50,
        "rotation_max_per_review": 1,
        # Ayni yon limiti yumusaktir: yalnizca elit ve mevcut en zayiftan
        # belirgin guclu aday icin iki arastirma slotu esneyebilir. Para
        # katmanindaki max_open_trades ASLA burada esnetilmez.
        "side_flex_slots": 2,
        "symbol_cooldown_hours": 24.0,
        # Gunluk sinyal ufku ayri, operasyonel sert tutma tavani ayri.
        # Kullanici istegi: bir pozisyon stop/hedef veya momentum cikisina
        # daha once ulasmazsa 4 saat sonunda kesin kapatilir ve slot bosalir.
        "max_hold_hours": 4.0,
        # DURGUN ISLEM CIKISI. Ufkunun bir kismini harcayip hicbir yere
        # gitmemis pozisyonu kapatir ve yeri yeni kuruluma acar. Trend
        # sistemlerinde getiri uzun kuyruktan gelir; 6 gunde 0.4R bile
        # hareket etmemis bir islem o kuyrugun parcasi olma ihtimalini
        # buyuk olcude tuketmistir. Kapatmak riskli bir tercihtir —
        # karne bunu OLCECEK, o yuzden ayardan kapatilabilir.
        "stale_exit": True,
        "stale_days": 6,            # bu kadar gun aciksa ve
        "stale_max_mfe_r": 0.4,     # lehte hic bu kadar gitmemisse -> kapat
        # NOT: auto_open ve max_open_trades buradan KALKTI. Kagit pozisyon
        # acmak artik copy_trade katmaninin isi (runtime "copy" bolumu).
        # Acilamayan ama kapilari gecen kurulumlar aday olarak listelenir.
        "selftest_enabled": True,          # gunde bir kez kendini backtest et
        "selftest_days": 120,
        "selftest_symbols": 12,
        "show_candidates": True,
        "max_candidates": 8,
    },
    # Ana motorun 15 dakikalık Avcı alt katmanı. Canlı emir kapısı, ileri
    # testte pozitif kanıt oluşana kadar bilinçli olarak kilitlidir.
    "hunter": {
        "enabled": True, "cycle_seconds": 120, "universe_size": 200,
        "min_listing_days": 14, "min_quote_volume_m": 4.0,
        "breakout_lookback": 40, "setup_window_bars": 8,
        "breakout_buffer_pct": 0.30, "min_breakout_move_pct": 4.0,
        "min_breakout_rel_volume": 3.0, "min_reclaim_rel_volume": 1.5,
        "max_stop_pct": 18.0, "stop_atr_mult": 0.25, "target_r": 2.0,
        "hold_hours": 4.0, "cooldown_hours": 4.0,
        "max_open": 6, "max_new_per_cycle": 2, "live_enabled": False,
    },
    # V4 karar katmanı önce gölge challenger olarak ölçülür. Aynı gerçek
    # TSMOM işlemlerinin yalnız rejim filtresinden geçen alt kümesi ayrı
    # raporlanır; kanıt oluşmadan champion davranışı değiştirilmez.
    "decision_v4": {
        "mode": "shadow",
        "require_validation_for_live": True,
        "quarantined_live_sides": ["SHORT"],
        "router_min_confidence": 0.67,
        "router_max_volatility_rank": 0.95,
        "router_allow_transition": False,
        "router_allow_range": False,
        "feature_store_enabled": True,
        # 15dk kaynaktan saatlik noktasal örnek alınır. Her iki dakikada bir
        # 200 sembol yazmak veri sayısını büyütür ama bağımsız bilgi üretmez.
        "feature_sample_minutes": 60,
        "feature_universe_size": 120,
        "feature_retention_days": 365,
    },
    # COPY TRADE — para katmani. 29.08'de ana motordan ayrildi: hesap
    # buyuklugune bagli her sinir burada, motorun kendisinde degil.
    "copy": {
        "auto_mirror": True,        # motor sinyalini parayla aynala
        "max_open_trades": 6,       # ayni anda kac kagit pozisyon
        # GUVEN ESIGI. Motor |t| >= min_abs_t (1.0) olan her kurulumu acar
        # ve olcer. Parayla aynalamak icin DAHA YUKSEK bir cita: yalnizca
        # sinyalin gucune yeterince guvendiklerimiz para tarafina gecer.
        # 0 yazilirsa cita kalkar, motorun actigi her kurulum aynalanir.
        "min_t_to_mirror": 1.25,
        # Kontrollü agresif bant: minimum eşik ile tam-risk eşiği arasındaki
        # LONG kurulumlar daha küçük riskle denenir. İşlem sıklığını artırır
        # fakat zayıf sinyale tam risk yüklemez.
        "full_risk_min_t": 1.5,
        "exploration_risk_fraction": 0.35,
        "max_exploration_open": 1,
        # İleri-test eşikleri canlı para doğrulaması ve performans raporu
        # içindir. Telegram bildirim akışını bloke etmezler.
        "validation_min_closed": 30,
        "validation_min_observation_days": 20,
        "validation_min_side_closed": 10,
        "validation_max_drawdown_r": 6.0,
        "validation_require_ci_positive": True,
    },
    "risk": {
        "risk_profile": "conservative", "max_risk_per_trade": settings.max_risk_per_trade,
        # GUNLUK ZARAR DURDURUCUSU: bugun GERCEKLESMIS zarar bunu asarsa
        # gun sonuna kadar yeni pozisyon acilmaz.
        "daily_loss_limit": 7.0,
        # Toplam bakiye. Yalnizca Copy Trade katmaninin isi — ana motorun
        # kurulum bulmasina karismaz.
        "total_margin": 380.0,
        "max_position_margin": settings.max_position_margin,
        "default_leverage": settings.default_leverage, "reward_r": 3.0,
        # Stop = ATR x stop_atr_mult. Maliyetin R cinsinden agirligi bu carpana
        # TERS oranlidir: stop iki kat genisse maliyet R olarak yariya iner.
        # 27.08 backtestinde maliyet 0.0858R/islem ile brut edge'in (0.0576R)
        # ustundeydi — bu yuzden ayarlanabilir olmasi gerekiyor.
        "stop_atr_mult": 1.5,
        # Birincil hedef, R cinsinden. Olculen ortalama MFE 1.02R iken hedef
        # 1.5R'deydi: ortalama islem hedefe hic ulasmiyor.
        "target_r": 1.5,
    },
    # HIZLI TEST MOTORU — 1 saatlik dongu, yalnizca olcum. Paraya ve ana
    # motora dokunmaz; amaci hizli geri bildirim uretmek.
    "test_engine": {
        "enabled": True,
        "cycle_seconds": 3600,      # saatte bir: ac / kapat / olc
        "hold_minutes": 60,         # bir islem bu kadar tutulur
        "concurrent": 2,            # ayni anda kac test pozisyonu
        "universe_size": 60,
        "min_quote_volume_m": 10,
        "target_r": 2.0,
    },
    # MENTOR GUNLUGU devre kesicileri.
    # Bunlar TAVSIYE degil KAPI: sunucu tarafinda kayit acmayi engelliyorlar.
    # 380$'lik bir hesapta hesabi koruma ihtimali en yuksek ozellik bu.
    # MENTOR OTOMATIK TARAMA. Kullanici sormadan piyasayi tarar ve dikkate
    # deger gozlemleri kaydeder. Amac ornekleme tarafliligini kirmak:
    # aksi halde sicil "tahminlerimiz iyi mi" degil "Taha'nin baktigi
    # anlarda iyi mi" sorusunu olcer.
    "mentor_tarama": {
        "enabled": True,
        "yon": "LONG",
        "maliyet_tavan_r": 0.08,
        "aralik_dakika": 60,
        # 07.09: 120 -> 150, VE DAHA ONEMLISI: bu 150 artik "hacme gore ilk
        # 150" degil. market_library butun evreni (~500 surekli kontrat)
        # her dakika tek cagriyla (agirlik 40) izliyor; buradaki 150,
        # sembolun KENDI hacim tabanina gore genisleyenler + 1 saatlik ivme
        # + bant konumuna gore secilen 150. Eski kuralda liste gunlerce
        # ayni kaliyordu (hacim siralamasi degismez), yani sistem her saat
        # ayni coinlere bakip geri kalanini hic gormuyordu.
        #
        # AGIRLIK BUTCESI: derin analiz sembol basina ~8-10 agirlik
        # (500 bar snapshot + 220 bar RSI + premiumIndex). 150 sembol =
        # ~1300-1500 agirlik, es zamanlilik 4 ile ~60-90 saniyeye yayiliyor.
        # Dakikalik limit 2400 ve binance.py %80'de kendini yavaslatiyor.
        # 200'un uzerine cikarmadan once o esigi olcmek gerekir.
        "evren": 150,
        # 5.0 -> 0.5. Hacim tabani ARTIK BURADA DEGIL: kutuphane 300 bin
        # dolarin altindaki (defteri olmayan) kontratlari zaten eliyor ve
        # secim mutlak hacme degil sembolun kendi normaline gore yapiliyor.
        # Yuksek sabit esik tam olarak kullanicinin sikayet ettigi seyi
        # yapiyordu: hep ayni buyuk coinleri kovalamak.
        "min_hacim_m": 0.5,
        "interval": "1h",
        # Es zamanlilik 4: 120 sembol 3 ile ~2 dakika suruyordu. 4 makul
        # bir orta yol; daha yukarisi anlik agirlik sicramasi yaratiyor.
        "es_zamanli": 4,
        "tekrar_pencere_saat": 6,     # ayni sembolu bu sure icinde tekrar yazma
        "min_lehte_kanit": 3,
    },
    "mentor_gunluk": {
        "gunluk_zarar_limiti": 12.0,        # $ — bugunku gerceklesmis zarar
        "zarar_sonrasi_bekleme_dk": 20,     # intikam islemine mekanik engel
        # Binance'teki acik pozisyonlari gunluge OTOMATIK yazar ve kapaninca
        # gercek cikis fiyatiyla kapatir. Elle giris bir yedek yol olarak
        # duruyor ama artik ana yol degil: on bir alanlik form doldurulmadigi
        # icin sicil bos kaliyordu, sicil bos kalinca karne hicbir sey
        # olcemiyordu.
        "otomatik_senkron": True,
    },
    # PLAN BILDIRIMI — tarama yeni kurulum buldukca telefona push.
    #
    # NEDEN VAR: kullanicinin sorunu sinyal uretmek degil, GIRISLERI
    # KACIRMAKTI. Ekrana bakmadigi saatlerde kurulum olusuyor ve o gorene
    # kadar fiyat gidiyor. Bu, otomatik emir acmadan cozulen bir sorun.
    #
    # NEDEN TEKRAR KAPISI VAR: tarama her 15 dakikada donuyor ve ayni
    # kurulum saatlerce listede kalabilir. Filtresiz gondermek gunde
    # yuzlerce bildirim demekti; bir sure sonra hepsi susturulur ve
    # bildirim kanali OLUR. Bir sembol icin bir bildirim, sonra bekleme.
    "plan_bildirim": {
        "enabled": True,
        # Yalnizca bu yondekiler. mentor_tarama.yon zaten suzuyor;
        # bu ikinci kapi bildirim tarafini ayri ayarlanabilir tutuyor.
        "yon": "LONG",
        "min_rr": 1.8,
        # "simdi" = zamanlama uygun; "hepsi" = bekle/gec de gonderilsin.
        # Varsayilan "simdi": gec kalmis bir kurulumu bildirmek, tam olarak
        # kullanicinin kacinmak istedigi kovalamayi tesvik eder.
        "zamanlama": "simdi",
        # Ayni sembol icin iki bildirim arasinda en az bu kadar saat.
        "tekrar_saat": 6,
        "gunluk_azami": 12,
        # Tarama basina en fazla; bir seferde 9 bildirim telefonu kilitler.
        "tarama_azami": 3,
        # BTC ile catisan kurulum bildirilmez (listede yine gorunur).
        "btc_catismasini_atla": True,
    },
    "notifications": {
        "news_minutes": 1440, "rsi_minutes": 60, "analysis_hours": 4,
        "send_signals": True, "send_research": False,
        "analysis_symbols": "BTCUSDT,ETHUSDT,XRPUSDT",
    },
    "dashboard": {
        "hero": True, "chart": True, "open_trades": True, "candidates": True,
        "derivatives": True, "movers": True, "news": True, "calendar": True,
    },
}


def _merge(base: Dict[str, Any], saved: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in saved.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        elif key in base:
            base[key] = value
    return base


def migrate() -> Dict[str, Any]:
    """Acilista bir kez calisan ayar gecisleri.

    get() DEFAULTS ile KAYITLI ayari birlestiriyor; kayitli deger her zaman
    kazaniyor. Yani bir varsayilani degistirmek, o ayari daha once kaydetmis
    kullanicilarda hicbir sey degistirmiyor. Kotu bir varsayilani duzeltmek
    icin kayitli degeri de tasimak gerekiyor — burasi o.

    Yalnizca kullanicinin ELLE degistirmedigi degerlere dokunuluyor: deger
    tam olarak eski varsayilana esitse tasiniyor, degilse birakiliyor.
    """
    saved = db.get_setting("runtime_settings", {})
    if not isinstance(saved, dict):
        return {}
    degisen = {}
    t = saved.get("tsmom")
    if isinstance(t, dict):
        # Saatte bir tarama kullanicinin en cok sikayet ettigi davranisti.
        if int(t.get("scan_interval_seconds", 0)) == 3600:
            t["scan_interval_seconds"] = 300
            degisen["scan_interval_seconds"] = "3600 -> 300"
        # Stop tavani kaldiraca bagliyken %15 cok siki kaliyordu; likidasyon
        # kontrolu copy_trade'e tasindiktan sonra bu tavanin dar kalmasinin
        # sebebi kalmadi.
        if float(t.get("max_stop_pct", 0)) == 15.0:
            t["max_stop_pct"] = 25.0
        # 30.08: hacim esigi 10M gereksiz yuksekti; kullanici elle
        # degistirmediyse 4M'ye tasi.
        if float(t.get("min_quote_volume_m", 0)) == 10.0:
            t["min_quote_volume_m"] = 4.0
            degisen["min_quote_volume_m"] = "10 -> 4"
            degisen["max_stop_pct"] = "15 -> 25"
        # Motorun kendi tavanlari: dar birakilirsa motor "para bitti" gibi
        # gorunen ama aslinda STRATEJI sinirindan kaynaklanan bir duvara
        # carpiyordu (5 LONG dolunca duruyordu).
        if int(t.get("max_open", 0)) == 8:
            t["max_open"] = 20
            degisen["max_open"] = "8 -> 20"
        if int(t.get("max_per_side", 0)) == 5:
            t["max_per_side"] = 10
            degisen["max_per_side"] = "5 -> 10"
        if int(t.get("max_new_per_scan", 0)) == 2:
            t["max_new_per_scan"] = 4
            degisen["max_new_per_scan"] = "2 -> 4"
        # 01.09: 4M hacim ve 220 gun yas kapisindan gecen kontrat sayisi
        # 182 iken ilk-150 kirpmasi taze SHORT adayini gorunmez yapiyordu.
        # Kullanici elle farkli bir evren secmediyse tam uygun evrene gec.
        if int(t.get("universe_size", 0)) == 150:
            t["universe_size"] = 250
            degisen["universe_size"] = "150 -> 250"
        # 05.09 kontrollü agresif profil: yalnız belirgin güçlü aday için
        # iki esnek araştırma slotu; para kapasitesi ayrıca sınırlı kalır.
        if float(t.get("rotation_min_candidate_t", 0)) == 1.75:
            t["rotation_min_candidate_t"] = 1.25
            degisen["rotation_min_candidate_t"] = "1.75 -> 1.25"
        if float(t.get("rotation_min_t_improvement", 0)) == .4:
            t["rotation_min_t_improvement"] = .3
            degisen["rotation_min_t_improvement"] = "0.4 -> 0.3"
        if int(t.get("side_flex_slots", 0)) == 1:
            t["side_flex_slots"] = 2
            degisen["side_flex_slots"] = "1 -> 2"
        # 05.09: acik pozisyonlarin yeni kurulumlari saatlerce bloke etmesini
        # engelleyen sert 4 saatlik timeout. Yalniz eski varsayilan (24) ise
        # tasinir; kullanicinin baska bir ozel degeri korunur.
        if float(t.get("max_hold_hours", 0)) == 24.0:
            t["max_hold_hours"] = 4.0
            degisen["max_hold_hours"] = "24 -> 4"
    # 31.08: Kullanici acik risk tavanini tamamen kaldirdi. Es zamanli
    # pozisyon kapasitesi artik yalnizca copy.max_open_trades ile yonetilir.
    # Kayitli eski anahtari da sil ki API ve sonraki kayitlarda geri donmesin.
    r = saved.get("risk")
    if isinstance(r, dict) and "max_open_risk" in r:
        r.pop("max_open_risk", None)
        degisen["max_open_risk"] = "kaldirildi"
    c = saved.get("copy")
    if isinstance(c, dict) and "validation_gate_notifications" in c:
        c.pop("validation_gate_notifications", None)
        degisen["validation_gate_notifications"] = "kaldirildi"
    if isinstance(c, dict) and float(c.get("min_t_to_mirror", 0)) == 1.5:
        c["min_t_to_mirror"] = 1.25
        degisen["min_t_to_mirror"] = "1.5 -> 1.25"
    # ------------------------------------------------------------------
    # 09.09 — BACKTEST KARARLARININ KAYITLI AYARA UYGULANMASI
    #
    # DEFAULTS'u degistirmek KAYITLI ayari degistirmez: _merge'te kayitli
    # deger kazanir. Bu yuzden 27.08 backtestinin iki karari, ayari daha
    # once kaydetmis bir kurulumda kendiliginden yururluge girmiyordu.
    #
    #   1) auto_open_score 5 -> 0. Rapor skor 5'i EN KOTU kova olarak
    #      olctu (-0,0876R; skor 4 -0,0189R). "En guclu sinyali otomatik
    #      ac" kurali tam tersini yapiyordu. Bu bir emniyet duzeltmesi.
    #   2) direction BOTH -> LONG. SHORT ortalama -0,1008R, %95 GA
    #      [-0,185, -0,016] — sifiri icermeyen tek bulgu.
    #
    # YALNIZCA BIR KEZ. Bayrak olmasaydi kullanici ayari elle geri
    # aldiginda her acilista tekrar ezerdik; ayarin sahibi kullanici.
    if not db.get_setting("gecis_20260909_backtest"):
        e_kayit = saved.setdefault("engine", {}) if isinstance(saved, dict) else {}
        if int(e_kayit.get("auto_open_score", 0) or 0) > 0:
            e_kayit["auto_open_score"] = 0
            degisen["auto_open_score"] = "otomatik açılış kapatıldı (backtest)"
        if str(e_kayit.get("direction", "")).upper() == "BOTH":
            e_kayit["direction"] = "LONG"
            degisen["direction"] = "BOTH -> LONG (backtest)"
        db.set_setting("gecis_20260909_backtest", True)

    if degisen:
        db.set_setting("runtime_settings", saved)
    return degisen


def get() -> Dict[str, Any]:
    saved = db.get_setting("runtime_settings", {})
    return _merge(deepcopy(DEFAULTS), saved if isinstance(saved, dict) else {})


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _merge(get(), raw)
    e, r, n, d = cfg["engine"], cfg["risk"], cfg["notifications"], cfg["dashboard"]
    # MENTOR GUNLUGU — sinir normalizasyonu. Devre kesici degerleri kullanici
    # tarafindan kaydedildigi icin burada da kirpiliyor.
    mt = cfg.setdefault("mentor_tarama", {})
    mt["enabled"] = bool(mt.get("enabled", True))
    mt["aralik_dakika"] = max(10, min(int(mt.get("aralik_dakika", 60)), 1440))
    mt["evren"] = max(5, min(int(mt.get("evren", 150)), 300))
    mt["min_hacim_m"] = max(0.0, min(float(mt.get("min_hacim_m", 0.5)), 1000.0))
    mt["es_zamanli"] = max(1, min(int(mt.get("es_zamanli", 4)), 8))
    mt["tekrar_pencere_saat"] = max(1, min(int(mt.get("tekrar_pencere_saat", 6)), 168))
    mt["min_lehte_kanit"] = max(1, min(int(mt.get("min_lehte_kanit", 3)), 10))
    # MALIYET TAVANI (R). 0 = kapali.
    # 0,08 varsayilani stopu ~%1,75'in altinda olan kurulumlari eliyor.
    # Gerekce aritmetik: maliyet = %0,14 x (giris/stop_mesafesi) ve
    # olculen brut beklenti +0,058R. Maliyet bunun yanina yaklastiginda
    # kurulum kagit uzerinde bile negatif.
    mt["maliyet_tavan_r"] = max(0.0, min(float(mt.get("maliyet_tavan_r", 0.08)), 1.0))
    # YON SUZGECI. Varsayilan LONG.
    # GEREKCE (27.08 backtesti, 1.578 islem): SHORT ortalama -0,1008R,
    # %95 guven araligi [-0,185, -0,016] — sifiri ICERMIYOR. Bu, raporun
    # tek istatistiksel olarak anlamli bulgusu ve MIHENK'te alti ayri veri
    # setinde cikan "LONG-only both-sides'i yener" sonucunun yedinci
    # tekrari. LONG tarafinda brut +0,13R var; maliyet sonrasi anlamli
    # degil ama negatif de degil.
    yon = str(mt.get("yon", "LONG")).upper().strip()
    mt["yon"] = yon if yon in ("LONG", "SHORT", "BOTH") else "LONG"

    pb = cfg.setdefault("plan_bildirim", {})
    pb["enabled"] = bool(pb.get("enabled", True))
    pb_yon = str(pb.get("yon", "LONG")).upper().strip()
    pb["yon"] = pb_yon if pb_yon in ("LONG", "SHORT", "BOTH") else "LONG"
    pb["min_rr"] = max(0.0, min(float(pb.get("min_rr", 1.8)), 20.0))
    pb["zamanlama"] = "hepsi" if str(pb.get("zamanlama", "simdi")) == "hepsi" else "simdi"
    pb["tekrar_saat"] = max(1, min(int(pb.get("tekrar_saat", 6)), 168))
    pb["gunluk_azami"] = max(1, min(int(pb.get("gunluk_azami", 12)), 100))
    pb["tarama_azami"] = max(1, min(int(pb.get("tarama_azami", 3)), 20))
    pb["btc_catismasini_atla"] = bool(pb.get("btc_catismasini_atla", True))

    mg = cfg.setdefault("mentor_gunluk", {})
    mg["otomatik_senkron"] = bool(mg.get("otomatik_senkron", True))
    mg["gunluk_zarar_limiti"] = max(0.0, min(float(mg.get("gunluk_zarar_limiti", 12.0)), 100000.0))
    mg["zarar_sonrasi_bekleme_dk"] = max(0, min(int(mg.get("zarar_sonrasi_bekleme_dk", 20)), 1440))

    t = cfg.setdefault("tsmom", {})
    t["enabled"] = bool(t.get("enabled", True))
    # Alt sinir 60 sn: tam tarama tum evrenin gunluk barlarina dokunuyor;
    # daha sik calistirmak agirlik butcesini gereksiz yakar ve GUNLUK bar
    # zaten gun icinde degismez.
    t["scan_interval_seconds"] = max(60, min(int(t.get("scan_interval_seconds", 300)), 86400))
    # Alt sinir 10 sn: gozcu yalnizca ticker + onbellekli barlarla calisiyor,
    # ama 10 sn'nin altinda fayda yok — fiyat o kadar hizli anlamlı
    # degismiyor, sadece istek sayisi artar.
    t["watch_interval_seconds"] = max(10, min(int(t.get("watch_interval_seconds", 30)), 3600))
    t["watchlist_size"] = max(0, min(int(t.get("watchlist_size", 12)), 60))
    t["rsi_leads_enabled"] = bool(t.get("rsi_leads_enabled", True))
    t["rsi_leads_universe_size"] = max(24, min(int(t.get("rsi_leads_universe_size", 80)), 200))
    t["rsi_leads_limit"] = max(0, min(int(t.get("rsi_leads_limit", 12)), 40))
    t["rsi_leads_min_t"] = max(.5, min(float(t.get("rsi_leads_min_t", 1.5)), 5.0))
    t["universe_size"] = max(0, min(int(t.get("universe_size", 150)), 500))
    # 191 gun stratejinin mutlak alt siniri (MIN_BARS); altina inilirse motor
    # hicbir sembol bulamaz, o yuzden ayardan da kirpilyor.
    t["min_listing_days"] = max(200, min(int(t.get("min_listing_days", 220)), 1000))
    t["min_quote_volume_m"] = max(0.5, min(float(t.get("min_quote_volume_m", 4)), 1000))
    t["min_abs_t"] = max(0.3, min(float(t.get("min_abs_t", 1.0)), 5.0))
    t["stop_atr_mult"] = max(1.0, min(float(t.get("stop_atr_mult", 2.5)), 6.0))
    t["target_r"] = max(1.0, min(float(t.get("target_r", 4.0)), 12.0))
    t["horizon_days"] = max(1, min(int(t.get("horizon_days", 14)), 90))
    t["max_cost_r"] = max(0.01, min(float(t.get("max_cost_r", 0.10)), 1.0))
    t["use_maker_entry"] = bool(t.get("use_maker_entry", True))
    t["funding_extreme_bp"] = max(0.5, min(float(t.get("funding_extreme_bp", 5.0)), 100.0))
    t["max_chase_atr"] = max(0.1, min(float(t.get("max_chase_atr", 0.75)), 5.0))
    # Cikis tabani giris esiginin uzerine cikamaz: aksi halde acilan pozisyon
    # bir sonraki kontrolde aninda kapanir.
    t["exit_t_floor"] = max(-1.0, min(float(t.get("exit_t_floor", 0.30)),
                                      t["min_abs_t"] * 0.8))
    t["max_stop_pct"] = max(1.0, min(float(t.get("max_stop_pct", 25.0)), 60.0))
    t["liq_safety"] = max(0.1, min(float(t.get("liq_safety", 0.65)), 0.95))
    t["min_atr_pct"] = max(0.05, min(float(t.get("min_atr_pct", 0.8)), 10.0))
    t["max_atr_pct"] = max(t["min_atr_pct"] + 0.1, min(float(t.get("max_atr_pct", 15.0)), 60.0))
    t["max_open"] = max(1, min(int(t.get("max_open", 8)), 40))
    t["max_per_side"] = max(1, min(int(t.get("max_per_side", 5)), t["max_open"]))
    t["max_new_per_scan"] = max(1, min(int(t.get("max_new_per_scan", 2)), 10))
    t["rotation_enabled"] = bool(t.get("rotation_enabled", True))
    t["rotation_review_hours"] = max(1.0, min(float(t.get("rotation_review_hours", 4)), 48.0))
    t["rotation_min_age_hours"] = max(1.0, min(float(t.get("rotation_min_age_hours", 4)), 72.0))
    t["rotation_min_candidate_t"] = max(t["min_abs_t"], min(float(t.get("rotation_min_candidate_t", 1.25)), 5.0))
    t["rotation_min_t_improvement"] = max(0.1, min(float(t.get("rotation_min_t_improvement", .3)), 3.0))
    t["rotation_protect_winner_r"] = max(-1.0, min(float(t.get("rotation_protect_winner_r", .5)), 5.0))
    t["rotation_max_per_review"] = max(0, min(int(t.get("rotation_max_per_review", 1)), 3))
    t["side_flex_slots"] = max(0, min(int(t.get("side_flex_slots", 2)), 3))
    t["symbol_cooldown_hours"] = max(0.0, min(float(t.get("symbol_cooldown_hours", 24)), 720.0))
    t["max_hold_hours"] = max(4.0, min(float(t.get("max_hold_hours", 4)), 2160.0))
    # Durgun islem cikisi
    t["stale_exit"] = bool(t.get("stale_exit", True))
    # Alt sinir 1 gun: gunluk bar uzerinde calisan bir sistemde "1 gunden
    # kisa surede hicbir yere gitmedi" demek anlamsiz olurdu.
    t["stale_days"] = max(1, min(int(t.get("stale_days", 6)), 90))
    t["stale_max_mfe_r"] = max(0.0, min(float(t.get("stale_max_mfe_r", 0.4)), 3.0))

    # HIZLI TEST MOTORU
    x = cfg.setdefault("test_engine", {})
    x["enabled"] = bool(x.get("enabled", True))
    x["cycle_seconds"] = max(300, min(int(x.get("cycle_seconds", 3600)), 86400))
    x["hold_minutes"] = max(5, min(float(x.get("hold_minutes", 60)), 1440))
    # Ust sinir 8: her acik pozisyon bir sembolu kilitliyor ve olcumu
    # seyreltiyor. Cok pozisyon = az bagimsiz gozlem.
    x["concurrent"] = max(1, min(int(x.get("concurrent", 2)), 8))
    x["universe_size"] = max(5, min(int(x.get("universe_size", 60)), 300))
    x["min_quote_volume_m"] = max(1, min(float(x.get("min_quote_volume_m", 10)), 1000))
    x["target_r"] = max(0.5, min(float(x.get("target_r", 2.0)), 10.0))

    # COPY TRADE — para katmani
    c = cfg.setdefault("copy", {})
    c["auto_mirror"] = bool(c.get("auto_mirror", True))
    c["max_open_trades"] = max(1, min(int(c.get("max_open_trades", 6)), 30))
    c["min_t_to_mirror"] = max(0.0, min(float(c.get("min_t_to_mirror", 1.25)), 10.0))
    c["full_risk_min_t"] = max(c["min_t_to_mirror"], min(
        float(c.get("full_risk_min_t", 1.5)), 10.0))
    c["exploration_risk_fraction"] = max(.1, min(
        float(c.get("exploration_risk_fraction", .35)), 1.0))
    c["max_exploration_open"] = max(0, min(int(c.get("max_exploration_open", 1)), 4))
    # Eski Telegram yayın kilidi kaldırıldı. Kalıcı ayarlarda eski anahtar
    # bulunsa bile normalize sırasında temizlenir; yeniden etkinleşemez.
    c.pop("validation_gate_notifications", None)
    c["validation_min_closed"] = max(10, min(int(c.get("validation_min_closed", 30)), 1000))
    c["validation_min_observation_days"] = max(5, min(int(c.get("validation_min_observation_days", 20)), 365))
    c["validation_min_side_closed"] = max(5, min(int(c.get("validation_min_side_closed", 10)), 500))
    c["validation_max_drawdown_r"] = max(1.0, min(float(c.get("validation_max_drawdown_r", 6.0)), 1000.0))
    c["validation_require_ci_positive"] = bool(c.get("validation_require_ci_positive", True))
    t["selftest_enabled"] = bool(t.get("selftest_enabled", True))
    t["selftest_days"] = max(30, min(int(t.get("selftest_days", 120)), 720))
    t["selftest_symbols"] = max(3, min(int(t.get("selftest_symbols", 12)), 40))
    t["show_candidates"] = bool(t.get("show_candidates", True))
    t["max_candidates"] = max(0, min(int(t.get("max_candidates", 8)), 40))
    h = cfg.setdefault("hunter", {})
    h["enabled"] = bool(h.get("enabled", True))
    h["cycle_seconds"] = max(60, min(int(h.get("cycle_seconds", 120)), 3600))
    h["universe_size"] = max(20, min(int(h.get("universe_size", 200)), 500))
    h["min_listing_days"] = max(0, min(int(h.get("min_listing_days", 14)), 365))
    h["min_quote_volume_m"] = max(.5, min(float(h.get("min_quote_volume_m", 4)), 1000))
    h["breakout_lookback"] = max(20, min(int(h.get("breakout_lookback", 40)), 120))
    h["setup_window_bars"] = max(3, min(int(h.get("setup_window_bars", 8)), 24))
    h["breakout_buffer_pct"] = max(.05, min(float(h.get("breakout_buffer_pct", .3)), 3))
    h["min_breakout_move_pct"] = max(1, min(float(h.get("min_breakout_move_pct", 4)), 30))
    h["min_breakout_rel_volume"] = max(1.2, min(float(h.get("min_breakout_rel_volume", 3)), 30))
    h["min_reclaim_rel_volume"] = max(1.0, min(float(h.get("min_reclaim_rel_volume", 1.5)), 20))
    h["max_stop_pct"] = max(2, min(float(h.get("max_stop_pct", 18)), 30))
    h["stop_atr_mult"] = max(.05, min(float(h.get("stop_atr_mult", .25)), 3))
    h["target_r"] = max(1, min(float(h.get("target_r", 2)), 6))
    h["hold_hours"] = max(1, min(float(h.get("hold_hours", 4)), 24))
    h["cooldown_hours"] = max(1, min(float(h.get("cooldown_hours", 4)), 72))
    h["max_open"] = max(1, min(int(h.get("max_open", 6)), 20))
    h["max_new_per_cycle"] = max(1, min(int(h.get("max_new_per_cycle", 2)), 5))
    # Kullanıcı ayarıyla yanlışlıkla açılamaz; kanıt sonrası kod sürümü gerekir.
    h["live_enabled"] = False
    v4 = cfg.setdefault("decision_v4", {})
    v4["mode"] = "shadow" if str(v4.get("mode", "shadow")) != "promoted" else "promoted"
    v4["require_validation_for_live"] = bool(v4.get("require_validation_for_live", True))
    sides = v4.get("quarantined_live_sides", ["SHORT"])
    if not isinstance(sides, list):
        sides = [str(sides)]
    v4["quarantined_live_sides"] = [x for x in {str(s).upper() for s in sides}
                                     if x in ("LONG", "SHORT")]
    v4["router_min_confidence"] = max(0.0, min(float(v4.get("router_min_confidence", .67)), 1.0))
    v4["router_max_volatility_rank"] = max(.5, min(float(v4.get("router_max_volatility_rank", .95)), 1.0))
    v4["router_allow_transition"] = bool(v4.get("router_allow_transition", False))
    v4["router_allow_range"] = bool(v4.get("router_allow_range", False))
    v4["feature_store_enabled"] = bool(v4.get("feature_store_enabled", True))
    v4["feature_sample_minutes"] = max(15, min(int(v4.get("feature_sample_minutes", 60)), 1440))
    v4["feature_universe_size"] = max(20, min(int(v4.get("feature_universe_size", 120)), 500))
    v4["feature_retention_days"] = max(30, min(int(v4.get("feature_retention_days", 365)), 3650))
    e["scan_interval_seconds"] = max(60, min(int(e["scan_interval_seconds"]), 3600))
    # 0 = sinir yok: hacim ve yas filtresini gecen TUM kontratlar taranir.
    e["universe_size"] = max(0, min(int(e["universe_size"]), 500))
    e["signal_interval"] = e["signal_interval"] if e["signal_interval"] in ("15m", "30m", "1h", "4h") else "1h"
    e["min_score"] = max(1, min(int(e["min_score"]), 5))
    # 0 = otomatik acma kapali. Aksi halde bu skor ve ustu paper islem
    # dogrudan 'open' olarak acilir (risk ve gunluk kayip limitleri yine gecerli).
    e["auto_open_score"] = max(0, min(int(e.get("auto_open_score", 0)), 5))
    e["direction"] = e["direction"] if e["direction"] in ("BOTH", "LONG", "SHORT") else "BOTH"
    e["cooldown_minutes"] = max(15, min(int(e["cooldown_minutes"]), 2880))
    # Kontrat yasi: yeni listelenenler trend filtrelerini yanlis tetikliyor.
    e["min_listing_days"] = max(0, min(int(e.get("min_listing_days", 30)), 365))
    # Minimum 24s hacim (milyon USDT).
    e["min_quote_volume_m"] = max(0.5, min(float(e.get("min_quote_volume_m", 5)), 5000))
    e["max_signals_per_scan"] = max(0, min(int(e["max_signals_per_scan"]), 20))
    e["max_signals_per_day"] = max(0, min(int(e["max_signals_per_day"]), 100))
    e["research_enabled"] = bool(e["research_enabled"])
    e["research_min_score"] = max(0, min(int(e["research_min_score"]), 5))
    e["research_max_per_day"] = max(0, min(int(e["research_max_per_day"]), 100))
    e["research_horizon_hours"] = max(1, min(int(e["research_horizon_hours"]), 72))
    r["risk_profile"] = r["risk_profile"] if r["risk_profile"] in ("conservative", "balanced", "aggressive") else "conservative"
    r["max_risk_per_trade"] = max(.25, min(float(r["max_risk_per_trade"]), 1000.0))
    r["daily_loss_limit"] = max(.25, min(float(r["daily_loss_limit"]), 10000.0))
    r["total_margin"] = max(1.0, min(float(r.get("total_margin", 380.0)), 10_000_000.0))
    r["max_position_margin"] = max(1.0, min(float(r["max_position_margin"]), 100000.0))
    r["default_leverage"] = max(1, min(int(r["default_leverage"]), 50))
    r["reward_r"] = max(.5, min(float(r["reward_r"]), 10.0))
    r["stop_atr_mult"] = max(.5, min(float(r.get("stop_atr_mult", 1.5)), 6.0))
    r["target_r"] = max(.3, min(float(r.get("target_r", 1.5)), 8.0))
    n["news_minutes"] = max(0, min(int(n["news_minutes"]), 10080))
    n["rsi_minutes"] = max(0, min(int(n["rsi_minutes"]), 1440))
    n["analysis_hours"] = max(0, min(int(n["analysis_hours"]), 168))
    n["send_signals"], n["send_research"] = bool(n["send_signals"]), bool(n["send_research"])
    symbols = [x.strip().upper() for x in str(n["analysis_symbols"]).split(",") if x.strip()]
    n["analysis_symbols"] = ",".join(symbols[:8]) or "BTCUSDT"
    for key in d:
        d[key] = bool(d[key])
    return cfg


def save(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = normalize(raw)
    db.set_setting("runtime_settings", cfg)
    return cfg
