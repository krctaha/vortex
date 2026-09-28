/* VORTEX — ikincil terminal sayfalari */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // Sayfa adi ARTIK DEGISKEN: router <main>'i degistirdiginde
  // window.VORTEX_PAGE guncellenmez (o satiri iceren <script>
  // yeniden calismaz), ama <main data-page> her zaman dogrudur.
  let page = window.VORTEX_PAGE;
  const state = { news: [], calendar: [], chart: null, flowChart: null, series: {}, priceLines: [], liquidityLines: [], indicatorCharts: new Map(), lastSeries: null, lastSnap: null, lastCandles: [], rsiData: null, marketRows: [], marketLifecycle: null, marketView: "trend", marketFilter: "all", symbol: "BTCUSDT", interval: "15m", engine: null, syncingRange: false };
  const INDICATORS = [
    { id: "ema", name: "EMA 20/50/200", desc: "Trend yönü ve dinamik destek/direnç.", chart: ["ema20", "ema50", "ema200"], pane: false, on: true },
    { id: "vwap", name: "VWAP", desc: "Hacim ağırlıklı adil fiyat; gün içi yön filtresi.", chart: ["vwap"], pane: false, on: true },
    { id: "bollinger", name: "Bollinger", desc: "Volatilite bandı ve sıkışma/genişleme görünümü.", chart: ["bbUpper", "bbLower"], pane: false, on: false },
    { id: "supertrend", name: "Supertrend", desc: "ATR tabanlı takip eden trend çizgisi.", chart: ["supertrend"], pane: false, on: false },
    { id: "ichimoku", name: "Ichimoku", desc: "Trend, momentum ve denge bölgelerini birlikte gösterir.", chart: ["ichiTenkan", "ichiKijun", "ichiA", "ichiB"], pane: false, on: false },
    { id: "rsi", name: "RSI 14", desc: "Momentumun 0–100 aralığındaki gücü; tek başına giriş değildir.", chart: [], pane: true, on: true },
    { id: "macd", name: "MACD", desc: "12/26 EMA farkı, sinyal çizgisi ve histogram.", chart: [], pane: true, on: true },
    { id: "stochrsi", name: "Stoch RSI", desc: "RSI içindeki kısa vadeli momentum uçlarını ölçer.", chart: [], pane: true, on: false },
    { id: "adx", name: "ADX / DMI", desc: "Trend gücünü ve +DI/−DI dengesini ölçer.", chart: [], pane: true, on: false },
    { id: "atr", name: "ATR 14", desc: "Volatiliteyi ölçer; yön göstergesi değildir.", chart: [], pane: true, on: false },
    { id: "cvd", name: "CVD", desc: "Agresif alış ve satış hacminin kümülatif farkı.", chart: [], pane: true, on: false },
    { id: "liquidity", name: "Liquidity Sweep", desc: "14/42/120 bar likidite bantları ve çoklu süpürme dönüşleri.", chart: [], pane: false, on: true },
    { id: "orderflow", name: "Order Flow", desc: "Agresif alış/satış deltası ve canlı aggTrade dengesi.", chart: [], pane: false, on: true },
  ];

  function table(headers, rows) {
    if (!rows.length) return `<div class="empty">Kayıt yok</div>`;
    return `<div style="overflow:auto"><table class="workspace__table"><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  function kpi(label, value, extra = "") {
    return `<div class="workspace__kpi"><div class="workspace__kpi-label">${VX.esc(label)}</div><div class="workspace__kpi-value">${value}</div>${extra ? `<div class="workspace__muted" style="margin-top:5px">${extra}</div>` : ""}</div>`;
  }

  /* Sayfa basligi (<header>) <main> icinde oldugu icin her gezinmede
     yeniden kuruluyor ve tekrar doldurulmasi gerekiyor. Ama ARDINDAKI
     VERI degismiyor: kim giris yapmis, veri modu ne. Onlari her gezinmede
     yeniden sormak bosuna iki istek ediyordu.
     Saat de ayni sebeple: tek bir zamanlayici yeterli — her gezinmede bir
     tane daha kurmak on gezinme sonra saniyede on DOM yazimi demekti. */
  let _ortak = null;      // {auth, demo} — oturum boyunca degismez
  let _saatKuruldu = false;

  function ortakBoya() {
    if (!_ortak) return;
    const { auth, demo } = _ortak;
    if (auth && auth.user && $("avatar")) {
      $("avatar").textContent =
        (auth.user.display_name || auth.user.username || "?").charAt(0).toUpperCase();
    }
    VX.live.paintStatus();
    VX.railStatus({ data: demo ? "demo" : "live" });
  }

  async function bootCommon() {
    if (!_saatKuruldu) {
      _saatKuruldu = true;
      // Bilincli olarak VX.interval DEGIL: bu saat sayfaya degil OTURUMA
      // ait, gezinmede sokulmemeli.
      setInterval(() => {
        const c = $("clock");
        if (c) c.textContent = new Date().toLocaleTimeString("tr-TR");
      }, 1000);
    }
    if (_ortak) { ortakBoya(); return; }
    const auth = await VX.get("/api/auth/state");
    if (!auth || !auth.authenticated) return;
    const sys = await VX.get("/api/system/status");
    _ortak = { auth, demo: sys.data_mode === "demo" };
    ortakBoya();
  }

  /* ------------------------------------------------ VORTEX Mentor
     Bu ekran EMIR ACMAZ. Kullanicinin tezini mevcut analiz verisiyle
     carpistirir; amaci "haklisin" demek degil, zayif plani erkenden kesmek. */
  function mentorBullets(items, empty) {
    const rows = items || [];
    return rows.length ? `<ul>${rows.map(x => `<li>${VX.esc(x)}</li>`).join("")}</ul>`
      : `<p class="workspace__muted">${VX.esc(empty)}</p>`;
  }

  /* ---- BUGUN NE VAR: sistem plan kurar ----
     Eski akis kullanicidan giris+stop isteyip sonra puanliyordu. Ama
     kullanici zaten "ne yapayim" diye soruyor; stopu bilse sormazdi. */
  /* Plan LISTESI — kompakt satirlar, detay istege bagli acilir.
     Onceki hali her plani dev bir kart yapiyordu; 13 kurulum ekrana
     sigmiyordu ve taramanin butun anlami "hepsini bir bakista gor"
     olmasiydi. Satir = karar icin gereken minimum; detay katlanir. */
  // Miktar 8 ondalikla yaziliyordu ("4.67137103 adet"). Borsa o hassasiyeti
  // istiyor ama INSAN icin gurultu; satiri tasirip iki satira kiriyordu.
  function planMiktar(v) {
    const x = Number(v);
    if (!isFinite(x) || x === 0) return "—";
    const a = Math.abs(x);
    return x.toLocaleString("tr-TR", {
      maximumFractionDigits: a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6,
    });
  }

  /* Plan satiri ARTIK IZGARA, esnek satir degil.
     Onceki hali flex-wrap idi: genis ekranda tek satira siğiyor, dar
     sutunda ikinci satira kiriliyordu ve her satir farkli yerde kiriliyordu.
     Sonuc, hizasi olmayan bir liste. Izgara ile giris/stop/hedef sutunlari
     ustuste hizali: goz asagi kayarken ayni yere bakiyor, tablo gibi. */
  /* ---- MINI GRAFIK ----
     Ek maliyeti yok: bu mumlar plan kurulurken RSI baglami icin zaten
     cekilmisti, sunucu son 60 kapanisi plana ekliyor.

     Neden var: "hedef %8 uzakta" cumlesi tek basina hicbir sey ifade
     etmiyor. Fiyat iki gundur duz gidiyorsa %8 uzak bir hedeftir; iki
     gundur %20 oynuyorsa yakin. Cizgi o baglami tek bakista veriyor.
     Karar aracı degil, YERINE OTURTMA araci — o yuzden eksen, etiket,
     izgara yok; yalnizca sekil. */
  function miniGrafik(seri, yon, giris) {
    if (!seri || seri.length < 4) return "";
    const n = seri.length;
    let lo = Math.min(...seri), hi = Math.max(...seri);
    if (!(hi > lo)) return "";
    const pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
    const y = v => (1 - (v - lo) / (hi - lo)) * 24;
    const x = idx => (idx / (n - 1)) * 100;
    const nokta = seri.map((v, idx) => `${x(idx).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
    const renk = yon === "LONG" ? "var(--up)" : "var(--down)";
    // Giris cizgisi: seri iyi ama fiyatin SIMDI nerede oldugu daha iyi.
    const gy = (giris >= lo && giris <= hi) ? y(giris) : null;
    const id = "g" + Math.random().toString(36).slice(2, 8);
    return `<svg class="mini" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${renk}" stop-opacity=".26"/>
        <stop offset="100%" stop-color="${renk}" stop-opacity="0"/>
      </linearGradient></defs>
      <polygon fill="url(#${id})" points="0,24 ${nokta} 100,24"/>
      <polyline fill="none" stroke="${renk}" stroke-width="1.1"
        vector-effect="non-scaling-stroke" stroke-linejoin="round" points="${nokta}"/>
      ${gy === null ? "" : `<line x1="0" x2="100" y1="${gy.toFixed(2)}" y2="${gy.toFixed(2)}"
        stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="3 3"
        vector-effect="non-scaling-stroke" opacity=".55"/>`}
    </svg>`;
  }

  /* Hedefleri R cinsinden yazmak fiyat rakamindan cok daha okunakli:
     "TP2 = 2.4R" demek "stopa carparsan kaybettiginin 2.4 katini
     kazanirsin" demek. R'yi her satirda gostermek, kullanicinin
     "bu R ne demek" sorusuna arayuzun kendisinin cevap vermesini
     sagliyor — aciklamayi baska yerde aramak zorunda kalmiyor. */
  /* BTC rozeti — akintiyla catisan planlar GORUNUR olsun.
     Gizlemek ya da elemek degil: bazen dogru islem tam da akintiya karsi
     olandir. Ama bunu bilmeden acmak pahali. */
  function btcRozet(b) {
    if (!b || b.durum === "bilinmiyor" || b.durum === "notr") return "";
    if (b.durum === "carpisiyor") {
      return `<span class="btc-roz btc-roz--ters" title="${VX.esc(b.mesaj)}">
        BTC'ye karşı · r ${b.r}</span>`;
    }
    if (b.durum === "destekliyor") {
      return `<span class="btc-roz btc-roz--yan" title="${VX.esc(b.mesaj)}">
        BTC ile aynı yön · r ${b.r}</span>`;
    }
    return `<span class="btc-roz" title="${VX.esc(b.mesaj)}">BTC r ${b.r}</span>`;
  }

  /* ---- ZAMAN DILIMI SERIDI ----
     Yonu buyuk resim verir: 4h en agir (en yavas, en kararli), 15m en
     hafif (en gurultulu). Uc ok tek bakista "kurulum tek zaman diliminde
     mi yasiyor" sorusunu cevapliyor. */
  function zdSerit(p) {
    const zd = p.zaman_dilimleri;
    if (!zd || !zd.dilimler) return "";
    const ok = k => k === "bull" ? "▲" : k === "bear" ? "▼" : "•";
    const sinif = k => k === "bull" ? "up" : k === "bear" ? "down" : "";
    const hucre = iv => {
      const k = zd.dilimler[iv];
      return `<i class="${sinif(k)}"><em>${iv}</em>${ok(k)}</i>`;
    };
    const durum = zd.durum === "hizali" ? "zd--tam"
      : zd.durum === "catisiyor" ? "zd--ters" : "zd--zayif";
    return `<span class="zd ${durum}" title="${VX.esc(zd.mesaj || "")}"
      >${["4h", "1h", "15m"].map(hucre).join("")}</span>`;
  }

  /* ---- GIRIS ZAMANLAMASI ----
     Zamanlamayi kucuk resim verir: 4 saatlik mum "su anda mi girmeliyim"
     sorusunu cevaplayamaz, o mum kapandiginda hareket biter.
     Bu bir GIRIS EMRI degil; kovalamayi engellemek icin olculmus baglam. */
  /* Iki etiket birden yaziliyor: genis sutunda uzunu, dar sutunda kisasi
     gorunuyor (CSS seciyor). Onceden dar sutunda bu rozetin TAMAMI
     dusuyordu ve "simdi mi girsem" sorusunun cevabi ekrandan kayboluyordu
     — satirdaki en eylem odakli bilgiyi dusurmek yanlis karardi. */
  const GZ_SINIF = {
    simdi: ["gz--simdi", "ŞİMDİ", "ŞİMDİ"],
    bekle: ["gz--bekle", "BEKLE", "BEKLE"],
    gec: ["gz--gec", "GEÇ KALINDI", "GEÇ"],
  };

  function gzRozet(p) {
    const g = p.giris_zamani;
    if (!g || !GZ_SINIF[g.durum]) return "";
    const [sinif, uzun, kisa] = GZ_SINIF[g.durum];
    const ek = (g.durum === "gec" && g.uzama_kat) ? ` · ${g.uzama_kat}×` : "";
    return `<span class="gz ${sinif}" title="${VX.esc(g.mesaj || "")}"
      ><b class="gz__uzun">${uzun}${ek}</b><i class="gz__kisa">${kisa}</i></span>`;
  }

  /* HEDEFIN KAYNAGI GORUNUR OLMALI.
     Bes kademenin hepsi gercek bir destek/direnc degil: fiyatin ustunde
     her zaman bes anlamli seviye bulunmuyor. Olmayani uydurmak yerine
     kalanini R katiyla dolduruyoruz ve HANGISININ NE oldugunu soyluyoruz.
     "yapi" isaretli bir hedefe guvenmekle "R" isaretliye guvenmek ayni
     sey degil; bunu gizlemek olcum gibi gorunen bir tahmin uretirdi. */
  function kaynakRoz(k) {
    if (k === "yapi") return ` <i class="tp-kay tp-kay--y" title="Gerçek destek/direnç seviyesi">yapı</i>`;
    if (k === "R") return ` <i class="tp-kay tp-kay--r" title="Saf risk katı — yapısal dayanağı yok">R</i>`;
    return "";
  }

  function tpSatiri(p) {
    const hd = p.hedef_detay || [];
    if (!hd.length) return "";
    return hd.map(h =>
      `<span class="tp" title="Bu hedefe ulaşırsa riskinin ${h.r} katını kazanırsın">
         <i>TP${h.no}</i><b>${VX.fmtPrice(h.fiyat)}</b><em>${h.r}R</em></span>`).join("");
  }

  /* PLAN SATIRI — RADAR TABLOSUYLA AYNI GORSEL DILDE.
     Kullanici radar tablosunu begendi ve haklıydı: basliklı, hizalı,
     yogun bir tablo bir kart listesinden cok daha hizli okunuyor. Goz
     asagi kayarken her satirda ayni yere bakiyor.
     On iki sutun dar bir kolona sigmazdi; bu yuzden panel tam genislige
     tasindi. Ayrinti (gerekce, hedef tablosu, zaman dilimi aciklamalari)
     satiri acinca geliyor — ozet satirda YALNIZCA karsilastirilabilir
     sayilar var. */
  function planSatiri(p, i) {
    if (!p.var_mi) return "";
    const b = p.boyut || {};
    const hd = (p.hedef_detay || [])[0];
    const liste = (baslik, dizi) => (dizi && dizi.length)
      ? `<div class="plan-det__blok"><span>${baslik}</span><ul>
         ${dizi.map(x => `<li>${VX.esc(x)}</li>`).join("")}</ul></div>` : "";
    return `<details class="ptr">
      <summary>
        <span class="ptr__sym">${VX.coinIcon(p.symbol, 17)}<b>${VX.esc(p.symbol.replace(/USDT$/, ""))}</b></span>
        <span class="ptr__mini">${miniGrafik(p.seri, p.yon, p.giris)}</span>
        <span class="ptr__yon ${p.yon === "LONG" ? "up" : "down"}">${p.yon}</span>
        <span class="ptr__rr">${p.rr}R</span>
        <span class="ptr__n">${VX.fmtPrice(p.giris)}</span>
        <span class="ptr__n down">${VX.fmtPrice(p.stop)}<em>%${p.stop_r_yuzde}</em></span>
        <span class="ptr__n up">${hd ? VX.fmtPrice(hd.fiyat) : "—"}${hd ? `<em>${hd.r}R</em>` : ""}</span>
        <span class="ptr__c">${zdSerit(p)}</span>
        <span class="ptr__c">${gzRozet(p)}</span>
        <span class="ptr__c">${btcRozet(p.btc)}</span>
        <span class="ptr__n">${b.riske_edilen}$<em>${b.kaldirac}x</em></span>
        <span class="ptr__u">${(p.uyarilar || []).length
          ? `<em title="${VX.esc((p.uyarilar || []).join(" · "))}">⚠${p.uyarilar.length}</em>` : ""}</span>
      </summary>
      <div class="plan-det">
        ${p.ozet ? `<p class="plan-det__ozet">${VX.esc(p.ozet)}</p>` : ""}
        <div class="plan-det__grid">
          ${liste("Neden bu yön", p.gerekce)}
          ${liste("Karşı görüş", p.karsi_gorus)}
        </div>
        ${(p.hedef_detay || []).length ? `<div class="plan-det__tp">
          <span>Hedefler — <b>R</b> = stopa kadar olan mesafe, yani bu işlemde riske ettiğin miktar</span>
          <table>
            <tr><td>Stop</td><td>${VX.fmtPrice(p.stop)}</td><td>%${p.stop_r_yuzde}</td>
                <td class="r-kayip">−1R</td><td>riskin tamamı</td></tr>
            ${p.maliyet_r ? `<tr><td>Maliyet</td><td>komisyon+slipaj</td><td>%0,14</td>
                <td class="r-kayip">−${String(p.maliyet_r).replace(".", ",")}R</td>
                <td>her işlemde, kazansan da</td></tr>` : ""}
            ${p.hedef_detay.map(h => `<tr><td>TP${h.no}${kaynakRoz(h.kaynak)}</td>
              <td>${VX.fmtPrice(h.fiyat)}</td>
              <td>%${h.yuzde}</td><td class="r-kar">+${h.r}R</td>
              <td>pozisyonun %${h.pay}'i</td></tr>`).join("")}
          </table>
          <small>Örnek: stop yersen ${b.riske_edilen}$ kaybedersin — bu 1R.
            TP${p.hedef_detay[p.hedef_detay.length - 1].no} tutarsa
            ${(b.riske_edilen * p.hedef_detay[p.hedef_detay.length - 1].r).toFixed(2)}$ kazanırsın.
            ${p.maliyet_r ? `<br><b>Maliyet ${String(p.maliyet_r).replace(".", ",")}R</b> —
              ölçülen brüt beklenti +0,058R olduğu için bu kalem küçük değil;
              stop ne kadar darsa R cinsinden o kadar büyür.` : ""}
            ${p.hedef_detay.some(h => h.kaynak === "R")
              ? `<b>yapı</b> işaretli kademeler gerçek destek/direnç seviyeleri;
                 <b>R</b> işaretliler saf risk katı — yapısal dayanağı yok,
                 pozisyonu kademeli kapatmak için var.` : ""}</small>
        </div>` : ""}
        ${(p.zaman_dilimleri || p.giris_zamani) ? `<div class="plan-det__mtf">
          ${p.zaman_dilimleri ? `<div><span>Yön — 4h / 1h / 15m</span>${VX.esc(p.zaman_dilimleri.mesaj)}</div>` : ""}
          ${p.giris_zamani ? `<div><span>Zamanlama — 5m / 1m</span>${VX.esc(p.giris_zamani.mesaj)}
            ${p.giris_zamani.tetik ? `<br><b>Geri çekilme tetiği: ${VX.fmtPrice(p.giris_zamani.tetik)}</b>` : ""}</div>` : ""}
          ${p.btc && p.btc.mesaj ? `<div><span>BTC akıntısı</span>${VX.esc(p.btc.mesaj)}</div>` : ""}
        </div>` : ""}
        ${p.gecersizlik ? `<div class="plan-det__satir"><span>Fikrin şurada çürür</span> ${VX.esc(p.gecersizlik)}</div>` : ""}
        ${p.teyit ? `<div class="plan-det__satir"><span>Teyit</span> ${VX.esc(p.teyit)}</div>` : ""}
        ${(p.uyarilar || []).length ? `<div class="plan-det__uyari">${p.uyarilar.map(u => `<div>⚠ ${VX.esc(u)}</div>`).join("")}</div>` : ""}
        <div class="plan-det__alt">
          <span class="workspace__muted">${planMiktar(b.qty)} adet · ${b.kaldirac}x · marj ${b.marj}$ · notional ${b.notional}$${b.kaldirac_dusuruldu ? ` · kaldıraç ${b.istenen_kaldirac}x→${b.kaldirac}x` : ""}</span>
          <a class="btn btn--sm" href="/analiz?symbol=${encodeURIComponent(p.symbol)}&interval=${encodeURIComponent(p.interval)}">Grafik ↗</a>
          <button class="btn btn--sm btn--primary" data-plan-uygula='${VX.esc(JSON.stringify({symbol:p.symbol,side:p.yon,entry:p.giris,initial_stop:p.stop,initial_target:(p.hedefler||[])[0],qty:b.qty,leverage:b.kaldirac}))}'>Günlüğe geçir</button>
        </div>
      </div>
    </details>`;
  }

  const PLAN_BASLIK = `<div class="ptr-head">
    <span>coin</span><span></span><span>yön</span><span>r/r</span><span>giriş</span>
    <span>stop</span><span>tp1</span><span>4h·1h·15m</span>
    <span><b class="th-uzun">zamanlama</b><i class="th-kisa">zaman</i></span>
    <span>btc</span><span>risk</span><span></span>
  </div>`;


  /* PLAN LISTESI — sunucu onbelleginden, tarayici hafizasindan degil.
     Sol menuden gezinmek TAM SAYFA yenilemesi yapiyor; JavaScript sifirdan
     basliyor ve eskiden bu her seferinde yeni bir 150 sembollu tarama
     tetikliyordu (~1300 agirlik, 60-90 saniye). Kullanicinin "sistem
     yoruluyor" ve "her donusumde veriler kayboluyor" gozlemlerinin ikisi
     de bu tek koke bagliydi.
     Iki katman:
       1) sessionStorage — sayfa acilir acilmaz ONCEKI listeyi boyar, ag
          beklenmez. Bos ekran gormuyorsun.
       2) sunucu onbellegi — arka planda tazelenen tek kopya. Gezinme
          artik hicbir tarama tetiklemiyor. */
  const PLAN_ANAHTAR = "vortex.plan";
  let PLAN_BEKLE = null;
  // Aktif yon suzgeci: null | "LONG" | "SHORT"
  let PLAN_SUZGEC = null;

  function planYaz(d) {
    try { sessionStorage.setItem(PLAN_ANAHTAR, JSON.stringify({ts: Date.now(), d})); }
    catch (_) { /* ozel pencere / kota — onemsiz */ }
  }
  function planOku() {
    try {
      const x = JSON.parse(sessionStorage.getItem(PLAN_ANAHTAR) || "null");
      // 15 dakikadan eski bir kopyayi boyamak yaniltici olur.
      return (x && Date.now() - x.ts < 900000) ? x.d : null;
    } catch (_) { return null; }
  }

  function planCiz(d) {
    const el = $("planListe");
    if (!el || !d) return;
    // ILK TARAMA SURUYOR.
    // Soguk onbellekte tarama 60-90 saniye suruyor; istegi o kadar
    // bekletmek tarayicida zaman asimina ve "calismiyor" izlenimine yol
    // aciyordu. Sunucu artik hemen "hazir degil" donuyor, biz de
    // durumu yazip birkac saniyede bir tekrar bakiyoruz.
    if (d.hazir === false) {
      el.innerHTML = `<div class="empty plan-bekle">
        <span class="plan-bekle__nokta"></span>
        ${VX.esc(d.mesaj || "İlk tarama sürüyor…")}</div>`;
      clearTimeout(PLAN_BEKLE);
      PLAN_BEKLE = setTimeout(() => planAra(true), 8000);
      VX.onTeardown(() => clearTimeout(PLAN_BEKLE));
      return;
    }
    // TABLO GENISLIGI KONTEYNERE GORE, EKRANA GORE DEGIL.
    // Terminal duzeninde ayni tablo 500 piksellik orta sutunda da,
    // 1150 piksellik tam genislikte de duruyor. Ekran genisligine bakan
    // medya sorgulari burada YANLIS olcu: ekran 1440 ama sutun 500.
    const gen = el.clientWidth || 900;
    // Esikler her duzenin GERCEK asgari genisligine gore secildi:
    // xs ~300px, s ~430px, m ~620px, tam duzen ~900px.
    el.classList.toggle("pt--xs", gen < 430);
    el.classList.toggle("pt--s", gen >= 430 && gen < 620);
    el.classList.toggle("pt--m", gen >= 620 && gen < 920);
    const hepsi = d.planlar || [];
    // YON SUZGECI. Rozetler artik sadece SAYI degil, DUGME.
    //
    // Sorun neydi: baslikta "4 long · 9 short" yaziyordu ama listede
    // yalnizca sekiz satir vardi. Kullanici hakli olarak "9 short diyor,
    // nerede geri kalanlar" diye sordu. Sayi ile liste birbirini tutmuyorsa
    // sayi guven vermez, supheye yol acar.
    //
    // Iki duzeltme: (1) liste artik BULUNANIN TAMAMINI gosteriyor —
    // orta sutun zaten kendi icinde kayiyor, kesmenin bir sebebi yoktu;
    // (2) rozetlere basinca yalnizca o yon suzuluyor.
    const p = PLAN_SUZGEC ? hepsi.filter(x => x.yon === PLAN_SUZGEC) : hepsi;
    if (!hepsi.length) {
      el.innerHTML = `<div class="empty">${d.bakilan} sembol bakıldı, <b>uygulanabilir kurulum yok</b>.
        Bu da bir cevap — beklemek doğru olabilir.</div>`;
    } else {
      const L = d.long_sayisi || 0, S = d.short_sayisi || 0;
      const rozet = (yon, sayi, sinif) => `<button type="button" class="plan-dag__b ${sinif}
        ${PLAN_SUZGEC === yon ? "is-acik" : ""}" data-yon="${yon}"
        title="${PLAN_SUZGEC === yon ? "Süzgeci kaldır" : "Yalnızca " + yon.toLowerCase() + " göster"}">
        ${sayi} ${yon.toLowerCase()}</button>`;
      const dagilim = (L || S)
        ? `<span class="plan-dag">${rozet("LONG", L, "up")}${rozet("SHORT", S, "down")}</span>`
        : "";
      const yas = (d.yas_sn === null || d.yas_sn === undefined) ? null : d.yas_sn;
      const yasMetin = yas === null ? ""
        : yas < 60 ? " Az önce tarandı." : ` ${Math.round(yas / 60)} dakika önce tarandı.`;
      // Liste kesildiyse bunu SOYLE. Sessizce kesmek, sayilarla listenin
      // celismesi demekti.
      const kesik = d.bulunan > hepsi.length
        ? ` En iyi ${hepsi.length} tanesi listede.` : "";
      const suzgecNot = PLAN_SUZGEC
        ? ` <b>${PLAN_SUZGEC.toLowerCase()} süzgeci açık</b> — ${p.length} satır.` : "";
      // SISTEM YON SUZGECI GORUNUR OLMALI.
      // Ayarlardaki yon suzgeci LONG iken bulunan shortlar listeye hic
      // girmiyor. Bunu sessizce yapmak kullaniciyi PIYASA hakkinda
      // yanıltır: "hic short kurulum yok" ile "shortlar gizlendi" ayni
      // sey degil. Kac tanesinin elendigini soyluyoruz.
      const sisNot = (d.yon_suzgec && d.yon_suzgec !== "BOTH")
        ? ` Sistem yön süzgeci <b>${VX.esc(d.yon_suzgec)}</b>${d.yon_elenen
            ? ` — ${d.yon_elenen} ${d.yon_suzgec === "LONG" ? "short" : "long"} kurulum gizlendi.` : "."}`
        : "";
      el.innerHTML = `<div class="plan-ust">
          <span class="workspace__muted">${d.bakilan} sembol tarandı, ${d.bulunan} kurulum bulundu.${kesik}
            ${d.zd_elenen ? ` ${d.zd_elenen} kurulum üst zaman dilimiyle çeliştiği için elendi.` : ""}${yasMetin}${sisNot}${suzgecNot}${d.bayat ? " <b>Son tarama başarısız — bu liste eski.</b>" : ""}</span>${dagilim}</div>`
        + `<div class="plan-tablo">` + PLAN_BASLIK + p.map(planSatiri).join("") + `</div>`;
      el.querySelectorAll("[data-yon]").forEach(btn => {
        btn.addEventListener("click", () => {
          const y = btn.getAttribute("data-yon");
          PLAN_SUZGEC = (PLAN_SUZGEC === y) ? null : y;
          planCiz(d);
        });
      });
      el.querySelectorAll("[data-plan-uygula]").forEach(x => {
        x.addEventListener("click", () => {
          const v = JSON.parse(x.getAttribute("data-plan-uygula"));
          const f = $("gunlukForm");
          if (!f) { VX.toast("Günlük formu bulunamadı", "err"); return; }
          Object.entries(v).forEach(([k, val]) => {
            const g = f.querySelector(`[name="${k}"]`);
            if (g && val !== null && val !== undefined) g.value = val;
          });
          // Form artik "Elle kayit" arac kartinin govdesinde. Kart
          // acilmadan kaydirmak kullaniciyi gizli bir yere goturur.
          aracAc("elle");
          f.scrollIntoView({ behavior: "smooth", block: "center" });
          const ger = f.querySelector('[name="gerekce"]');
          if (ger) ger.focus();
          VX.toast("Forma dolduruldu — gerekçeni yaz ve kaydet", "ok", 6000);
        });
      });
    }
    const zaman = $("planZaman");
    if (zaman) {
      const t = new Date(Date.now() - (d.yas_sn || 0) * 1000);
      zaman.textContent = t.toLocaleTimeString("tr-TR", {hour: "2-digit", minute: "2-digit"});
    }
  }

  // Pencere boyu degisince tablo sutunlari yeniden hesaplanmali.
  let PLAN_SON = null;
  function planTazeCiz() { if (PLAN_SON) planCiz(PLAN_SON); }

  async function planAra(sessiz, taze) {
    const btn = $("planBtn"), el = $("planListe");
    if (btn) { btn.disabled = true; btn.textContent = taze ? "taranıyor…" : "…"; }
    const ilk = !el.querySelector(".ptr");
    if (ilk) el.innerHTML = `<div class="empty">Son tarama getiriliyor…</div>`;
    try {
      // Zaman dilimi secicisi kaldirildi: yon 4h/1h/15m komitesinden,
      // zamanlama 5m/1m'den geliyor. 1h yalnizca kurulumun KURULDUGU
      // dilim; komite onu zaten kapsiyor.
      const ivEl = $("planInterval");
      const iv = ivEl ? ivEl.value : "1h";
      const d = await VX.get(
        // limit 20: onbellek zaten 20 plan tutuyor ve orta sutun kendi
      // icinde kayiyor. Sekizde kesmek, baslikta yazan sayiyla
      // listenin celismesine yol aciyordu.
      `/api/mentor/planlar?limit=20&interval=${iv}${taze ? "&taze=1" : ""}`);
      if (d.hazir !== false) { planYaz(d); PLAN_SON = d; }  // yarim veriyi hafizaya yazma
      planCiz(d);
      // R'nin dolar karsiligi planin kendi boyut hesabindan geliyor;
      // ayri bir ayar cagrisi yapmaya gerek yok.
      const ilkPlan = (d.planlar || [])[0];
      const rd = $("rDolar");
      if (rd && ilkPlan && ilkPlan.boyut) rd.textContent = ilkPlan.boyut.riske_edilen + "$";
    } catch (err) {
      if (ilk) el.innerHTML = `<div class="empty">Plan aranamadı: ${VX.esc(err.message)}</div>`;
    } finally { if (btn) { btn.disabled = false; btn.textContent = "Şimdi tara"; } }
  }


  function renderMentorReview(d) {
    const tone = d.verdict_code === "blocked" ? "blocked" : d.verdict_code === "wait" ? "wait" : d.verdict_code === "reviewable" ? "reviewable" : "observe";
    const rr = d.reward_risk === null || d.reward_risk === undefined ? "—" : `${Number(d.reward_risk).toFixed(2)}R`;
    $("mentorResult").innerHTML = `
      <section class="mentor-verdict mentor-verdict--${tone}">
        <div class="mentor-verdict__main">
          <span class="mentor-verdict__label">VORTEX KARARI</span>
          <h2>${VX.esc(d.verdict)}</h2>
          <p>${VX.esc(d.summary || "Yeterli bağlam üretilemedi.")}</p>
        </div>
        <div class="mentor-verdict__market">
          <span>${VX.coinIcon(d.symbol, 34)}<b>${VX.esc(d.symbol)}</b><small>${VX.esc(d.interval)}</small></span>
          <strong>${VX.fmtPrice(d.price)}</strong>
          <a class="btn btn--sm" href="/analiz?symbol=${encodeURIComponent(d.symbol)}&interval=${encodeURIComponent(d.interval)}">Grafikte aç ↗</a>
        </div>
      </section>
      <div class="mentor-metrics">
        ${kpi("Yakın destek", VX.fmtPrice(d.support))}
        ${kpi("Yakın direnç", VX.fmtPrice(d.resistance))}
        ${kpi("Önerilen geçersizlik", VX.fmtPrice(d.proposed_stop), "Kendi stopunun yerine geçmez")}
        ${kpi("Yapısal risk/getiri", rr, `Kabul edilen risk ${VX.fmtUsd(d.risk_usdt)}`)}
      </div>
      <!-- NE YAPMALI en uste: kullanici once "peki simdi ne yapacagim"
           sorusunun cevabini gormeli. Sadece hayir diyen bir mentor
           ise yaramaz. -->
      <section class="panel mentor-card mentor-card--wide mentor-card--action">
        <h3>Şimdi ne yapmalı</h3>${mentorBullets(d.ne_yapmali, "—")}</section>
      <div class="mentor-grid">
        <section class="panel mentor-card mentor-card--danger"><h3>Planı bozanlar</h3>${mentorBullets(d.blockers, "Kesin engel bulunmadı.")}</section>
        <section class="panel mentor-card mentor-card--warn"><h3>Beklemeyi gerektirenler</h3>${mentorBullets(d.cautions, "Ek bekleme gerekçesi bulunmadı.")}</section>
        <section class="panel mentor-card mentor-card--good"><h3>Lehte kanıtlar</h3>${mentorBullets([...(d.positives || []), ...(d.evidence_for || [])], "Lehte bağımsız kanıt yok.")}</section>
        <section class="panel mentor-card"><h3>Karşı tez</h3>${mentorBullets(d.evidence_against, "Belirgin karşı tez yok; bu kesinlik değildir.")}</section>
        <section class="panel mentor-card"><h3>Teyit gelmeden ne eksik?</h3><p>${VX.esc(d.confirmation || "—")}</p><h4>Tez nerede bozulur?</h4><p>${VX.esc(d.invalidation || "—")}</p></section>
        <section class="panel mentor-card mentor-card--wide"><h3>Kör noktalar</h3>${mentorBullets(d.blind_spots, "Teknik analiz dışında haber ve likidite riski devam eder.")}</section>
        <section class="panel mentor-card mentor-card--questions mentor-card--wide"><h3>Kendine cevap vermeden işlem düşünme</h3>${mentorBullets(d.mentor_questions, "—")}</section>
      </div>`;
  }

  async function loadMentorRecent() {
    try {
      const d = await VX.get("/api/mentor/recent?limit=8");
      const rows = (d.items || []).map(x => `<tr><td><a class="sym" href="/?symbol=${encodeURIComponent(x.symbol)}">${VX.coinIcon(x.symbol, 20)}<span>${VX.esc(x.symbol)}</span></a></td><td>${VX.esc(x.interval)}</td><td>${VX.esc(x.direction)}</td><td><span class="tag">${VX.esc(x.verdict)}</span></td><td class="right">${x.reward_risk === null || x.reward_risk === undefined ? "—" : Number(x.reward_risk).toFixed(2) + "R"}</td><td class="right workspace__muted">${VX.fmtAgo(x.created_at)}</td></tr>`);
      $("mentorRecent").innerHTML = rows.length ? table(["Coin", "Zaman", "Fikir", "Karar", "R/R", ""], rows) : `<div class="empty">Henüz değerlendirme yok</div>`;
      aracRozet("aracRozetDeger", rows.length, "kayıt");
    } catch (e) { $("mentorRecent").innerHTML = `<div class="empty">Geçmiş yüklenemedi: ${VX.esc(e.message)}</div>`; }
  }

  async function reviewMentor(e) {
    e.preventDefault();
    const btn = $("mentorReviewBtn");
    const numberOrNull = id => { const v = $(id).value.trim(); return v ? Number(v) : null; };
    const body = {
      symbol: $("mentorSymbol").value.trim().toUpperCase().replace("/", ""),
      interval: $("mentorInterval").value,
      direction: $("mentorDirection").value,
      entry: numberOrNull("mentorEntry"), stop: numberOrNull("mentorStop"),
      // risk_usdt artik sorulmuyor: Ayarlar > Risk icindeki
      // max_risk_per_trade sunucu tarafinda okunuyor.
    };
    if (!body.symbol) return VX.toast("Coin seç", "warn");
    btn.disabled = true; btn.textContent = "Tez sorgulanıyor…";
    $("mentorResult").innerHTML = `<div class="mentor-empty"><span>V</span><h2>Veriler karşılaştırılıyor</h2><p>Yapı, momentum, akış, türev bağlamı ve seviyeler okunuyor…</p></div>`;
    try {
      const d = await VX.post("/api/mentor/review", body);
      renderMentorReview(d);
      await loadMentorRecent();
      VX.live.watch([body.symbol]);
    } catch (err) {
      $("mentorResult").innerHTML = `<div class="mentor-empty mentor-empty--error"><span>!</span><h2>Analiz tamamlanamadı</h2><p>${VX.esc(err.message)}</p></div>`;
    } finally { btn.disabled = false; btn.textContent = "Planımı sorgula"; }
  }

  /* ================================================================
     GUNLUK — kullanicinin KENDI sicili.
     Motor karnesi stratejiyi olcer; burasi Taha'yi olcer.
     ================================================================ */
  let GUNLUK_KONTROL = [];

  function gNum(x, ek = "") {
    return (x === null || x === undefined) ? "—" : (x + ek);
  }

  function gunlukKapiCiz(k) {
    const el = $("gunlukKapi");
    if (!el) return;
    const form = $("gunlukForm");
    el.hidden = false;
    if (k.acik) {
      // Kapi ACIKKEN paneli hic gosterme: ayni sayilar zaten ust seritte.
      // Ayni bilgiyi iki yerde tekrar etmek ekrani sisiriyordu.
      el.hidden = true;
      el.innerHTML = "";
      if (form) form.querySelectorAll("input,select,button").forEach(x => { x.disabled = false; });
    } else {
      /* KAPI KAPALI. Formu gercekten kilitliyoruz, sadece uyari yazmiyoruz —
         "bu sefer farkli" diyecek kisi kullanicinin kendisi. Sunucu tarafinda
         da ayrica 423 donuyor, bu yalnizca gorunur katman. */
      el.className = "panel workspace__section gunluk-gate";
      el.innerHTML = `<div style="display:flex;gap:11px;align-items:flex-start">
        <span style="color:var(--down);font-size:17px;line-height:1">⛔</span>
        <div style="font-size:13px;line-height:1.6"><b>İşlem kaydı kilitli.</b><br>${VX.esc(k.mesaj)}</div></div>`;
      if (form) form.querySelectorAll("input,select,button").forEach(x => { x.disabled = true; });
    }
  }

  function gunlukKontrolCiz() {
    const el = $("gunlukKontrol");
    if (!el) return;
    el.innerHTML = GUNLUK_KONTROL.map(m =>
      `<label class="workspace__switch"><input type="checkbox" data-kontrol="${VX.esc(m.anahtar)}">
       <span>${VX.esc(m.metin)}</span></label>`).join("");
  }

  function gunlukBaglamRozet(b) {
    if (!b) return "";
    const p = [];
    if (b.rsi !== undefined && b.rsi !== null) p.push(`RSI ${b.rsi}`);
    if (b.verdict_label) p.push(VX.esc(b.verdict_label));
    else if (b.trend_label) p.push(VX.esc(b.trend_label));
    if (b.funding_bp !== undefined && b.funding_bp !== null) p.push(`funding ${b.funding_bp}bp`);
    if (b.t_stat !== undefined && b.t_stat !== null) p.push(`t=${Number(b.t_stat).toFixed(2)}`);
    if (b.divergence) p.push("uyumsuzluk");
    if (b.rejim) p.push(VX.esc(b.rejim));
    let h = p.length ? `<div class="gunluk-trade__ctx">${p.map(x => `<span>${x}</span>`).join("")}</div>` : "";

    /* AYRINTILI GEREKCE — katlanabilir.
       Rozetler "ne vardi" der; burasi "neye dayaniyordu" der. Aylar sonra
       karneye bakip "bu kurulumda ne goruyordum" sorusunu ancak bu
       cevaplayabilir. Varsayilan kapali, ozet satiri kalabalik yapmasin. */
    const a = b.ayrinti;
    if (a && (a.ozet || (a.lehte || []).length || (a.aleyhte || []).length)) {
      const liste = (baslik, dizi) => (dizi && dizi.length)
        ? `<div style="margin-top:5px"><span class="workspace__muted" style="font-size:11px">${baslik}</span>
           <ul style="margin:2px 0 0;padding-left:15px;font-size:11.5px;line-height:1.5">
           ${dizi.map(y => `<li>${VX.esc(y)}</li>`).join("")}</ul></div>` : "";
      h += `<details style="margin-top:6px">
        <summary style="cursor:pointer;font-size:11.5px;color:var(--ink-3)">Girişteki tam gerekçe</summary>
        <div style="margin-top:6px;padding-left:4px;border-left:2px solid var(--border)">
          ${a.ozet ? `<div style="font-size:12px;line-height:1.6">${VX.esc(a.ozet)}</div>` : ""}
          ${liste("Lehte", a.lehte)}
          ${liste("Aleyhte", a.aleyhte)}
          ${liste("Kör nokta", a.kor_nokta)}
          ${a.gecersizlik ? `<div style="margin-top:5px;font-size:11.5px"><span class="workspace__muted">Geçersizlik:</span> ${VX.esc(a.gecersizlik)}</div>` : ""}
          ${a.teyit ? `<div style="margin-top:3px;font-size:11.5px"><span class="workspace__muted">Teyit:</span> ${VX.esc(a.teyit)}</div>` : ""}
        </div></details>`;
    }
    return h;
  }

  /* ---- ACIK ISLEMLER ----
     Kaynak ayrimi GORUNUR: "borsadan" etiketi tasiyan satirlar senkron
     servisinin otomatik aldigi kayitlar. Stop borsada yoksa bunu SAKLAMIYORUZ
     — R hesaplanamayacagi acikca yaziliyor, cunku o satir SQN, bootstrap ve
     R ortalamasi gibi hicbir R istatistigine girmeyecek. */
  /* ---- ACIK POZISYON DENETIMI ----
     Plan kurmak ile acik pozisyonu denetlemek farkli iki istir. Girerken
     soru "girmeli miyim", acikken soru "TEZIM HALA AYAKTA MI". Ikincisi
     cok daha sik ihmal edilir ve daha pahaliya mal olur: insan girerken
     dikkatli, tutarken umutludur.
     Kutu bir KAPATMA EMRI degil — tezin hangi ayaginin kirildigini
     yaziyor, karari birakiyor. */
  function denetimKutu(r) {
    const d = r.denetim;
    if (!d) {
      // Denetim yoksa en azindan BTC catismasi gosterilsin.
      return (r.btc && r.btc.durum === "carpisiyor")
        ? `<div class="dnt dnt--zayifladi">⚠ ${VX.esc(r.btc.mesaj)}</div>` : "";
    }
    const acikR = (d.ayrinti || {}).acik_r;
    const rMetin = (acikR === null || acikR === undefined) ? ""
      : `<span class="dnt__r">${acikR >= 0 ? "+" : ""}${acikR}R</span>`;
    const madde = (d.kirik || []).length
      ? `<ul>${d.kirik.map(x => `<li>${VX.esc(x)}</li>`).join("")}</ul>` : "";
    return `<div class="dnt dnt--${d.durum}">${rMetin}<b>${VX.esc(d.baslik)}</b>${madde}</div>`;
  }

  function gunlukAcikCiz(rows) {
    const el = $("gunlukOpen");
    if ($("gunlukOpenCount")) $("gunlukOpenCount").textContent = rows.length;
    if (!rows.length) {
      el.innerHTML = `<div class="empty">Açık işlemin yok. Binance'te bir pozisyon açtığında 90 saniye içinde kendiliğinden buraya düşer.</div>`;
      return;
    }
    el.innerHTML = rows.map(r => {
      const oto = r.etiket === "binance-oto";
      const bayrak = (r.bayraklar || []).map(b => `<span class="gunluk-flag">${VX.esc(b)}</span>`).join(" ");
      return `<div class="gunluk-trade">
        <div style="min-width:126px">
          <b>${VX.esc(r.symbol)}</b>
          <span class="tag ${r.side === "LONG" ? "tag--up" : "tag--down"}">${r.side}</span>
          ${oto ? `<span class="evidence" title="Binance'ten otomatik alındı">borsadan</span>` : ""}
          ${(!oto && !r.kural_uyumu) ? `<span class="gunluk-flag">kural dışı</span>` : ""}
        </div>
        <div style="font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;min-width:210px">
          giriş ${r.entry}${r.qty ? ` · ${r.qty} adet` : ""}${r.leverage ? ` · ${r.leverage}x` : ""}<br>
          ${r.initial_stop
            ? `stop ${r.initial_stop}${r.initial_target ? ` · hedef ${r.initial_target}` : ""}`
            : `<span style="color:var(--down)">borsada stop yok — R hesaplanamaz</span>`}
        </div>
        <div class="gunluk-trade__why">
          ${denetimKutu(r)}
          <input class="input input--xs" style="width:100%" maxlength="400" data-not="${r.id}"
                 value="${VX.esc(r.gerekce || "")}" placeholder="neden girdin? (isteğe bağlı, sonradan da yazabilirsin)">
          ${gunlukBaglamRozet(r.baglam)}
          ${bayrak ? `<div class="gunluk-trade__ctx">${bayrak}</div>` : ""}
        </div>
        <div style="display:flex;gap:7px;align-items:center">
          <input class="input input--xs" style="width:112px" type="number" step="any" inputmode="decimal"
                 placeholder="çıkış" data-exit="${r.id}">
          <button class="btn btn--sm" data-kapat="${r.id}">Kapat</button>
        </div>
      </div>`;
    }).join("");

    // Gerekce alani odagi birakinca kaydedilir. Ayri bir "kaydet"
    // dugmesi koymadim: bu alan zorunlu degil, tiklama maliyeti olmamali.
    el.querySelectorAll("[data-not]").forEach(inp => {
      const ilk = inp.value;
      inp.addEventListener("blur", async () => {
        if (inp.value === ilk) return;
        try { await VX.post(`/api/mentor/gunluk/${inp.getAttribute("data-not")}/not`,
                            { gerekce: inp.value }); }
        catch (e) { VX.toast(e.message, "err", 6000); }
      });
    });

    el.querySelectorAll("[data-kapat]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-kapat");
        const inp = el.querySelector(`[data-exit="${id}"]`);
        const px = parseFloat(inp && inp.value);
        if (!px || px <= 0) {
          VX.toast("Çıkış fiyatı gir — ya da Binance'te kapat, senkron kendisi yakalar", "err", 7000);
          return;
        }
        btn.disabled = true;
        try {
          const r = await VX.post(`/api/mentor/gunluk/${id}/kapat`, { exit_price: px });
          VX.toast(r.result_r === null || r.result_r === undefined
            ? `Kapandı (stop yoktu, R hesaplanmadı)`
            : `Kapandı: ${r.result_r >= 0 ? "+" : ""}${r.result_r}R`);
          await gunlukYukle();
        } catch (e) { VX.toast(e.message, "err", 7000); btn.disabled = false; }
      });
    });
  }

  function gunlukKarneCiz(d) {
    /* EKSIK ALAN BUTUN SAYFAYI DUSURMEMELI.
       Bu fonksiyon karnenin TAM seklini varsayiyordu: k.genel, k.kural,
       k.kural.uydugumda... Sunucu herhangi bir sebeple eksik bir govde
       dondurse (yeni hesap, gocuk sirasinda bir alan, kismi hata)
       `undefined.n` firlatiyor, bu da bootMentor'un catch'ine dusup
       "Sayfa yuklenemedi" toast'i veriyor ve MENTOR SAYFASININ TAMAMI
       cizilmiyordu. Bir yan panelin eksik verisi, ana sayfayi
       goturmemeli. */
    const k = (d && d.karne) || {};
    const g = k.genel || {};
    k.kural = k.kural || {};
    k.kural.uydugumda = k.kural.uydugumda || { n: 0 };
    k.kural.uymadigimda = k.kural.uymadigimda || { n: 0 };
    k.kirilim = k.kirilim || {};
    k.verimlilik = k.verimlilik || {};
    k.dolar = k.dolar || {};
    k.bayrak_sayimi = k.bayrak_sayimi || {};
    const oel = $("gunlukOgutler");
    oel.innerHTML = (d.ogutler || []).map(o =>
      `<div class="gunluk-advice gunluk-advice--${VX.esc(o.tip)}">
         <span class="gunluk-advice__dot"></span><div>${VX.esc(o.metin)}</div></div>`).join("");

    if (!g.n) { $("gunlukKarne").innerHTML = `<div class="empty">Henüz kapanmış işlem yok.</div>`; return; }

    const ci = k.guven_araligi, sq = k.sqn;
    /* Yetersiz orneklem TUM karneyi soluklastirir. Amac: az veriden
       cikarim yapmayi gorsel olarak da caydirmak. */
    const zayif = k.yeterli_ornek ? "" : " gunluk-weak";
    let h = `<div class="gunluk-stats${zayif}">
      <div class="gunluk-stat"><small>işlem</small><b>${g.n}</b></div>
      <div class="gunluk-stat"><small>toplam</small><b>${g.toplam_r >= 0 ? "+" : ""}${g.toplam_r}R</b></div>
      <div class="gunluk-stat"><small>ortalama</small><b>${g.ortalama_r >= 0 ? "+" : ""}${g.ortalama_r}R</b></div>
      <div class="gunluk-stat"><small>isabet</small><b>${g.isabet}%</b></div>
      <div class="gunluk-stat"><small>profit factor</small><b>${gNum(g.profit_factor)}</b></div>
      <div class="gunluk-stat"><small>SQN</small><b>${sq ? sq.deger : "—"}</b>
        ${sq ? `<small style="margin-top:4px">${VX.esc(sq.yorum)}</small>` : ""}</div>
    </div>`;

    if (ci) {
      h += `<div class="gunluk-advice gunluk-advice--${ci.sifiri_iceriyor ? "uyari" : "iyi"}" style="margin-top:12px">
        <span class="gunluk-advice__dot"></span>
        <div><b>%90 bootstrap aralığı: [${ci.alt}, ${ci.ust}]R</b><br>
        ${ci.sifiri_iceriyor
          ? "Aralık sıfırı içeriyor — ortalaman pozitif olsa bile <b>kanıtlanmış bir avantaj yok</b>."
          : "Aralık sıfırın tamamen dışında — bu anlamlı bir sonuç."}</div></div>`;
    }

    const ku = k.kural;
    h += `<div class="gunluk-split">
      <div class="gunluk-stat"><small>kural uyum oranı</small><b>${gNum(ku.uyum_orani, "%")}</b></div>
      <div class="gunluk-stat"><small>kurallara uyduğumda</small>
        <b>${ku.uydugumda.n ? (ku.uydugumda.ortalama_r >= 0 ? "+" : "") + ku.uydugumda.ortalama_r + "R" : "—"}</b>
        <small style="margin-top:4px">${ku.uydugumda.n || 0} işlem</small></div>
      <div class="gunluk-stat"><small>uymadığımda</small>
        <b>${ku.uymadigimda.n ? (ku.uymadigimda.ortalama_r >= 0 ? "+" : "") + ku.uymadigimda.ortalama_r + "R" : "—"}</b>
        <small style="margin-top:4px">${ku.uymadigimda.n || 0} işlem</small></div>
    </div>`;

    const v = k.verimlilik;
    h += `<div class="gunluk-split">
      <div class="gunluk-stat"><small>çıkış verimi (kazananlar)</small><b>${gNum(v.ort_cikis_verimi, "%")}</b></div>
      <div class="gunluk-stat"><small>hedefe gidiş (updraw)</small><b>${gNum(v.ort_updraw, "%")}</b></div>
      <div class="gunluk-stat"><small>kazananlarda stopa yakınlık</small><b>${gNum(v.kazananlarda_ort_drawdown, "%")}</b></div>
    </div>`;
    $("gunlukKarne").innerHTML = h;

    const kir = $("gunlukKirilim");
    if (kir) {
      const blok = (baslik, rows) => {
        if (!rows || !rows.length) return "";
        return `<div class="workspace__muted" style="margin:12px 0 6px">${baslik}</div>` +
          `<table class="tbl"><thead><tr><th>—</th><th>işlem</th><th>ort. R</th><th>isabet</th></tr></thead><tbody>` +
          rows.map(r => `<tr class="${r.yeterli ? "" : "gunluk-weak"}">
            <td>${VX.esc(r.anahtar)}${r.yeterli ? "" : ` <span class="evidence">yetersiz</span>`}</td>
            <td>${r.n}</td><td>${r.ortalama_r >= 0 ? "+" : ""}${r.ortalama_r}R</td>
            <td>${r.isabet}%</td></tr>`).join("") + `</tbody></table>`;
      };
      kir.innerHTML =
        `<div class="workspace__muted">Soluk satırlar ${k.min_ornek ? "" : ""}yetersiz örnekli — bunlardan sonuç çıkarma.</div>` +
        blok("Kurulum etiketine göre", k.kirilim.etiket) +
        blok("Yöne göre", k.kirilim.yon) +
        blok("Sembole göre", k.kirilim.sembol) +
        blok("Saate göre (TRT)", k.kirilim.saat);
    }
    if ($("gunlukKarneMeta")) $("gunlukKarneMeta").textContent =
      `${g.n} kapanmış işlem · en az ${k.min_ornek} gerekli`;
  }

  function gunlukGecmisCiz(rows) {
    const el = $("gunlukGecmis");
    if (!el) return;
    if (!rows.length) { el.innerHTML = `<div class="empty">Kapanmış işlem yok.</div>`; return; }
    el.innerHTML = rows.map(r => `<div class="gunluk-trade">
      <div style="min-width:118px"><b>${VX.esc(r.symbol)}</b>
        <span class="tag ${r.side === "LONG" ? "tag--up" : "tag--down"}">${r.side}</span></div>
      <div class="gunluk-trade__why">
        ${r.gerekce ? VX.esc(r.gerekce) : `<i style="color:var(--ink-3)">gerekçe yok</i>`}
        ${gunlukBaglamRozet(r.baglam)}</div>
      <div style="font-variant-numeric:tabular-nums;font-size:12px">
        <b style="color:${r.result_r >= 0 ? "var(--up)" : "var(--down)"}">
          ${r.result_r >= 0 ? "+" : ""}${r.result_r}R</b>
        ${r.exit_efficiency !== null && r.exit_efficiency !== undefined
          ? `<span class="evidence">verim %${r.exit_efficiency}</span>` : ""}
        ${r.kural_uyumu ? "" : `<span class="gunluk-flag">kural dışı</span>`}
      </div></div>`).join("");
  }

  /* ---- BINANCE'TEN OTOMATIK CEKME ----
     Elle giris hem yorucu hem hatali: yanlis yazilan bir giris fiyati tum
     R hesabini bozar. Butun sayilar zaten borsada duruyor. */
  /* ---- BORSA BAGLANTI UYARISI ----
     Bu fonksiyon eskiden Binance pozisyonlarini AYRI bir panelde
     listeliyordu ve kullanici her birini tek tek "forma doldur" ile
     gunluge tasiyordu. O adim artik yok: mentor_sync servisi ayni veriyi
     90 saniyede bir kendisi yaziyor. Geriye tek bir is kaldi — anahtar
     bagli DEGILSE bunu sessizce gecmemek. Sessiz gecerse kullanici
     gunlugun neden bos oldugunu anlayamaz. */
  function gunlukBinanceCiz(d) {
    const el = $("gunlukBinance");
    if (!el) return;

    /* OLCULMUS HATA (10.09): BASARIDA VERIYI SILIYORDU.
       Onceki satir aynen soyleydi:
           if (d && d.ok) { el.hidden = true; el.innerHTML = ""; return; }
       Yani /api/mentor/gunluk/binance ucu Binance'ten pozisyonlari, mark
       fiyatini ve gerceklesmemis K/Z'yi dogru dogru cekiyordu; fonksiyon
       BASARILI cevabi alinca paneli gizleyip iceriGi siliyordu.
       `d.pozisyonlar` hicbir yerde okunmuyordu. Sistemdeki tek gercek
       zamanli pozisyon ucu, hicbir zaman hicbir sey cizmiyordu.
       Kullanicinin "binance acik islemlerim sisteme islemiyor" cumlesinin
       en son halkasi buydu. */
    if (d && d.ok) {
      const poz = d.pozisyonlar || [];
      if (!poz.length) { el.hidden = true; el.innerHTML = ""; return; }
      el.hidden = false;
      el.className = "panel workspace__section gb";
      el.innerHTML = `<div class="gb__head">
          <span>Binance'te açık</span>
          <b>${poz.length} pozisyon</b>
          <a class="btn btn--sm" href="/portfoy">Portföy ↗</a>
        </div>
        <div class="gb__liste">${poz.map(p => {
          const yon = (p.side || p.yon) === "LONG" ? "up" : "down";
          const pnl = Number(p.unrealized ?? p.pnl ?? 0);
          return `<div class="gb__satir">
            <span class="gb__sym">${VX.coinIcon(p.symbol, 16)}
              <b>${VX.esc(String(p.symbol || "").replace(/USDT$/, ""))}</b>
              <i class="gb__yon ${yon}">${VX.esc(p.side || p.yon || "")}</i></span>
            <span class="gb__n">${VX.fmtPrice(p.entry ?? p.giris)}</span>
            <span class="gb__n">${VX.fmtPrice(p.mark ?? p.mark_price)}</span>
            <span class="gb__pnl ${pnl > 0 ? "up" : pnl < 0 ? "down" : ""}"
              >${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}$</span>
          </div>`;
        }).join("")}</div>`;
      return;
    }
    el.hidden = false;
    el.className = "panel workspace__section gunluk-gate";
    el.innerHTML = `<div style="font-size:12.5px;line-height:1.6">
      <b>Otomatik kayıt çalışmıyor.</b> ${VX.esc((d && d.mesaj) || "Binance'e ulaşılamadı.")}
      Günlük kendiliğinden dolmayacak — <b>boş bir günlüğü "işlem yapmamışım" diye okuma.</b><br>
      <span class="workspace__muted">Anahtarı <a href="/ayarlar">Ayarlar &rsaquo; Binance</a> bölümünden bağlayabilirsin;
      bu arada sayfanın en altındaki <b>Elle kayıt</b> formu çalışıyor.</span></div>`;
  }

  /* ---- PIYASA NABZI: BTC, ETH, konumlanma ----
     Fiyatlar kutuphaneden geliyor (ek Binance cagrisi yok). Long/short
     orani HESAP sayisi oranidir, pozisyon buyuklugu degil — bin kucuk
     hesap long, on dev hesap short olabilir ve oran yine "long agirlikli"
     der. Kutunun altindaki yazi bunu acikca soyluyor; "piyasa long"
     demek yanlis olurdu ve tam da kalabaligi takip etmeye yol acan
     cumle odur. */
  function nabizFiyat(el, d) {
    if (!el) return;
    const b = el.querySelector("b"), i = el.querySelector("i");
    if (!d || !d.price) { if (b) b.textContent = "—"; return; }
    // WS fiyati varsa o kazanir: kutuphane degeri en fazla 60 saniyelik.
    const canli = (VX.live && d.symbol) ? VX.live.price(d.symbol) : null;
    if (b) b.textContent = radarFiyat(canli || d.price) + " $";
    if (i) {
      const c = d.change_pct;
      const yon = (c === null || c === undefined) ? "" : (c >= 0 ? "up" : "down");
      i.className = yon;
      i.textContent = (c === null || c === undefined)
        ? "24s —" : `24s ${c >= 0 ? "+" : ""}${Number(c).toFixed(2)}%`;
    }
  }

  /* BTC/ETH ARTIK WEBSOCKET'TEN.
     Onceki hali kutuphaneden okuyordu; kutuphane 60 saniyede bir REST ile
     tazeleniyor, yani "anlik fiyat" bir dakikaya kadar eski olabiliyordu.
     Sayfada zaten acik bir /ws/live baglantisi var (VX.live) — BTC ve
     ETH'yi ona abone edip tick geldikce yazmak hem gercekten anlik hem de
     EK MALIYETSIZ: yeni baglanti yok, yeni istek yok.
     24 saatlik degisim yuzdesi WS'te gelmiyor, o kutuphaneden okunmaya
     devam ediyor; dakikalik cozunurluk orada yeterli. */
  const NABIZ_SEMBOL = ["BTCUSDT", "ETHUSDT"];
  const NABIZ_SON = Object.create(null);

  function nabizCanliBagla() {
    if (!VX.live) return;
    // HATA BUYDU: mentor sayfasi /ws/live baglantisini HIC ACMIYORDU.
    // VX.live.connect() yalnizca terminal ve analiz sayfalarinda
    // cagriliyordu; burada watch() cagrilsa bile gonderecek soket yoktu,
    // fiyat 60 saniyede bir REST anlik goruntusunden geliyordu.
    // Kullanicinin "websocket'ten cekmiyor galiba" tespiti dogruydu.
    if (!VX.live.socket) VX.live.connect();
    VX.live.watch(NABIZ_SEMBOL);
    // onTick bir SOKME fonksiyonu donduruyor; kaydetmezsek her mentor
    // ziyaretinde bir dinleyici daha birikir ve hepsi ayni DOM'u aramaya
    // devam eder.
    VX.onTeardown(VX.live.onTick((batch) => {
      NABIZ_SEMBOL.forEach(sym => {
        const t = batch.get ? batch.get(sym) : null;
        if (!t || !t.price) return;
        const el = $(sym === "BTCUSDT" ? "kpiBtc" : "kpiEth");
        const b = el && el.querySelector("b");
        if (!b) return;
        const yeni = radarFiyat(t.price) + " $";
        if (b.textContent === yeni) return;
        // Yon flasi: sayiyi okumadan da fiyatin hangi yone gittigi
        // goruluyor. Yanip sonen bir nokta degil, tek seferlik bir vurgu.
        const onceki = NABIZ_SON[sym];
        b.textContent = yeni;
        if (onceki !== undefined && t.price !== onceki) {
          b.classList.remove("tick-flash-up", "tick-flash-down");
          void b.offsetWidth;
          b.classList.add(t.price > onceki ? "tick-flash-up" : "tick-flash-down");
        }
        NABIZ_SON[sym] = t.price;
      });
    }));
  }

  /* ---- BTC REJIMI ----
     Ekranin en ustundeki "akinti hangi yone akiyor" kutusu.
     Rejim bir TAHMIN degil bir AKINTI olcumu: akintiya karsi yuzmek
     yasak degil, farkinda olmadan yuzmek pahali. */
  const REJIM_RENK = {
    guclu_yukari: "up", yukari: "up",
    guclu_asagi: "down", asagi: "down", yatay: "",
  };

  async function rejimYukle() {
    const el = $("kpiRejim");
    if (!el) return;
    try {
      const d = await VX.get("/api/mentor/btc-rejim?interval=1h");
      const b = el.querySelector("b"), not = el.querySelector(".rejnot");
      if (!d.ok) { if (b) b.textContent = "—"; if (not) not.textContent = d.sebep || "okunamadı"; return; }
      if (b) {
        b.textContent = d.etiket;
        b.className = REJIM_RENK[d.kod] || "";
      }
      const g = d.genislik;
      if (not) {
        // Piyasa genisligi: hareket coine mi PIYASAYA mi ait?
        // %85'i yesilse o gun hicbir coin kendi hikayesini yasamiyordur.
        not.textContent = g
          ? `24s ${d.degisim_24s >= 0 ? "+" : ""}${d.degisim_24s}% · evrenin %${g.yukselen_yuzde}'i yeşil`
          : `24s ${d.degisim_24s >= 0 ? "+" : ""}${d.degisim_24s}%`;
      }
    } catch (e) { /* ikincil */ }
  }

  async function nabizYukle() {
    const kutu = $("kpiLs");
    try {
      const d = await VX.get("/api/mentor/nabiz");
      nabizFiyat($("kpiBtc"), d.btc);
      nabizFiyat($("kpiEth"), d.eth);
      mselOzetCiz(d);
      const ls = d.long_short;
      if (!kutu) return;
      const b = kutu.querySelector("b"), bar = kutu.querySelector(".lsbar i"),
            not = kutu.querySelector(".lsnot");
      if (!ls) {
        if (b) b.textContent = "—";
        if (not) not.textContent = "oran okunamadı";
        return;
      }
      const uzun = ls.long_yuzde;
      if (b) {
        b.textContent = `%${String(uzun).replace(".", ",")} long`;
        b.className = uzun >= 50 ? "up" : "down";
      }
      if (bar) bar.style.width = Math.max(0, Math.min(100, uzun)) + "%";
      if (not) {
        not.textContent = ls.demo
          ? "demo veri — gerçek oran değil"
          // ornek alani gelmeyebiliyor; sartsiz yazinca ekranda
          // "(undefined sembol)" cikiyordu. Eksik bir sayiyi bosluk
          // birakmak, "undefined" yazmaktan her zaman iyidir.
          : `%${String(ls.short_yuzde).replace(".", ",")} short · hesap sayısı oranı,`
            + ` pozisyon büyüklüğü değil${ls.ornek ? ` (${ls.ornek} sembol)` : ""}`;
      }
    } catch (e) {
      if (kutu) { const n = kutu.querySelector(".lsnot"); if (n) n.textContent = "okunamadı"; }
    }
  }

  /* ---- ARACLAR IZGARASI ----
     Sekme gibi davraniyor: bir karta basinca govdesi altinda aciliyor,
     digerleri kapaniyor. Ayni anda tek arac acik olmasi kasitli — hepsini
     acik tutmak, duzeltmeye calistigimiz yedi satirlik yigina geri
     donmek olurdu. Ayni karta tekrar basmak kapatiyor. */
  function aracAc(ad) {
    const grid = $("aracGrid"), govde = $("aracGovde");
    if (!grid || !govde) return;
    const kart = grid.querySelector(`[data-arac="${ad}"]`);
    const acikti = kart && kart.classList.contains("is-acik");
    grid.querySelectorAll("[data-arac]").forEach(k => k.classList.remove("is-acik"));
    govde.querySelectorAll(".arac-pane").forEach(p => { p.hidden = true; });
    if (acikti || !kart) return;          // ayni karta basmak kapatir
    kart.classList.add("is-acik");
    const pane = govde.querySelector(`[data-pane="${ad}"]`);
    if (pane) pane.hidden = false;
  }

  // Karttaki sayi, aracı ACMADAN "icinde is var mi" sorusunu cevapliyor.
  // Bos bir araci acmak icin tiklamak, listelerin en can sikici yaniydi.
  function aracRozet(id, sayi, birim) {
    const e = $(id);
    if (!e) return;
    const n = Number(sayi) || 0;
    e.textContent = n ? `${n} ${birim}` : "boş";
    e.classList.toggle("is-bos", !n);
  }

  /* ---- BAGLANTI TESHISI ----
     "Cekemiyorum" cumlesini bir SEBEBE ve bir YAPILACAK ISE cevirir.
     Onceki hali tek bir genel mesaj veriyordu: anahtar mi yok, anahtar
     mi yanlis, IP kisiti mi var, izin mi eksik, ulke engeli mi — hepsi
     ayni cumleye cikiyor ve hicbiri duzeltilemiyordu. */
  function teshisCiz(d) {
    const el = $("teshisListe");
    if (!el) return;
    const adimlar = d.adimlar || [];
    const ikon = a => a.ok === true ? "✓" : a.ok === false ? "✕" : "!";
    const sinif = a => a.ok === true ? "ok" : a.ok === false ? "hata" : "uyari";
    el.innerHTML = `<div class="tsh">${adimlar.map(a => `
      <div class="tsh__s tsh__s--${sinif(a)}">
        <span class="tsh__i">${ikon(a)}</span>
        <div>
          <div class="tsh__ad">${VX.esc(a.ad)}</div>
          <div class="tsh__d">${VX.esc(a.detay)}</div>
          ${a.cozum ? `<div class="tsh__c">${a.cozum.replace(/`([^`]+)`/g, "<code>$1</code>")}</div>` : ""}
        </div>
      </div>`).join("")}</div>`;
    // SON SUNUCU HATALARI.
    // "Internal Server Error" cumlesi kullaniciya hicbir sey
    // soylemiyordu: hangi uc, hangi istisna. Artik burada duruyor —
    // SSH ile log okumaya gerek yok.
    const hatalar = d.son_hatalar || [];
    if (hatalar.length) {
      el.insertAdjacentHTML("beforeend", `
        <div class="tsh__hata">
          <b>Son sunucu hataları (${hatalar.length})</b>
          ${hatalar.map(h => `<details><summary>
              <span>${VX.esc(h.tip)}</span> ${VX.esc(h.mesaj)}
              <i>${VX.esc((h.yol || "").replace(/^https?:\/\/[^/]+/, ""))} · ${VX.fmtAgo(h.ts)}</i>
            </summary><pre>${VX.esc(h.iz || "")}</pre></details>`).join("")}
        </div>`);
    }
    const oz = $("teshisOzet");
    if (oz) {
      oz.textContent = d.ok
        ? "Bağlantı çalışıyor — senkron işlemleri çekebilir."
        : `İlk kırılan yer: ${d.asama}`;
    }
    // Cip rozeti: teshis calistirmadan da durumu gosteriyor.
    const r = $("aracRozetBag");
    if (r) {
      r.textContent = d.ok ? "bağlı" : "kırık";
      r.classList.toggle("is-bos", !d.ok);
    }
  }

  async function teshisCalistir() {
    const btn = $("teshisBtn");
    if (btn) { btn.disabled = true; btn.textContent = "deneniyor…"; }
    try {
      teshisCiz(await VX.get("/api/mentor/senkron/teshis"));
    } catch (e) {
      const el = $("teshisListe");
      if (el) el.innerHTML = `<div class="empty">Teşhis çalıştırılamadı: ${VX.esc(e.message)}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Teşhis çalıştır"; }
    }
  }

  /* Sag raydaki sekmeler: Acik islem / Karne.
     Ikisi de dar icerikli; alt alta koymak sutunu gereksiz uzatir ve
     ikisini birden gormek zaten gerekmiyor — biri "su an ne var",
     digeri "genel olarak nasilim". */
  function bootSagSeg() {
    const seg = $("sagSeg");
    if (!seg) return;
    seg.addEventListener("click", e => {
      const b = e.target.closest("[data-sag]");
      if (!b) return;
      const ad = b.getAttribute("data-sag");
      seg.querySelectorAll("button").forEach(x => x.classList.toggle("is-active", x === b));
      document.querySelectorAll("[data-sagpane]").forEach(p => {
        p.hidden = p.getAttribute("data-sagpane") !== ad;
      });
    });
  }

  /* Durum cubugundaki ikizler. Ust seritteki sayilarin aynisi ama
     terminalin en altinda: Prime'daki "OPERATIONAL" satirinin isi bu —
     bir sey durduysa goz ucuyla gorunsun. */
  function tbarTazele() {
    const kop = (kaynak, hedef) => {
      const a = $(kaynak), b = $(hedef);
      if (a && b) b.textContent = a.textContent;
    };
    kop("stripEvren", "stripEvren2");
    kop("stripSenkron", "stripSenkron2");
  }

  function bootAraclar() {
    const grid = $("aracGrid");
    if (!grid) return;
    grid.addEventListener("click", e => {
      const k = e.target.closest("[data-arac]");
      if (!k) return;
      const ad = k.getAttribute("data-arac");
      aracAc(ad);
      // Teshis paneli acilinca kendini calistiriyor: "cekemiyorum"
      // diyen birine bir dugme daha gostermenin anlami yok.
      if (ad === "baglanti" && !$("teshisListe").querySelector(".tsh")) {
        teshisCalistir();
      }
    });
    const tBtn = $("teshisBtn");
    if (tBtn) tBtn.addEventListener("click", teshisCalistir);
  }

  /* ---- RADAR: ucuz ve EVRENSEL katman ----
     Neden ayri bir panel: derin analiz sembol basina ~10 agirlik
     harciyor, 500 kontratin hepsine uygulanamiyor. Ama ticker_24h TEK
     istekte (agirlik 40) hepsini getiriyor. Sunucudaki kutuphane o veriyi
     sakliyor ve her sembolu KENDI gecmisiyle kiyasliyor. Burada gorunen
     sey, pahali analizin nereye harcanacagina karar veren siralama.

     "hacim ×" sutunu bu isin can alici yeri: mutlak hacim buyuk coinleri
     one cikarir ve liste gunlerce degismez. Sembolun kendi medyanina
     gore 4 kat hacim ise BTC'de de yeni bir kontratta da ayni seyi
     soyler — hareket basliyor. */
  let RADAR_SIRA = "ilgi";

  // Fiyat basamagi buyuklige gore. Sabit basamak sayisi iki tarafta da
  // yaniltiyordu: BTC'de "76463.0052436" gurultu, PEPE'de "0.00" bilgi kaybi.
  function radarFiyat(v) {
    const x = Number(v);
    if (!isFinite(x) || x === 0) return "—";
    const a = Math.abs(x);
    const d = a >= 1000 ? 1 : a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 5 : 7;
    return x.toLocaleString("tr-TR", { minimumFractionDigits: 0, maximumFractionDigits: d });
  }

  function radarSatiri(r) {
    const n = (v, d = 2) => (v === null || v === undefined) ? "—" : Number(v).toFixed(d);
    const isaret = v => (v === null || v === undefined) ? "" : (v >= 0 ? "up" : "down");
    const hacim = r.quote_volume >= 1e9 ? `${(r.quote_volume / 1e9).toFixed(1)}B`
                : `${(r.quote_volume / 1e6).toFixed(1)}M`;
    const bant = r.band_pos === null || r.band_pos === undefined ? null : Math.round(r.band_pos * 100);
    return `<div class="radar-row">
      <a class="radar-row__sym" href="/mentor?symbol=${encodeURIComponent(r.symbol)}">${VX.esc(r.symbol.replace(/USDT$/, ""))}</a>
      <span class="radar-row__px">${VX.esc(radarFiyat(r.price))}</span>
      <span class="radar-row__c radar-row__c--${isaret(r.change_pct)}">${r.change_pct >= 0 ? "+" : ""}${n(r.change_pct)}%</span>
      <span class="radar-row__c radar-row__c--${isaret(r.mom_1h)}" title="1 saatlik değişim — 24s değişim geç kalmış bir ölçü">${r.mom_1h === null || r.mom_1h === undefined ? "—" : (r.mom_1h >= 0 ? "+" : "") + n(r.mom_1h)}${r.mom_1h === null || r.mom_1h === undefined ? "" : "%"}</span>
      <span class="radar-row__vr ${r.vol_ratio >= 2 ? "is-hot" : ""}" title="Hacim, bu sembolün kendi 14 günlük medyanının kaç katı">${r.vol_ratio ? n(r.vol_ratio, 1) + "×" : "—"}</span>
      <span class="radar-row__v">${hacim}</span>
      <span class="radar-row__bant" title="24s aralıkta fiyatın konumu (0 dip, 100 tepe)">
        ${bant === null ? "—" : `<i style="left:${Math.max(0, Math.min(100, bant))}%"></i>`}</span>
    </div>`;
  }

  function radarBlok(rows) {
    return `<div class="radar-blok">
      <div class="radar-head">
        <span>coin</span><span>fiyat</span><span>24s</span><span>1s</span>
        <span title="kendi medyanına göre hacim">hacim ×</span><span>hacim</span><span>24s bant</span>
      </div>
      <div class="radar-liste">${rows.map(radarSatiri).join("")}</div>
    </div>`;
  }

  async function radarYukle() {
    const el = $("radarListe");
    if (!el) return;
    try {
      const d = await VX.get(`/api/mentor/kutuphane?limit=40&sirala=${RADAR_SIRA}`);
      const o = d.ozet || {};
      const sy = $("radarSayi");
      if (sy) sy.textContent = `${o.izlenen_sembol || 0} kontrat`;
      const se = $("stripEvren");
      if (se) se.textContent = `${o.izlenen_sembol || 0}`;
      const rows = d.satirlar || [];
      if (!rows.length) {
        el.innerHTML = `<div class="empty">Kütüphane henüz dolmadı — ilk tarama birkaç saniye sürer.</div>`;
        return;
      }
      // IKI SUTUN. Tek sutunda tablo 1130 piksel genisligi kaplamasina
      // ragmen icinde 7 dar hucre vardi; aradaki her sey OLU ALANDI ve
      // 40 satir ekranin iki katini kapliyordu. Ikiye bolmek genisligi
      // gercekten kullaniyor ve yuksekligi yariya indiriyor.
      // 14 satirin altinda bolmuyoruz: iki kisa sutun daginik gorunur.
      // Sol rayda (dar) tek sutun, genis yerlesimde iki sutun.
      // Konteyner genisligine bakiyoruz cunku ayni bilesen iki farkli
      // yerde kullanilabiliyor; ekran genisligi burada yanlis olcu.
      const dar = el.clientWidth > 0 && el.clientWidth < 420;
      el.classList.toggle("radar--dar", dar);
      const govde = (!dar && rows.length >= 14)
        ? `<div class="radar-ikili">${radarBlok(rows.slice(0, Math.ceil(rows.length / 2)))}
             ${radarBlok(rows.slice(Math.ceil(rows.length / 2)))}</div>`
        : `<div class="radar-ikili">${radarBlok(rows)}</div>`;
      // Taban cizgisi kac sembolde var: hacim orani ancak gecmis
      // biriktikce anlamli. Bunu gizlemek yerine yaziyoruz.
      const taban = o.taban_cizgisi_olan || 0;
      el.innerHTML = govde +
        `<div class="workspace__muted" style="margin-top:10px;font-size:10.5px">
           ${dar
             ? `${o.izlenen_sembol || 0} kontrat izleniyor · ${taban} tanesinde hacim tabanı var`
             : `${o.izlenen_sembol || 0} kontratın tamamı her dakika tek çağrıyla okunuyor.
                ${taban} tanesinde kendi hacim tabanı oluştu; kalanlarda “hacim ×” geçmiş
                birikene kadar boş kalır — uydurulmuş bir taban, taban olmamasından kötüdür.`}
         </div>`;
    } catch (e) {
      el.innerHTML = `<div class="empty">Radar okunamadı: ${VX.esc(e.message)}</div>`;
    }
  }


  /* ---- SISTEMIN KENDI GOZLEMLERI ---- */
  function taramaCiz(d, durum) {
    if ($("taramaDurum")) {
      $("taramaDurum").textContent = durum && durum.son_tarama
        ? `son tarama ${VX.fmtAgo(durum.son_tarama)} · ${durum.taranan || 0} sembol, ${durum.kaydedilen || 0} gözlem`
        : "henüz taranmadı";
    }
    const el = $("taramaGozlemler");
    const g = (d && d.gozlemler) || [];
    aracRozet("aracRozetGozlem", g.length, "gözlem");
    if (!g.length) {
      el.innerHTML = `<div class="empty">Henüz kayıtlı gözlem yok. Tarama saatte bir çalışıyor.</div>`;
      return;
    }
    el.innerHTML = g.map(x => {
      const p = x.payload || {};
      const liste = (baslik, dizi, renk) => (dizi && dizi.length)
        ? `<div style="margin-top:6px"><span class="workspace__muted" style="font-size:11px">${baslik}</span>
           <ul style="margin:3px 0 0;padding-left:16px;font-size:12px;line-height:1.55;color:${renk}">
           ${dizi.map(y => `<li>${VX.esc(y)}</li>`).join("")}</ul></div>` : "";
      return `<div class="gunluk-trade" style="align-items:flex-start">
        <div style="min-width:118px">
          <b>${VX.esc(x.symbol)}</b>
          <div class="workspace__muted" style="font-size:11px">${VX.fmtAgo(x.created_at)}</div>
          ${p.kalite ? `<span class="evidence">${VX.esc(p.kalite)}</span>` : ""}
        </div>
        <div style="flex:1 1 280px;min-width:0">
          ${p.ozet ? `<div style="font-size:12.5px;line-height:1.6">${VX.esc(p.ozet)}</div>` : ""}
          ${(p.kayit_sebebi || []).length
            ? `<div class="gunluk-trade__ctx">${p.kayit_sebebi.map(s => `<span>${VX.esc(s)}</span>`).join("")}</div>` : ""}
          ${liste("Lehte", p.lehte, "var(--ink-2)")}
          ${liste("Aleyhte", p.aleyhte, "var(--ink-2)")}
          ${liste("Kör nokta", p.kor_nokta, "var(--ink-3)")}
          ${p.gecersizlik ? `<div style="margin-top:6px;font-size:12px"><span class="workspace__muted">Geçersizlik:</span> ${VX.esc(p.gecersizlik)}</div>` : ""}
          ${p.teyit ? `<div style="margin-top:3px;font-size:12px"><span class="workspace__muted">Teyit:</span> ${VX.esc(p.teyit)}</div>` : ""}
        </div>
        <div style="font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;text-align:right">
          ${p.rsi != null ? `RSI ${p.rsi}<br>` : ""}
          ${p.funding_bp != null ? `funding ${p.funding_bp}bp<br>` : ""}
          ${p.t_stat != null ? `t=${Number(p.t_stat).toFixed(2)}` : ""}
        </div>
      </div>`;
    }).join("");
  }

  /* ---- BINANCE GECMISINI ICERI AKTARMA ----
     Elle girise dayali gunluk yanlidir: insan kazandigini hatirlar,
     kaybettigini yazmaz. Borsa gecmisi ikisini de esit tasir. */
  let ICT_ISLEMLER = [];

  function ictCiz(d) {
    const el = $("ictListe"), ozet = $("ictOzet"), btn = $("ictAktarBtn");
    ICT_ISLEMLER = [];
    if (!d.ok) {
      el.innerHTML = `<div class="empty">${VX.esc(d.mesaj || "Geçmiş okunamadı")}</div>`;
      ozet.textContent = ""; btn.disabled = true; return;
    }
    const hepsi = d.islemler || [];
    const yeni = hepsi.filter(x => !x.zaten_var);
    ICT_ISLEMLER = yeni;
    ozet.textContent = `${d.sembol_sayisi} sembol · ${hepsi.length} işlem · ${d.yeni} yeni · net ${Number(d.toplam_pnl || 0).toFixed(2)}$`;
    btn.disabled = yeni.length === 0;
    if (!hepsi.length) { el.innerHTML = `<div class="empty">Bu aralıkta kapanmış işlem bulunamadı.</div>`; return; }

    el.innerHTML = `<div style="overflow-x:auto"><table class="tbl">
      <thead><tr><th><input type="checkbox" id="ictHepsi" checked></th><th>Sembol</th><th>Yön</th>
      <th>Giriş</th><th>Çıkış</th><th>Net $</th><th>Süre</th><th>Kapanış</th><th>Stop (ops.)</th></tr></thead>
      <tbody>${hepsi.map((x, i) => {
        const saat = x.opened_at && x.closed_at ? ((x.closed_at - x.opened_at) / 3600000).toFixed(1) + "s" : "—";
        return `<tr class="${x.zaten_var ? "gunluk-weak" : ""}">
          <td>${x.zaten_var ? `<span class="evidence">kayıtlı</span>`
              : `<input type="checkbox" class="ict-sec" data-i="${i}" checked>`}</td>
          <td>${VX.esc(x.symbol)}</td>
          <td><span class="tag ${x.side === "LONG" ? "tag--up" : "tag--down"}">${x.side}</span></td>
          <td>${x.entry}</td><td>${x.exit_price}</td>
          <td style="color:${x.pnl_usdt >= 0 ? "var(--up)" : "var(--down)"}">${x.pnl_usdt >= 0 ? "+" : ""}${Number(x.pnl_usdt).toFixed(2)}</td>
          <td>${saat}</td><td>${VX.fmtAgo(x.closed_at)}</td>
          <td>${x.zaten_var ? "—" : `<input class="input" style="width:104px;padding:4px 7px;font-size:11.5px"
              type="number" step="any" inputmode="decimal" placeholder="R için" data-stop="${i}">`}</td>
        </tr>`;
      }).join("")}</tbody></table></div>`;

    const hepsiKutu = $("ictHepsi");
    if (hepsiKutu) hepsiKutu.addEventListener("change", () => {
      el.querySelectorAll(".ict-sec").forEach(c => { c.checked = hepsiKutu.checked; });
    });
  }

  async function ictAktar() {
    const el = $("ictListe"), btn = $("ictAktarBtn");
    const secili = [];
    el.querySelectorAll(".ict-sec").forEach(c => {
      if (!c.checked) return;
      const i = Number(c.getAttribute("data-i"));
      // data-i, tablonun cizildigi TAM diziye (ICT_TABLO) gore verildi.
      // Filtrelenmis bir diziye bakmak satirlari kaydirirdi.
      const satir = ICT_TABLO[i];
      if (!satir) return;
      const st = el.querySelector(`[data-stop="${i}"]`);
      const v = st && st.value.trim();
      secili.push(v ? { ...satir, initial_stop: parseFloat(v) } : satir);
    });
    if (!secili.length) { VX.toast("Hiç işlem seçilmedi", "err"); return; }
    btn.disabled = true; btn.textContent = "aktarılıyor…";
    try {
      const r = await VX.post("/api/mentor/gunluk/ice-aktar", { islemler: secili });
      VX.toast(`${r.eklendi} işlem aktarıldı (${r.r_li} tanesi R'li)`, "ok", 7000);
      await gunlukYukle();
      await ictGetir();
    } catch (err) { VX.toast(err.message, "err", 9000); }
    finally { btn.disabled = false; btn.textContent = "Seçilenleri aktar"; }
  }

  let ICT_TABLO = [];
  async function ictGetir() {
    const btn = $("ictBakBtn");
    btn.disabled = true; btn.textContent = "getiriliyor…";
    try {
      const gun = Number($("ictGun").value || 30);
      const d = await VX.get(`/api/mentor/gunluk/binance-gecmis?gun=${gun}`);
      ICT_TABLO = d.islemler || [];
      ictCiz(d);
    } catch (err) { VX.toast(err.message, "err", 9000); }
    finally { btn.disabled = false; btn.textContent = "Getir"; }
  }

  async function gunlukYukle() {
    const [durum, karne, gec] = await Promise.all([
      VX.get("/api/mentor/gunluk/durum"),
      VX.get("/api/mentor/gunluk/karne"),
      VX.get("/api/mentor/gunluk/gecmis?limit=60"),
    ]);
    GUNLUK_KONTROL = durum.kontrol_maddeleri || [];
    if (!$("gunlukKontrol").children.length) gunlukKontrolCiz();
    gunlukKapiCiz(durum.kapi);
    // Ust seritteki sayilar — karar icin gereken ozet, tek bakista.
    const k = durum.kapi || {};
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("stripAcik", (durum.acik || []).length);

    // BORSA SENKRONU. Bu satirin gorunur olmasi onemli: gunluk artik
    // otomatik dolduguna gore, calismadigi an kullanicinin bunu BILMESI
    // gerekiyor — yoksa bos gunlugu "islem yapmamisim" diye okur.
    try {
      const sd = await VX.get("/api/mentor/senkron/durum");
      const se = $("senkronDurum");
      if (!sd.anahtar) {
        set("stripSenkron", "anahtar yok");
        if (se) se.textContent = "Binance anahtarı bağlı değil";
      } else if (sd.hata) {
        set("stripSenkron", "hata");
        if (se) se.textContent = `son hata: ${sd.hata}`;
      } else {
        set("stripSenkron", sd.son ? VX.fmtAgo(sd.son) : "bekliyor");
        if (se) se.textContent = sd.son
          ? `borsadan ${sd.acik} açık pozisyon · ${VX.fmtAgo(sd.son)}`
          : "ilk okuma bekleniyor — 90 saniyede bir denenir";
      }
    } catch (e) { set("stripSenkron", "—"); }
    gunlukAcikCiz(durum.acik || []);
    /* KARNE KENDI TRY'INDA — ve bu yapisal bir duzeltme.
       Karne govdesindeki HERHANGI bir eksik alan (once `kural.uydugumda`,
       sonra `verimlilik.ort_cikis_verimi`) bootMentor'un catch'ine dusup
       "Sayfa yuklenemedi" toast'i veriyor ve MENTOR SAYFASININ TAMAMINI
       cizdirmiyordu. Alan alan varsayilan atamak her seferinde bir
       SONRAKI eksik alanda kirilmaya devam eder; asil kural su: bir yan
       panelin verisi ana sayfayi goturmemeli. */
    try { gunlukKarneCiz(karne); }
    catch (e) {
      console.error("karne cizilemedi:", e);
      const kel = $("gunlukKarne");
      if (kel) kel.innerHTML = `<div class="empty">Karne çizilemedi —
        veri eksik geldi. Sayfanın geri kalanı etkilenmedi.</div>`;
    }
    gunlukGecmisCiz(gec.islemler || []);
    aracRozet("aracRozetGecmis", (gec.islemler || []).length, "işlem");

    // Otomatik gozlemler ve tarama durumu. Basarisiz olursa gunluk yine
    // calisir — tarama ikincil bir ozellik, ana akisi bloklamamali.
    try {
      const [gz, td] = await Promise.all([
        VX.get("/api/mentor/tarama/gozlemler?limit=15"),
        VX.get("/api/mentor/tarama/durum"),
      ]);
      taramaCiz(gz, td);
    } catch (e) {
      if ($("taramaGozlemler")) $("taramaGozlemler").innerHTML =
        `<div class="empty">Gözlemler okunamadı: ${VX.esc(e.message)}</div>`;
    }
  }

  async function bootGunluk() {
    const form = $("gunlukForm");
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const fd = new FormData(form);
      const body = { kontrol: {} };
      ["symbol", "side", "gerekce", "etiket"].forEach(k => { body[k] = (fd.get(k) || "").toString().trim(); });
      ["entry", "initial_stop", "initial_target", "qty"].forEach(k => {
        const v = fd.get(k); if (v !== null && String(v).trim() !== "") body[k] = parseFloat(v);
      });
      const lev = fd.get("leverage");
      if (lev && String(lev).trim() !== "") body.leverage = parseInt(lev, 10);
      form.querySelectorAll("[data-kontrol]").forEach(c => {
        body.kontrol[c.getAttribute("data-kontrol")] = c.checked;
      });
      $("gunlukFormState").textContent = "kaydediliyor…";
      try {
        const r = await VX.post("/api/mentor/gunluk/ac", body);
        $("gunlukFormState").textContent = "";
        VX.toast(r.kural_uyumu ? "Kaydedildi" : "Kaydedildi — kural dışı olarak işaretlendi",
                 r.kural_uyumu ? "ok" : "err", 6000);
        form.reset();
        gunlukKontrolCiz();
        await gunlukYukle();
      } catch (err) {
        $("gunlukFormState").textContent = "";
        VX.toast(err.message, "err", 9000);
      }
    });
    const iBtn = $("ictBakBtn");
    if (iBtn) iBtn.addEventListener("click", ictGetir);
    const aBtn = $("ictAktarBtn");
    if (aBtn) aBtn.addEventListener("click", ictAktar);

    const sBtn = $("senkronBtn");
    if (sBtn) sBtn.addEventListener("click", async () => {
      sBtn.disabled = true; sBtn.textContent = "çekiliyor…";
      try {
        const r = await VX.post("/api/mentor/senkron/simdi", {});
        VX.toast(r.ok
          ? `Borsa okundu: ${r.acilan} yeni kayıt, ${r.kapanan} kapandı`
          : (r.sebep === "anahtar_yok"
              ? "Binance anahtarı bağlı değil — Ayarlar'dan bağlayabilirsin"
              : (r.sebep || "okunamadı")),
          r.ok ? "ok" : "err", 7000);
        await gunlukYukle();
      } catch (err) { VX.toast(err.message, "err", 9000); }
      finally { sBtn.disabled = false; sBtn.textContent = "Şimdi çek"; }
    });

    const rBtn = $("radarYenile");
    if (rBtn) rBtn.addEventListener("click", radarYukle);
    const rSeg = $("radarSirala");
    if (rSeg) rSeg.addEventListener("click", e => {
      const b = e.target.closest("[data-sirala]");
      if (!b) return;
      RADAR_SIRA = b.getAttribute("data-sirala");
      rSeg.querySelectorAll("button").forEach(x => x.classList.toggle("is-active", x === b));
      radarYukle();
    });

    const tBtn = $("taramaSimdiBtn");
    if (tBtn) tBtn.addEventListener("click", async () => {
      tBtn.disabled = true; tBtn.textContent = "taranıyor…";
      try {
        const r = await VX.post("/api/mentor/tarama/simdi", {});
        VX.toast(r.ok ? `${r.taranan} sembol incelendi, ${r.kaydedilen} gözlem` : (r.sebep || "tarama yapılamadı"),
                 r.ok ? "ok" : "err", 6000);
        await gunlukYukle();
      } catch (err) { VX.toast(err.message, "err", 9000); }
      finally { tBtn.disabled = false; tBtn.textContent = "Şimdi tara"; }
    });

    // ACILISTA PLAN ARA.
    // Kullanicinin "on ayak olacak bir yapi istiyorum" demesinin karsiligi
    // bu: sayfa acildiginda cevap ZATEN orada olmali, dugmeye basmasi
    // gerekmemeli.
    bootAraclar();
    bootSagSeg();
    const boyut = () => { planTazeCiz(); radarYukle().catch(() => {}); };
    let boyutT = null;
    const boyutDinle = () => { clearTimeout(boyutT); boyutT = setTimeout(boyut, 250); };
    window.addEventListener("resize", boyutDinle);
    VX.onTeardown(() => { window.removeEventListener("resize", boyutDinle); clearTimeout(boyutT); });
    // ONCE hafizadan boya (ag beklemeden), SONRA sunucudan tazele.
    // Sayfa gecisinde bos ekran gormemenin tek yolu bu; sunucu onbellegi
    // zaten anlik cevap veriyor ama ag gidis-donusu bile goze carpiyordu.
    const onceki = planOku();
    if (onceki) { try { planCiz(onceki); } catch (_) {} }
    planAra().catch(() => { /* plan bulunamazsa sayfa yine calisir */ });
    radarYukle().catch(() => { /* radar ikincil, ana akisi bloklamaz */ });
    nabizCanliBagla();
    nabizYukle().catch(() => { /* nabiz ikincil */ });
    rejimYukle().catch(() => { /* rejim ikincil */ });

    await gunlukYukle();
    // Acilista Binance pozisyonlarini da cek: kullanici "Yenile"ye basmak
    // zorunda kalmasin. Anahtar yoksa panel kendi mesajini gosterir.
    // POZISYONLAR TEKRAR TEKRAR CEKILIYOR.
    // Onceden yalnizca acilista bir kez cekiliyordu: sayfayi acik
    // birakip Binance'te pozisyon acan/kapatan kullanici ekranda
    // eski durumu goruyordu ve degistigini fark etmiyordu.
    const binanceYukle = async () => {
      try { gunlukBinanceCiz(await VX.get("/api/mentor/gunluk/binance")); }
      catch (e) { /* panel kendi mesajini koruyor */ }
    };
    await binanceYukle();
    VX.interval(binanceYukle, 45000, false);
    VX.interval(gunlukYukle, 90000, false);
    // KENDI KENDINE TARAMA. Kullanicinin "ben basip taratmayayim"
    // istegi bu: liste 3 dakikada bir kendini yeniliyor. 3 dakika
    // keyfi degil — bir tarama 150 sembole ~1300 agirlik harciyor ve
    // Binance'in dakikalik butcesi 2400; daha sik calistirmak butceyi
    // radar ve senkronla carpistirirdi. 1h mumlarla calisan bir plan
    // zaten 3 dakikada anlamli olcude degismez.
    // Sunucu onbellegi 3 dakikada bir tazeleniyor; arayuz 60 saniyede bir
    // OKUYOR. Okuma bedava (tarama yok), o yuzden daha sik bakmak
    // maliyetsiz ve liste hicbir zaman 3 dakikadan bayat gorunmuyor.
    VX.interval(() => planAra(true), 60000, false);

    // Radar ucuz (tek sorgu, sunucu tarafinda zaten onbellekli tablo);
    // gunlukten daha sik tazelenebilir.
    VX.interval(radarYukle, 45000, false);
    VX.interval(nabizYukle, 60000, false);
    // Rejim 60 saniye sunucu onbellekli; 90 saniyede bir okumak yeterli.
    VX.interval(rejimYukle, 90000, false);
    VX.interval(tbarTazele, 5000, false);
  }


  /* KARSILAMA — saate ve kullaniciya gore.
     Sistem etiketi ("VORTEX / KARAR DESTEGI") yerine bir cumle.
     Metin bilincli olarak sakin: tasarim islem acma durtusunu
     artirmamali. */
  async function mselCiz() {
    const ad = $("mselAd");
    if (!ad) return;
    const s = new Date().getHours();
    const selam = s < 6 ? "İyi geceler" : s < 12 ? "Günaydın"
                : s < 18 ? "İyi günler" : "İyi akşamlar";
    let isim = "";
    try {
      const d = await VX.get("/api/auth/state");
      isim = (d.user && (d.user.display_name || d.user.username)) || "";
      // Kullanici adi kucuk harfle saklaniyor ("taha"); bir selamlamada
      // kucuk harfli isim ozensiz duruyor. Yalnizca ILK harf buyutuluyor
      // — tamamini buyutmek bagirmak olurdu.
      if (isim) isim = isim.charAt(0).toLocaleUpperCase("tr") + isim.slice(1);
    } catch (e) { /* isim olmadan da cumle calisiyor */ }
    ad.textContent = isim ? `${selam}, ${isim}` : selam;
  }

  /* Ust ozet: BTC/ETH/rejim/L-S/kontrat — hero bandindaki AYNI veriden,
     ikinci bir istek YOK. Dar ekranda hero bandi zaten alta iniyor;
     burasi gorunmuyor. */
  function mselOzetCiz(d) {
    const el = $("mselOzet");
    if (!el || !d) return;
    const p = [];
    if (d.btc && d.btc.price) p.push(`BTC ${radarFiyat(d.btc.price)}`);
    if (d.eth && d.eth.price) p.push(`ETH ${radarFiyat(d.eth.price)}`);
    el.textContent = p.join("  ·  ");
  }

  /* ================================================================
     VERI SERIDI — plan tablosunun altindaki bosluk.

     Uc panel, ucu de MEVCUT uclardan. Yeni veri kaynagi yok, sahte
     veri hic yok: bir uc bos donerse panel bunu soyluyor.
     ================================================================ */
  const vserR = (x) => (x === null || x === undefined || isNaN(x)) ? "—"
    : (x > 0 ? "+" : "") + Number(x).toLocaleString("tr-TR",
        {minimumFractionDigits: 2, maximumFractionDigits: 2}) + "R";

  function vserKarneCiz(k) {
    const el = $("vserKarne");
    if (!el) return;
    const g = (k && k.genel) || {};
    if (!g.n) {
      el.innerHTML = `<div class="vser__bos">Henüz kapanmış işlem yok —
        karne buradan doldukça anlam kazanacak.</div>`;
      return;
    }
    /* YETERSIZ ORNEKLEM TUM SERIDI SOLUKLASTIRIYOR.
       Amac az veriden cikarim yapmayi gorsel olarak caydirmak: on
       islemlik bir "isabet %70" rakami, altmis islemlik bir rakamla
       ayni parlaklikta durmamali. */
    const zayif = k.yeterli_ornek ? "" : " is-zayif";
    const kutu = (ad, deger, sinif) => `<div class="vser__k">
      <span>${VX.esc(ad)}</span><b class="${sinif || ""}">${deger}</b></div>`;
    const ci = k.guven_araligi;
    el.className = "vser__stat" + zayif;
    el.innerHTML =
        kutu("İşlem", g.n)
      + kutu("Ortalama", vserR(g.ortalama_r), g.ortalama_r > 0 ? "up" : g.ortalama_r < 0 ? "down" : "")
      + kutu("Toplam", vserR(g.toplam_r), g.toplam_r > 0 ? "up" : g.toplam_r < 0 ? "down" : "")
      + kutu("İsabet", g.isabet === undefined ? "—" : `%${String(g.isabet).replace(".", ",")}`)
      + kutu("Profit factor", g.profit_factor === null || g.profit_factor === undefined
          ? "—" : String(g.profit_factor).replace(".", ","))
      + kutu("Kural uyumu", (k.kural && k.kural.uyum_orani !== null && k.kural.uyum_orani !== undefined)
          ? `%${String(k.kural.uyum_orani).replace(".", ",")}` : "—")
      + (k.yeterli_ornek
          ? (ci ? `<div class="vser__not">%95 güven aralığı
              [${vserR(ci[0])}, ${vserR(ci[1])}] — sıfırı içeriyorsa sonuç
              sıfırdan ayırt edilemez.</div>` : "")
          : `<div class="vser__not"><b>${g.n}/${k.min_ornek} işlem.</b>
              Bu sayılar henüz bulgu değil; örneklem yeterli olana kadar
              onlardan çıkarım yapma.</div>`);
  }

  function vserGecmisCiz(islemler) {
    const el = $("vserGecmis");
    if (!el) return;
    const l = islemler || [];
    if (!l.length) {
      el.innerHTML = `<div class="empty">Kapanmış işlem yok.
        Binance'ten otomatik gelenler burada birikecek.</div>`;
      return;
    }
    const satir = (t) => {
      const r = t.r_multiple;
      const yon = t.side === "LONG" ? "up" : "down";
      const rs = r > 0 ? "up" : r < 0 ? "down" : "";
      // R cubugu: sayiyi okumadan da buyuklugu goruluyor. 3R'de
      // doyuyor — daha genis olcek tek bir aykiri islemi butun
      // satirlari ezecek kadar buyutur.
      const gen = Math.min(100, Math.abs(r || 0) / 3 * 100);
      return `<div class="vser__s">
        <span class="vser__sym">${VX.coinIcon(t.symbol, 16)}
          <b>${VX.esc(String(t.symbol || "").replace(/USDT$/, ""))}</b>
          <i class="vser__yon ${yon}">${VX.esc(t.side || "")}</i></span>
        <span class="vser__cub"><i class="${rs}" style="width:${gen}%"></i></span>
        <span class="vser__r ${rs}">${vserR(r)}</span>
        <span class="vser__et">${t.etiket ? VX.esc(t.etiket) : ""}</span>
        <span class="vser__tar">${t.closed_at ? VX.fmtAgo(t.closed_at) : "—"}</span>
      </div>`;
    };
    el.innerHTML = `<div class="vser__bas">
        <span>coin</span><span></span><span>R</span><span>etiket</span><span>kapanış</span>
      </div>` + l.slice(0, 8).map(satir).join("");
  }

  let VSER_LIDER = "artan";
  async function vserLiderYukle() {
    const el = $("vserLider");
    if (!el) return;
    try {
      const d = await VX.get("/api/mentor/kutuphane?sirala=degisim&limit=60");
      let r = (d.satirlar || []).filter(x => x.degisim !== null && x.degisim !== undefined);
      r.sort((a, b) => VSER_LIDER === "artan" ? b.degisim - a.degisim : a.degisim - b.degisim);
      r = r.slice(0, 7);
      if (!r.length) { el.innerHTML = `<div class="empty">Veri bekleniyor.</div>`; return; }
      el.innerHTML = r.map(x => {
        const d24 = Number(x.degisim);
        return `<a class="vser__l" href="/analiz?symbol=${encodeURIComponent(x.symbol)}&interval=1h">
          <span class="vser__sym">${VX.coinIcon(x.symbol, 16)}
            <b>${VX.esc(String(x.symbol).replace(/USDT$/, ""))}</b></span>
          <span class="vser__lf">${radarFiyat(x.price)}</span>
          <span class="vser__ld ${d24 > 0 ? "up" : d24 < 0 ? "down" : ""}"
            >${d24 > 0 ? "+" : ""}%${String(d24.toFixed(2)).replace(".", ",")}</span>
        </a>`;
      }).join("");
    } catch (e) {
      el.innerHTML = `<div class="empty">Piyasa kütüphanesi okunamadı.</div>`;
    }
  }

  async function vserYukle() {
    // Ikisi paralel: biri digerini beklemesin.
    // KARNE KENDI UCUNDAN.
    // Ilk yazimda /gunluk/durum'dan okuyordum; o uc `acik`, `btc_rejim`,
    // `config`, `kapi` ve `kontrol_maddeleri` donuyor — karne YOK. Yanlis
    // varsayimla yazilmis bir okuma sessizce bos panel verirdi ve
    // "veri yok" diye okunurdu. Dogru uc /gunluk/karne.
    const [karne, gecmis] = await Promise.allSettled([
      VX.get("/api/mentor/gunluk/karne"),
      VX.get("/api/mentor/gunluk/gecmis?limit=8"),
    ]);
    if (karne.status === "fulfilled" && karne.value)
      vserKarneCiz(karne.value.karne || karne.value);
    if (gecmis.status === "fulfilled")
      vserGecmisCiz((gecmis.value || {}).islemler);
    else vserGecmisCiz([]);
    await vserLiderYukle();
  }

  function vserBagla() {
    const seg = $("vserLiderSeg");
    if (!seg) return;
    seg.querySelectorAll("[data-lider]").forEach(b => {
      b.addEventListener("click", () => {
        VSER_LIDER = b.getAttribute("data-lider");
        seg.querySelectorAll("[data-lider]").forEach(x =>
          x.classList.toggle("is-active", x === b));
        vserLiderYukle();
      });
    });
  }

  /* ---- ÜST KARTLARIN SPARKLINE'I ----------------------------------------
     Referans tasarımda kartın tabanında 24 saatlik bir seri var. Burada da
     GERÇEK seri: /api/market/klines 1h x 24. Uç nokta cevap vermezse
     grafik HİÇ çizilmiyor — boş bir grafik iskeleti "veri var ama düz"
     diye okunurdu ve bu yanlış olurdu.

     Tek sefer yükleniyor, canlı fiyatla birlikte yeniden çizilmiyor:
     24 saatlik bir eğrinin şekli bir saniyede değişmez, her tikte
     yeniden çizmek sadece işlemci yakar. */
  async function heroSparkCiz() {
    const hedef = [["kpiBtc", "BTCUSDT"], ["kpiEth", "ETHUSDT"]];
    await Promise.all(hedef.map(async ([id, sym]) => {
      const kart = $(id);
      if (!kart) return;
      try {
        const d = await VX.get(`/api/market/klines?symbol=${sym}&interval=1h&limit=24`);
        const kapanis = (d.candles || []).map(c => c.close);
        if (kapanis.length < 3) return;
        let kap = kart.querySelector(".mhero__spark");
        if (!kap) {
          kap = document.createElement("div");
          kap.className = "mhero__spark";
          kart.appendChild(kap);
        }
        kap.innerHTML = VX.sparkline(kapanis, { w: 320, h: 52 });
      } catch (e) { /* grafik ikincil; kart sayısıyla zaten çalışıyor */ }
    }));
  }

  async function bootMentor() {
    vserBagla();
    heroSparkCiz().catch(() => {});
    vserYukle().catch(() => { /* veri seridi ikincil; sayfa etkilenmez */ });
    VX.interval(() => vserYukle().catch(() => {}), 120000, false);
    mselCiz().catch(() => { /* karsilama ikincil */ });
    $("mentorForm").addEventListener("submit", reviewMentor);
    // "R nedir?" — kullanici sordu, arayuz cevaplasin. Bir kez acilip
    // kapaniyor; surekli goze gorunen bir aciklama metni sayfayi sisirirdi.
    const rBtn = $("rNedir"), rAc = $("rAciklama");
    if (rBtn && rAc) rBtn.addEventListener("click", () => {
      rAc.hidden = !rAc.hidden;
      rBtn.textContent = rAc.hidden ? "R nedir?" : "kapat";
    });

    const pBtn = $("planBtn");
    // Dugme ARTIK GERCEK TARAMA yaptiriyor (taze=1). Normal yenilemeler
    // onbellekten geliyor; kullanici "simdi bak" derse butceyi harcamaya
    // degecek tek an odur.
    if (pBtn) pBtn.addEventListener("click", () => planAra(false, true));
    const premiumBtn = $("premiumMentorScanBtn");
    const renderPremium = (data) => {
      const candidates = data.candidates || [];
      const meta = $("premiumMentorMeta");
      const list = $("premiumMentorList");
      if (meta) meta.textContent = `${data.scanned || 0} sembol tarandı · ${candidates.length} yapısal aday · varsayılan PAPER`;
      if (!list) return;
      if (!candidates.length) { list.innerHTML = '<div class="empty">Sıralı SMC eşiğini geçen aday yok. Bu normaldir; motor teyit uydurmaz.</div>'; return; }
      window.__premiumCandidates = candidates.slice(0, 10);
      list.innerHTML = `<div class="premium-mentor-cards">${window.__premiumCandidates.map((row, index) => {
        const stopPct = row.price ? Math.abs((row.stop - row.price) / row.price * 100).toFixed(1) : "—";
        const tpPct = row.price ? Math.abs((row.target - row.price) / row.price * 100).toFixed(1) : "—";
        const rr = stopPct !== "—" && parseFloat(stopPct) > 0 ? (parseFloat(tpPct) / parseFloat(stopPct)).toFixed(1) : "—";
        const action = '<span class="tag tag--info">Araştırma · emir kapalı</span>';
        return `<article class="premium-mentor-card"><div class="premium-mentor-card__top"><a class="premium-mentor-card__coin" href="/analiz?symbol=${encodeURIComponent(row.symbol)}&interval=${encodeURIComponent(row.ltf_interval || "15m")}">${VX.coinIcon(row.symbol, 18)}<span><b>${VX.esc(row.symbol.replace("USDT", ""))}</b><small>${VX.fmtPrice(row.price)}</small></span></a><span class="premium-mentor-card__side ${row.side === "LONG" ? "delta--up" : "delta--down"}">${VX.esc(row.side)}</span></div><div class="premium-mentor-card__stats"><span>Model <b>${VX.esc((row.model || "SMC").replaceAll("_", " "))}</b></span><span>Skor <b>${row.score}/${row.max_score}</b></span></div><div class="premium-mentor-card__stats"><span>Aşama <b>${VX.esc(row.phase || "—")}</b></span><span>Funding <b>${row.funding_bp == null ? "—" : Number(row.funding_bp).toFixed(2)} bp</b></span></div><div class="premium-mentor-card__levels"><span class="premium-mentor-card__sl">Stop <b>${VX.fmtPrice(row.stop)}</b> <small>−${stopPct}%</small></span><span class="premium-mentor-card__tp">Hedef <b>${VX.fmtPrice(row.target)}</b> <small>+${tpPct}%</small></span><span class="premium-mentor-card__rr">R:R <b>${Number(row.rr || 0).toFixed(1)}</b></span></div><div class="premium-mentor-card__actions">${action}</div><details class="premium-mentor-card__detail"><summary>Sıralı teyitleri aç</summary><p>${VX.esc((row.reasons || []).join(" → "))}</p>${(row.missing || []).length ? `<p>Beklenen: ${VX.esc(row.missing.join(" · "))}</p>` : ""}</details></article>`;
      }).join("")}</div>`;

    };
    const premiumStatus = async () => {
      try {
        const data = await VX.get("/api/engine/premium/status");
        renderPremium(data);
        if (data.enabled && !data.last_scan_at && !data.scanning) premiumScan();
      } catch (err) { const list = $("premiumMentorList"); if (list) list.innerHTML = `<div class="empty">Premium motor yüklenemedi: ${VX.esc(err.message)}</div>`; }
    };
    const premiumScan = async () => {
      if (premiumBtn) { premiumBtn.disabled = true; premiumBtn.textContent = "Taranıyor…"; }
      try { renderPremium(await VX.post("/api/engine/premium/scan-now", {})); }
      catch (err) { VX.toast(err.message, "err", 7000); }
      finally {
        if (premiumBtn) { premiumBtn.disabled = false; premiumBtn.textContent = "Şimdi tara"; }
      }
    };
    if (premiumBtn) premiumBtn.addEventListener("click", premiumScan);
    premiumStatus();
    const requested = new URLSearchParams(window.location.search).get("symbol");
    if (requested) $("mentorSymbol").value = requested.toUpperCase();
    let timer;
    $("mentorSymbol").addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const d = await VX.get(`/api/market/symbols?q=${encodeURIComponent($("mentorSymbol").value)}&limit=30`);
          $("mentorSymbolList").innerHTML = (d.symbols || []).map(s => `<option value="${VX.esc(s.symbol)}"></option>`).join("");
        } catch (_) {}
      }, 180);
    });
    await loadMentorRecent();
  }

  function line(chart, color) {
    return chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  }

  function thinLine(chart, color, width = 1) {
    return chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false });
  }

  function buildAnalysisChart() {
    const host = $("analysisChart");
    state.chart = LightweightCharts.createChart(host, {
      layout: { background: { color: "#080808" }, textColor: "#a9aaad", fontSize: 11, fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif' },
      grid: { vertLines: { color: "rgba(255,255,255,.055)", style: 2 }, horzLines: { color: "rgba(255,255,255,.055)", style: 2 } },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)", scaleMargins: { top: .18, bottom: .2 } },
      timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true },
      handleScale: { axisPressedMouseMove: { time: true, price: true }, mouseWheel: true, pinch: true },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      autoSize: true,
    });
    state.series.candles = state.chart.addCandlestickSeries({ upColor: "#e6e6e6", downColor: "#666", borderVisible: false, wickUpColor: "#e6e6e6", wickDownColor: "#777" });
    state.series.volume = state.chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    state.chart.priceScale("vol").applyOptions({ scaleMargins: { top: .84, bottom: 0 } });
    state.series.ema20 = line(state.chart, "#d8d3ff");
    state.series.ema50 = line(state.chart, "#9ca9ff");
    state.series.ema200 = line(state.chart, "#b994dc");
    state.series.vwap = line(state.chart, "#f6c76b");
    state.series.bbUpper = thinLine(state.chart, "rgba(70,165,255,.8)");
    state.series.bbLower = thinLine(state.chart, "rgba(70,165,255,.8)");
    state.series.supertrend = thinLine(state.chart, "#28d7a1", 2);
    state.series.ichiTenkan = thinLine(state.chart, "#f15b5b");
    state.series.ichiKijun = thinLine(state.chart, "#3f8cff");
    state.series.ichiA = thinLine(state.chart, "rgba(43,212,138,.58)");
    state.series.ichiB = thinLine(state.chart, "rgba(255,84,112,.58)");
    state.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range || state.syncingRange) return;
      state.syncingRange = true;
      const times=state.chart.timeScale().getVisibleRange();
      if(times)state.indicatorCharts.forEach(pane=>{try{pane.chart.timeScale().setVisibleRange(times);}catch(e){/* Empty panes are filled after the snapshot. */}});
      state.syncingRange = false;
    });
    $("analysisLegend").innerHTML = `<span style="color:#d8d3ff">EMA 20</span><span style="color:#9ca9ff">EMA 50</span><span style="color:#b994dc">EMA 200</span><span style="color:#f6c76b">VWAP</span><span>Fare tekeri: yakınlaştır · sürükle: incele</span>`;
  }

  function indicatorChartOptions() {
    return {
      layout: { background: { color: "transparent" }, textColor: "#97999d", fontSize: 9, fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif' },
      grid: { vertLines: { color: "rgba(255,255,255,.025)" }, horzLines: { color: "rgba(255,255,255,.035)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,.07)", scaleMargins: { top: .18, bottom: .14 } },
      timeScale: { borderColor: "rgba(255,255,255,.07)", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      handleScale: false,
      handleScroll: false,
      autoSize: true,
    };
  }

  function addGuide(series, price, color, title) {
    series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
  }

  function destroyIndicatorPanes() {
    state.indicatorCharts.forEach(pane => pane.chart.remove());
    state.indicatorCharts.clear();
    const host = $("indicatorPanes");
    if (host) host.replaceChildren();
  }

  function buildIndicatorPane(indicator) {
    const wrap = document.createElement("section");
    wrap.className = "workspace__indicator-pane";
    wrap.dataset.indicatorPane = indicator.id;
    const head = document.createElement("div");
    head.className = "workspace__indicator-pane-head";
    head.innerHTML = `<b>${VX.esc(indicator.name)}</b><span>veri bekleniyor</span>`;
    const host = document.createElement("div");
    host.className = "workspace__indicator-pane-chart";
    wrap.append(head, host);
    $("indicatorPanes").appendChild(wrap);
    const chart = LightweightCharts.createChart(host, indicatorChartOptions());
    const series = {};
    if (indicator.id === "rsi") {
      series.rsi = line(chart, "#c084fc"); addGuide(series.rsi, 70, "rgba(255,84,112,.52)", "70"); addGuide(series.rsi, 30, "rgba(43,212,138,.52)", "30");
    } else if (indicator.id === "macd") {
      series.histogram = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      series.macd = line(chart, "#60a5fa"); series.signal = line(chart, "#f59e0b");
    } else if (indicator.id === "stochrsi") {
      series.k = line(chart, "#22d3ee"); series.d = line(chart, "#f59e0b"); addGuide(series.k, 80, "rgba(255,84,112,.48)", "80"); addGuide(series.k, 20, "rgba(43,212,138,.48)", "20");
    } else if (indicator.id === "adx") {
      series.adx = line(chart, "#e5e7eb"); series.plus = line(chart, "#2bd48a"); series.minus = line(chart, "#ff5470"); addGuide(series.adx, 25, "rgba(255,255,255,.22)", "25");
    } else if (indicator.id === "atr") {
      series.atr = line(chart, "#fb923c");
    } else if (indicator.id === "cvd") {
      series.cvd = line(chart, "#34d399");
    }
    const pane = { chart, series, indicator, valueLabel: head.querySelector("span") };
    state.indicatorCharts.set(indicator.id, pane);
    return pane;
  }

  function lastFinite(values) {
    for (let i = (values || []).length - 1; i >= 0; i -= 1) if (values[i] !== null && values[i] !== undefined && Number.isFinite(Number(values[i]))) return Number(values[i]);
    return null;
  }

  function fillIndicatorPane(pane) {
    const source = state.lastSeries || {};
    const candles = source.candles || [];
    const times = candles.map(c => c.time);
    const id = pane.indicator.id;
    if (id === "rsi") {
      pane.series.rsi.setData(seriesData(times, source.rsi)); pane.valueLabel.textContent = `RSI ${human(lastFinite(source.rsi))}`;
    } else if (id === "macd") {
      pane.series.macd.setData(seriesData(times, source.macd)); pane.series.signal.setData(seriesData(times, source.macd_signal));
      pane.series.histogram.setData((source.macd_histogram || []).map((v, i) => v === null || v === undefined ? null : ({ time: times[i], value: v, color: v >= 0 ? "rgba(43,212,138,.58)" : "rgba(255,84,112,.58)" })).filter(Boolean));
      pane.valueLabel.textContent = `MACD ${human(lastFinite(source.macd))} · sinyal ${human(lastFinite(source.macd_signal))}`;
    } else if (id === "stochrsi") {
      pane.series.k.setData(seriesData(times, source.stoch_rsi_k)); pane.series.d.setData(seriesData(times, source.stoch_rsi_d)); pane.valueLabel.textContent = `K ${human(lastFinite(source.stoch_rsi_k))} · D ${human(lastFinite(source.stoch_rsi_d))}`;
    } else if (id === "adx") {
      pane.series.adx.setData(seriesData(times, source.adx)); pane.series.plus.setData(seriesData(times, source.plus_di)); pane.series.minus.setData(seriesData(times, source.minus_di)); pane.valueLabel.textContent = `ADX ${human(lastFinite(source.adx))} · +DI ${human(lastFinite(source.plus_di))} · −DI ${human(lastFinite(source.minus_di))}`;
    } else if (id === "atr") {
      pane.series.atr.setData(seriesData(times, source.atr)); pane.valueLabel.textContent = `ATR ${human(lastFinite(source.atr))}`;
    } else if (id === "cvd") {
      pane.series.cvd.setData(seriesData(times, source.cvd)); pane.valueLabel.textContent = `CVD ${human(lastFinite(source.cvd))}`;
    }
    const range = state.chart && state.chart.timeScale().getVisibleRange();
    if (range) pane.chart.timeScale().setVisibleRange(range); else pane.chart.timeScale().fitContent();
  }

  function renderIndicatorPanes() {
    destroyIndicatorPanes();
    INDICATORS.filter(ind => ind.on && ind.pane).forEach(ind => fillIndicatorPane(buildIndicatorPane(ind)));
  }

  function buildOrderFlowChart() {
    const host = $("orderFlowChart");
    state.flowChart = LightweightCharts.createChart(host, {
      layout: { background: { color: "transparent" }, textColor: "#a9aaad", fontSize: 10, fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif' },
      grid: { vertLines: { color: "rgba(255,255,255,.025)" }, horzLines: { color: "rgba(255,255,255,.035)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)" },
      timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true },
      handleScale: { mouseWheel: true, pinch: true }, handleScroll: { mouseWheel: true, pressedMouseMove: true }, autoSize: true,
    });
    state.series.flowDelta = state.flowChart.addHistogramSeries({ priceFormat: { type: "price", precision: 1, minMove: .1 }, lastValueVisible: true, priceLineVisible: false });
    state.series.flowDelta.createPriceLine({ price: 0, color: "rgba(255,255,255,.22)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
  }

  function activeIndicator(id) { return INDICATORS.find(x => x.id === id)?.on; }

  function applyIndicatorVisibility() {
    INDICATORS.forEach(ind => (ind.chart || []).forEach(key => {
      if (state.series[key]) state.series[key].applyOptions({ visible: ind.on });
    }));
    if ($("orderFlowWrap")) $("orderFlowWrap").hidden = !activeIndicator("orderflow");
    renderLiquidityOverlay();
    renderIndicatorPanes();
    renderActiveIndicators();
    try { localStorage.setItem("vortex.analysis.indicators", JSON.stringify(INDICATORS.filter(x => x.on).map(x => x.id))); } catch (_) {}
    if (state.lastSnap) renderIndicatorReadout(state.lastSnap);
  }

  function renderLiquidityOverlay() {
    if (!state.series.candles) return;
    (state.liquidityLines || []).forEach(x => state.series.candles.removePriceLine(x)); state.liquidityLines = [];
    const hunter = (((state.lastSnap || {}).findings || {}).liquidity_hunter || {});
    if (!activeIndicator("liquidity")) { state.series.candles.setMarkers([]); return; }
    const levels = [...(hunter.upper_levels || []), ...(hunter.lower_levels || [])];
    state.liquidityLines = levels.map(x => state.series.candles.createPriceLine({ price: x.price,
      color: x.side === "upper" ? `rgba(255,84,112,${.25 + (x.strength || 0) / 180})` : `rgba(43,212,138,${.25 + (x.strength || 0) / 180})`,
      lineWidth: x.strength >= 66 ? 2 : 1, lineStyle: 1, axisLabelVisible: true,
      title: `${x.side === "upper" ? "LQ↑" : "LQ↓"} ${Math.round(x.strength || 0)}` }));
    const candles = state.lastCandles || [];
    const markers = (hunter.signals || []).filter(x => candles[x.index]).map(x => ({ time: candles[x.index].time,
      position: x.direction === "bull" ? "belowBar" : "aboveBar", color: x.direction === "bull" ? "#4ade80" : "#f87171",
      shape: x.direction === "bull" ? "arrowUp" : "arrowDown", text: `SWEEP ${x.band_count}` }));
    state.series.candles.setMarkers(markers);
  }

  function renderActiveIndicators() {
    const host = $("activeIndicators"); if (!host) return;
    host.innerHTML = INDICATORS.filter(ind => ind.on).map(ind => `<button class="workspace__active-chip" type="button" data-remove-indicator="${ind.id}" title="Grafikten kaldır">${VX.esc(ind.name)}<i>×</i></button>`).join("") || `<span class="workspace__muted">Grafikte aktif gösterge yok</span>`;
    host.querySelectorAll("[data-remove-indicator]").forEach(btn => btn.addEventListener("click", () => {
      const ind = INDICATORS.find(x => x.id === btn.dataset.removeIndicator); if (!ind) return;
      ind.on = false; renderIndicatorButtons($("indicatorSearch")?.value || ""); applyIndicatorVisibility();
    }));
  }

  function renderIndicatorButtons(query = "") {
    const needle = query.trim().toLocaleLowerCase("tr-TR");
    const visible = INDICATORS.filter(ind => !needle || `${ind.name} ${ind.desc}`.toLocaleLowerCase("tr-TR").includes(needle));
    $("indicatorToggles").innerHTML = visible.map(ind => `
      <button class="workspace__indicator ${ind.on ? "is-on" : ""}" data-indicator="${ind.id}" title="${VX.esc(ind.desc)}">
        <b>${ind.on ? "✓ " : "＋ "}${VX.esc(ind.name)}</b><span>${VX.esc(ind.desc)}</span><small class="workspace__indicator-kind">${ind.pane ? "Alt panel" : "Fiyat üstü"}</small>
      </button>`).join("");
    $("indicatorToggles").querySelectorAll("button").forEach(btn => btn.addEventListener("click", () => {
      const ind = INDICATORS.find(x => x.id === btn.dataset.indicator); ind.on = !ind.on;
      renderIndicatorButtons($("indicatorSearch")?.value || ""); applyIndicatorVisibility();
    }));
  }

  function seriesData(times, values) {
    return (values || []).map((v, i) => v === null || v === undefined ? null : ({ time: times[i], value: v })).filter(Boolean);
  }

  function human(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.length ? value.slice(0, 4).map(v => typeof v === "object" ? JSON.stringify(v) : v).join(" · ") : "Yok";
    if (typeof value === "object") return Object.entries(value).slice(0, 6).map(([k, v]) => `${k}: ${human(v)}`).join(" · ");
    return String(value);
  }

  function p(value) { return value === null || value === undefined ? "—" : VX.fmtPrice(Number(value)); }
  function pct(value, digits = 2) { return value === null || value === undefined ? "—" : `%${Number(value).toFixed(digits)}`; }
  function item(label, value, sub = "") {
    return `<div class="workspace__finding"><div class="workspace__finding-label">${VX.esc(label)}</div><div class="workspace__finding-value">${value}</div>${sub ? `<div class="workspace__muted">${sub}</div>` : ""}</div>`;
  }

  function renderAnalysisSummary(snap) {
    const structure = snap.structure || {}; const findings = snap.findings || {}; const levels = snap.levels || {};
    const last = structure.last_event;
    const eventText = last ? `<b>${last.kind}</b> · ${last.direction === "bull" ? "yukarı" : "aşağı"} · ${p(last.price)}` : "Belirgin kırılım yok";
    const events = (structure.events || []).slice(-4).map(e => `${e.kind} ${e.direction === "bull" ? "↑" : "↓"} ${p(e.price)}`).join(" · ") || "Yakın olay yok";
    const sweeps = findings.sweeps || []; const hunter = findings.liquidity_hunter || {};
    const gaps = [...(findings.fvg_active || []), ...(findings.fvg_inversed || [])];
    const sr = (levels.support_resistance || []).slice().sort((a,b) => (b.strength||0)-(a.strength||0)).slice(0,4);
    const vp = levels.volume_profile || {}; const ctx = snap.context || {}; const funding = ctx.funding || {}; const oi = ctx.open_interest || {}; const interpretation = oi.interpretation || {};
    $("analysisSummary").innerHTML = [
      item("Piyasa yapısı", `<span class="tag ${structure.bias === "bull" ? "tag--long" : structure.bias === "bear" ? "tag--short" : ""}">${structure.bias === "bull" ? "BOĞA" : structure.bias === "bear" ? "AYI" : "NÖTR"}</span> ${eventText}`, events),
      item("Likidite süpürmeleri", sweeps.length ? sweeps.slice(-3).map(x => `${x.direction === "bull" ? "Alt likidite" : "Üst likidite"} · ${p(x.price || x.level)}`).join("<br>") : "Son 30 mumda doğrulanmış süpürme yok", `${sweeps.length} güncel bulgu`),
      item("Liquidity Sweep Hunter", (hunter.signals || []).length ? (hunter.signals || []).slice(-3).map(x => `${x.direction === "bull" ? "Boğa" : "Ayı"} dönüşü · ${x.band_count} bant · geri alım ${p(x.reclaim_level)} · güç ${x.strength}`).join("<br>") : "Çoklu bant süpürme dönüşü yok", `${(hunter.lower_levels || []).length} alt · ${(hunter.upper_levels || []).length} üst aktif bant`),
      item("FVG / IFVG", gaps.length ? gaps.slice(-4).map(x => `${VX.esc(x.direction || "bölge")} · ${p(x.bottom)} – ${p(x.top)}`).join("<br>") : "Aktif FVG / IFVG yok", `${(findings.fvg_active || []).length} aktif · ${(findings.fvg_inversed || []).length} tersine dönmüş`),
      item("Güçlü destek / direnç", sr.length ? sr.map(x => `${x.kind === "support" ? "Destek" : "Direnç"} <b>${p(x.price)}</b> · ${x.touches || 0} temas · uzaklık ${pct(x.distance_pct)}`).join("<br>") : "Seviye üretilemedi"),
      item("Hacim profili", `POC <b>${p(vp.poc)}</b> · VAH ${p(vp.vah)} · VAL ${p(vp.val)}`, "POC en yoğun işlem bölgesi; tek başına yön sinyali değildir."),
      item("Vadeli piyasa bağlamı", `Funding ${pct(funding.rate_pct, 4)} · OI ${oi.value_usd ? VX.fmtUsd(oi.value_usd, 0) : "—"} · 24s OI ${pct(oi.change_24h_pct)}`, `<b>${VX.esc(interpretation.tr || "Belirgin değil")}</b> · ${VX.esc(interpretation.note || "")}`),
    ].join("");
  }

  function renderDetailedAnalysis(snap) {
    const d = snap.detailed_analysis || {};
    if (!d.summary) { $("analysisNarrative").innerHTML = `<div class="empty">Detaylı analiz üretilemedi</div>`; return; }
    const bullets = values => `<ul>${(values || []).map(value => `<li>${VX.esc(value)}</li>`).join("")}</ul>`;
    const metrics = d.metrics || {};
    $("analysisNarrative").innerHTML = `
      <div class="workspace__narrative-hero is-${VX.esc(d.direction_code || "neutral")}">
        <div><h3>${VX.esc(d.direction || "Kararsız")}</h3><span class="tag">${VX.esc(d.quality || "—")}</span></div>
        <p>${VX.esc(d.summary)}</p>
        <div class="workspace__active-indicators" style="margin-top:10px">
          <span class="chip">Destek ${VX.esc(metrics.support || "—")}</span><span class="chip">Direnç ${VX.esc(metrics.resistance || "—")}</span><span class="chip">RSI ${VX.esc(metrics.rsi || "—")}</span><span class="chip">ADX ${VX.esc(metrics.adx || "—")}</span>
        </div>
      </div>
      <div class="workspace__narrative-grid">
        <section class="workspace__narrative-card"><h4>Ana tezi destekleyenler</h4>${bullets(d.evidence_for)}</section>
        <section class="workspace__narrative-card"><h4>Karşı tez / çelişkiler</h4>${bullets(d.evidence_against)}</section>
        <section class="workspace__narrative-card"><h4>Yükseliş senaryosu</h4><p>${VX.esc(d.bull_scenario || "—")}</p></section>
        <section class="workspace__narrative-card"><h4>Düşüş senaryosu</h4><p>${VX.esc(d.bear_scenario || "—")}</p></section>
        <section class="workspace__narrative-card"><h4>Teyit için ne gerekli?</h4><p>${VX.esc(d.confirmation || "—")}</p></section>
        <section class="workspace__narrative-card"><h4>Tez nerede bozulur?</h4><p>${VX.esc(d.invalidation || "—")}</p></section>
        <section class="workspace__narrative-card workspace__narrative-card--risk" style="grid-column:1/-1"><h4>Kör noktalar ve riskler</h4>${bullets(d.blind_spots)}</section>
      </div>`;
  }

  function renderTradePlan(plan) {
    if (!plan) { $("tradePlan").innerHTML = `<div class="empty">Plan üretilemedi</div>`; return; }
    $("planScore").className = `tag ${plan.qualified ? "tag--long" : "tag--warn"}`;
    $("planScore").textContent = `${plan.score}/${plan.max_score} ${plan.qualified ? "MOTOR UYUMLU" : "SADECE SENARYO"}`;
    $("tradePlan").innerHTML = `
      <div class="workspace__plan-cell"><span>Yön</span><b class="${plan.side === "LONG" ? "delta--up" : "delta--down"}">${plan.side}</b></div>
      <div class="workspace__plan-cell"><span>Giriş</span><b>${p(plan.entry)}</b></div>
      ${(plan.targets || []).map((x,i) => `<div class="workspace__plan-cell"><span>TP${i+1}</span><b class="delta--up">${p(x)}</b></div>`).join("")}
      <div class="workspace__plan-cell"><span>SL</span><b class="delta--down">${p(plan.stop)}</b></div>
      <div class="workspace__plan-reasons">${(plan.reasons || []).map(x => `<span class="chip">✓ ${VX.esc(x)}</span>`).join("") || `<span class="workspace__muted">Filtre uyumu zayıf; işlem sinyali değildir.</span>`}</div>`;
  }

  function renderIndicatorReadout(snap) {
    const i = snap.indicators || {};
    const rows = {
      ema: [`EMA 20/50/200`, `${p(i.ema20)} / ${p(i.ema50)} / ${p(i.ema200)}`, i.ema20 > i.ema50 ? "Kısa eğilim yukarı" : "Kısa eğilim aşağı"],
      vwap: ["VWAP", p(i.vwap), snap.price >= i.vwap ? "Fiyat VWAP üstünde" : "Fiyat VWAP altında"],
      bollinger: ["Bollinger", `${p(i.bb_lower)} – ${p(i.bb_upper)}`, i.bb_squeeze?.is_squeeze ? "Sıkışma var" : "Belirgin sıkışma yok"],
      supertrend: ["Supertrend", p(i.supertrend), i.supertrend_dir === 1 ? "Yukarı" : "Aşağı"],
      ichimoku: ["Ichimoku", `Tenkan ${p(i.ichimoku_tenkan)} · Kijun ${p(i.ichimoku_kijun)}`, snap.price >= Math.max(i.ichimoku_span_a || 0, i.ichimoku_span_b || 0) ? "Bulut üstü" : "Bulut içi/altı"],
      rsi: ["RSI 14", i.rsi14?.toFixed(2) ?? "—", i.rsi14 >= 70 ? "Aşırı alım" : i.rsi14 <= 30 ? "Aşırı satım" : "Nötr bant"],
      macd: ["MACD", `${human(i.macd)} / sinyal ${human(i.macd_signal)}`, i.macd_histogram > 0 ? "Pozitif histogram" : "Negatif histogram"],
      stochrsi: ["Stoch RSI", `K ${human(i.stoch_rsi_k)} · D ${human(i.stoch_rsi_d)}`, i.stoch_rsi_k >= 80 ? "Üst uç" : i.stoch_rsi_k <= 20 ? "Alt uç" : "Orta bant"],
      adx: ["ADX / DMI", `ADX ${human(i.adx14)} · +DI ${human(i.plus_di)} · −DI ${human(i.minus_di)}`, i.adx14 >= 25 ? "Trend güçlü" : "Trend zayıf/orta"],
      atr: ["ATR 14", `${p(i.atr14)} · ${pct(i.atr_pct)}`, "Yön değil volatilite ölçer"],
      liquidity: ["Liquidity Sweep", `${((((snap.findings || {}).liquidity_hunter || {}).signals) || []).length} sinyal`, `${((((snap.findings || {}).liquidity_hunter || {}).lower_levels) || []).length} alt · ${((((snap.findings || {}).liquidity_hunter || {}).upper_levels) || []).length} üst aktif bant`],
      orderflow: ["Order Flow", `Delta ${human(i.order_flow_delta_pct)}% · alış ${human(i.order_flow_buy_ratio)}%`, `20 mum delta ${human(i.order_flow_delta_20)} · CVD ${human(i.cvd)}`],
    };
    $("indicatorReadout").innerHTML = INDICATORS.filter(x => x.on).map(ind => {
      const x = rows[ind.id]; return `<div class="workspace__readout-card"><span>${VX.esc(x[0])}</span><b>${x[1]}</b><small>${VX.esc(x[2])}</small></div>`;
    }).join("") || `<div class="empty">Gösterge seçilmedi</div>`;
  }

  function renderSmc(smc) {
    const host = $("smcFlow"), phase = $("smcPhase");
    if (!host || !smc) return;
    if (smc.error) { phase.textContent = "Veri yok"; host.textContent = smc.error; return; }
    const ready = Boolean(smc.ready);
    phase.textContent = (smc.phase || "bekliyor").replaceAll("_", " ");
    phase.className = `tag ${ready ? "tag--long" : "tag--warn"}`;
    const steps = (smc.steps || []).map(step => `<div class="smc-flow__step ${step.ok ? "is-ok" : "is-wait"}"><i>${step.ok ? "✓" : "·"}</i><span>${VX.esc(step.label)}</span><b>${step.weight}</b></div>`).join("");
    const htf = smc.htf || {};
    host.innerHTML = `
      <div class="smc-flow__summary">
        <div><span>Model</span><b>${VX.esc((smc.model || "SMC").replaceAll("_", " "))}</b></div>
        <div><span>Yön / DOL</span><b class="${smc.side === "LONG" ? "delta--up" : smc.side === "SHORT" ? "delta--down" : ""}">${VX.esc(smc.side)} · ${VX.esc((htf.draw_on_liquidity || "—").replaceAll("_", " "))}</b></div>
        <div><span>Plan</span><b>${VX.fmtPrice(smc.entry)} / SL ${VX.fmtPrice(smc.stop)} / TP ${VX.fmtPrice(smc.target)}</b></div>
        <div><span>R:R / skor</span><b>${Number(smc.rr || 0).toFixed(2)}R · ${smc.score}/${smc.max_score}</b></div>
      </div>
      <div class="smc-flow__steps">${steps}</div>
      <div class="smc-flow__footer"><b>${ready ? "Sıralı teyit tamam" : "Henüz işlem hazır değil"}</b><span>${VX.esc((smc.missing || []).join(" · ") || smc.note || "")}</span></div>`;
  }

  let analysisRequest = 0;
  async function chartRequest(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try { return await VX.api(url, { signal: controller.signal }); }
    catch (error) {
      if (error.name === "AbortError") throw new Error("Veri kaynağı zamanında yanıt vermedi. Yeniden deneyin.");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function analysisLoad(message, retry = false) {
    const host = $("analysisLoadState");
    if (!host) return;
    host.hidden = !message;
    $("analysisLoadText").textContent = message;
    $("analysisRetry").hidden = !retry;
  }

  async function analyze() {
    const symbol = $("symbolInput").value.trim().toUpperCase().replace("/", "");
    const interval = $("intervalInput").value;
    if (!symbol) return VX.toast("Sembol yaz", "warn");
    const btn = $("analyzeBtn"); btn.disabled = true; btn.textContent = "Analiz ediliyor…";
    const request = ++analysisRequest, main = document.querySelector("main[data-page]");
    const current = () => request === analysisRequest && main === document.querySelector("main[data-page]");
    state.lastCandles = []; state.lastSnap = null;
    $("chartScreenshotBtn").disabled = true;
    analysisLoad(`${symbol} mum verileri yükleniyor…`);
    try {
      state.symbol = symbol; state.interval = interval;
      const smcLtf = ["5m", "15m", "30m", "1h"].includes(interval) ? interval : "15m";
      // Optional SMC analysis must never delay the price chart.
      if ($("smcPhase")) $("smcPhase").textContent = "Analiz yükleniyor…";
      if ($("smcFlow")) $("smcFlow").textContent = "SMC teyitleri ayrıca hesaplanıyor.";
      chartRequest(`/api/market/smc?symbol=${encodeURIComponent(symbol)}&htf=4h&ltf=${smcLtf}`)
        .catch(error => ({ error: error.message }))
        .then(smc => { if (current()) renderSmc(smc); });
      const snap = await chartRequest(`/api/market/snapshot?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=500&series=true`);
      if (!current()) return;
      if (!snap?.series?.candles?.length) throw new Error("Mum verisi alınamadı. Yeniden deneyin.");
      const s = snap.series || {}; state.lastSeries = s; const candles = s.candles || []; const times = candles.map(c => c.time);
      state.lastCandles = candles.map(c => ({ ...c }));
      state.series.candles.setData(candles);
      state.series.volume.setData(candles.map(c => ({ time: c.time, value: c.volume, color: c.close >= c.open ? "rgba(43,212,138,.22)" : "rgba(255,84,112,.22)" })));
      state.series.ema20.setData(seriesData(times, s.ema20)); state.series.ema50.setData(seriesData(times, s.ema50)); state.series.ema200.setData(seriesData(times, s.ema200)); state.series.vwap.setData(seriesData(times, s.vwap));
      state.series.bbUpper.setData(seriesData(times, s.bb_upper)); state.series.bbLower.setData(seriesData(times, s.bb_lower));
      state.series.supertrend.setData(seriesData(times, s.supertrend));
      state.series.ichiTenkan.setData(seriesData(times, s.ichimoku_tenkan)); state.series.ichiKijun.setData(seriesData(times, s.ichimoku_kijun));
      state.series.ichiA.setData(seriesData(times, s.ichimoku_span_a)); state.series.ichiB.setData(seriesData(times, s.ichimoku_span_b));
      state.series.flowDelta.setData((s.order_flow_delta_pct || []).map((v,i) => v === null || v === undefined ? null : ({ time: times[i], value: v, color: v >= 0 ? "rgba(43,212,138,.68)" : "rgba(255,84,112,.68)" })).filter(Boolean));
      state.priceLines.forEach(pl => state.series.candles.removePriceLine(pl));
      const sr = ((snap.levels || {}).support_resistance || []).slice().sort((a, b) => (b.strength || 0) - (a.strength || 0)).slice(0, 4);
      state.priceLines = sr.map(l => state.series.candles.createPriceLine({ price: l.price, color: l.kind === "support" ? "rgba(43,212,138,.45)" : "rgba(255,84,112,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.kind === "support" ? "D" : "R" }));
      state.chart.timeScale().fitContent();
      state.chart.timeScale().setVisibleRange({from:candles[Math.max(0,candles.length-140)].time,to:candles.at(-1).time});
      state.lastSnap = snap; applyIndicatorVisibility();
      const last = candles[candles.length - 1] || {}; const regime = snap.regime || {}; const ind = snap.indicators || {}; const structure = snap.structure || {};
      $("analysisTitle").textContent = symbol; $("analysisMeta").textContent = `${interval} · ${snap.bars || candles.length} bar`;
      $("analysisKpis").innerHTML = [
        kpi("Anlık fiyat", `<span id="analysisKpiPrice">${VX.fmtPrice(snap.price || last.close)}</span>`, "Binance WebSocket"),
        kpi("Piyasa rejimi", VX.esc(regime.label || regime.code || "—"), `ADX ${regime.adx ?? "—"}`),
        kpi("RSI 14", ind.rsi14 === null || ind.rsi14 === undefined ? "—" : Number(ind.rsi14).toFixed(2)),
        kpi("Yapı yönü", structure.bias === "bull" ? "BOĞA" : structure.bias === "bear" ? "AYI" : "NÖTR", snap.last_bar_closed === false ? "Son mum açık" : "Son mum kapalı"),
      ].join("");
      renderAnalysisSummary(snap); renderDetailedAnalysis(snap); renderTradePlan(snap.trade_plan); renderIndicatorReadout(snap);
      $("analysisLivePrice").textContent = VX.fmtPrice(snap.price || last.close);
      $("sendAnalysisBtn").disabled = false;
      $("chartScreenshotBtn").disabled = false;
      $("xAnalysisText").value = snap.x_post || ""; $("xPostCount").textContent = `${$("xAnalysisText").value.length} karakter`; $("copyXAnalysisBtn").disabled = !snap.x_post;
      VX.live.watch([symbol]);
      analysisLoad("");
    } catch (e) { if (current()) { analysisLoad("Grafik yüklenemedi: " + e.message, true); VX.toast("Analiz başarısız: " + e.message, "err", 7000); } }
    finally { btn.disabled = false; btn.textContent = "Analiz et"; }
  }

  function intervalSeconds(interval) {
    const unit = interval.slice(-1), value = parseInt(interval, 10) || 1;
    return value * (unit === "m" ? 60 : unit === "h" ? 3600 : unit === "d" ? 86400 : 60);
  }

  function applyAnalysisTicks(batch) {
    if (!state.lastSnap || !state.series.candles) return;
    const tick = batch.get(state.symbol); if (!tick) return;
    VX.paintPrice($("analysisLivePrice"), `analysis-head-${state.symbol}`, tick.price);
    VX.paintPrice($("analysisKpiPrice"), `analysis-kpi-${state.symbol}`, tick.price);
    if ($("analysisBidAsk")) $("analysisBidAsk").textContent = `Alış ${VX.fmtPrice(tick.bid)} · Satış ${VX.fmtPrice(tick.ask)} · ${new Date(tick.ts).toLocaleTimeString("tr-TR", {hour12:false})}`;
    const candles = state.lastCandles; if (candles.length) {
      const seconds = intervalSeconds(state.interval); const bucket = Math.floor((tick.ts / 1000) / seconds) * seconds;
      let bar = candles[candles.length - 1];
      if (bucket > bar.time) { bar = { time: bucket, open: bar.close, high: tick.price, low: tick.price, close: tick.price, volume: 0 }; candles.push(bar); }
      else { bar.high = Math.max(bar.high, tick.price); bar.low = Math.min(bar.low, tick.price); bar.close = tick.price; }
      state.series.candles.update(bar);
    }
    if ($("flowLiveDelta") && tick.flow_delta_usdt !== undefined) {
      $("flowLiveDelta").className = tick.flow_delta_usdt >= 0 ? "delta--up" : "delta--down";
      $("flowLiveDelta").textContent = `Açık 1dk Delta ${VX.fmtUsd(tick.flow_delta_usdt, 0)}`;
      $("flowLiveRatio").textContent = `Agresif alış %${Number(tick.flow_buy_ratio).toFixed(1)}`;
    }
  }

  let chartLibraryPromise;
  function ensureChartLibrary() {
    if (window.LightweightCharts?.createChart) return Promise.resolve();
    if (!chartLibraryPromise) {
      chartLibraryPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/vendor/lightweight-charts.standalone.production.js";
        let timer;
        const finish = (error) => {
          clearTimeout(timer);
          script.onload = script.onerror = null;
          if (error) { script.remove(); reject(error); }
          else resolve();
        };
        script.onload = () => finish(window.LightweightCharts?.createChart ? null : new Error("Grafik kütüphanesi başlatılamadı. Sayfayı yeniden açın."));
        script.onerror = () => finish(new Error("Grafik kütüphanesi yüklenemedi. Bağlantınızı kontrol edip yeniden deneyin."));
        timer = setTimeout(() => finish(new Error("Grafik kütüphanesi yüklenirken zaman aşımı. Yeniden deneyin.")), 15000);
        document.head.appendChild(script);
      }).catch(error => { chartLibraryPromise = null; throw error; });
    }
    return chartLibraryPromise;
  }

  function setupChartTerminal(main){
    const sidebar=$('chartSidebarToggle');
    const syncSidebar=()=>sidebar.setAttribute('aria-expanded',String(innerWidth<=850?main.classList.contains('is-sidebar-open'):!main.classList.contains('is-sidebar-hidden')));
    sidebar.addEventListener('click',()=>{main.classList.toggle(innerWidth<=850?'is-sidebar-open':'is-sidebar-hidden');syncSidebar();});
    window.addEventListener('resize',syncSidebar);VX.onTeardown(()=>window.removeEventListener('resize',syncSidebar));syncSidebar();
    const timeframe=()=>document.querySelectorAll('[data-chart-interval]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chartInterval===$('intervalInput').value)));
    timeframe();$('intervalInput').addEventListener('change',()=>{timeframe();analyze();});
    document.querySelectorAll('[data-chart-interval]').forEach(b=>b.addEventListener('click',()=>{$('intervalInput').value=b.dataset.chartInterval;timeframe();analyze();}));
    $('chartFitBtn').addEventListener('click',()=>state.chart?.timeScale().fitContent());
    const zoom=factor=>{const scale=state.chart?.timeScale(),r=scale?.getVisibleLogicalRange();if(!r)return;const span=Math.max(12,(r.to-r.from)*factor);scale.setVisibleLogicalRange({from:r.to-span,to:r.to});};
    $('chartZoomInBtn').addEventListener('click',()=>zoom(.7));$('chartZoomOutBtn').addEventListener('click',()=>zoom(1.4));
    $('chartCursorBtn').addEventListener('click',e=>{const btn=e.currentTarget,on=btn.getAttribute('aria-pressed')!=='true';btn.setAttribute('aria-pressed',String(on));state.chart?.applyOptions({crosshair:{vertLine:{visible:on,labelVisible:on},horzLine:{visible:on,labelVisible:on}}});});
    $('chartFullscreenBtn').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await main.requestFullscreen();}catch(e){VX.toast('Tam ekran tarayıcı tarafından açılamadı.','warn');}});
    const rowsEl=$('chartWatchRows'),search=$('chartWatchSearch');let rows=[],busy=false;
    const render=()=>{if(!rowsEl.isConnected)return;const query=search.value.trim().toUpperCase();const shown=rows.filter(r=>r.symbol.includes(query)).slice(0,15);
      rowsEl.innerHTML=shown.length?shown.map(r=>`<button type="button" class="chart-watch-row ${r.symbol===$('symbolInput').value?'is-selected':''}" data-watch-symbol="${VX.esc(r.symbol)}"><span>${VX.esc(r.symbol.replace('USDT',''))}</span><span data-chart-price="${VX.esc(r.symbol)}">${VX.fmtPrice(r.price)}</span><span class="${r.change>=0?'up':'down'}" data-chart-change="${VX.esc(r.symbol)}">${r.change>0?'+':''}${Number(r.change).toFixed(2)}%</span></button>`).join(''):'<div class="empty">Eşleşen piyasa yok.</div>';};
    const fetchWatch=async()=>{if(busy||!rowsEl.isConnected)return;busy=true;try{const d=await VX.get('/api/engine/smc/dashboard');if(!rowsEl.isConnected)return;rows=d.markets||[];render();}catch(e){if(rowsEl.isConnected&&!rows.length)rowsEl.innerHTML='<div class="empty">Piyasa listesine erişilemiyor.</div>';}finally{busy=false;}};
    search.addEventListener('input',render);rowsEl.addEventListener('click',e=>{const button=e.target.closest('[data-watch-symbol]');if(!button)return;$('symbolInput').value=button.dataset.watchSymbol;render();if(innerWidth<=850){main.classList.remove('is-sidebar-open');syncSidebar();}analyze();});
    VX.onTeardown(VX.live.onTick(batch=>{if(!rowsEl.isConnected)return;for(const [symbol,tick] of batch){if(tick.demo||VX.live.age(tick)>5000)continue;rowsEl.querySelectorAll(`[data-chart-price="${symbol}"]`).forEach(el=>el.textContent=VX.fmtPrice(tick.price));if(tick.change_24h!=null)rowsEl.querySelectorAll(`[data-chart-change="${symbol}"]`).forEach(el=>{el.textContent=`${tick.change_24h>0?'+':''}${Number(tick.change_24h).toFixed(2)}%`;el.className=tick.change_24h>=0?'up':'down';});}}));
    fetchWatch();VX.interval(()=>{if(!document.hidden)fetchWatch();},15000);
  }

  async function bootAnalysis() {
    const main = document.querySelector("main[data-page]");
    $("analysisRetry")?.addEventListener("click", () => {
      if (state.chart) analyze(); else location.reload();
    });
    await ensureChartLibrary();
    // Kullanıcı yükleme sırasında başka bir sayfaya geçmiş olabilir.
    if (main !== document.querySelector("main[data-page]")) return;
    buildAnalysisChart();
    buildOrderFlowChart();
    try {
      const saved = JSON.parse(localStorage.getItem("vortex.analysis.indicators") || "null");
      if (Array.isArray(saved)) INDICATORS.forEach(ind => { ind.on = saved.includes(ind.id); });
    } catch (_) {}
    renderIndicatorButtons(); applyIndicatorVisibility();
    const requestedSymbol = new URLSearchParams(window.location.search).get("symbol");
    if (requestedSymbol) $("symbolInput").value = requestedSymbol.toUpperCase();
    const requestedInterval = new URLSearchParams(window.location.search).get("interval");
    if (requestedInterval && [...$("intervalInput").options].some(o => o.value === requestedInterval)) {
      $("intervalInput").value = requestedInterval;
    }
    setupChartTerminal(main);
    $("analyzeBtn").addEventListener("click", analyze);
    $("symbolInput").addEventListener("keydown", e => { if (e.key === "Enter") analyze(); });
    $("indicatorMenuBtn").addEventListener("click", () => {
      const menu = $("indicatorMenu"); menu.hidden = !menu.hidden;
      if (!menu.hidden) { $("indicatorSearch").focus(); renderIndicatorButtons($("indicatorSearch").value); }
    });
    $("indicatorSearch").addEventListener("input", e => renderIndicatorButtons(e.target.value));
    // Global dinleyici: gezinmede sokulmezse her analiz ziyaretinde bir
    // kopya daha birikir ve kaldirilan DOM'a referans tutar.
    const disTikla = e => {
      const menu = $("indicatorMenu");
      if (!menu) return;
      if (!menu.hidden && !menu.contains(e.target) && e.target !== $("indicatorMenuBtn")) menu.hidden = true;
    };
    document.addEventListener("click", disTikla);
    VX.onTeardown(() => document.removeEventListener("click", disTikla));
    $("resetIndicatorsBtn").addEventListener("click", () => { INDICATORS.forEach(x => x.on = ["ema","vwap","rsi","macd","liquidity","orderflow"].includes(x.id)); renderIndicatorButtons(); applyIndicatorVisibility(); });
    $("sendAnalysisBtn").addEventListener("click", async () => {
      const snap = state.lastSnap; if (!snap) return;
      const structure = snap.structure || {}; const ind = snap.indicators || {};
      const fallback = [`<b>VORTEX · ${VX.esc(snap.symbol)} PİYASA ANALİZİ</b>`, `${VX.esc(snap.interval)} · fiyat <code>${VX.esc(snap.price)}</code>`, `Yapı: <b>${VX.esc((structure.bias || "neutral").toUpperCase())}</b>`, `RSI ${VX.esc(ind.rsi14 ?? "—")} · ADX ${VX.esc(ind.adx14 ?? "—")} · Order flow delta %${VX.esc(ind.order_flow_delta_pct ?? "—")}`, "", "<i>Piyasa bağlamı analizidir; giriş emri değildir.</i>"].join("\n");
      try { await VX.post("/api/telegram/send", { text: snap.analysis_report || fallback }); VX.toast("Piyasa analizi gruba gönderildi"); } catch(e) { VX.toast(e.message, "err", 7000); }
    });
    $("chartScreenshotBtn").addEventListener("click", captureAnalysisChart);
    $("downloadChart").addEventListener("click",()=>sharePackage("download"));
    $("shareAnalysis").addEventListener("click",()=>sharePackage("share"));
    $("copyAnalysisData").addEventListener("click",()=>sharePackage("data"));
    $("composeX").addEventListener("click",()=>sharePackage("x"));
    $("copyXAnalysisBtn").addEventListener("click", copyXAnalysisText);
    let searchTimer;
    $("symbolInput").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(async () => { try { const d = await VX.get(`/api/market/symbols?q=${encodeURIComponent($("symbolInput").value)}&limit=30`); $("symbolList").innerHTML = d.symbols.map(s => `<option value="${VX.esc(s.symbol)}"></option>`).join(""); } catch (_) {} }, 180); });
    VX.live.connect();
    VX.onTeardown(VX.live.onTick(applyAnalysisTicks));
    // GRAFIKLERI YIK. LightweightCharts DOM'a ResizeObserver ve olay
    // dinleyicileri bagliyor; <main> degistiginde node gider ama grafik
    // nesnesi kalir ve kaldirilmis bir kabi olcmeye calisir. Ayrica
    // state.chart bayat bir referans olarak durur ve sonraki ziyarette
    // "grafik zaten var" gibi gorunur.
    VX.onTeardown(() => {
      try { destroyIndicatorPanes(); } catch (e) { /* zaten yok */ }
      [state.chart, state.flowChart].forEach(c => { try { c && c.remove(); } catch (e) {} });
      state.chart = null; state.flowChart = null;
      state.series = {}; state.priceLines = []; state.liquidityLines = [];
      state.lastSeries = null; state.lastSnap = null; state.lastCandles = [];
    });
    await analyze();
  }

  async function sharePackage(mode) {
    if(!state.lastSnap||!state.chart)return VX.toast("Önce analizi yükle","warn");
    const snap=state.lastSnap;
    const text=`VORTEX · ${snap.symbol} · ${snap.interval}\n${new Date().toLocaleString('tr-TR')}\n${(snap.x_post||'Piyasa yapısı analizi.').slice(0,3500)}\nAraştırma çıktısı; işlem emri veya getiri garantisi değildir.`;
    try {
      if(mode==='x'){window.open('https://x.com/intent/post?text='+encodeURIComponent(`${snap.symbol} · ${snap.interval} VORTEX piyasa analizi\nFiyat: ${VX.fmtPrice(snap.price)}\nAraştırma çıktısı; işlem emri değildir.`),'_blank','noopener,noreferrer');return;}
      if(mode==='data'){await navigator.clipboard.writeText(JSON.stringify({symbol:snap.symbol,interval:snap.interval,captured_at:new Date().toISOString(),price:snap.price,indicators:snap.indicators,structure:snap.structure,note:'Araştırma verisi; getiri garantisi değildir.'},null,2));VX.toast('Analiz verileri kopyalandı');return;}
      const blob=await new Promise(resolve=>state.chart.takeScreenshot().toBlob(resolve,'image/png'));
      if(!blob)throw Error('Görsel oluşturulamadı');
      const filename=`VORTEX-${snap.symbol}-${snap.interval}.png`,file=new File([blob],filename,{type:'image/png'});
      if(mode==='share'&&navigator.canShare?.({files:[file]})){await navigator.share({title:`${snap.symbol} analizi`,text,files:[file]});return;}
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      if(mode==='share'){await navigator.clipboard.writeText(text);VX.toast('Grafik indirildi; analiz metni kopyalandı. İstediğin platforma ekleyebilirsin.');}
    }catch(e){if(e.name!=='AbortError')VX.toast('Paylaşım tamamlanamadı: '+e.message,'warn');}
  }

  window.addEventListener('vortex:briefing',event=>{
    const d=event.detail;
    if(!document.getElementById('newsPageList')||!d.items?.length)return;
    state.news=d.items;
    document.getElementById('newsMeta').textContent=`${d.items.length} başlık · RSS 60 sn · kaynak gecikmesi değişken · ${VX.fmtTime(d.news_fetched)}`;
    document.getElementById('newsPageList').innerHTML=d.items.map(n=>`<a class="workspace__feed-item" href="${/^https?:\/\//i.test(n.link||'')?VX.esc(n.link):'#'}" target="_blank" rel="noopener noreferrer"><div class="workspace__feed-title">${n.important?'❗ ':''}${VX.esc(n.title)}</div><div class="workspace__feed-meta">${VX.esc(n.source)} · ${VX.fmtAgo(n.published)}${n.important?' · ekonomik anahtar kelime eşleşmesi':''}</div></a>`).join('');
  });

  async function copyXAnalysisText() {
    const box = $("xAnalysisText"); if (!box.value) return VX.toast("Önce analiz çalıştır", "warn");
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(box.value);
      else { box.focus(); box.select(); document.execCommand("copy"); box.setSelectionRange(0, 0); }
      VX.toast("X analiz metni panoya kopyalandı");
    } catch (e) { VX.toast("Metin kopyalanamadı: " + e.message, "err", 7000); }
  }

  async function captureAnalysisChart() {
    if (!state.chart || !state.lastSnap || typeof state.chart.takeScreenshot !== "function") return VX.toast("Grafik görüntüsü henüz hazır değil", "warn");
    const btn = $("chartScreenshotBtn"); btn.disabled = true; btn.textContent = "Hazırlanıyor…";
    try {
      const chartCanvas = state.chart.takeScreenshot(); const header = 76;
      const out = document.createElement("canvas"); out.width = chartCanvas.width; out.height = chartCanvas.height + header;
      const ctx = out.getContext("2d"); ctx.fillStyle = "#05070a"; ctx.fillRect(0, 0, out.width, out.height); ctx.drawImage(chartCanvas, 0, header);
      const snap = state.lastSnap; const live = VX.live.price(state.symbol) || snap.price;
      ctx.textBaseline = "middle"; ctx.fillStyle = "#f2f5f8"; ctx.font = "700 22px Inter, Arial, sans-serif"; ctx.fillText(`${snap.symbol} · ${snap.interval}`, 22, 28);
      ctx.fillStyle = "#ededed"; ctx.font = "700 20px ui-monospace, Consolas, monospace"; const priceText = VX.fmtPrice(live); const pw = ctx.measureText(priceText).width; ctx.fillText(priceText, Math.max(22, out.width - pw - 22), 28);
      ctx.fillStyle = "#7f8996"; ctx.font = "500 12px Inter, Arial, sans-serif"; ctx.fillText(`VORTEX PİYASA ANALİZİ · ${new Date().toLocaleString("tr-TR")}`, 22, 57);
      const blob = await new Promise((resolve, reject) => out.toBlob(x => x ? resolve(x) : reject(new Error("PNG üretilemedi")), "image/png", 1));
      if (!navigator.clipboard || !window.ClipboardItem || !window.isSecureContext) throw new Error("Tarayıcı görsel panosuna izin vermiyor");
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      VX.toast("Grafik PNG olarak panoya kopyalandı");
    } catch (e) {
      try {
        const chartCanvas = state.chart.takeScreenshot(); const blob = await new Promise(resolve => chartCanvas.toBlob(resolve, "image/png")); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `VORTEX-${state.symbol}-${state.lastSnap.interval}.png`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        VX.toast("Tarayıcı panoyu engelledi; mail açılmadı, PNG indirildi", "warn", 7000);
      } catch (_) { VX.toast("SS panoya kopyalanamadı: " + e.message, "err", 7000); }
    }
    finally { btn.disabled = false; btn.textContent = "SS kopyala"; }
  }

  /* Beklenti rozeti. İKİ AYRI güçte okuma var ve karıştırılmamalı:
       - aşırı bölgedeyse: beş bileşenli KARAR (dolu rozet)
       - eşiği geçmiyorsa: tek bileşenli TREND gözlemi (soluk rozet)
     İkisini aynı görünümde vermek, zayıf bir gözlemi güçlü bir karar
     gibi okutur. */
  function beklentiRozeti(ctx) {
    if (!ctx) return `<span class="evidence">—</span>`;
    if (ctx.expect) {
      const yukari = ctx.expect === "up";
      return `<span class="tag ${yukari ? "tag--long" : "tag--short"}" title="Aşırı bölge kararı · skor ${ctx.score}">`
        + `${yukari ? "▲" : "▼"} ${VX.esc(ctx.label)}</span>`;
    }
    if (ctx.zone !== "neutral") {
      return `<span class="tag" title="Bileşenler birbirini götürüyor · skor ${ctx.score}">Belirsiz</span>`;
    }
    const t = ctx.trend;
    if (!t) return `<span class="evidence" title="t=${ctx.t_stat ?? "—"}">${VX.esc(ctx.trend_label || "Yönsüz")}</span>`;
    return `<span class="evidence ${t === "up" ? "delta--up" : "delta--down"}" title="Aşırı bölgede değil — bu bir karar değil, trend gözlemi. t=${ctx.t_stat}">`
      + `${t === "up" ? "▲" : "▼"} ${VX.esc(ctx.trend_label)}</span>`;
  }

  function analysisHref(symbol, interval = "1h") {
    return `/analiz?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`;
  }

  function rsiRows(items) {
    return items.map(x => `<tr><td><a class="sym" href="${analysisHref(x.symbol)}">${VX.coinIcon(x.symbol, 20)}<span>${VX.esc(x.symbol)}</span></a></td><td class="right">${VX.fmtPrice(x.price)}</td><td class="right"><b class="${x.rsi <= 30 ? "delta--up" : x.rsi >= 70 ? "delta--down" : ""}">${x.rsi.toFixed(2)}</b><div class="workspace__rsi-bar"><div class="workspace__rsi-fill" style="width:${Math.min(100, Math.max(0, x.rsi))}%"></div></div></td><td>${beklentiRozeti(x.ctx)}</td><td class="right"><a class="btn btn--sm rsi-analyze-btn" href="${analysisHref(x.symbol)}">Analiz yap</a></td></tr>`);
  }

  /* KATMANLI RSI. Her satirin KARARI ve GEREKCESI birlikte gosteriliyor:
     karari gerekcesinden ayirmak, kullaniciyi bir sayiya guvenmeye
     zorlamak olurdu. Ustte de siniflandiricinin KENDI isabet karnesi —
     %50 yazi-turadir, altinda kalan bir siniflandirici bilgi uretmiyordur. */
  function renderRsiContext(d) {
    const box = $("rsiContextBody");
    if (!box) return;
    const list = d.context || [];
    const sc = d.context_scorecard || {};

    const karne = sc.n
      ? `<div class="workspace__muted" style="margin-bottom:10px">
           Bu sınıflandırıcının kendi karnesi · <b>${sc.n}</b> sonuçlanmış çağrı ·
           isabet <b class="${sc.better_than_coinflip ? "delta--up" : "delta--down"}">%${sc.accuracy}</b>
           ${sc.better_than_coinflip ? "" : " — yazı-turanın altında, henüz bilgi üretmiyor"}
           ${Object.entries(sc.by_verdict || {}).map(([k, v]) =>
             ` · ${k === "devam" ? "devam" : "tükenme"} %${v.accuracy} (${v.n})`).join("")}
           ${sc.pending ? ` · ${sc.pending} çağrı sonucu bekliyor` : ""}
         </div>`
      : `<div class="workspace__warning" style="margin-bottom:10px">
           <b>Henüz ölçülmüş sonuç yok.</b> ${VX.esc(sc.note || "")}
           Her karar kaydediliyor ve 12 bar sonra fiyata bakılıp isabet edip etmediği işaretleniyor;
           sayı birikince buraya isabet oranı gelecek.
         </div>`;

    if (!list.length) {
      setHTML("rsiContextBody", karne + `<div class="empty">Şu an aşırı bölgede kontrat yok.</div>`);
      setText("rsiCtxMeta", "aşırı bölgede 0 kontrat");
      return;
    }

    /* RENK, kararın adına göre değil BEKLENEN FİYAT YÖNÜNE göre.
       "devam" aşırı alımda yükseliş, aşırı satımda DÜŞÜŞ demek —
       ikisini de yeşil göstermek aşırı satımdaki düşüş beklentisini
       iyi haber gibi okutuyordu. */
    const rozet = (c) => {
      if (!c.expect) return `<span class="tag">Belirsiz</span>`;
      const yukari = c.expect === "up";
      return `<span class="tag ${yukari ? "tag--long" : "tag--short"}">`
        + `${yukari ? "▲" : "▼"} ${VX.esc(c.label || "")}</span>`;
    };

    /* Sirala: once net kararlar (mutlak skoru buyuk olanlar), sonra belirsizler.
       Belirsiz bir satiri en uste koymak, en az bilgi taşıyanı öne çıkarırdı. */
    const sirali = [...list].sort((a, b) => Math.abs(b.score) - Math.abs(a.score));

    const satirlar = sirali.map(c => {
      const bolge = c.zone === "overbought" ? "aşırı alım" : "aşırı satım";
      const kanit = (c.evidence || []).map(e =>
        `<span class="workspace__target" title="${VX.esc(e.aciklama)}">
           <b class="${e.katki > 0 ? "delta--up" : e.katki < 0 ? "delta--down" : ""}">${e.katki > 0 ? "+" : ""}${e.katki}</b>
           ${VX.esc(e.ad)}</span>`).join(" ");
      return `<tr>
        <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(c.symbol)}">${VX.coinIcon(c.symbol, 20)}<span>${VX.esc(c.symbol)}</span></a></td>
        <td class="right"><b>${c.rsi}</b><div style="font-size:10.5px;color:var(--ink-3)">${bolge}</div></td>
        <td>${rozet(c)}</td>
        <td class="right ${c.score > 0 ? "delta--up" : c.score < 0 ? "delta--down" : ""}">${c.score > 0 ? "+" : ""}${c.score}</td>
        <td class="right">${c.t_stat ?? "—"}</td>
        <td class="right">${c.stretch_atr ?? "—"}</td>
        <td class="right">${c.persistence}/${c.persist_window}</td>
        <td>${c.divergence ? `<span class="tag tag--warn">var</span>` : "—"}</td>
        <td class="right">${c.funding_bp ?? "—"}</td>
        <td>${kanit || "—"}</td>
      </tr>`;
    });

    setHTML("rsiContextBody", karne + table(
      ["Sembol", "RSI", "Karar", "Skor", "t", "Gerilme (ATR)", "Kalıcılık", "Uyumsuzluk", "Fonlama (bp)", "Gerekçe"],
      satirlar));
    const yuk = list.filter(c => c.expect === "up").length;
    const dus = list.filter(c => c.expect === "down").length;
    const bel = list.filter(c => !c.expect).length;
    setText("rsiCtxMeta",
      `${list.length} kontrat aşırı bölgede · ${yuk} yükseliş · ${dus} düşüş · ${bel} belirsiz`);
  }

  async function scanRsi() {
    const btn = $("scanRsiBtn"); btn.disabled = true; btn.textContent = "Taranıyor…";
    try {
      const d = await VX.get(`/api/market/rsi-radar?interval=${$("rsiInterval").value}&limit=80`);
      const dist = d.distribution || {}; state.rsiData = d;
      $("rsiAverage").textContent = `Ortalama ${d.average_rsi ?? "—"}`; $("rsiCount").textContent = `${d.count} likit kontrat`;
      $("oversoldCount").textContent = `≤30: ${dist.oversold || 0}`; $("overboughtCount").textContent = `≥70: ${dist.overbought || 0}`;
      $("rsiKpis").innerHTML = [kpi("Ortalama RSI", d.average_rsi ?? "—"), kpi("Medyan RSI", d.median_rsi ?? "—"), kpi("Aşırı satım", dist.oversold || 0, "RSI ≤ 30"), kpi("Aşırı alım", dist.overbought || 0, "RSI ≥ 70")].join("");
      const buckets = [["Aşırı satım",dist.oversold,"#4ade80"],["Zayıf",dist.weak,"#8a8a8a"],["Nötr",dist.neutral,"#6a6a6a"],["Güçlü",dist.strong,"#8a8a8a"],["Aşırı alım",dist.overbought,"#f87171"]];
      $("rsiDistribution").innerHTML = buckets.map(x => `<div class="workspace__dist-item"><div><span>${x[0]}</span><b>${x[1] || 0}</b></div><div class="workspace__dist-track"><i style="width:${d.count ? (x[1]||0)/d.count*100 : 0}%;background:${x[2]}"></i></div></div>`).join("");
      $("lowestList").innerHTML = table(["Sembol", "Fiyat", "RSI", "Beklenti", ""], rsiRows(d.lowest || []));
      $("highestList").innerHTML = table(["Sembol", "Fiyat", "RSI", "Beklenti", ""], rsiRows(d.highest || []));
      renderRsiContext(d);
    } catch (e) { VX.toast(e.message, "err"); } finally { btn.disabled = false; btn.textContent = "Radarı yenile"; }
  }

  async function loadNews() {
    const d = await VX.get("/api/news?limit=50"); state.news = d.items || []; $("newsMeta").textContent = `${state.news.length} haber · ${[...new Set(state.news.map(n => n.source))].join(", ")}`; $("newsPageList").innerHTML = state.news.length ? state.news.map(n => `<a class="workspace__feed-item" href="${VX.esc(n.link)}" target="_blank" rel="noopener"><div class="workspace__feed-title">${VX.esc(n.title)}</div><div class="workspace__feed-meta">${VX.esc(n.source)} · ${VX.fmtAgo(n.published)}</div></a>`).join("") : `<div class="empty">Haber alınamadı</div>`;
  }

  /* Takvim etki rozeti — GÖRÜNÜM Türkçe, VERİ İngilizce kalıyor.
     e.impact ham değeri sendCalendar() içinde `=== "High"` ile
     süzülüyor; veriyi çevirseydim o süzgeç sessizce hiçbir olay
     bulamaz hale gelirdi. Üç seviye üç AYRI görünüm alıyor: üçü de
     aynı gri çip olduğunda "etki" sütunu hiçbir şey söylemiyordu. */
  function etkiRozeti(impact) {
    const harita = {
      High:   ["Yüksek", "tag--warn"],
      Medium: ["Orta", "tag--info"],
      Low:    ["Düşük", ""],
    };
    const c = harita[impact] || [impact || "—", ""];
    return `<span class="tag ${c[1]}">${VX.esc(c[0])}</span>`;
  }

  async function loadCalendar() {
    const d = await VX.get("/api/calendar"); state.calendar = (d.events || []).filter(e => e.time > Date.now() - 3600000); const rows = state.calendar.map(e => `<tr><td>${VX.fmtTime(e.time)}</td><td><b>${VX.esc(e.currency)}</b></td><td>${VX.esc(e.title)}</td><td>${etkiRozeti(e.impact)}</td><td>${VX.esc(e.forecast || "—")}</td><td>${VX.esc(e.previous || "—")}</td></tr>`); $("calendarPageList").innerHTML = table(["Zaman", "Para", "Olay", "Etki", "Beklenti", "Önceki"], rows);
  }

  async function sendNews() { if (!state.news.length) return VX.toast("Gönderilecek haber yok", "warn"); const lines = ["<b>VORTEX · Gündem</b>", "", ...state.news.slice(0, 8).map((n, i) => `${i + 1}. <a href="${n.link}">${VX.esc(n.title)}</a> <i>(${VX.esc(n.source)})</i>`)]; try { await VX.post("/api/telegram/send", { text: lines.join("\n") }); VX.toast("Telegram grubuna gönderildi"); } catch (e) { VX.toast(e.message, "err", 7000); } }
  async function sendCalendar() { const items = state.calendar.filter(e => e.impact === "High").slice(0, 10); if (!items.length) return VX.toast("Kritik olay yok", "warn"); const lines = ["<b>VORTEX · Ekonomik Takvim</b>", "", ...items.map(e => `⚡ <b>${VX.esc(e.title)}</b> (${VX.esc(e.currency)}) · ${VX.fmtTime(e.time)}`)]; try { await VX.post("/api/telegram/send", { text: lines.join("\n") }); VX.toast("Telegram grubuna gönderildi"); } catch (e) { VX.toast(e.message, "err", 7000); } }

  /* GUVENLI DOM YAZIMI — 28.08.
     Sayfalari (Copy Trade / Karne / Sistem Testleri) ayirdiktan sonra ayni
     yukleyici birden fazla sayfada calisiyor ama her sayfada her eleman yok.
     Dagitik "if ($(x))" kontrolleri kacak veriyordu; tek kapi daha guvenli. */
  const setHTML = (id, html) => { const e = $(id); if (e) e.innerHTML = html; };
  const setText = (id, txt) => { const e = $(id); if (e) e.textContent = txt; };

  /* ------------------------------------------------ Piyasa Tarayıcı */
  const screenerFrame = (row, interval) => (row.frames || {})[interval] || {};
  const derivativesFrame = row => row.derivatives || {};
  const pctCell = value => {
    const n = Number(value);
    return Number.isFinite(n)
      ? `<b class="${VX.deltaClass(n)}">${n > 0 ? "+" : ""}${n.toFixed(2)}%</b>` : "—";
  };
  const rsiCell = value => {
    const n = Number(value);
    const cls = n >= 70 ? "is-hot" : n <= 30 ? "is-cold" : n >= 55 ? "is-up" : n <= 45 ? "is-down" : "";
    return Number.isFinite(n) ? `<b class="market-rsi ${cls}">${n.toFixed(1)}</b>` : "—";
  };
  const signalCell = value => {
    const map = {
      bull: ["Yükseliş", "is-bull"], bear: ["Düşüş", "is-bear"],
      inside: ["İçinde", "is-neutral"], above: ["Üstünde", "is-bull"], below: ["Altında", "is-bear"],
    };
    const item = map[value] || ["—", "is-neutral"];
    return `<span class="market-signal ${item[1]}">${item[0]}</span>`;
  };
  const coinCell = row => `<div class="market-coin">${VX.coinIcon(row.symbol, 25)}<div><b>${VX.esc(row.symbol.replace("USDT", ""))}${row.is_new ? `<span class="market-new-badge">YENİ</span>` : ""}</b><small>${VX.esc(row.symbol)} · ${VX.esc(row.volume_label || "Hacim ölçülüyor")}</small></div></div>`;
  const analyzeCell = row => `<a class="btn btn--sm market-analyze" href="/analiz?symbol=${encodeURIComponent(row.symbol)}&interval=1h">Analiz yap</a>`;

  function marketRowsForView() {
    const needle = (($("marketScreenerSearch") || {}).value || "").trim().toUpperCase();
    let rows = state.marketRows.filter(row => !needle || row.symbol.includes(needle));
    if (state.marketFilter === "high") rows = rows.filter(row => row.volume_tier === "high");
    else if (state.marketFilter === "medium") rows = rows.filter(row => row.volume_tier === "medium");
    else if (state.marketFilter === "emerging") rows = rows.filter(row => row.volume_tier === "emerging");
    else if (state.marketFilter === "new") rows = rows.filter(row => row.is_new);
    else if (state.marketFilter === "up") rows = rows.filter(row => Number(row.change_24h) > 0)
      .sort((a, b) => Number(b.change_24h) - Number(a.change_24h));
    else if (state.marketFilter === "down") rows = rows.filter(row => Number(row.change_24h) < 0)
      .sort((a, b) => Number(a.change_24h) - Number(b.change_24h));
    return rows;
  }

  function renderMarketScreener() {
    const rows = marketRowsForView();
    const common = row => `<td class="market-rank">${row.rank}</td><td>${coinCell(row)}</td><td class="right num"><b>${VX.fmtPrice(row.price)}</b></td>`;
    const end = row => `<td class="right">${analyzeCell(row)}</td>`;
    let headers = [], body = [];

    if (state.marketView === "rsi") {
      headers = ["#", "Coin", "Fiyat", "RSI 15D", "RSI 1S", "RSI 4S", "RSI 1G", "1S %", "4S %", "24S %", "7G %", ""];
      body = rows.map(row => `<tr>${common(row)}<td>${rsiCell(screenerFrame(row,"15m").rsi)}</td><td>${rsiCell(screenerFrame(row,"1h").rsi)}</td><td>${rsiCell(screenerFrame(row,"4h").rsi)}</td><td>${rsiCell(screenerFrame(row,"1d").rsi)}</td><td>${pctCell(screenerFrame(row,"1h").change_pct)}</td><td>${pctCell(screenerFrame(row,"4h").change_pct)}</td><td>${pctCell(row.change_24h)}</td><td>${pctCell(row.change_7d)}</td>${end(row)}</tr>`);
    } else if (state.marketView === "macd") {
      headers = ["#", "Coin", "Fiyat", "MACD 15D", "MACD 1S", "MACD 4S", "MACD 1G", "BB 15D", "BB 1S", "BB 4S", "RSI 1S", ""];
      body = rows.map(row => `<tr>${common(row)}<td>${signalCell(screenerFrame(row,"15m").macd)}</td><td>${signalCell(screenerFrame(row,"1h").macd)}</td><td>${signalCell(screenerFrame(row,"4h").macd)}</td><td>${signalCell(screenerFrame(row,"1d").macd)}</td><td>${signalCell(screenerFrame(row,"15m").bb)}</td><td>${signalCell(screenerFrame(row,"1h").bb)}</td><td>${signalCell(screenerFrame(row,"4h").bb)}</td><td>${rsiCell(screenerFrame(row,"1h").rsi)}</td>${end(row)}</tr>`);
    } else if (state.marketView === "ema") {
      headers = ["#", "Coin", "Fiyat", "EMA 15D", "EMA 1S", "EMA 4S", "EMA 1G", "Trend 1S", "Trend 4S", "RSI 1S", "24S %", ""];
      body = rows.map(row => `<tr>${common(row)}<td>${signalCell(screenerFrame(row,"15m").ema)}</td><td>${signalCell(screenerFrame(row,"1h").ema)}</td><td>${signalCell(screenerFrame(row,"4h").ema)}</td><td>${signalCell(screenerFrame(row,"1d").ema)}</td><td>${signalCell(screenerFrame(row,"1h").trend)}</td><td>${signalCell(screenerFrame(row,"4h").trend)}</td><td>${rsiCell(screenerFrame(row,"1h").rsi)}</td><td>${pctCell(row.change_24h)}</td>${end(row)}</tr>`);
    } else if (state.marketView === "derivatives") {
      headers = ["#", "Coin", "Fiyat", "Funding bp", "OI değeri", "OI 24S %", "Fiyat 24S %", "Bağlam", ""];
      body = rows.map(row => {
        const d = derivativesFrame(row);
        const funding = Number(d.funding_bp);
        const fundingCell = Number.isFinite(funding) ? `<b class="${funding > 0 ? "delta--up" : funding < 0 ? "delta--down" : ""}">${funding > 0 ? "+" : ""}${funding.toFixed(2)}</b>` : "—";
        return `<tr>${common(row)}<td>${fundingCell}</td><td class="right num">${Number.isFinite(Number(d.open_interest_usd)) ? VX.fmtUsd(d.open_interest_usd, 0) : "—"}</td><td>${pctCell(d.oi_change_pct)}</td><td>${pctCell(d.price_change_pct)}</td><td><span class="market-signal is-neutral">${VX.esc(d.oi_context || "Veri yok")}</span></td>${end(row)}</tr>`;
      });
    } else if (state.marketView === "day") {
      headers = ["#", "Coin", "Fiyat", "RSI 15D", "MACD 15D", "Trend 15D", "15D %", "1S %", "ATR 15D", "BB 15D", "24S %", ""];
      body = rows.map(row => `<tr>${common(row)}<td>${rsiCell(screenerFrame(row,"15m").rsi)}</td><td>${signalCell(screenerFrame(row,"15m").macd)}</td><td>${signalCell(screenerFrame(row,"15m").trend)}</td><td>${pctCell(screenerFrame(row,"15m").change_pct)}</td><td>${pctCell(screenerFrame(row,"1h").change_pct)}</td><td class="num">%${Number(screenerFrame(row,"15m").atr_pct || 0).toFixed(2)}</td><td>${signalCell(screenerFrame(row,"15m").bb)}</td><td>${pctCell(row.change_24h)}</td>${end(row)}</tr>`);
    } else if (state.marketView === "long") {
      headers = ["#", "Coin", "Fiyat", "Skor", "Trend 4S", "Trend 1G", "EMA 1G", "RSI 4S", "RSI 1G", "24S %", "7G %", ""];
      body = rows.map(row => `<tr>${common(row)}<td><span class="market-score">${row.score}</span></td><td>${signalCell(screenerFrame(row,"4h").trend)}</td><td>${signalCell(screenerFrame(row,"1d").trend)}</td><td>${signalCell(screenerFrame(row,"1d").ema)}</td><td>${rsiCell(screenerFrame(row,"4h").rsi)}</td><td>${rsiCell(screenerFrame(row,"1d").rsi)}</td><td>${pctCell(row.change_24h)}</td><td>${pctCell(row.change_7d)}</td>${end(row)}</tr>`);
    } else {
      headers = ["#", "Coin", "Fiyat", "Skor", "Trend 15D", "Trend 1S", "Trend 4S", "Trend 1G", "RSI 1S", "24S %", "Hacim", ""];
      body = rows.map(row => `<tr>${common(row)}<td><span class="market-score">${row.score}</span></td><td>${signalCell(screenerFrame(row,"15m").trend)}</td><td>${signalCell(screenerFrame(row,"1h").trend)}</td><td>${signalCell(screenerFrame(row,"4h").trend)}</td><td>${signalCell(screenerFrame(row,"1d").trend)}</td><td>${rsiCell(screenerFrame(row,"1h").rsi)}</td><td>${pctCell(row.change_24h)}</td><td class="right num">${VX.fmtUsd(row.quote_volume, 0)}</td>${end(row)}</tr>`);
    }
    setHTML("marketScreenerTable", body.length ? `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body.join("")}</tbody></table>` : `<div class="empty">Bu filtrede coin bulunamadı.</div>`);
  }

  async function loadMarketScreener(force = false) {
    const btn = $("marketScreenerRefresh");
    if (btn) { btn.disabled = true; btn.textContent = "Taranıyor…"; }
    try {
      const data = await VX.get(`/api/market/screener?limit=180&min_quote_volume=5000000${force ? "&refresh=true" : ""}`);
      state.marketRows = data.rows || [];
      const breadth = data.breadth || {};
      const bands = data.volume_bands || {};
      setText("marketScreenerMeta", `${data.scanned || 0} coin · ${data.universe || 0} uygun Futures kontratı · 24s hacim ≥ 5M USDT`);
      const derivatives = state.marketRows.filter(row => Number.isFinite(Number(row.derivatives?.funding_bp)) || Number.isFinite(Number(row.derivatives?.oi_change_pct))).length;
      setHTML("marketScreenerSummary", `<span><b>${data.scanned || 0}</b> incelendi</span><span><b>${bands.high || 0}</b> yüksek hacim</span><span><b>${bands.medium || 0}</b> orta hacim</span><span><b>${bands.emerging || 0}</b> gelişen hacim</span><span><b>${derivatives}</b> türev verisi mevcut</span><span class="is-up"><b>${breadth.up || 0}</b> yükselen</span><span class="is-down"><b>${breadth.down || 0}</b> düşen</span><span><b>${breadth.average > 0 ? "+" : ""}${breadth.average ?? "—"}%</b> ort.</span>`);
      renderMarketScreener();
    } catch (err) {
      setHTML("marketScreenerTable", `<div class="empty">Tarayıcı yüklenemedi: ${VX.esc(err.message)}</div>`);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Yenile"; }
    }
  }

  const contractDate = value => value ? new Date(value).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "Tarih bekleniyor";
  function lifecycleCard(title, tone, rows, emptyText) {
    const body = rows.length ? rows.map(row => {
      const analyze = row.analysis_available ? `<a class="btn btn--sm" href="/analiz?symbol=${encodeURIComponent(row.symbol)}&interval=1h">Analiz</a>` : "";
      const delta = row.price ? `<span class="${VX.deltaClass(Number(row.change_24h || 0))}">${Number(row.change_24h || 0) > 0 ? "+" : ""}${Number(row.change_24h || 0).toFixed(2)}%</span>` : `<span>${VX.esc(row.status || "Bekliyor")}</span>`;
      return `<div class="market-lifecycle__row"><div class="market-lifecycle__coin">${VX.coinIcon(row.symbol, 27)}<div><b>${VX.esc(row.symbol)}</b><small>${VX.esc(row.volume_label || row.status || "Futures")}</small></div></div><div class="market-lifecycle__event"><b>${contractDate(row.event_at)}</b>${delta}</div>${analyze}</div>`;
    }).join("") : `<div class="market-lifecycle__empty">${VX.esc(emptyText)}</div>`;
    return `<article class="market-lifecycle__card market-lifecycle__card--${tone}"><div class="market-lifecycle__card-head"><span></span><b>${VX.esc(title)}</b><em>${rows.length}</em></div>${body}</article>`;
  }

  function renderListingLifecycle(data) {
    const newly = data.new || [], upcoming = data.upcoming || [], delisting = data.delisting || [];
    setText("marketLifecycleMeta", `${data.source || "Binance Futures"} · ${VX.fmtAgo(data.checked_at)} kontrol edildi`);
    setHTML("marketLifecycleGrid", [
      lifecycleCard("Yeni listelenenler", "new", newly, "Son 45 günde yeni Futures kontratı yok."),
      lifecycleCard("Yakında açılacaklar", "upcoming", upcoming, "Resmî veride yaklaşan açılış görünmüyor."),
      lifecycleCard("Delist edilecekler", "delist", delisting, "Resmî sözleşme verisinde planlanan delist yok."),
    ].join(""));
  }

  async function loadListingLifecycle(force = false) {
    try {
      const data = await VX.get(`/api/market/listing-calendar${force ? "?refresh=true" : ""}`);
      state.marketLifecycle = data;
      renderListingLifecycle(data);
    } catch (err) {
      setHTML("marketLifecycleGrid", `<div class="empty">Listeleme takvimi yüklenemedi: ${VX.esc(err.message)}</div>`);
    }
  }

  async function bootMarkets() {
    $("marketScreenerViews").addEventListener("click", e => {
      const view = e.target.closest("[data-market-view]");
      const filter = e.target.closest("[data-market-filter]");
      if (view) {
        state.marketView = view.dataset.marketView;
        $("marketScreenerViews").querySelectorAll("[data-market-view]").forEach(b => b.classList.toggle("is-active", b === view));
      }
      if (filter) {
        state.marketFilter = filter.dataset.marketFilter;
        $("marketScreenerViews").querySelectorAll("[data-market-filter]").forEach(b => b.classList.toggle("is-active", b === filter));
      }
      if (view || filter) renderMarketScreener();
    });
    $("marketScreenerSearch").addEventListener("input", renderMarketScreener);
    $("marketScreenerRefresh").addEventListener("click", () => Promise.all([loadMarketScreener(true), loadListingLifecycle(true)]));
    await Promise.all([loadMarketScreener(false), loadListingLifecycle(false)]);
  }

  async function loadCopy() {
    const d = await VX.get("/api/trades?status=open,candidate,closed"); const r = d.risk_frame;
    setHTML("riskKpis", [kpi("Toplam margin", VX.fmtUsd(r.total_margin, 0)), kpi("Pozisyon limiti", VX.fmtUsd(r.max_position_margin, 0)), kpi("İşlem riski", VX.fmtUsd(r.max_risk_per_trade, 0)), kpi("Varsayılan kaldıraç", `${r.default_leverage}x`, "Stop zorunlu")].join(""));
    const ENGINE = { tsmom: ["TSMOM", "tag--info"] };
    const rows = d.trades.map(t => {
      const m = t.meta || {};
      const [ad, cls] = ENGINE[m.engine] || ["Elle", ""];
      const durum = t.status === "open" ? "Açık"
        : t.status === "candidate" ? "Aday"
        : (m.exit_reason === "stop" ? "Stop" : m.exit_reason === "target" ? "Hedef"
          : m.exit_reason === "flip" ? "Sinyal döndü" : m.exit_reason === "sure" ? "Süre doldu" : "Kapandı");
      const r = t.status === "closed" ? t.r_multiple : t.r_live;
      return `<tr>
        <td><div class="sym">${VX.coinIcon(t.symbol, 20)}<div>
          <div style="font-size:12.8px">${VX.esc(t.symbol)}</div>
          <div style="font-size:10.5px;color:var(--ink-3)">${VX.esc(t.interval || "")} · ${VX.fmtAgo(t.opened_at)}</div>
        </div></div></td>
        <td><span class="tag ${t.side === "LONG" ? "tag--long" : "tag--short"}">${t.side}</span></td>
        <td><span class="tag ${t.mode === "live" ? "tag--short" : "tag--info"}">${t.mode === "live" ? "CANLI" : "KÂĞIT"}</span></td>
        <td><span class="tag ${cls}">${ad}</span></td>
        <td>${VX.esc(durum)}</td>
        <td class="right num" style="font-size:12.5px">${VX.fmtPrice(t.entry)}</td>
        <td class="right num" style="font-size:12.5px" data-price="${VX.esc(t.symbol)}">${VX.fmtPrice(t.live_price)}</td>
        <td class="right num delta--down" style="font-size:12.5px">${VX.fmtPrice(t.stop)}${m.stop_pct ? `<div style="font-size:10px">−%${Number(m.stop_pct).toFixed(1)}</div>` : ""}</td>
        <td class="right"><b class="${VX.deltaClass(r)}">${r === null || r === undefined ? "—" : Number(r).toFixed(2) + "R"}</b></td>
        <td class="right"><b class="${VX.deltaClass(t.status === "closed" ? t.pnl_usdt : t.pnl_live)}">${VX.fmtUsd(t.status === "closed" ? t.pnl_usdt : t.pnl_live)}</b></td>
        <td class="right">${t.status === "open" && m.research_id
          ? `<button class="btn btn--sm btn--danger" data-close="${m.research_id}">Kapat</button>` : ""}</td>
      </tr>`;
    });
    setHTML("copyTrades", rows.length
      ? table(["Sembol", "Yön", "Mod", "Motor", "Durum", "Giriş", "Anlık", "SL", "R", "PnL", ""], rows)
      : `<div class="empty">Henüz pozisyon yok. Ana motor tarama yaptığında buraya düşer.</div>`);
    wireCloseButtons("copyTrades", loadCopy);
  }

  /* ---------------------------------------------------- TSMOM + carry */
  async function loadTsmom() {
    let st, items;
    try {
      [st, items] = await Promise.all([
        VX.get("/api/engine/tsmom/status"),
        VX.get("/api/engine/tsmom/recent?limit=25"),
      ]);
    } catch (e) {
      $("tsmomMeta").textContent = "Durum alınamadı: " + e.message;
      return;
    }
    const cfg = st.config || {};
    const acik = st.enabled !== false && cfg.enabled !== false;
    const btn = $("tsmomToggleBtn");
    btn.textContent = acik ? "Motoru durdur" : "Motoru başlat";
    btn.className = `btn ${acik ? "btn--danger" : "btn--primary"}`;
    /* 29.08: bu panelde PARA GOSTERGESI YOK. Ana motor bakiyeye bakmadan
       kurulum bulur; margin, risk butcesi ve acik risk Copy Trade
       sayfasinin isi. Ikisi ayni kutuda dururken "para bitti" ile "motor
       calismiyor" birbirine karisiyordu. */
    const c = st.copy || {};
    $("tsmomKpis").innerHTML = [
      kpi("Açık kayıt", st.open ?? 0, `en fazla ${st.max_open ?? cfg.max_open ?? "—"} · para sınırı yok`),
      kpi("Taranan evren", st.last_universe || 0,
          `son taramada ${st.last_passed ?? 0} kurulum geçti`),
      kpi("Ortalama sonuç", st.avg_result_r === null || st.avg_result_r === undefined
        ? "—" : `${st.avg_result_r}R`, `${st.closed ?? 0} tamamlandı · net, maliyet dahil`),
      kpi("Parayla aynalanan", `${c.open_trades ?? 0}/${c.max_open_trades ?? "—"}`,
          "Copy Trade katmanı"),
    ].join("");
    $("tsmomMeta").textContent =
      `${acik ? "Çalışıyor" : "DURDURULDU"} · günlük bar · |t| ≥ ${cfg.min_abs_t ?? "—"} · `
      + `stop ${cfg.stop_atr_mult ?? "—"}×ATR · ${cfg.horizon_days ?? "—"} gün ufuk · `
      + `portföy inceleme ${cfg.rotation_review_hours ?? 4}s · sert tavan ${cfg.max_hold_hours ?? 24}s · `
      + `tekrar bekleme ${cfg.symbol_cooldown_hours ?? 24}s · `
      + `maliyet tavanı ${cfg.max_cost_r ?? "—"}R · `
      + `durgun çıkışı ${cfg.stale_exit === false ? "kapalı" : `${cfg.stale_days}g / ${cfg.stale_max_mfe_r}R`} · `
      + `evren ${st.last_universe || 0} · tam tarama her ${cfg.scan_interval_seconds}sn`
      + `${st.last_scan_at ? ` (${VX.fmtAgo(st.last_scan_at)})` : ""} · `
      + `gözcü her ${cfg.watch_interval_seconds}sn`
      + `${st.last_watch_at ? ` (${VX.fmtAgo(st.last_watch_at)}, ${st.last_watch_checked ?? 0} sembol)` : ""}`
      + (st.last_opened ? ` · son taramada ${st.last_opened} işlem` : "")
      + (st.last_error ? ` · HATA: ${st.last_error}` : "");

    /* Hangi kapı ne kadar eledi. Motorun neyi reddettiğini görmeden
       "neden sinyal yok" sorusu cevaplanamaz. */
    const blocks = Object.entries(st.block_reasons || {});
    const obs = Object.entries(st.open_blocks || {});
    let bh = "";
    const rot = st.rotation || st.last_rotation || {};
    if (rot.at) {
      const repl = (rot.replacements || []).map(x =>
        `${VX.esc(x.closed_symbol)} (${Number(x.closed_t).toFixed(2)}) → ${VX.esc(x.candidate_symbol)} (${Number(x.candidate_t).toFixed(2)})`).join(" · ");
      bh += `<div class="workspace__warning" style="margin:12px 0">
        <b>4 saatlik portföy incelemesi:</b> ${rot.action === "rotated" ? (repl || "rotasyon yapıldı") : VX.esc(rot.reason || "değişiklik yok")}
        · ${VX.fmtAgo(rot.at)}. Para pozisyon limiti esnetilmez; yalnız zayıf slot daha güçlü adayla değiştirilir.
      </div>`;
    }
    /* EVREN TEŞHİSİ — hangi filtre gerçekten eliyor?
       Hacim eşiğini indirip "daha çok coin taransın" beklemek, eşiği geçen
       sembol sayısı zaten "taranan evren"den fazlaysa boş umut: o durumda
       eşik hiçbir şey elemiyor, evreni tamamen universe_size belirliyor.
       Tahmin ettirmek yerine sayıyı gösteriyoruz. */
    const ev = st.evren_teshis || {};
    if (ev.borsadaki_kontrat) {
      const kirpik = (ev.kirpilan || 0) > 0;
      bh += `<div class="workspace__muted" style="margin:12px 0 4px">Evren nasıl daraldı</div>`
        + `<div class="workspace__blocks">`
        + [[`borsadaki kontrat`, ev.borsadaki_kontrat],
           [`yaş filtresi eledi`, ev.yas_elenen],
           [`hacim eşiği (${ev.hacim_esigi_m}M) eledi`, ev.hacim_elenen],
           [`eşiği geçen`, ev.esigi_gecen],
           [`evren kırpması attı`, ev.kirpilan],
           [`taranan`, ev.taranan]].map(([k, v]) =>
            `<div class="workspace__dist-item"><div><span>${VX.esc(k)}</span><b>${v}</b></div></div>`).join("")
        + `</div>`;
      if (kirpik) {
        bh += `<div class="workspace__warning" style="margin:8px 0">
          <b>HACİM EŞİĞİ ŞU AN İŞ YAPMIYOR.</b> Eşiği geçen <b>${ev.esigi_gecen}</b> sembolden
          <b>${ev.kirpilan}</b> tanesi zaten <b>taranan evren = ${ev.universe_size}</b> sınırına takıldı.
          Eşiği daha da düşürmek taranan coin sayısını <b>değiştirmez</b>; onu büyütmek için
          <b>taranan evren</b> ayarını yükseltmek gerekiyor.
          Şu an taranan en küçük coinin hacmi <b>${ev.en_dusuk_taranan_hacim_m}M</b>.
        </div>`;
      } else if (ev.hacim_elenen > 0) {
        bh += `<div class="workspace__muted" style="margin:8px 0">
          Hacim eşiği gerçekten eliyor — eşiği geçen ${ev.esigi_gecen} sembolün hepsi taranıyor.
          Eşiği düşürmek taranan coin sayısını artırır.</div>`;
      }
    }

    /* İZLEME LİSTESİ. Gözcünün her turda canlı fiyata karşı yeniden
       değerlendirdiği kısa liste. Boşsa gözcünün yapacak işi yok demektir —
       bu da bir cevap. */
    const wl = st.watchlist || [];
    bh += `<div class="workspace__muted" style="margin:10px 0 4px">Gözcü izleme listesi
      <span class="evidence">canlı fiyata göre her ${cfg.watch_interval_seconds} sn yeniden değerlendirilir</span></div>`
      + (wl.length
         ? `<div class="workspace__blocks">` + wl.map(sym =>
             `<div class="workspace__dist-item"><a class="sym" href="/analiz?symbol=${encodeURIComponent(sym)}">${VX.coinIcon(sym, 18)}<span>${VX.esc(sym)}</span></a></div>`).join("") + `</div>`
         : `<div class="workspace__muted">Liste boş — son taramada ne açılabilir kurulum kaldı ne de fiyat geri çekilse açılabilecek bir aday.</div>`);

    if (obs.length) {
      bh += `<div class="workspace__muted" style="margin:10px 0 4px">Son taramada işlem AÇILAMAMA sebepleri</div>`
        + `<div class="workspace__blocks">` + obs.map(([k, v]) =>
            `<div class="workspace__dist-item"><div><span>${VX.esc(k)}</span><b>${v}</b></div></div>`).join("")
        + `</div>`;
      if (st.open_detail) bh += `<div class="workspace__muted" style="margin-top:6px;font-size:11.5px">${VX.esc(st.open_detail)}</div>`;
    }
    if (blocks.length) {
      bh += `<div class="workspace__muted" style="margin:14px 0 4px">Kurulum elenme sebepleri</div>`
        + `<div class="workspace__blocks">` + blocks.map(([k, v]) =>
            `<div class="workspace__dist-item"><div><span>${VX.esc(k)}</span><b>${v}</b></div></div>`).join("")
        + `</div>`;
    }
    $("tsmomBlocks").innerHTML = bh;

    const topRows = (st.top || []).map(x => `<tr>
      <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(x.symbol)}">${VX.coinIcon(x.symbol, 20)}<span>${VX.esc(x.symbol)}</span></a></td>
      <td><span class="tag ${x.side === "LONG" ? "tag--long" : "tag--short"}">${VX.esc(x.side)}</span></td>
      <td class="right"><b>${Number(x.t).toFixed(2)}</b></td>
      <td class="right">${x.atr_pct === undefined ? "—" : "%" + Number(x.atr_pct).toFixed(1)}</td>
      <td class="right ${Number(x.stop_pct) > 12 ? "delta--down" : ""}">${x.stop_pct === undefined ? "—" : "−%" + Number(x.stop_pct).toFixed(1)}</td>
      <td class="right ${Number(x.cost_r) > (cfg.max_cost_r || 0.1) ? "delta--down" : ""}">${Number(x.cost_r).toFixed(4)}R</td>
      <td class="right">${x.funding_bp === null || x.funding_bp === undefined ? "—" : Number(x.funding_bp).toFixed(2) + "bp"}</td></tr>`);
    $("tsmomTop").innerHTML = topRows.length
      ? `<div class="workspace__muted" style="margin:12px 0 4px">Son taramanın en güçlü adayları</div>`
        + table(["Sembol", "Yön", "t değeri", "ATR", "Stop", "Maliyet", "Fonlama"], topRows)
      : "";

    const rows = (items.items || []).map(x => {
      const m = x.meta || {};
      return `<tr>
        <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(x.symbol)}">${VX.coinIcon(x.symbol, 20)}<span>${VX.esc(x.symbol)}</span></a></td>
        <td><span class="tag ${x.side === "LONG" ? "tag--long" : "tag--short"}">${VX.esc(x.side)}</span></td>
        <td class="right"><b>${m.t_stat === undefined ? "—" : Number(m.t_stat).toFixed(2)}</b></td>
        <td class="right">${m.funding_bp === undefined || m.funding_bp === null ? "—" : Number(m.funding_bp).toFixed(2) + "bp"}</td>
        <td class="right">${m.total_cost_r === undefined ? "—" : Number(m.total_cost_r).toFixed(4) + "R"}</td>
        <td class="right">${VX.fmtPrice(x.entry)}</td>
        <td class="right delta--down">${VX.fmtPrice(x.stop)}${m.stop_pct ? ` <span style="font-size:10.5px">−%${Number(m.stop_pct).toFixed(1)}</span>` : ""}</td>
        <td>${VX.esc(x.status === "open" ? "İzleniyor" : (x.exit_reason || x.outcome || "Kapandı"))}</td>
        <td class="right" title="Brüt ${x.gross_result_r ?? "—"}R · maliyet ${x.cost_r ?? "—"}R"><b class="${Number(x.result_r) > 0 ? "delta--up" : Number(x.result_r) < 0 ? "delta--down" : ""}">${x.result_r === null || x.result_r === undefined ? "—" : Number(x.result_r).toFixed(2) + "R"}</b></td>
        <td>${new Date(x.created_at).toLocaleString("tr-TR")}</td></tr>`;
    });
    $("tsmomTrades").innerHTML = rows.length
      ? `<div class="workspace__muted" style="margin:12px 0 4px">Kayıtlar</div>`
        + table(["Sembol", "Yön", "t", "Fonlama", "Maliyet", "Giriş", "SL", "Durum", "Net sonuç", "Açılış"], rows)
      : `<div class="empty">Henüz kayıt yok. Motor günlük barla çalışıyor; ilk kayıtlar için tarama beklenmeli.</div>`;
  }

  /* --------------------------------------------------- karne */
  function renderScorecard(st) {
    const sc = st.scorecard || {};
    const box = $("scoreKpis"); if (!box) return;
    if (!sc.closed) {
      box.innerHTML = [
        kpi("Kapanmış işlem", 0, "karne için sonuç bekleniyor"),
        kpi("Açık pozisyon", sc.open ?? 0, "henüz sonuç üretmedi"),
      ].join("");
      $("scoreDetail").innerHTML = `<div class="empty">${VX.esc(sc.note || "Henüz kapanmış işlem yok.")}</div>`;
      return;
    }
    const ci = sc.ci95 || [];
    box.innerHTML = [
      kpi("Kapanmış işlem", sc.closed,
          `${sc.open} açık · ${sc.entry_days ?? "—"} giriş günü${sc.manual_closed ? ` · ${sc.manual_closed} elle kapanış hariç` : ""}`),
      kpi("Ortalama sonuç", `${sc.avg_r > 0 ? "+" : ""}${sc.avg_r}R`,
          `toplam ${sc.total_r > 0 ? "+" : ""}${sc.total_r}R`),
      kpi("İsabet", `%${sc.win_rate}`, `PF ${sc.profit_factor ?? "—"}`),
      kpi("En derin düşüş", `${sc.max_drawdown_r}R`, "tepe noktasından"),
    ].join("");

    /* GUVEN ARALIGI en onemli satir: sifiri iceriyorsa ne "kazaniyor"
       ne "kaybediyor" denebilir. En sik yapilan hata bunu atlamak. */
    const belirsiz = !sc.significant;
    let h = `<div class="workspace__warning" style="margin:12px 0;${belirsiz ? "" : "border-left-color:var(--up)"}">
      <b>%95 güven aralığı: [${ci[0] > 0 ? "+" : ""}${ci[0]}R , ${ci[1] > 0 ? "+" : ""}${ci[1]}R]</b><br>
      ${belirsiz
        ? `Aralık sıfırı içeriyor — bu sonuçla motorun kazandığı da kaybettiği de <b>söylenemez</b>.`
          + (sc.n_needed ? ` Bu büyüklükte bir farkı kanıtlamak için ~<b>${sc.n_needed}</b> kapanmış işlem gerekir.` : "")
        : `Aralık sıfırı içermiyor — sonuç sıfırdan <b>ayırt edilebilir</b>.`}
    </div>`;

    if (sc.avg_cost_r !== null && sc.avg_cost_r !== undefined) {
      h += `<div class="workspace__muted" style="margin-bottom:10px">
        Brüt ${sc.gross_r > 0 ? "+" : ""}${sc.gross_r}R − maliyet ${sc.avg_cost_r}R = net ${sc.avg_r > 0 ? "+" : ""}${sc.avg_r}R</div>`;
    }
    const ex = Object.entries(sc.exits || {});
    if (ex.length) {
      const AD = { stop: "Stop", target: "Hedef", flip: "Yön döndü", fade: "Trend soldu", sure: "Süre doldu" };
      h += `<div class="workspace__muted" style="margin:10px 0 4px">Çıkış dağılımı</div><div class="workspace__blocks">`
        + ex.map(([k, v]) => `<div class="workspace__dist-item"><div><span>${VX.esc(AD[k] || k)}</span><b>${v}</b></div></div>`).join("")
        + `</div>`;
    }
    const bs = sc.by_side || {};
    if (bs.LONG || bs.SHORT) {
      h += `<div class="workspace__muted" style="margin:14px 0 4px">Yöne göre</div>`
        + table(["Yön", "İşlem", "Ortalama", "Toplam"],
            ["LONG", "SHORT"].filter(k => bs[k]).map(k => `<tr>
              <td><span class="tag ${k === "LONG" ? "tag--long" : "tag--short"}">${k}</span></td>
              <td>${bs[k].n}</td>
              <td class="right"><b class="${VX.deltaClass(bs[k].avg_r)}">${bs[k].avg_r > 0 ? "+" : ""}${bs[k].avg_r}R</b></td>
              <td class="right">${bs[k].total_r > 0 ? "+" : ""}${bs[k].total_r}R</td></tr>`));
    }
    $("scoreDetail").innerHTML = h;
  }

  function renderSelftest(st) {
    const t = st.selftest || {};
    const box = $("selftestBox"); if (!box) return;
    if (!t.at) {
      box.innerHTML = `<div class="workspace__muted" style="margin-top:14px">Otomatik geçmiş testi henüz çalışmadı. Günde bir kez kendiliğinden koşar; "Kendini test et" ile hemen tetikleyebilirsin.</div>`;
      return;
    }
    if (!t.ok) {
      box.innerHTML = `<div class="workspace__muted" style="margin-top:14px">Son geçmiş testi sonuç üretemedi: ${VX.esc(t.error || "bilinmiyor")}</div>`;
      return;
    }
    const ci = t.ci95 || [];
    box.innerHTML = `
      <div class="workspace__muted" style="margin:18px 0 6px">Geçmiş testi · ${t.days} gün · ${t.symbols} sembol · ${VX.fmtAgo(t.at)}</div>
      <div class="workspace__blocks">
        <div class="workspace__dist-item"><div><span>işlem</span><b>${t.n}</b></div></div>
        <div class="workspace__dist-item"><div><span>ortalama</span><b>${t.avg_r > 0 ? "+" : ""}${t.avg_r}R</b></div></div>
        <div class="workspace__dist-item"><div><span>isabet</span><b>%${t.win_rate}</b></div></div>
        <div class="workspace__dist-item"><div><span>PF</span><b>${t.profit_factor ?? "—"}</b></div></div>
        <div class="workspace__dist-item"><div><span>maxDD</span><b>${t.max_drawdown_r}R</b></div></div>
        <div class="workspace__dist-item"><div><span>%95 GA</span><b>${ci[0]} / ${ci[1]}</b></div></div>
      </div>
      <div class="workspace__muted" style="margin-top:8px;font-size:11.5px">${VX.esc(t.note || "")}</div>`;
  }

  async function runSelftest() {
    const b = $("selftestBtn"); b.disabled = true; b.textContent = "Test ediliyor…";
    try {
      const r = await VX.post("/api/engine/tsmom/selftest", {});
      VX.toast(r.ok ? `Geçmiş testi bitti · ${r.n} işlem, ortalama ${r.avg_r > 0 ? "+" : ""}${r.avg_r}R`
                    : (r.error || "Test sonuç üretemedi"), r.ok ? "ok" : "warn", 8000);
      await loadTsmom();
    } catch (e) { VX.toast(e.message, "err", 8000); }
    finally { b.disabled = false; b.textContent = "Kendini test et"; }
  }

  /* Karne kendi sayfasinda: TSMOM durumundan yalnizca karne kismini okur. */
  async function loadKarne() {
    let st;
    try { st = await VX.get("/api/engine/tsmom/status"); }
    catch (e) { $("scoreDetail").innerHTML = `<div class="empty">Durum alınamadı: ${VX.esc(e.message)}</div>`; return; }
    renderScorecard(st);
    renderSelftest(st);
  }

  async function tsmomToggle() {
    const btn = $("tsmomToggleBtn"); btn.disabled = true;
    try {
      const st = await VX.get("/api/engine/tsmom/status");
      const acik = st.enabled !== false;
      await VX.post("/api/engine/tsmom/toggle", { enabled: !acik });
      VX.toast(acik ? "TSMOM motoru durduruldu" : "TSMOM motoru başlatıldı — tarama başlıyor");
      await loadTsmom();
    } catch (e) { VX.toast(e.message, "err", 7000); }
    finally { btn.disabled = false; }
  }

  async function tsmomScan() {
    const btn = $("tsmomScanBtn"); btn.disabled = true; btn.textContent = "Taranıyor…";
    try {
      const r = await VX.post("/api/engine/tsmom/scan-now", {});
      VX.toast(r.ok ? `Tarama bitti · ${r.passed || 0} aday, ${r.saved || 0} kayıt, ${r.opened || 0} işlem`
                    : (r.error || "Tarama başarısız"),
               r.ok ? "ok" : "err", 6000);
      await loadTsmom();
    } catch (e) { VX.toast(e.message, "err", 7000); }
    finally { btn.disabled = false; btn.textContent = "Şimdi tara"; }
  }

  /* Runtime ayar formunu bagla. Iki yerde kullaniliyor: Ayarlar sayfasi ve
     Copy Trade sayfasindaki para formu. Ayni mekanizma iki kez yazilsaydi
     biri degisince digeri geride kalirdi. Alan adi "grup.anahtar" biciminde;
     hangi gruba yazacagini isminden okuyor. */
  async function wireRuntimeForm(formId, stateId, onSaved) {
    const form = $(formId);
    if (!form) return null;
    const cfg = await VX.get("/api/runtime-settings");
    form.querySelectorAll("[name]").forEach(input => {
      const [group, key] = input.name.split(".");
      const value = cfg[group]?.[key];
      if (value === undefined || value === null) return;
      if (input.type === "checkbox") input.checked = Boolean(value);
      else if (input.tagName === "SELECT") input.value = String(value);
      else input.value = value;
    });
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const body = {};
      form.querySelectorAll("[name]").forEach(input => {
        const [group, key] = input.name.split(".");
        body[group] ||= {};
        let v;
        if (input.type === "checkbox") v = input.checked;
        else if (input.type === "number") v = Number(input.value);
        else if (input.value === "true" || input.value === "false") v = input.value === "true";
        else v = input.value.trim();
        body[group][key] = v;
      });
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      if ($(stateId)) $(stateId).textContent = "Kaydediliyor…";
      try {
        await VX.post("/api/runtime-settings", { settings: body });
        if ($(stateId)) $(stateId).textContent = "Kaydedildi";
        VX.toast("Ayarlar çalışana uygulandı");
        if (onSaved) await onSaved();
      } catch (err) {
        if ($(stateId)) $(stateId).textContent = "Kaydedilemedi";
        VX.toast(err.message, "err", 7000);
      } finally { btn.disabled = false; }
    });
    return cfg;
  }

  /* Kapatma dugmeleri DELEGE ediliyor: tablo her yenilemede yeniden
     ciziliyor, satira dogrudan baglanan dinleyici kaybolurdu. */
  function wireCloseButtons(boxId, sonra) {
    const box = $(boxId);
    if (!box || box.dataset.wiredClose === "1") return;
    box.dataset.wiredClose = "1";
    box.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-close]");
      if (!btn) return;
      if (!window.confirm("Bu pozisyon anlık fiyattan kapatılsın mı?")) return;
      btn.disabled = true;
      try {
        const r = await VX.post("/api/engine/tsmom/close", { id: Number(btn.dataset.close) });
        if (r.ok === false) return VX.toast(r.error || "Kapatılamadı", "err", 7000);
        VX.toast(`${r.symbol} kapatıldı · ${r.result_r >= 0 ? "+" : ""}${r.result_r}R`);
        if (sonra) await sonra();
      } catch (err) { VX.toast(err.message, "err", 7000); }
      finally { btn.disabled = false; }
    });
  }

  /* ---------------------------------------------- Copy Trade (para katmanı) */
  async function loadCopyStatus() {
    let st;
    try { st = await VX.get("/api/engine/copy/status"); }
    catch (_) { return; }
    const btn = $("copyToggleBtn");
    if (btn) {
      btn.dataset.enabled = st.enabled ? "1" : "0";
      btn.textContent = st.enabled ? "Durdur" : "Başlat";
      btn.className = "btn " + (st.enabled ? "btn--danger" : "btn--primary");
    }
    setText("copyMeta",
      (st.enabled ? "Çalışıyor" : "Durdu")
      + ` · ${st.open_trades}/${st.max_open_trades} açık işlem`
      + ` · canlı ${st.live_enabled ? "AÇIK" : "kapalı"} (${st.live_open || 0} pozisyon)`
      + ` · ${st.open_risk.toFixed(2)}$ açık risk`
      + ` · bugün ${st.realized_today >= 0 ? "+" : ""}${st.realized_today.toFixed(2)}$ gerçekleşmiş`
      + (st.has_keys ? ` · API anahtarı bağlı (…${st.key_hint})` : " · API anahtarı yok")
      + (st.live_reason ? ` · ${st.live_reason}` : "")
      + (st.last_block ? ` · son para engeli: ${st.last_block}` : ""));
    const liveBtn = $("liveToggleBtn");
    if (liveBtn) {
      liveBtn.dataset.enabled = st.live_enabled ? "1" : "0";
      liveBtn.textContent = st.live_enabled ? "Canlıyı durdur" : "Canlıyı aç";
      liveBtn.className = `btn ${st.live_enabled ? "btn--danger" : "btn--primary"}`;
    }
    const warn = $("copyModeWarning");
    if (warn) warn.innerHTML = st.live_enabled
      ? `<b>CANLI EMİR AÇIK:</b> ${VX.esc(st.live_reason || "Yeni ve para kapısını geçen sinyaller Binance Futures'a gönderilir.")} Her canlı girişin borsada duran zorunlu stopu vardır.`
      : `<b>KÂĞIT MOD:</b> Canlı emir anahtarı kapalı. Motor ölçmeye ve kâğıt pozisyon üretmeye devam eder.`;
    return st;
  }

  async function toggleLive() {
    const btn = $("liveToggleBtn");
    const opening = btn.dataset.enabled !== "1";
    if (opening) {
      const word = prompt("GERÇEK PARA ile yeni Binance Futures emirleri açılacak.\nMevcut eski sinyaller açılmayacak. Devam için CANLI yaz:");
      if ((word || "").trim().toUpperCase() !== "CANLI") return;
    } else if (!confirm("Yeni canlı girişler durdurulsun mu? Açık canlı pozisyonların stop ve çıkış takibi devam eder.")) return;
    btn.disabled = true;
    try {
      await VX.post("/api/trading/live/toggle", { enabled: opening, confirmation: opening ? "CANLI" : "" });
      VX.toast(opening ? "Canlı Copy Trade açıldı" : "Yeni canlı girişler durduruldu", opening ? "warn" : "ok", 8000);
      await Promise.all([loadCopyStatus(), loadTrading()]);
    } catch (e) { VX.toast(e.message, "err", 10000); }
    finally { btn.disabled = false; }
  }

  async function toggleCopy() {
    const btn = $("copyToggleBtn");
    const enabled = btn.dataset.enabled !== "1";
    btn.disabled = true;
    try {
      await VX.post("/api/engine/copy/toggle", { enabled });
      VX.toast(enabled ? "Copy Trade açıldı" : "Copy Trade durdu — motor çalışmaya devam ediyor");
      await loadCopyStatus();
    } catch (e) { VX.toast(e.message, "err", 7000); }
    finally { btn.disabled = false; }
  }

  async function bootCopy() {
    $("refreshCopyBtn").addEventListener("click", loadCopy);
    $("copyToggleBtn").addEventListener("click", toggleCopy);
    $("liveToggleBtn")?.addEventListener("click", toggleLive);
    await loadCopy();
    await loadCopyStatus();
    await wireRuntimeForm("copySettingsForm", "copySaveState", loadCopyStatus);
    if ($("tradingPanel")) await bootTrading();
    VX.interval(loadCopy, 30000, false);
    VX.interval(loadCopyStatus, 30000, false);
  }

  /* ---------------------------------------------- Motor Kontrol Merkezi */
  function renderMotorCenter(data) {
    const pipe = data.pipeline || {};
    const val = data.validation || {};
    const overall = val.overall || { checks: [], state: "INSUFFICIENT" };
    const health = data.health || {};
    const publishing = Boolean(health.telegram_configured && health.signal_notifications_enabled);
    const tag = $("motorPublishTag");
    if (tag) {
      tag.textContent = publishing ? "SİNYAL YAYINI AÇIK" : "SİNYAL YAYINI KAPALI";
      tag.className = `tag ${publishing ? "tag--long" : ""}`;
    }

    const stage = (name, value, note, tone = "") => `<div class="motor-stage ${tone}">
      <div class="motor-stage__name">${name}</div><div class="motor-stage__value">${value}</div>
      <div class="workspace__muted">${note}</div></div>`;
    const runtime = data.runtime || {};
    const hunterPulse = runtime.hunter || {};
    const execution = data.execution || {};
    const live = execution.live || {};
    const account = live.account || {};
    setHTML("motorPipeline", [
      stage("1 · Veri", (data.health?.stream || {}).connected ? "CANLI" : "KOPUK",
            `${runtime.last_universe || 0} kontratlık son evren`, (data.health?.stream || {}).connected ? "is-ready" : "is-locked"),
      stage("2 · Sıralama", runtime.last_passed || 0,
            `günlük geçen · V4 gölge denetimi · Avcı ${hunterPulse.last_ready || 0} hazır / ${hunterPulse.watching?.length || 0} izleme`),
      stage("3 · Risk", pipe.candidates || 0,
            `aday · ${pipe.paper_open || 0} paper`),
      stage("4 · Yürütme", live.enabled ? `${live.open_live || 0} CANLI` : "KAPALI",
            `${live.protected || 0}/${live.open_live || 0} stop korumalı`, live.enabled ? "is-ready" : "is-locked"),
      stage("5 · Bildirim", publishing ? "AÇIK" : "KAPALI",
             publishing ? "uygun yeni sinyaller gruba gönderilir" : "Telegram veya sinyal ayarı kapalı",
             publishing ? "is-ready" : ""),
    ].join(`<div class="motor-arrow">→</div>`));

    const checks = (overall.checks || []).map(c => `<li class="${c.passed ? "is-pass" : "is-fail"}">
      <span>${c.passed ? "✓" : "×"}</span><b>${VX.esc(c.label)}</b>
      <em>${c.current === null || c.current === undefined ? "—" : VX.esc(String(c.current))} / ${VX.esc(String(c.required))}</em></li>`).join("");
    const sideLine = (label, gate) => {
      const st = gate || {};
      const d = st.direction || {};
      return `<div class="motor-side"><b>${label}</b><span class="tag ${st.allowed ? "tag--long" : ""}">${st.allowed ? "yeterli" : "veri birikiyor"}</span>
        <span>${d.n || 0} işlem · ort. ${d.avg_r === null || d.avg_r === undefined ? "—" : `${d.avg_r >= 0 ? "+" : ""}${d.avg_r}R`}</span></div>`;
    };
    setHTML("motorValidation", `<div class="motor-card__title">Performans ölçümü</div>
      <div class="workspace__muted">Bu karne yalnız kapanmış ileri-test kayıtlarını ölçer; Telegram bildirimini durdurmaz.</div>
      <ul class="motor-checks">${checks}</ul>${sideLine("LONG", val.long)}${sideLine("SHORT", val.short)}`);

    const source = health.data || {};
    const stream = health.stream || {};
    const selftest = health.selftest || {};
    setHTML("motorHealth", `<div class="motor-card__title">Sistem sağlığı</div>
      <div class="motor-health-row"><span>Fiyat verisi</span><b>${VX.esc(String(source.mode || "bilinmiyor").toUpperCase())}</b></div>
      <div class="motor-health-row"><span>Canlı akış</span><b>${stream.connected ? "BAĞLI" : "KOPUK"} · ${VX.esc(String(stream.mode || "bilinmiyor").toUpperCase())}</b></div>
      <div class="motor-health-row"><span>Yeniden bağlantı</span><b>${Number(stream.reconnects || 0)}</b></div>
      <div class="motor-health-row"><span>Telegram bağlantısı</span><b>${health.telegram_configured ? "HAZIR" : "AYARLANMAMIŞ"}</b></div>
      <div class="motor-health-row"><span>İşlem yayını ayarı</span><b>${health.signal_notifications_enabled ? "AÇIK" : "KAPALI"}</b></div>
      <div class="motor-health-row"><span>Öz-test</span><b>${selftest.ok ? `${selftest.n || 0} işlem · ${selftest.avg_r >= 0 ? "+" : ""}${selftest.avg_r}R` : "kanıt değil / sonuç yok"}</b></div>
      <div class="workspace__muted">Telegram yayını performans karnesinden bağımsızdır. Canlı Binance emri ayrı güvenlik kurallarına tabidir.</div>`);

    const engine = data.engine || {};
    const cr = engine.criteria || {};
    const hunter = engine.hunter || hunterPulse;
    const v4 = engine.v4 || runtime.v4 || {};
    const features = engine.feature_store || runtime.feature_store || {};
    const accepted = v4.challenger_allowed || {};
    const rejected = v4.challenger_rejected || {};
    const qSides = (v4.quarantined_live_sides || []).join(", ") || "yok";
    setHTML("motorLogic", `<div class="motor-card__title">Motor neye göre işlem arıyor?</div>
      <div class="motor-health-row"><span>Evren</span><b>İlk ${cr.universe || "—"} · ≥ ${cr.min_quote_volume_m ?? "—"}M hacim</b></div>
      <div class="motor-health-row"><span>Geçmiş</span><b>≥ ${cr.min_listing_days || "—"} gün</b></div>
      <div class="motor-health-row"><span>Günlük momentum</span><b>|t| ≥ ${cr.min_abs_t ?? "—"}</b></div>
      <div class="motor-health-row"><span>Oynaklık</span><b>ATR %${(cr.atr_range || ["—","—"])[0]}–${(cr.atr_range || ["—","—"])[1]}</b></div>
      <div class="motor-health-row"><span>Canlı fiyat</span><b>kovalama ≤ ${cr.max_chase_atr ?? "—"} ATR</b></div>
      <div class="motor-health-row"><span>Maliyet / fonlama</span><b>≤ ${cr.max_cost_r ?? "—"}R · kalabalık ±${cr.funding_extreme_bp ?? "—"}bp</b></div>
      <div class="motor-health-row"><span>Parayla aynalama</span><b>|t| ≥ ${cr.copy_min_t ?? "—"} · max ${cr.max_open_trades ?? "—"}</b></div>
      <div class="motor-health-row"><span>Kontrollü agresif bant</span><b>|t| ${cr.copy_min_t ?? "—"}–${cr.copy_full_risk_min_t ?? "—"} · risk %${Math.round(Number(cr.copy_exploration_fraction || 0) * 100)} · ${cr.copy_exploration_slots ?? 0} slot</b></div>
      <div class="motor-health-row"><span>Portföy inceleme</span><b>her ${cr.rotation_review_hours ?? 4}s · min aday ${cr.rotation_min_candidate_t ?? "—"}</b></div>
      <div class="motor-health-row"><span>Rotasyon farkı</span><b>en az +${cr.rotation_min_t_improvement ?? "—"}t · ${cr.rotation_enabled ? "AKTİF" : "KAPALI"}</b></div>
      <div class="motor-health-row"><span>Tekrar / sert çıkış</span><b>${cr.symbol_cooldown_hours ?? "—"}s bekleme · ${cr.max_hold_hours ?? "—"}s tavan</b></div>
      <div class="motor-health-row"><span>Elit yön esnemesi</span><b>+${cr.side_flex_slots ?? 0} araştırma slotu · para limiti sabit</b></div>
      <div class="motor-health-row"><span>Intraday Avcı</span><b>${hunter.enabled ? "AKTİF" : "KAPALI"} · 15dk kırılım + geri-alım</b></div>
      <div class="motor-health-row"><span>Avcı son tarama</span><b>${hunter.last_scanned || 0} tarandı · ${hunter.watching?.length || 0} izleniyor · ${hunter.last_ready || 0} hazır</b></div>
      <div class="motor-health-row"><span>Avcı para yetkisi</span><b>${hunter.live_enabled ? "AÇIK" : "KAPALI · yalnız ileri-test"}</b></div>
      <div class="motor-health-row"><span>V4 rejim yönlendirici</span><b>${String(v4.mode || "shadow").toUpperCase()} · ana kararı bozmadan ölçüyor</b></div>
      <div class="motor-health-row"><span>Canlı emir doğrulaması</span><b>${v4.live_requires_validation ? "İLERİ-TEST YETERLİLİĞİ ZORUNLU" : "DEVRE DIŞI"}</b></div>
      <div class="motor-health-row"><span>Canlı yön karantinası</span><b>${VX.esc(qSides)}</b></div>
      <div class="workspace__muted" style="margin-top:9px">Bir sembol ancak bütün kapıları geçerse araştırma kaydı açar; para katmanı ayrıca günlük zarar ve pozisyon limitini kontrol eder.</div>`);

    const rsi = engine.rsi || {};
    const leads = rsi.leads || [];
    const leadRows = leads.slice(0, 8).map(x => `<tr>
      <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(x.symbol)}">${VX.coinIcon(x.symbol, 18)}<span>${VX.esc(x.symbol)}</span></a></td>
      <td><span class="tag ${x.side === "LONG" ? "tag--long" : "tag--short"}">${VX.esc(x.side)}</span></td>
      <td class="right">${Number(x.rsi || 0).toFixed(1)}</td><td>${VX.esc(x.label || "—")}</td></tr>`);
    const rsiBlocks = Object.entries(rsi.blocks || {}).map(([k, v]) => `${VX.esc(k)}: ${v}`).join(" · ");
    setHTML("motorRsiLeads", `<div class="motor-card__title">RSI Radar → Ana motor takibi</div>
      <div class="workspace__muted">${rsi.enabled ? `1 saatlik radar hacme göre ilk ${rsi.universe || "—"} kontratı tarar; en fazla ${rsi.limit || 0} adayı gözcüye taşır.` : "RSI motor beslemesi kapalı."} Çıplak RSI işlem açmaz: yön, günlük TSMOM ile aynı olmalı ve bütün ana kapılar geçmelidir.</div>
      <div class="workspace__muted" style="margin:8px 0">Son radar ${rsi.last_scan_at ? VX.fmtAgo(rsi.last_scan_at) : "henüz çalışmadı"} · son gözcüde ${rsi.last_checked || 0} incelendi · ${rsi.last_aligned || 0} yön teyitli${rsiBlocks ? ` · ${rsiBlocks}` : ""}</div>
      ${leadRows.length ? table(["Sembol", "Yön", "RSI", "Radar kararı"], leadRows) : `<div class="empty">Şu an yönlü RSI adayı yok.</div>`}`);

    const copy = execution.copy || {};
    setHTML("motorExecution", `<div class="motor-card__title">Emir ve risk yürütme</div>
      <div class="motor-health-row"><span>Canlı emir</span><b>${live.enabled ? "AÇIK" : "KAPALI"}</b></div>
      <div class="motor-health-row"><span>Yeni canlı işlem kapısı</span><b>${v4.live_requires_validation ? "KANIT ZORUNLU" : "AYARA BAĞLI"}</b></div>
      <div class="motor-health-row"><span>VORTEX canlı pozisyon</span><b>${live.open_live || 0} · ${live.protected || 0} korumalı</b></div>
      <div class="motor-health-row"><span>Binance bağlantısı</span><b>${account.connected ? "BAĞLI" : "KOPUK"}</b></div>
      <div class="motor-health-row"><span>Borsa pozisyonu</span><b>${account.open_positions ?? "—"}</b></div>
      <div class="motor-health-row"><span>Cüzdan / kullanılabilir</span><b>${account.connected ? `${VX.fmtUsd(account.wallet)} / ${VX.fmtUsd(account.available)}` : "—"}</b></div>
      <div class="motor-health-row"><span>Günlük gerçekleşen</span><b class="${VX.deltaClass(copy.realized_today)}">${VX.fmtUsd(copy.realized_today || 0)}</b></div>
      <div class="motor-health-row"><span>Son risk engeli</span><b>${VX.esc(copy.last_block || "yok")}</b></div>
      ${live.error ? `<div class="motor-alert">${VX.esc(live.error)}</div>` : ""}`);

    setHTML("motorArchitecture", `<div class="motor-card__title">Terminal mimarisi · ne aktif?</div>
      <div class="motor-health-row"><span>Göreli güç / momentum</span><b>AKTİF · TSMOM</b></div>
      <div class="motor-health-row"><span>V4 rejim yönlendirici</span><b>${String(v4.mode || "shadow").toUpperCase()} · ${v4.audited || 0} karar denetlendi</b></div>
      <div class="motor-health-row"><span>V4 kabul edilen karne</span><b>${accepted.n || 0} işlem · ${accepted.avg_r === null || accepted.avg_r === undefined ? "sonuç yok" : `${accepted.avg_r >= 0 ? "+" : ""}${accepted.avg_r} net R`}</b></div>
      <div class="motor-health-row"><span>V4 reddedilen karne</span><b>${rejected.n || 0} işlem · ${rejected.avg_r === null || rejected.avg_r === undefined ? "sonuç yok" : `${rejected.avg_r >= 0 ? "+" : ""}${rejected.avg_r} net R`}</b></div>
      <div class="motor-health-row"><span>Noktasal özellik deposu</span><b>${features.enabled ? "AKTİF" : "KAPALI"} · ${features.total || 0} satır / ${features.symbols || 0} sembol · ${features.sample_minutes || 60}dk</b></div>
      <div class="motor-health-row"><span>Yapı araştırması</span><b>BOS/CHoCH · sweep · FVG · EQH/EQL ölçülüyor</b></div>
      <div class="motor-health-row"><span>Gecikmeli sonuç etiketi</span><b>${features.labeled || 0} tamamlandı · geleceği görme engelli</b></div>
      <div class="motor-health-row"><span>RSI yükselen-düşen beslemesi</span><b>${rsi.enabled ? "AKTİF" : "KAPALI"}</b></div>
      <div class="motor-health-row"><span>15dk hacimli kırılım Avcısı</span><b>${hunter.enabled ? "AKTİF · İLERİ-TEST" : "KAPALI"}</b></div>
      <div class="motor-health-row"><span>Brüt / maliyet / net R muhasebesi</span><b>AKTİF · komisyon + slipaj + fonlama</b></div>
      <div class="motor-health-row"><span>Bracket stop / hedef</span><b>${live.enabled ? "AKTİF" : "HAZIR"}</b></div>
      <div class="motor-health-row"><span>On-chain fundamental veri</span><b>BAĞLI DEĞİL</b></div>
      <div class="motor-health-row"><span>Çoklu borsa yürütme</span><b>BAĞLI DEĞİL · Binance</b></div>
      <div class="motor-health-row"><span>Eski skor motoru</span><b>KALDIRILDI · geçmiş korundu</b></div>
      <div class="workspace__muted" style="margin-top:9px">Bağlı olmayan veri kaynağı karar veriyormuş gibi gösterilmez. Yeni faktörler önce ölçümden geçer, sonra ana motora veto veya sıralama girdisi olarak eklenir.</div>`);

    const recent = data.recent || [];
    setHTML("motorRecent", recent.length ? `<div class="motor-card__title" style="margin-top:18px">Ana motor araştırma defteri</div>`
      + table(["Sembol", "Yön", "Durum", "Giriş", "Stop", "Hedef", "Net sonuç", "Çıkış"], recent.map(r => `<tr>
        <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(r.symbol)}">${VX.coinIcon(r.symbol, 20)}<span>${VX.esc(r.symbol)}</span></a></td>
        <td><span class="tag ${r.side === "LONG" ? "tag--long" : "tag--short"}">${VX.esc(r.side)}</span></td>
        <td>${r.status === "open" ? "izleniyor" : r.status === "invalid" ? "geçersiz / karantina" : "kapandı"}</td><td class="right">${VX.fmtPrice(r.entry)}</td>
        <td class="right">${VX.fmtPrice(r.stop)}</td><td class="right">${VX.fmtPrice(r.target)}</td>
        <td class="right ${Number(r.result_r) > 0 ? "delta--up" : Number(r.result_r) < 0 ? "delta--down" : ""}" title="Brüt ${r.gross_result_r ?? "—"}R · maliyet ${r.cost_r ?? "—"}R">${r.result_r === null ? "—" : `${r.result_r >= 0 ? "+" : ""}${r.result_r}R`}</td>
        <td>${VX.esc(r.exit_reason || "—")}</td></tr>`)) : `<div class="empty">Henüz araştırma kaydı yok.</div>`);
  }

  async function loadMotorCenter() {
    try { renderMotorCenter(await VX.get("/api/engine/control-center")); }
    catch (e) { setHTML("motorValidation", `<div class="empty">Motor merkezi okunamadı: ${VX.esc(e.message)}</div>`); }
  }

  /* ---------------------------------------------- Hızlı test motoru */
  function renderTestEngine(st) {
    const sc = st.scorecard || {};
    const o = sc.overall || { n: 0 };
    const cfg = st.config || {};
    // Dugmenin durumu render'da da yazilmali: yalnizca tiklamada
    // yazsaydim sayfa yenilendiginde motor calisirken "Başlat" gorunurdu.
    const tbtn = $("testToggleBtn");
    if (tbtn) {
      tbtn.textContent = st.enabled ? "Durdur" : "Başlat";
      tbtn.className = "btn " + (st.enabled ? "btn--danger" : "btn--primary");
    }
    setText("testMeta",
      (st.enabled ? "Çalışıyor" : "Durdu")
      + ` · ${cfg.hold_minutes} dk tutuş · ${Math.round((cfg.cycle_seconds || 3600) / 60)} dk çevrim`
      + ` · aynı anda ${cfg.concurrent} pozisyon · evren ${cfg.universe_size}`
      + ` · ${sc.open || 0} açık · ${o.n || 0} kapandı`
      + (st.last_cycle_at ? ` · son çevrim ${VX.fmtAgo(st.last_cycle_at)}` : "")
      + (st.last_error ? ` · HATA: ${st.last_error}` : ""));

    const kutu = (l, v, s) => `<div class="workspace__kpi"><div class="workspace__kpi-label">${l}</div><div class="workspace__kpi-value">${v}</div>${s ? `<div class="workspace__muted">${s}</div>` : ""}</div>`;
    const rcls = (v) => v > 0 ? "delta--up" : v < 0 ? "delta--down" : "";

    /* Guven araligi yoksa (az gozlem) OLMAYAN bir kesinlik uydurmuyoruz. */
    const ci = o.ci95 ? `%95 GA [${o.ci95[0]}, ${o.ci95[1]}]` : "güven aralığı için en az 5 işlem";
    const kpis = [
      kutu("Kapanmış işlem", o.n || 0, `${sc.open || 0} açık`),
      kutu("Ortalama sonuç", o.n ? `<span class="${rcls(o.avg_r)}">${o.avg_r >= 0 ? "+" : ""}${o.avg_r}R</span>` : "—", ci),
      kutu("İsabet", o.n ? o.win_rate + "%" : "—", o.n ? `toplam ${o.total_r >= 0 ? "+" : ""}${o.total_r}R` : ""),
      kutu("Sonuç anlamlı mı", o.significant === true ? "Evet" : o.n ? "Hayır" : "—",
           o.significant ? "sıfırdan ayırt edilebilir"
           : sc.n_needed ? `bu farkı kanıtlamak için ~${sc.n_needed} işlem gerekir` : "veri birikiyor"),
    ].join("");

    const varyant = (sc.variants || []).length
      ? `<div class="workspace__muted" style="margin:14px 0 6px">Parametre varyantları — motor kuralları kendi değiştirmiyor, hangisinin önde olduğunu <b>gösteriyor</b></div>`
        + table(["Varyant", "İşlem", "Ortalama", "Toplam", "İsabet", "%95 GA", "Anlamlı"],
            sc.variants.map(v => `<tr><td><b>${VX.esc(v.variant)}</b></td><td>${v.n}</td>
              <td class="right ${rcls(v.avg_r)}">${v.avg_r >= 0 ? "+" : ""}${v.avg_r}R</td>
              <td class="right">${v.total_r >= 0 ? "+" : ""}${v.total_r}R</td>
              <td class="right">${v.win_rate}%</td>
              <td class="right">${v.ci95 ? `[${v.ci95[0]}, ${v.ci95[1]}]` : "—"}</td>
              <td>${v.significant ? "evet" : "hayır"}</td></tr>`))
      : `<div class="workspace__muted" style="margin-top:12px">Varyant kırılımı için kapanmış işlem bekleniyor.</div>`;

    const acik = (st.open_rows || []).length
      ? `<div class="workspace__muted" style="margin:14px 0 6px">Şu an açık test pozisyonları</div>`
        + table(["Sembol", "Yön", "Varyant", "t", "Giriş", "Stop", "Hedef", "Kapanış"],
            st.open_rows.map(r => `<tr>
              <td><a class="sym" href="/analiz?symbol=${encodeURIComponent(r.symbol)}">${VX.coinIcon(r.symbol, 20)}<span>${VX.esc(r.symbol)}</span></a></td>
              <td><span class="tag ${r.side === "LONG" ? "tag--long" : "tag--short"}">${r.side}</span></td>
              <td>${VX.esc(r.meta.variant || "—")}</td>
              <td class="right">${r.meta.t_stat ?? "—"}</td>
              <td class="right">${VX.fmtPrice(r.entry)}</td>
              <td class="right">${VX.fmtPrice(r.stop)}</td>
              <td class="right">${VX.fmtPrice(r.target)}</td>
              <td>${VX.fmtIn(r.expires_at)}</td></tr>`))
      : "";

    const cikis = Object.entries(sc.exits || {});
    const cikislar = cikis.length
      ? `<div class="workspace__muted" style="margin-top:12px">Çıkış dağılımı: ${cikis.map(([k, v]) => `${k} ${v}`).join(" · ")}</div>` : "";

    setHTML("testEngineBody", `<div class="workspace__kpis">${kpis}</div>${varyant}${acik}${cikislar}`);
  }

  async function loadTestEngine() {
    try { renderTestEngine(await VX.get("/api/engine/test/status")); }
    catch (e) { setHTML("testEngineBody", `<div class="empty">Test motoru okunamadı: ${VX.esc(e.message)}</div>`); }
  }

  async function toggleTestEngine() {
    const btn = $("testToggleBtn");
    const enabled = btn.textContent.trim() !== "Durdur";   // "Başlat" ise ac
    btn.disabled = true;
    try {
      const st = await VX.post("/api/engine/test/toggle", { enabled });
      btn.textContent = st.enabled ? "Durdur" : "Başlat";
      btn.className = "btn " + (st.enabled ? "btn--danger" : "btn--primary");
      VX.toast(enabled ? "Hızlı test motoru başladı" : "Hızlı test motoru durdu");
      await loadTestEngine();
    } catch (e) { VX.toast(e.message, "err", 7000); }
    finally { btn.disabled = false; }
  }

  async function testCycleNow() {
    const btn = $("testCycleBtn");
    btn.disabled = true; btn.textContent = "Çevriliyor…";
    try {
      const r = await VX.post("/api/engine/test/cycle-now", {});
      VX.toast(r.ok ? `Çevrim bitti: ${r.closed} kapandı, ${r.opened} açıldı`
                    : "Çevrim hata verdi: " + r.error, r.ok ? "ok" : "err", 7000);
      await loadTestEngine();
    } catch (e) { VX.toast(e.message, "err", 7000); }
    finally { btn.disabled = false; btn.textContent = "Şimdi çevir"; }
  }

  async function bootSettings() {
    const openHash = () => {
      const id = decodeURIComponent(location.hash.slice(1));
      const target = document.getElementById(id);
      if (!target) return;
      if (target.matches("details")) target.open = true;
      let parent = target.parentElement;
      while (parent) { if (parent.matches("details")) parent.open = true; parent = parent.parentElement; }
    };
    openHash();
    window.addEventListener("hashchange", openHash);
    VX.onTeardown(() => window.removeEventListener("hashchange", openHash));
    const s = await VX.get("/api/auth/state"); $("settingsUsername").value = s.user.username; $("settingsDisplay").value = s.user.display_name || ""; $("settingsEmail").value = s.user.email || "";
    $("profileForm").addEventListener("submit", async e => { e.preventDefault(); const form = e.currentTarget; const fd = new FormData(form); const body = {}; for (const [k, v] of fd.entries()) if (String(v).trim()) body[k] = String(v); try { await VX.post("/api/auth/profile", body); VX.toast("Profil kaydedildi"); form.elements.current_password.value = ""; form.elements.new_password.value = ""; } catch (err) { VX.toast(err.message, "err", 7000); } });
    $("telegramForm").addEventListener("submit", async e => { e.preventDefault(); const form = e.currentTarget; const fd = new FormData(form); const body = {}; for (const [k, v] of fd.entries()) if (String(v).trim()) body[k] = String(v).trim(); try { await VX.post("/api/auth/profile", body); VX.toast("Telegram ayarları kaydedildi"); form.elements.telegram_token.value = ""; } catch (err) { VX.toast(err.message, "err", 7000); } });
    $("testTelegramBtn").addEventListener("click", async () => { try { await VX.post("/api/telegram/test", {}); VX.toast("Telegram bağlantısı başarılı"); } catch (e) { VX.toast(e.message, "err", 7000); } });
    /* Kullanıcının değiştirebildiği her şey bu sayfada: motor/bildirim,
       para katmanı ve yönetici için Binance bağlantısı. */
    await wireRuntimeForm("runtimeSettingsForm", "runtimeSaveState");
    await loadCopyStatus();
    $("copyToggleBtn")?.addEventListener("click", toggleCopy);
    $("liveToggleBtn")?.addEventListener("click", toggleLive);
    await wireRuntimeForm("copySettingsForm", "copySaveState", loadCopyStatus);
    if ($("tradingPanel")) await bootTrading();
    VX.interval(loadCopyStatus, 30000, false);
    if ($("runtimeSaveState")) $("runtimeSaveState").textContent = "Motor ve bildirim ayarları hazır";
    $("logoutBtn").addEventListener("click", async () => { await VX.post("/api/auth/logout", {}); window.location.href = "/giris"; });
    // Panel yalnizca yoneticiye render ediliyor (sunucu tarafinda {% if is_admin %}).
    // Yoksa bu blok hic calismaz.
    if ($("usersPanel")) await bootUsers();
    if ($("pbilKutu")) await bootPlanBildirim();
  }

  /* ---------------------------------------- Kurulum bildirimi durumu
     "Neden bildirim gelmiyor" en sik sorulacak sey ve tahminle
     cevaplanmamali. Onizleme sunucudaki AYNI secim mantigini calistirip
     her elenen kurulumun sebebini yaziyor — hicbir sey gondermeden. */
  async function bootPlanBildirim() {
    const ozet = $("pbilOzet"), liste = $("pbilListe");

    async function ciz() {
      ozet.textContent = "bakılıyor…";
      liste.innerHTML = "";
      let d;
      try { d = await VX.get("/api/mentor/bildirim/onizleme?interval=1h"); }
      catch (e) { ozet.textContent = "okunamadı: " + e.message; return; }

      if (d.hazir === false) {
        ozet.textContent = d.mesaj || "İlk tarama sürüyor…";
        return;
      }
      const s = d.durum || {};
      ozet.innerHTML = `${s.enabled ? "açık" : "<b>kapalı</b>"} · yön ${VX.esc(s.yon || "-")}
        · min ${s.min_rr}R · bugün ${s.bugun_gonderilen}/${s.gunluk_azami}
        · ${s.abonelik ? s.abonelik + " cihaz" : "<b>cihaz kayıtlı değil</b>"}`;

      const gid = (d.gonderilecek || []).map(m => `<div class="pbil__ok">
        <b>${VX.esc(m.baslik)}</b><pre>${VX.esc(m.govde)}</pre></div>`).join("");
      const eln = (d.elenen || []).map(e => `<div class="pbil__el">
        <span>${VX.esc((e.symbol || "").replace(/USDT$/, ""))}</span>${VX.esc(e.sebep)}</div>`).join("");
      liste.innerHTML = (gid || `<div class="workspace__muted">Şu an bildirilecek kurulum yok.</div>`) + eln;
    }

    $("pbilYenile").addEventListener("click", ciz);
    $("pbilDene").addEventListener("click", async () => {
      try {
        const r = await VX.post("/api/mentor/bildirim/dene?interval=1h", {});
        VX.toast(r.ok ? "Örnek bildirim gönderildi" : (r.hata || "Gönderilemedi"),
                 r.ok ? "ok" : "err", 6000);
      } catch (e) { VX.toast(e.message, "err", 7000); }
    });
    await ciz();
  }

  /* ---------------------------------------------- Binance bağlantısı */
  async function bootTrading() {
    $("tradeKeyForm").addEventListener("submit", async e => {
      e.preventDefault();
      const form = e.currentTarget, fd = new FormData(form);
      const body = { api_key: (fd.get("api_key") || "").trim(),
                     api_secret: (fd.get("api_secret") || "").trim() };
      if (!body.api_key || !body.api_secret) return VX.toast("İki alanı da doldur", "warn");
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; btn.textContent = "Kaydediliyor…";
      try {
        await VX.post("/api/trading/keys", body);
        form.reset();
        VX.toast("Anahtar şifrelenerek kaydedildi. Şimdi bağlantıyı test et.");
        await loadTrading();
      } catch (err) { VX.toast(err.message, "err", 9000); }
      finally { btn.disabled = false; btn.textContent = "Anahtarı kaydet"; }
    });
    $("tradeKeyDelete").addEventListener("click", async () => {
      if (!confirm("Kayıtlı API anahtarı silinsin mi?")) return;
      try { await VX.del("/api/trading/keys"); VX.toast("Anahtar silindi"); await loadTrading(); }
      catch (e) { VX.toast(e.message, "err"); }
    });
    $("tradeTestBtn").addEventListener("click", testTrading);
    await loadTrading();
  }

  async function loadTrading() {
    let st;
    try { st = await VX.get("/api/trading/status"); }
    catch (e) { $("tradeState").textContent = "Durum alınamadı: " + e.message; return; }
    const acc = st.account || {};
    const live = st.live || {};
    if (!st.has_keys) {
      $("tradeState").innerHTML = `<b>Anahtar kayıtlı değil.</b> Aşağıya yapıştırıp kaydet.`;
    } else if (acc.connected) {
      $("tradeState").innerHTML = `<b>Bağlı</b> · anahtar •••${VX.esc(st.hint || "")} · `
        + `cüzdan <b>${acc.wallet} USDT</b> · kullanılabilir ${acc.available} USDT · `
        + `açık pozisyon ${acc.open_positions} · emir yetkisi ${acc.can_trade ? "açık" : "KAPALI"}`;
    } else {
      $("tradeState").innerHTML = `<b>Anahtar kayıtlı (•••${VX.esc(st.hint || "")}) ama bağlanılamıyor.</b> `
        + `${VX.esc(acc.error || "Test et ve sebebi gör.")}`;
    }
    if (st.order_layer) {
      $("tradeChecks").innerHTML = `<div class="workspace__muted" style="margin-top:10px;font-size:12px">
        Emir katmanı hazır · canlı anahtar <b>${live.enabled ? "AÇIK" : "kapalı"}</b> ·
        ${live.open_live || 0} canlı pozisyon · ${live.protected || 0} borsa stopuyla korumalı.</div>`;
    } else {
      $("tradeChecks").innerHTML = `<div class="workspace__muted" style="margin-top:10px;font-size:12px">
        Bu sürümde emir gönderme katmanı <b>yok</b>. Anahtar bağlansa bile borsaya emir gitmez —
        yalnızca hesap okunur.</div>`;
    }
  }

  async function testTrading() {
    const b = $("tradeTestBtn"); b.disabled = true; b.textContent = "Test ediliyor…";
    try {
      const r = await VX.post("/api/trading/test", {});
      const ICON = { ok: "✓", err: "✕", warn: "!", info: "·" };
      const COLOR = { ok: "var(--up)", err: "var(--down)", warn: "var(--warn)", info: "var(--ink-3)" };
      $("tradeChecks").innerHTML =
        `<div class="workspace__blocks" style="margin-top:12px">`
        + ([...(r.checks || []), ...((r.live_preflight || {}).checks || [])]).map(c => `<div class="workspace__dist-item" style="border-left:3px solid ${COLOR[c.level] || "var(--border)"}">
             <div style="display:block"><b style="color:${COLOR[c.level]}">${ICON[c.level] || "·"} ${VX.esc(c.name)}</b>
             <div style="font-size:11.5px;color:var(--ink-2);margin-top:3px;line-height:1.45">${VX.esc(c.detail)}</div></div>
           </div>`).join("")
        + `</div>`
        + (r.safe === false
            ? `<div class="workspace__warning" style="margin-top:12px"><b>GÜVENLİ DEĞİL:</b>
               Bu anahtarla devam etme. Yukarıdaki kırmızı maddeleri Binance'te düzelt.</div>`
            : r.ok ? `<div class="workspace__muted" style="margin-top:10px;color:var(--up)">Bağlantı ve güvenlik denetimi geçti.</div>` : "");
      VX.toast(r.ok ? (r.safe ? "Bağlantı ve güvenlik denetimi geçti" : "Bağlantı var ama GÜVENLİK sorunu var")
                    : "Bağlantı kurulamadı — detaylar panelde",
               r.ok && r.safe ? "ok" : "err", 9000);
      await loadTrading();
    } catch (e) { VX.toast(e.message, "err", 9000); }
    finally { b.disabled = false; b.textContent = "Bağlantıyı test et"; }
  }

  /* ------------------------------------------------- kullanici yonetimi */
  async function bootUsers() {
    $("usersRefreshBtn").addEventListener("click", loadUsers);
    $("userCreateForm").addEventListener("submit", async e => {
      e.preventDefault();
      const form = e.currentTarget, fd = new FormData(form), body = {};
      for (const [k, v] of fd.entries()) if (String(v).trim()) body[k] = String(v).trim();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; btn.textContent = "Ekleniyor…";
      try {
        const r = await VX.post("/api/users", body);
        VX.toast(`${r.user.username} eklendi`);
        form.reset();
        await loadUsers();
      } catch (err) { VX.toast(err.message, "err", 8000); }
      finally { btn.disabled = false; btn.textContent = "Kullanıcı ekle"; }
    });
    await loadUsers();
  }

  async function loadUsers() {
    let d;
    try { d = await VX.get("/api/users"); }
    catch (e) { $("usersMeta").textContent = "Liste alınamadı: " + e.message; return; }
    const admins = d.admin_count;
    $("usersMeta").textContent = `${d.items.length} kullanıcı · ${admins} aktif yönetici`;

    const rows = d.items.map(u => {
      const u2 = u.usage || {};
      const kayit = (u2.trades || 0) + (u2.signals || 0);
      return `<tr data-id="${u.id}">
        <td><div class="sym">
          <span class="sym__badge">${VX.esc((u.display_name || u.username).slice(0, 2).toUpperCase())}</span>
          <div><div style="font-size:12.8px">${VX.esc(u.username)}${u.is_self ? ' <span class="tag">sen</span>' : ""}</div>
          <div style="font-size:10.5px;color:var(--ink-3)">${VX.esc(u.display_name || "—")}${u.email ? " · " + VX.esc(u.email) : ""}</div></div>
        </div></td>
        <td><span class="tag ${u.role === "admin" ? "tag--warn" : ""}">${u.role === "admin" ? "Yönetici" : "Kullanıcı"}</span></td>
        <td><span class="tag ${u.is_active ? "tag--long" : "tag--short"}">${u.is_active ? "Aktif" : "Pasif"}</span></td>
        <td class="right" style="font-size:12px;color:var(--ink-3)">${kayit}</td>
        <td style="font-size:11.5px;color:var(--ink-3)">${u.last_login ? VX.fmtAgo(u.last_login) : "hiç girmedi"}</td>
        <td class="right"><div class="panel__actions" style="justify-content:flex-end;flex-wrap:wrap;gap:5px">
          ${u.is_self ? "" : `
          <button class="btn btn--sm" data-act="role">${u.role === "admin" ? "Yetkiyi al" : "Yönetici yap"}</button>`}
          <button class="btn btn--sm" data-act="pw">Şifre</button>
          ${u.is_self ? `<span class="workspace__muted" style="font-size:11px;align-self:center">kendi hesabın</span>` : `
          <button class="btn btn--sm" data-act="active">${u.is_active ? "Pasife al" : "Aktifleştir"}</button>
          <button class="btn btn--sm btn--danger" data-act="del">Sil</button>`}
        </div></td></tr>`;
    });
    $("usersTable").innerHTML = table(
      ["Kullanıcı", "Yetki", "Durum", "Kayıt", "Son giriş", ""], rows);

    $("usersTable").querySelectorAll("button[data-act]").forEach(btn => {
      btn.addEventListener("click", () => {
        const tr = btn.closest("tr[data-id]");
        const u = d.items.find(x => String(x.id) === tr.dataset.id);
        userAction(btn.dataset.act, u);
      });
    });
  }

  async function userAction(act, u) {
    try {
      if (act === "role") {
        const yeni = u.role === "admin" ? "user" : "admin";
        if (!confirm(`${u.username} → ${yeni === "admin" ? "Yönetici" : "Kullanıcı"}?`)) return;
        await VX.post(`/api/users/${u.id}/role`, { role: yeni });
        VX.toast("Yetki güncellendi");
      } else if (act === "active") {
        if (!confirm(u.is_active
          ? `${u.username} pasife alınsın mı? Açık oturumu anında kapanır.`
          : `${u.username} tekrar aktifleştirilsin mi?`)) return;
        await VX.post(`/api/users/${u.id}/active`, { is_active: !u.is_active });
        VX.toast(u.is_active ? "Hesap pasife alındı" : "Hesap aktifleştirildi");
      } else if (act === "pw") {
        const pw = prompt(`${u.username} için yeni şifre (en az 8 karakter):`);
        if (!pw) return;
        if (pw.length < 8) return VX.toast("Şifre en az 8 karakter olmalı", "warn");
        await VX.post(`/api/users/${u.id}/password`, { new_password: pw });
        VX.toast("Şifre sıfırlandı — kullanıcıya kendin ilet");
      } else if (act === "del") {
        if (!confirm(`${u.username} SİLİNSİN mi? Bu geri alınamaz.`)) return;
        try {
          await VX.del(`/api/users/${u.id}`);
        } catch (err) {
          // 409 = kullanicinin kayitlari var; sunucu neyin silinecegini sayip
          // soyluyor, onayi ondan SONRA aliyoruz.
          if (!/işlemi|sinyali/.test(err.message)) throw err;
          if (!confirm(err.message + "\n\nBu kayıtlar da silinsin mi?")) return;
          await VX.del(`/api/users/${u.id}?purge=true`);
        }
        VX.toast("Kullanıcı silindi");
      }
      await loadUsers();
    } catch (e) { VX.toast(e.message, "err", 8000); }
  }

  /* ================================================================
     PORTFOY — BINANCE HESABININ CANLI OZETI

     Kullanicinin istegi "bakiyemi Binance'ten otomatik gorsun"du. Veri
     sistemde ZATEN vardi; sorun onun yalnizca yonetici kilidi arkasinda,
     kapali bir <details> icinde ve TEK SEFER ciziliyor olmasiydi.

     Burada uc sey farkli:
       1) current_user ile aciliyor — kendi bakiyeni gormek yonetici isi degil
       2) 15 saniyede bir tazeleniyor
       3) baglanti kopunca SESSIZ KALMIYOR: bayat veriyi bayat oldugunu
          yazarak gosteriyor. Eski bir sayiyi guncelmis gibi sunmak,
          hic gostermemekten daha tehlikeli.
     ================================================================ */
  /* SAYI BICIMI TEK YERDE VE TURKCE.
     Ilk surumde para "$405,25" (virgullu, tr-TR) ama yuzde "44.39%"
     (noktali, en-US) cikiyordu; ayni satirda iki farkli ondalik
     isareti okuyani sayinin hangisi oldugundan suphelendiriyor.
     Ayrica negatif para "$-0,02" olarak yaziliyordu — isaret para
     biriminin ONUNE gelmeli, yoksa goz once "$0" gorup sonra
     duzeltiyor. */
  function pfPara(x, basamak) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    const n = Number(x);
    const b = basamak === undefined ? 2 : basamak;
    const govde = Math.abs(n).toLocaleString("tr-TR",
      {minimumFractionDigits: b, maximumFractionDigits: b});
    return (n < 0 ? "−$" : "$") + govde;
  }

  function pfYuzde(x, basamak) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    const n = Number(x);
    const b = basamak === undefined ? 2 : basamak;
    return (n < 0 ? "−" : n > 0 ? "+" : "") + "%" +
      Math.abs(n).toLocaleString("tr-TR",
        {minimumFractionDigits: b, maximumFractionDigits: b});
  }

  const pfIsaret = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");

  function pfSatir(ad, deger, sinif, ipucu) {
    return `<div class="pf-satir${sinif === "vurgu" ? " pf-satir--vurgu" : ""}">
      <span class="pf-satir__ad">${VX.esc(ad)}${ipucu
        ? `<i class="pf-ipucu" title="${VX.esc(ipucu)}">?</i>` : ""}</span>
      <span class="pf-satir__deger ${sinif && sinif !== "vurgu" ? sinif : ""}">${deger}</span>
    </div>`;
  }

  function pfPozSatiri(p) {
    const yon = p.yon === "LONG" ? "up" : "down";
    const pnl = pfIsaret(p.pnl);
    const roi = pfIsaret(p.roi);
    return `<div class="pf-poz">
      <div class="pf-poz__sol">
        ${VX.coinIcon(p.symbol, 18)}
        <span class="pf-poz__sym">${VX.esc(p.symbol.replace(/USDT$/, ""))}</span>
        <span class="pf-poz__yon ${yon}">${p.yon}</span>
        ${p.kaldirac ? `<span class="pf-poz__kald">${p.kaldirac}x</span>` : ""}
      </div>
      <div class="pf-poz__pnl ${pnl}">${p.pnl > 0 ? "+" : ""}${pfPara(p.pnl)}</div>
      <div class="pf-poz__alt">
        <span>giriş ${VX.fmtPrice(p.giris)}</span>
        <span>mark ${VX.fmtPrice(p.mark)}</span>
        <span>${pfPara(p.notional)} notional</span>
      </div>
      <div class="pf-poz__roi ${roi}">${pfYuzde(p.roi)}</div>
    </div>`;
  }

  function pfBagliDegil(d) {
    const anahtar = d.sebep === "anahtar_yok";
    return `<div class="pf-bos">
      <b>${VX.esc(d.mesaj || "Binance'e ulaşılamadı.")}</b><br>
      ${anahtar
        ? `Portföy ve otomatik günlük, Binance API anahtarı olmadan çalışmaz.
           <br><a class="btn btn--sm btn--primary" href="/ayarlar#trading-settings">Anahtarı bağla</a>`
        : `Bağlantının hangi adımda kırıldığını sağdaki <b>Teşhis</b> düğmesi söyler.`}
    </div>`;
  }

  function pfCiz(d) {
    const hero = $("pfHero");
    if (!hero) return;

    // BAGLI DEGIL: butun kartlar ayni sebebi tekrar etmesin, tek yerde soyle.
    if (!d.ok && !d.bayat) {
      $("pfOzkaynak").textContent = "—";
      // TEKRAR DUZELTMESI: buraya da d.mesaj yaziliyordu ve ayni cumle
      // ("Binance API anahtari bagli degil.") ekranda IKI kez cikiyordu —
      // ustelik yukaridaki yorum tam da bunu yapmamayi soyluyordu.
      // Sebep tek yerde (Hesap kartinda) anlatiliyor; buradaki satir
      // yalnizca DURUMU soyluyor.
      $("pfDegisim").textContent = d.sebep === "anahtar_yok"
        ? "bağlı değil" : "okunamadı";
      $("pfSatirlar").innerHTML = pfBagliDegil(d);
      $("pfPozisyonlar").innerHTML =
        `<div class="pf-bos pf-bos--ince">Bağlantı kurulunca açık pozisyonlar
          burada 90 saniyede bir tazelenir.</div>`;
      $("pfPozSayi").textContent = "—";
      $("pfRisk").innerHTML = `<div class="pf-risk__not">Bağlantı yokken risk hesaplanamaz.</div>`;
      $("pfBar").hidden = true;
      $("pfBarNot").textContent = "";
      return;
    }

    $("pfOzkaynak").textContent = pfPara(d.ozkaynak);
    const g = d.gerceklesmemis || 0;
    $("pfDegisim").innerHTML = d.pozisyon_sayisi
      ? `Açık pozisyonlarda <b class="${pfIsaret(g)}">${g > 0 ? "+" : ""}${pfPara(g)}</b>
         · ${d.pozisyon_sayisi} pozisyon`
      : `Açık pozisyon yok — tamamı nakit.`;

    const yas = d.yas_sn;
    $("pfTazelik").textContent = d.bayat ? "bağlantı yok"
      : (yas === undefined || yas < 2) ? "az önce" : `${Math.round(yas)} sn önce`;

    // MARJ KULLANIMI. Bar, "ne kadarim bagli" sorusunu tek bakista
    // cevapliyor; sayilar satirlarda zaten var ama oran orada gorunmuyor.
    const oz = d.ozkaynak || 0;
    const kullanilan = d.kullanilan_marj || 0;
    if (oz > 0) {
      const yuzde = Math.min(100, kullanilan / oz * 100);
      $("pfBar").hidden = false;
      const dolu = $("pfBarDolu");
      dolu.style.width = yuzde.toFixed(1) + "%";
      dolu.classList.toggle("is-yuksek", yuzde >= 50 && yuzde < 80);
      dolu.classList.toggle("is-kritik", yuzde >= 80);
      $("pfBarNot").innerHTML = `Özkaynağının
        <b>${pfYuzde(yuzde, 1).replace("+", "")}</b>'i marj olarak bağlı
        · ${pfPara(d.kullanilabilir)} serbest`;
    } else {
      $("pfBar").hidden = true;
      $("pfBarNot").textContent = "";
    }

    $("pfSatirlar").innerHTML =
      (d.bayat ? `<div class="pf-bayat">${VX.esc(d.mesaj || "")}</div>` : "")
      + pfSatir("Cüzdan bakiyesi", pfPara(d.cuzdan), null,
                "Gerçekleşmiş bakiye. Açık pozisyonların kâr/zararını İÇERMEZ.")
      + pfSatir("Gerçekleşmemiş K/Z", (g > 0 ? "+" : "") + pfPara(g), pfIsaret(g),
                "Açık pozisyonlar şu an kapatılsaydı oluşacak kâr/zarar.")
      + pfSatir("Özkaynak", pfPara(d.ozkaynak), "vurgu",
                "Cüzdan + gerçekleşmemiş K/Z. Portföy değeri budur.")
      + pfSatir("Kullanılabilir", pfPara(d.kullanilabilir), null,
                "Yeni pozisyon açmak için serbest teminat.")
      + pfSatir("Kullanılan marj", pfPara(d.kullanilan_marj), null,
                "Açık pozisyonlara bağlanmış teminat.")
      + pfSatir("Açık notional", pfPara(d.toplam_notional), null,
                "Pozisyonların toplam büyüklüğü — teminat değil, POZİSYON değeri.");

    const poz = d.pozisyonlar || [];
    $("pfPozSayi").textContent = poz.length ? `${poz.length} pozisyon` : "yok";
    $("pfPozisyonlar").innerHTML = poz.length
      ? poz.map(pfPozSatiri).join("")
      : `<div class="pf-bos">Açık pozisyonun yok.<br>
         <span class="workspace__muted">Bu da bir durum — beklemek bir karardır.</span></div>`;

    pfRiskCiz(d);
  }

  /* GERCEK KALDIRAC — tek tek pozisyon kaldiraclarinin gostermedigi sey.
     Binance her pozisyonda "3x" yaziyor olabilir; onemli olan TOPLAM
     notional'in ozkaynaga orani. Uc pozisyon 3x ise gercek kaldirac 9x'e
     yaklasir ve likidasyon mesafesi buna gore daralir. */
  function pfRiskCiz(d) {
    const el = $("pfRisk");
    if (!el) return;
    const kald = d.kaldirac_orani;
    const oz = d.ozkaynak || 0;
    const uyari = [];
    if (kald !== null && kald !== undefined && kald >= 3)
      uyari.push(`Gerçek kaldıracın <b>${Number(kald).toLocaleString("tr-TR",
        {minimumFractionDigits: 2, maximumFractionDigits: 2})}x</b>. Fiyat aleyhine
        <b>${pfYuzde(100 / kald, 1).replace("+", "")}</b> giderse
        özkaynağının tamamı gider.`);
    if (oz > 0 && (d.kullanilan_marj || 0) / oz >= 0.8)
      uyari.push(`Özkaynağının %80'inden fazlası marjda bağlı — tek bir ters
        hareket ek teminat çağırabilir.`);

    el.innerHTML =
      `<div class="pf-risk__satir"><span class="pf-risk__ad">Gerçek kaldıraç</span>
        <span class="pf-risk__deger">${kald === null || kald === undefined ? "—"
          : Number(kald).toLocaleString("tr-TR", {minimumFractionDigits: 2,
              maximumFractionDigits: 2}) + "x"}</span></div>`
      + `<div class="pf-risk__satir"><span class="pf-risk__ad">Pozisyon sayısı</span>
        <span class="pf-risk__deger">${d.pozisyon_sayisi ?? "—"}</span></div>`
      + `<div class="pf-risk__satir"><span class="pf-risk__ad">İşlem yetkisi</span>
        <span class="pf-risk__deger">${d.islem_yetkisi ? "var" : "yok"}</span></div>`
      + uyari.map(u => `<div class="pf-risk__uyari">${u}</div>`).join("")
      + `<div class="pf-risk__not">Gerçek kaldıraç = açık notional / özkaynak.
         Tek tek pozisyonlardaki kaldıraç bunu göstermez: üç pozisyon 3x ise
         gerçek kaldıracın 9x'e yaklaşır.</div>`;
  }

  async function pfYukle(taze) {
    try {
      const d = await VX.get("/api/mentor/portfoy" + (taze ? "?taze=1" : ""));
      pfCiz(d);
    } catch (e) {
      const el = $("pfDegisim");
      if (el) el.textContent = "Portföy okunamadı: " + e.message;
    }
  }

  async function pfTeshisCalistir() {
    const el = $("pfTeshisKutu");
    el.innerHTML = `<div class="workspace__muted">Sınanıyor…</div>`;
    try {
      const d = await VX.get("/api/mentor/senkron/teshis");
      const adimlar = d.adimlar || d.checks || [];
      el.innerHTML = adimlar.length
        ? adimlar.map(a => `<div class="pf-teshis__adim">
            <span class="pf-teshis__nokta ${VX.esc(a.seviye || a.level || "")}"></span>
            <span><span class="pf-teshis__ad">${VX.esc(a.ad || a.name || "")}</span>
              <span class="pf-teshis__mesaj">${VX.esc(a.mesaj || a.detail || "")}</span></span>
          </div>`).join("")
        : `<div class="workspace__muted">${VX.esc(d.mesaj || "Teşhis çıktısı boş.")}</div>`;
    } catch (e) {
      el.innerHTML = `<div class="workspace__muted">Teşhis çalıştırılamadı: ${VX.esc(e.message)}</div>`;
    }
  }

  async function bootPortfoy() {
    $("pfYenile")?.addEventListener("click", () => pfYukle(true));
    $("pfTeshis")?.addEventListener("click", pfTeshisCalistir);
    await pfYukle(false);
    // 15 saniye: bakiye saniyede bir degismiyor ve her istek imzali bir
    // Binance cagrisi. Sunucu tarafinda 10 saniyelik onbellek var, yani
    // birden fazla sekme acikken bile borsaya giden istek sayisi sabit.
    VX.interval(() => pfYukle(false), 15000, false);
  }

  /* ================================================================
     MAKRO PANEL — kripto disi baglam.

     BU EKRAN SINYAL URETMIYOR. Sayilari gosteriyor, yorum yapmiyor;
     karar motoru ayri yazilacak.

     VERI YOKSA "VERI BEKLENIYOR" — sifir degil, tire degil, eski deger
     degil. Bir fiyat alaninda gorunen 0, gercek bir fiyat gibi okunur
     ve yanlis karar urettirir.
     ================================================================ */
  const MKR_GRUP = {endeks: "Endeksler", maden: "Değerli madenler", makro: "Makro"};

  /* Isaret ONCE, yuzde SONRA: "−%0,82".
     Ilk yazimda zincirlemeyi yanlis sirayla kurmustum ve "%−0,82"
     cikiyordu — isaret yuzde isaretinin ARDINDA. Kucuk gorunuyor ama
     ayni ekranda portfoy tarafi "−%0,82" yaziyor; iki bicim yan yana
     kullaniciyi hangisinin dogru oldugundan supheye dusurur. */
  function mkrYuzde(d) {
    if (d === null || d === undefined || isNaN(d)) return "—";
    const n = Number(d);
    const iz = n > 0 ? "+" : n < 0 ? "−" : "";
    return iz + "%" + Math.abs(n).toLocaleString("tr-TR",
      {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }

  function mkrKart(x) {
    if (x.veri_yok) {
      return `<div class="mkr__k mkr__k--yok">
        <span class="mkr__ad">${VX.esc(x.ad)}</span>
        <b class="mkr__f">Veri bekleniyor</b>
        <span class="mkr__not" title="${VX.esc(x.sebep || "")}">kaynak cevap vermedi</span>
      </div>`;
    }
    const d = x.degisim;
    const yon = d > 0 ? "up" : d < 0 ? "down" : "";
    // Degisim tabani kaynaga gore farkli. Ikisini ayni etiketle
    // sunmak yaniltirdi; satirda hangisi oldugu yaziyor.
    const taban = x.degisim_tabani === "acilis" ? "açılışa göre" : "önceki kapanışa göre";
    return `<div class="mkr__k">
      <span class="mkr__ad">${VX.esc(x.ad)}
        <i class="mkr__kay">${VX.esc(x.kaynak || "")}</i></span>
      <b class="mkr__f">${x.fiyat === null || x.fiyat === undefined ? "—"
        : Number(x.fiyat).toLocaleString("tr-TR",
            {minimumFractionDigits: 2, maximumFractionDigits: 4})}</b>
      <span class="mkr__d ${yon}">${mkrYuzde(d)}</span>
      <span class="mkr__not">${VX.esc(taban)}${x.piyasa_durumu
        ? ` · ${VX.esc(String(x.piyasa_durumu).toLowerCase())}` : ""}</span>
    </div>`;
  }

  async function mkrYukle(taze) {
    const el = $("mkrIzgara");
    if (!el) return;
    try {
      const d = await VX.get("/api/makro/ozet" + (taze ? "?taze=1" : ""));
      const satirlar = d.satirlar || [];
      if (!satirlar.length) {
        el.innerHTML = `<div class="empty">${VX.esc(d.sebep || "Veri bekleniyor.")}</div>`;
        return;
      }
      const gruplar = {};
      satirlar.forEach(x => (gruplar[x.grup] = gruplar[x.grup] || []).push(x));
      el.innerHTML = Object.keys(MKR_GRUP)
        .filter(g => gruplar[g] && gruplar[g].length)
        .map(g => `<div class="mkr__grup"><span>${VX.esc(MKR_GRUP[g])}</span>
          <div class="mkr__satir">${gruplar[g].map(mkrKart).join("")}</div></div>`)
        .join("");
      const y = $("mkrYas");
      if (y) {
        // Kac varlik okunabildigi ACIKCA yaziliyor: alti varliktan
        // ikisi geldiyse kullanici bunu bilmeli.
        y.textContent = `${d.okunan}/${d.toplam} varlık` +
          (d.yas_sn > 2 ? ` · ${Math.round(d.yas_sn)} sn önce` : " · az önce");
      }
    } catch (e) {
      el.innerHTML = `<div class="empty">Makro verisi okunamadı: ${VX.esc(e.message)}</div>`;
    }
  }

  async function mkrTeshisCalistir() {
    const el = $("mkrTeshisKutu");
    el.innerHTML = `<div class="workspace__muted">Kaynaklar sınanıyor…</div>`;
    try {
      const d = await VX.get("/api/makro/teshis");
      el.innerHTML = (d.denemeler || []).map(t => `<div class="mkr__t">
        <span class="mkr__tn ${t.ok ? "ok" : "err"}"></span>
        <span><b>${VX.esc(t.kaynak)}</b> <i>${VX.esc(t.sembol || "")}</i>
          <span>${t.ok
            ? `fiyat ${VX.esc(String((t.sonuc || {}).fiyat))} · ${t.sure_ms} ms`
            : VX.esc(t.hata || "hata")}</span></span>
      </div>`).join("") +
        (d.not ? `<div class="mkr__tnot">${VX.esc(d.not)}</div>` : "");
    } catch (e) {
      el.innerHTML = `<div class="workspace__muted">Teşhis çalıştırılamadı: ${VX.esc(e.message)}</div>`;
    }
  }

  async function bootMakro() {
    $("mkrYenile")?.addEventListener("click", () => mkrYukle(true));
    $("mkrTeshis")?.addEventListener("click", mkrTeshisCalistir);
    await mkrYukle(false);
    // 60 saniye: sunucu tarafinda da 60 saniyelik onbellek var, yani
    // birden fazla sekme acikken bile disariya giden istek sayisi sabit.
    VX.interval(() => mkrYukle(false), 60000, false);
  }

  async function boot() {
    const m = document.querySelector("main[data-page]");
    page = (m && m.getAttribute("data-page")) || window.VORTEX_PAGE;
    try {
      if (page === "analiz") bootCommon().catch(() => {});
      else await bootCommon();
      if (page === "mentor") { await bootMentor(); await bootGunluk(); }
      else if (page === "portfoy") await bootPortfoy();
      else if (page === "makro") await bootMakro();
      else if (page === "markets") await bootMarkets();
      else if (page === "analiz") await bootAnalysis();
      else if (page === "rsi") { $("scanRsiBtn").addEventListener("click", scanRsi); await scanRsi(); }
      else if (page === "haber") { $("refreshNewsBtn").addEventListener("click", loadNews); $("sendNewsPageBtn").addEventListener("click", sendNews); await loadNews(); }
      else if (page === "takvim") { $("refreshCalendarBtn").addEventListener("click", loadCalendar); $("sendCalendarPageBtn").addEventListener("click", sendCalendar); await loadCalendar(); }
      else if (page === "karne") { $("selftestBtn").addEventListener("click", runSelftest); await loadKarne(); VX.interval(loadKarne, 60000, false); }
      else if (page === "motor") {
        $("motorCenterRefresh")?.addEventListener("click", loadMotorCenter);
        $("tsmomScanBtn").addEventListener("click", tsmomScan);
        $("tsmomToggleBtn").addEventListener("click", tsmomToggle);
        await wireRuntimeForm("tsmomSettingsForm", "tsmomSaveState", loadTsmom);
        await loadMotorCenter(); await loadTsmom();
        VX.interval(loadMotorCenter, 30000, false);
        VX.interval(loadTsmom, 30000, false);
      }
      else if (page === "test") {
        $("testToggleBtn").addEventListener("click", toggleTestEngine);
        $("testCycleBtn").addEventListener("click", testCycleNow);
        await loadTestEngine();
        VX.interval(loadTestEngine, 30000, false);
      }
      else if (page === "ayarlar") await bootSettings();
    } catch (e) { if (page === "analiz") analysisLoad("Grafik başlatılamadı: " + e.message, true); VX.toast("Sayfa yüklenemedi: " + e.message, "err", 8000); }
  }

  // Ilk yukleme ve her gezinme ayni yoldan geciyor.
  VX.registerBoot(boot);
})();
