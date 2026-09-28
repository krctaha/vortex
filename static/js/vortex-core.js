/* VORTEX çekirdek: API istemcisi, canlı fiyat soketi, biçimleyiciler, bildirim.
   Global tek nesne: window.VX — başka global tanımlanmaz. */
(function () {
  "use strict";

  const VX = {};

  function apiErrorText(data, status) {
    const raw = data && (data.detail !== undefined ? data.detail : data.error);
    if (typeof raw === "string" && raw.trim()) return raw;
    if (Array.isArray(raw)) {
      const msgs = raw.map(x => x && (x.msg || x.message || x.detail)).filter(Boolean);
      if (msgs.length) return msgs.join(" · ");
    }
    if (raw && typeof raw === "object") {
      const title = raw.message || raw.error || raw.detail || "İşlem tamamlanamadı";
      const failed = Array.isArray(raw.checks)
        ? raw.checks.filter(x => x && x.ok === false)
            .map(x => `${x.name || "Kontrol"}: ${x.detail || "başarısız"}`)
        : [];
      return [title, ...failed].filter(Boolean).join(" — ");
    }
    return "HTTP " + status;
  }

  /* ---------------------------------------------------------------- API */
  async function api(path, options = {}) {
    const opts = {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      ...options,
    };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (_) { data = { detail: text }; } }
    if (!res.ok) {
      if (res.status === 401 && !path.startsWith("/api/auth")) {
        window.location.href = "/giris";
        return null;
      }
      const err = new Error(apiErrorText(data, res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }
  VX.api = api;
  VX.get = (p) => api(p);
  VX.post = (p, body) => api(p, { method: "POST", body: body || {} });
  VX.del = (p) => api(p, { method: "DELETE" });

  /* -------------------------------------------------------- Biçimleyici */
  function decimalsFor(value) {
    const v = Math.abs(value);
    if (v >= 100) return 2;
    if (v >= 1) return 3;
    if (v >= 0.01) return 5;
    return 7;
  }

  VX.fmtPrice = function (value, decimals) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    const d = decimals === undefined ? decimalsFor(value) : decimals;
    return Number(value).toLocaleString("tr-TR", {
      minimumFractionDigits: d, maximumFractionDigits: d,
    });
  };

  VX.fmtPct = function (value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    return (value > 0 ? "+" : "") + Number(value).toFixed(digits) + "%";
  };

  VX.fmtCompact = function (value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    const abs = Math.abs(value);
    const units = [[1e12, "T"], [1e9, "Mr"], [1e6, "Mn"], [1e3, "B"]];
    for (const pair of units) {
      if (abs >= pair[0]) return (value / pair[0]).toFixed(abs / pair[0] >= 100 ? 0 : 1) + pair[1];
    }
    return Number(value).toFixed(0);
  };

  VX.fmtUsd = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
    ? "—" : "$" + Number(v).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });

  VX.fmtTime = function (ms) {
    if (!ms) return "—";
    return new Date(ms).toLocaleString("tr-TR", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  };

  VX.fmtAgo = function (ms) {
    if (!ms) return "—";
    const diff = Math.floor((Date.now() - ms) / 1000);
    if (diff < 60) return "az önce";
    if (diff < 3600) return Math.floor(diff / 60) + " dk önce";
    if (diff < 86400) return Math.floor(diff / 3600) + " sa önce";
    return Math.floor(diff / 86400) + " gün önce";
  };

  VX.fmtIn = function (ms) {
    if (!ms) return "—";
    const diff = Math.floor((ms - Date.now()) / 1000);
    if (diff < 0) return "geçti";
    if (diff < 3600) return Math.floor(diff / 60) + " dk sonra";
    if (diff < 86400) return Math.floor(diff / 3600) + " sa sonra";
    return Math.floor(diff / 86400) + " gün sonra";
  };

  VX.deltaClass = (v) => v > 0 ? "delta--up" : v < 0 ? "delta--down" : "delta--flat";

  VX.esc = function (s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };

  VX.greeting = function () {
    const h = new Date().getHours();
    if (h < 6) return "İyi geceler";
    if (h < 12) return "Günaydın";
    if (h < 18) return "İyi günler";
    if (h < 22) return "İyi akşamlar";
    return "İyi geceler";
  };

  /* ------------------------------------------------------------ Toast */
  VX.toast = function (message, kind = "ok", timeout = 4200) {
    let host = document.querySelector(".toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "toasts";
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = "toast toast--" + kind;
    el.innerHTML = "<span>" + VX.esc(message) + "</span>";
    host.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .25s, transform .25s";
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      setTimeout(() => el.remove(), 260);
    }, timeout);
  };

  /* --------------------------------------------------- Canlı fiyat WS */
  const Live = {
    socket: null,
    ticks: Object.create(null),
    handlers: new Set(),
    bookHandlers: new Set(),
    orderbook: null,
    watched: new Set(),
    backoff: 1000,
    pending: new Map(),
    flushTimer: null,
    mode: "?",
    lastMessageAt: 0,
    lastFreshAt: 0,
    serverOffsetMs: null,

    paintStatus() {
      const label=document.getElementById('modeText'),chip=document.getElementById('modeChip');
      if(!label||!chip)return;
      let state='reconnecting',text='Yeniden bağlanıyor';
      if(Live.socket?.readyState===0){state='connecting';text='Bağlanıyor';}
      else if(Live.socket?.readyState===1){
        if(Live.mode==='demo'){state='demo';text='DEMO veri';}
        else if(Live.lastFreshAt&&Date.now()-Live.lastFreshAt<5000){state='live';text='Canlı veri';}
        else {state='stale';text='Veri bekleniyor';}
      }
      label.textContent=text;chip.dataset.streamState=state;
      chip.title=state==='live'?'WebSocket bağlı; güncel piyasa işlemleri alınıyor.':state==='demo'?'Sentetik veri; gerçek piyasa değil.':'Güncel işlem akışı henüz doğrulanmadı.';
    },

    connect() {
      if (Live.socket && Live.socket.readyState < 2) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      let sock;
      try { sock = new WebSocket(proto + "://" + location.host + "/ws/live"); }
      catch (_) { return setTimeout(Live.connect, Live.backoff); }
      Live.socket = sock;
      Live.paintStatus();

      sock.onopen = () => {
        Live.backoff = 1000;
        Live.lastMessageAt = Date.now();
        Live.lastFreshAt = 0;
        Live.paintStatus();
        if (Live.watched.size) Live.send({ type: "watch", symbols: [...Live.watched] });
      };
      sock.onmessage = (ev) => {
        Live.lastMessageAt = Date.now();
        let msg; try { msg = JSON.parse(ev.data); } catch (_) { return; }
        if (Number.isFinite(msg.server_time_ms)) {
          const offset = Date.now() - msg.server_time_ms;
          Live.serverOffsetMs = Live.serverOffsetMs === null ? offset : Math.min(Live.serverOffsetMs, offset);
        }
        if(msg.orderbook || msg.type==='snapshot'){
          Live.orderbook=msg.orderbook||null;
          Live.bookHandlers.forEach(fn=>{try{fn(Live.orderbook);}catch(e){console.error(e);}});
        }
        if (msg.type === "snapshot") {
          Live.mode = msg.mode;
          Object.assign(Live.ticks, msg.ticks || {});
          Live.schedule();
        } else if (msg.type === "batch") {
          for (const tick of msg.ticks || []) {
            if (Live.ticks[tick.symbol]?.ts > tick.ts) continue;
            Live.ticks[tick.symbol] = tick;
            Live.pending.set(tick.symbol,tick);
          }
          if(msg.ticks?.length)Live.schedule();
        } else if (msg.type === "tick") {
          Live.ticks[msg.symbol] = msg;
          Live.pending.set(msg.symbol, msg);
          Live.schedule();
        }
        const incoming=msg.type==='snapshot'?Object.values(msg.ticks||{}):msg.type==='batch'?(msg.ticks||[]):msg.type==='tick'?[msg]:[];
        if(incoming.some(t=>!t.demo&&Live.age(t)<5000))Live.lastFreshAt=Date.now();
        Live.paintStatus();
      };
      sock.onclose = () => {
        Live.socket = null;
        Live.lastMessageAt = 0;
        Live.lastFreshAt = 0;
        Live.paintStatus();
        setTimeout(Live.connect, Live.backoff);
        Live.backoff = Math.min(Live.backoff * 1.8, 20000);
      };
      sock.onerror = () => { try { sock.close(); } catch (_) {} };
    },

    /* Tick akışı saniyede yüzlerce mesaj olabilir; DOM'a doğrudan yazmak
       tarayıcıyı kilitler. requestAnimationFrame ile ~60fps'e sınırlanır. */
    schedule() {
      if (Live.flushTimer) return;
      Live.flushTimer = requestAnimationFrame(() => {
        Live.flushTimer = null;
        const batch = Live.pending.size
          ? new Map(Live.pending)
          : new Map(Object.entries(Live.ticks));
        Live.pending.clear();
        Live.handlers.forEach((fn) => {
          try { fn(batch, Live.ticks); } catch (e) { console.error(e); }
        });
      });
    },

    send(obj) {
      if (Live.socket && Live.socket.readyState === 1) Live.socket.send(JSON.stringify(obj));
    },

    watch(symbols) {
      const fresh = symbols.filter((s) => s && !Live.watched.has(s));
      fresh.forEach((s) => Live.watched.add(s));
      if (fresh.length) Live.send({ type: "watch", symbols: fresh });
    },

    onTick(fn) { Live.handlers.add(fn); return () => Live.handlers.delete(fn); },
    onBook(fn) { Live.bookHandlers.add(fn); return () => Live.bookHandlers.delete(fn); },
    age(tick) { return tick ? Math.max(0, Date.now() - (Live.serverOffsetMs || 0) - tick.ts) : Infinity; },
    price(symbol) { const t = Live.ticks[symbol]; return t && Live.age(t) < 10000 ? t.price : null; },
  };
  VX.live = Live;
  setInterval(Live.paintStatus,1000);
  /* Açık kalan ama veri taşımayan soket mobil ağ değişimlerinde görülebiliyor. */
  setInterval(() => {
    if (document.hidden || !Live.socket || Live.socket.readyState !== 1) return;
    if (Live.lastMessageAt && Date.now() - Live.lastMessageAt > 8000) Live.socket.close();
  }, 2000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Live.watched.size) {
      if (Live.socket?.readyState === 1 && Date.now() - Live.lastMessageAt > 8000) Live.socket.close();
      else Live.connect();
    }
  });

  /* Fiyat hücresini yeşil/kırmızı flaşla güncelle */
  const lastValues = Object.create(null);
  VX.paintPrice = function (el, symbol, value, decimals) {
    if (!el || value === null || value === undefined) return;
    const prev = lastValues[symbol];
    el.textContent = VX.fmtPrice(value, decimals);
    if (prev !== undefined && prev !== value) {
      el.classList.remove("tick-flash-up", "tick-flash-down");
      void el.offsetWidth;
      el.classList.add(value > prev ? "tick-flash-up" : "tick-flash-down");
    }
    lastValues[symbol] = value;
  };

  /* Sekme arka plandayken ağır yenilemeleri atla */
  /* ==================================================== Temizlik kaydi
     Sayfa gecisleri artik tam yenileme DEGIL (bkz. VX.router): <main>
     degisiyor ama JavaScript ayakta kaliyor. Bu, kurulan her zamanlayici
     ve her global dinleyicinin SONRAKI sayfada da calismaya devam etmesi
     demek. Uc gezinmede uc kopya birikir, altinci gezinmede sayfa saniyede
     onlarca gereksiz istek atar — tam olarak kacinmaya calistigimiz
     "sistem yoruluyor" durumu.

     Bu yuzden omru sayfaya bagli olan her sey buraya kaydediliyor ve
     gezinmede toptan sokuluyor. VX.interval bunu kendiliginden yapiyor;
     elle eklenen document/window dinleyicileri icin VX.onTeardown var. */
  let _teardowns = [];
  VX.onTeardown = function (fn) { if (typeof fn === "function") _teardowns.push(fn); return fn; };
  VX.runTeardowns = function () {
    const liste = _teardowns; _teardowns = [];
    liste.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
  };

  VX.interval = function (fn, ms, runNow = true) {
    const tick = () => { if (!document.hidden) fn(); };
    if (runNow) fn();
    const timer = setInterval(tick, ms);
    // Bu dinleyici eskiden HIC kaldirilmiyordu: her VX.interval cagrisi
    // document'a kalici bir dinleyici birakiyordu. Tek sayfalik omurde
    // fark etmezdi, gezinmeli mimaride sizinti olur.
    const gorunur = () => { if (!document.hidden) tick(); };
    document.addEventListener("visibilitychange", gorunur);
    const iptal = () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", gorunur);
    };
    VX.onTeardown(iptal);
    return iptal;
  };

  /* Coin ikonu — /icon/SEMBOL her zaman bir sey dondurur (PNG ya da
     harften uretilmis SVG rozet), o yuzden onerror yedegi gerekmiyor.
     loading=lazy: 150 satirlik listede 150 istek pesin atilmasin. */
  VX.coinIcon = function (symbol, size) {
    const px = size || 26;
    // Boyut inline style ile veriliyor: CSS kurali HTML width/height
    // ozniteliklerini ezerdi, o zaman size parametresi ise yaramazdi.
    return '<img class="sym__icon" style="width:' + px + 'px;height:' + px + 'px" ' +
      'src="/icon/' + encodeURIComponent(symbol) + '" loading="lazy" decoding="async" alt="">';
  };

  /* BTCUSDT -> BTC (1000PEPEUSDT -> PEPE). Sunucudaki base_asset ile ayni is. */
  VX.baseAsset = function (symbol) {
    return String(symbol || "").toUpperCase()
      .replace(/(USDT|USDC|BUSD|FDUSD|TUSD)$/, "")
      .replace(/^(1000+|1M)/, "");
  };

  /* Sparkline — kart arkasina yerlesen 24 saatlik mini seri.
     Canvas degil SVG: kart yeniden cizildiginde ayri bir yasam dongusu
     yonetmek gerekmiyor, innerHTML ile gidip geliyor. */
  VX.sparkline = function (values, opts) {
    /* Referans tasarimda kart grafigi NOTR gri. Yon bilgisi zaten yanindaki
       yuzde rakaminda ve o renkli — cizgiyi de renklendirmek ayni seyi iki
       kez soyluyor ve kart sirasini alacalandiriyordu. */
    const o = Object.assign({ w: 240, h: 46, up: "#a3a3a3", down: "#a3a3a3" }, opts || {});
    const v = (values || []).filter(x => typeof x === "number" && isFinite(x));
    if (v.length < 3) return "";
    const min = Math.min(...v), max = Math.max(...v);
    const span = (max - min) || 1;
    const step = o.w / (v.length - 1);
    const y = (x) => o.h - 3 - ((x - min) / span) * (o.h - 6);
    const pts = v.map((x, i) => `${(i * step).toFixed(1)},${y(x).toFixed(1)}`);
    const color = v[v.length - 1] >= v[0] ? o.up : o.down;
    const id = "sg" + Math.random().toString(36).slice(2, 8);
    return `<svg class="spark" viewBox="0 0 ${o.w} ${o.h}" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity=".28"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="M0,${o.h} L${pts.join(" L")} L${o.w},${o.h} Z" fill="url(#${id})"/>
      <polyline points="${pts.join(" ")}" fill="none" stroke="${color}"
        stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    </svg>`;
  };


  /* ==================================================== Yan menu durumu
     Menudeki renkli kareler. Kare RENK KODLUYOR; sayfa hangisi olursa
     olsun ayni uc soruyu cevapliyor: veri geliyor mu, motor acik mi,
     kac pozisyon acik. Alan yoksa (giris ekrani) sessizce cikiyor. */
  VX.railStatus = function (o) {
    o = o || {};
    function set(dotId, valId, cls, text) {
      const d = document.getElementById(dotId), v = document.getElementById(valId);
      if (d) d.className = "rail__dot rail__dot--" + cls;
      if (v) v.textContent = text;
    }
    if (o.data !== undefined) {
      const demo = o.data === "demo";
      set("railDataDot", "railDataVal", demo ? "demo" : "live", demo ? "demo" : "canlı");
    }
    if (o.engine !== undefined) {
      set("railEngineDot", "railEngineVal", o.engine ? "engine" : "flat", o.engine ? "açık" : "kapalı");
    }
    if (o.open !== undefined) {
      set("railOpenDot", "railOpenVal", Number(o.open) > 0 ? "engine" : "flat", String(o.open));
    }
  };

  /* ==================================================== Sembol arama
     Menudeki arama satiri ve "/" tusu. Sahte bir kutu degil: sembol
     girisi olan sayfadaysan oraya odaklanir, degilsen Analiz sayfasina
     goturup odaklar. */
  VX.symbolSearch = function () {
    const inp = document.getElementById("symbolInput");
    if (inp) { inp.focus(); inp.select(); return; }
    window.location.href = "/analiz#ara";
  };

  /* Menudeki motor/acik islem satirlarini HER sayfada doldur. Tek istek;
     ayni ucta ikisi de var. Basarisiz olursa satirlar "—" kalir, sayfa
     etkilenmez — menu suslemesi icin sayfa kirilmaz. */
  VX.initRailStatus = function () {
    if (!document.getElementById("rayDAlt")) return;
    /* The rail represents SMC; preserve the legacy pulse event for old tools. */
    VX.get("/api/engine/smc/status").then(function (e) {
      VX.railStatus({ engine: !!e.enabled });
      const label = document.getElementById("rayDAlt");
      if (label) label.textContent = "SMC " + (e.enabled ? "aktif" : "kapalı") + " · emir kapalı";
    }).catch(function () {});
    VX.get("/api/engine/tsmom/pulse").then(function (e) {
      VX.pulse = e;
      document.dispatchEvent(new CustomEvent("vx:pulse", { detail: e }));
    }).catch(function () {});
  };

  VX.initSearch = function () {
    const btn = document.getElementById("tnavAra") || document.getElementById("railSearch");
    if (btn) btn.addEventListener("click", VX.symbolSearch);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target || {};
      const tag = (t.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable) return;
      e.preventDefault();
      VX.symbolSearch();
    });
    if (window.location.hash === "#ara") {
      const inp = document.getElementById("symbolInput");
      if (inp) setTimeout(function () { inp.focus(); inp.select(); }, 120);
    }
  };

  /* ==================================================== Yan menu daraltma
     Genis ekranda menu 218px kapliyor. Kullanici bu yeri grafige
     verebilsin diye ac/kapa. Tercih localStorage'da; ilk deger
     base.html'deki inline blokta, sayfa boyanmadan once yaziliyor —
     burasi yalnizca DEVAMINI yonetiyor (tiklama + ekran boyu degisimi).
     1180px altinda karar kullanicinin degil: yer fiziksel olarak yok. */
  VX.initRail = function () {
    var root = document.documentElement;
    var btn = document.getElementById("railToggle");

    function pref() {
      try { return localStorage.getItem("vx.rail") === "icon" ? "icon" : "wide"; }
      catch (e) { return "wide"; }
    }
    function apply() {
      var forced = window.innerWidth <= 1180;
      var mode = forced ? "icon" : pref();
      if (root.getAttribute("data-rail") !== mode) root.setAttribute("data-rail", mode);
      if (!btn) return;
      var icon = mode === "icon";
      var label = icon ? "Menüyü genişlet" : "Menüyü daralt";
      btn.setAttribute("aria-expanded", String(!icon));
      btn.setAttribute("aria-label", label);
      btn.title = label + " (Ctrl+B)";
    }

    if (btn) {
      btn.addEventListener("click", function () {
        var next = root.getAttribute("data-rail") === "icon" ? "wide" : "icon";
        try { localStorage.setItem("vx.rail", next); } catch (e) {}
        apply();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
      if ((e.key || "").toLowerCase() !== "b") return;
      if (window.innerWidth <= 1180) return;
      var t = e.target || {};
      var tag = (t.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable) return;
      e.preventDefault();
      if (btn) btn.click();
    });

    var tmr = null;
    window.addEventListener("resize", function () {
      clearTimeout(tmr);
      tmr = setTimeout(apply, 120);
    });
    apply();
  };

  /* Telefonda alttaki ikon sırası yerine sol alttan açılan gerçek bir menü.
     iOS/Android dokunma hedefi, Escape ve perde tıklaması aynı kapıyı kullanır. */
  VX.initMobileNav = function () {
    var toggle = document.getElementById("mobileNavToggle");
    var backdrop = document.getElementById("mobileNavBackdrop");
    // Sol rail ust navigasyona tasindi; menu artik #tnavMenu.
    // Eski #railNav kimligi yedek olarak duruyor: dashboard gibi henuz
    // gecmemis bir sablon kalirsa sessizce calismaya devam etsin.
    var rail = document.getElementById("ray")
      || document.getElementById("tnavMenu") || document.getElementById("railNav");
    if (!toggle || !backdrop || !rail) return;

    // 780 -> 860: cekmece esigi CSS'teki @media(max-width:860px) ile
    // AYNI olmali. Ikisi ayrisirsa 780-860 arasinda hamburger gorunur
    // ama basinca hicbir sey acilmaz — sessiz ve tarif edilmesi zor
    // bir hata.
    var ESIK = 860;

    function setOpen(open) {
      if (window.innerWidth > ESIK) open = false;
      document.body.classList.toggle("mobile-nav-open", !!open);
      toggle.setAttribute("aria-expanded", String(!!open));
      toggle.setAttribute("aria-label", open ? "Panel menüsünü kapat" : "Panel menüsünü aç");
      rail.setAttribute("aria-hidden", String(!open && window.innerWidth <= ESIK));
    }
    toggle.addEventListener("click", function () {
      setOpen(!document.body.classList.contains("mobile-nav-open"));
    });
    backdrop.addEventListener("click", function () { setOpen(false); });
    rail.addEventListener("click", function (event) {
      if (event.target.closest("a") && window.innerWidth <= ESIK) setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setOpen(false);
    });
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () { setOpen(false); }, 120);
    });
    setOpen(false);
  };

  /* ==================================================== TEK SAYFA GEZINME
     SORUN: sol menudeki her baglanti TAM SAYFA yenilemesiydi. Tarayici
     dokumani atip bastan kuruyor, JavaScript sifirlaniyor, WebSocket
     kopuyor ve ekrandaki her sey — taranmis planlar, radar tablosu, acik
     islemler — yok oluyordu. Kullanici bunu "her sekme ayri ayri
     calisiyor, surekli refresh atiyor, onceki veriler gidiyor" diye
     tarif etti; tarif tam olarak dogruydu.

     COZUM: baglantiyi yakala, HEDEF SAYFAYI getir, yalnizca <main>
     bolumunu degistir. Kabuk (menu, WebSocket, onbellekler, JS durumu)
     yerinde kaliyor.

     UC KURAL:
     1) Ayni PAKET icinde kaliyorsak gecis yapiyoruz. workspace.js ile
        dashboard.js ayri paketler ve ayri CSS yukluyorlar; aralarinda
        DOM degistirmek eksik betikle calisan bir sayfa uretir. Bunu
        <main data-bundle> ile isaretliyoruz; farkliysa normal tam
        yukleme yapiliyor. Sessizce bozulmaktansa acikca yavas olmak.
     2) Her gecisten ONCE VX.runTeardowns() — zamanlayicilar ve global
        dinleyiciler sokuluyor, yoksa birikirler.
     3) Getirme basarisiz olursa (ag hatasi, 500, oturum bitmis) tarayici
        gezinmesine dusuyoruz. Kullanici hicbir zaman kirik bir ekranda
        kalmiyor.

     Bu bir cerceve degil, ~80 satirlik bir gezinme yakalayicisi. Amac
     uygulamayi "SPA'ya cevirmek" degil; VERIYI SAYFA GECISINDE
     KAYBETMEMEK. */
  const Router = {
    aktif: false,
    yukleniyor: false,

    paket() {
      const m = document.querySelector("main[data-bundle]");
      return m ? m.getAttribute("data-bundle") : null;
    },

    icerideMi(a) {
      if (!a || !a.href) return false;
      if (a.target && a.target !== "_self") return false;
      if (a.hasAttribute("download") || a.dataset.noSpa !== undefined) return false;
      const u = new URL(a.href, location.href);
      if (u.origin !== location.origin) return false;
      // Cikis, indirme ve API uclari gercek gezinme istiyor.
      if (/^\/(api|static|icon|ws|cikis|logout)\b/.test(u.pathname)) return false;
      // Yalnizca kirpik degisiyorsa tarayiciya birak.
      if (u.pathname === location.pathname && u.search === location.search) return false;
      return true;
    },

    async git(url, pushla = true) {  // url yonlendirmede guncellenebilir
      if (Router.yukleniyor) {
        // A new navigation must not be swallowed by slow data requests.
        // A full navigation also cancels the old page's pending setup.
        location.href = url;
        return;
      }
      Router.yukleniyor = true;
      document.documentElement.classList.add("vx-gecis");
      try {
        const cevap = await fetch(url, {
          credentials: "same-origin",
          headers: { "X-Vortex-Nav": "1" },
        });
        if (!cevap.ok) throw new Error("HTTP " + cevap.status);
        // Yonlendirme olduysa (ornek: /copy-trade -> /ayarlar) adres
        // cubuguna GERCEK varilan adresi yaziyoruz; yoksa geri tusu
        // kullaniciyi tekrar yonlendiren adrese goturur.
        if (cevap.redirected && cevap.url) url = cevap.url;
        const metin = await cevap.text();
        const doc = new DOMParser().parseFromString(metin, "text/html");
        const yeniMain = doc.querySelector("main[data-bundle]");
        const eskiMain = document.querySelector("main[data-bundle]");
        if (!yeniMain || !eskiMain) throw new Error("main yok");
        // Farkli paket -> tam yukleme. Yanlis betikle acilmis bir sayfa,
        // yavas bir sayfadan cok daha kotudur.
        if (yeniMain.getAttribute("data-bundle") !== eskiMain.getAttribute("data-bundle")) {
          location.href = url; return;
        }

        VX.runTeardowns();
        eskiMain.replaceWith(yeniMain);
        const t = doc.querySelector("title");
        if (t) document.title = t.textContent;
        if (pushla) history.pushState({ vx: 1 }, "", url);
        Router.railTazele();
        // Mobilde menu acikken gezinilirse ustte asili kalirdi; kapat.
        // (Rail'in kendi tiklama dinleyicisi zaten kapatiyor ama router
        // programatik cagrilarda da devreye giriyor.)
        document.body.classList.remove("mobile-nav-open");
        const mnt = document.getElementById("mobileNavToggle");
        if (mnt) mnt.setAttribute("aria-expanded", "false");
        // Sayfa govdesi kendi kaydirma kabindaydi; tepeye don.
        const kap = document.querySelector("main[data-bundle]");
        if (kap) kap.scrollTop = 0;
        window.scrollTo(0, 0);
        document.dispatchEvent(new CustomEvent("vx:navigate", {
          detail: { page: yeniMain.getAttribute("data-page"), url },
        }));

        // BOOT AYRI BIR TRY ICINDE — ve bu bir hata duzeltmesi.
        //
        // Onceki halde boot da disaridaki try'in icindeydi ve catch
        // blogu "location.href = url" yapiyordu. Yani bir sayfanin
        // kurulum kodunda TEK BIR hata olsa (bir uc nokta 500 donse,
        // bir eleman bulunamasa) tarayici TAM SAYFA YENILEMESI
        // yapiyordu. Kullanicinin gordugu sey tam olarak buydu:
        // "sistem hala kendini refresh atiyor."
        //
        // Oysa bu noktada DOM zaten dogru sekilde degismis durumda.
        // Kurulumun bir parcasi calismadiysa dogru davranis sayfayi
        // yeniden yuklemek degil, hatayi soylemek: yeniden yukleme de
        // ayni hatayla karsilasacak, ustelik butun durumu kaybederek.
        try {
          if (typeof VX._boot === "function") await VX._boot();
        } catch (bootHata) {
          console.error("sayfa kurulumu:", bootHata);
          if (VX.toast) VX.toast("Sayfa kurulumunda hata: " + bootHata.message, "err", 8000);
        }
      } catch (e) {
        // Buraya YALNIZCA getirme/degistirme asamasi basarisiz olursa
        // duseriz (ag hatasi, 500, oturum bitmis). Orada gercek
        // gezinmeye dusmek dogru: kullanici kirik bir ekranda kalmasin.
        console.error("gezinme:", e);
        location.href = url;
      } finally {
        Router.yukleniyor = false;
        document.documentElement.classList.remove("vx-gecis");
      }
    },

    railTazele() {
      // "/" ve "/mentor" AYNI sayfa. Duz karsilastirma yapinca menuden
      // Mentor'a basildiginda ("/") hicbir baglanti aktif gorunmuyordu.
      const esle = (y) => y;
      const yol = esle(location.pathname);
      document.querySelectorAll(".rail__item").forEach(a => {
        const u = new URL(a.getAttribute("href"), location.origin);
        const active = esle(u.pathname) === yol;
        a.classList.toggle("is-active", active);
        if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
    },

    init() {
      if (Router.aktif || !Router.paket()) return;
      Router.aktif = true;
      document.addEventListener("click", e => {
        if (e.defaultPrevented || e.button !== 0) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest && e.target.closest("a[href]");
        if (!Router.icerideMi(a)) return;
        e.preventDefault();
        Router.git(a.href);
      });
      window.addEventListener("popstate", () => Router.git(location.href, false));
      history.replaceState({ vx: 1 }, "", location.href);
    },
  };
  VX.router = Router;

  /* Sayfa paketleri boot fonksiyonunu buraya kaydediyor; router gecisten
     sonra ayni fonksiyonu YENIDEN cagiriyor. Boylece hem ilk yuklemede
     hem gecisde tek bir yol calisiyor — iki ayri kurulum yolu tutmak
     kacinilmaz olarak birinin unutulmasiyla biterdi. */
  VX.registerBoot = function (fn) {
    VX._boot = fn;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => { Router.init(); fn(); });
    } else {
      Router.init();
      fn();
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { VX.initRail(); VX.initMobileNav(); VX.initSearch(); VX.initRailStatus(); });
  } else {
    VX.initRail();
    VX.initMobileNav();
    VX.initSearch();
    VX.initRailStatus();
  }

  window.VX = VX;
  if(document.getElementById('modeChip'))Live.connect();
})();
