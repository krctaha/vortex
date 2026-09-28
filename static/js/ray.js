/* =========================================================================
   YAN MENÜ DAVRANIŞI
   -------------------------------------------------------------------------
   Bu dosya SPA gezinmesinden ETKİLENMİYOR. Yönlendirici yalnız
   `main[data-bundle]` içeriğini değiştiriyor; yan menü ve üst şerit
   yerinde kalıyor. Bu yüzden buradaki dinleyiciler bir kez kuruluyor ve
   zamanlayıcı VX.interval DEĞİL, düz setInterval: sayfaya değil OTURUMA
   ait.

   ÜÇ İŞ:
   1) Üst şeritteki kimlik (@kullanıcı, ad, rol).
   2) Yan menüdeki açık pozisyon listesi — referanstaki "Active Staking"
      bloğunun karşılığı. GERÇEK pozisyonlar; anahtar bağlı değilse liste
      sessizce boş kalmıyor, sebebini yazıyor. Sessiz boşluk "pozisyonum
      yok" diye okunur ve bu yanlış olur.
   3) Alttaki veri kaynağı kutusu.
   ========================================================================= */
(function () {
  "use strict";
  if (!window.VX) return;

  var $ = function (id) { return document.getElementById(id); };

  /* ---------------------------------------------------------------- kimlik */
  function kimlikYaz(u) {
    if (!u) return;
    var av = $("avatar");
    if (av) av.textContent = (u.display_name || u.username || "?").charAt(0).toUpperCase();
    var k = $("ustKullanici"); if (k) k.textContent = "@" + (u.username || "—");
    var a = $("ustAd");        if (a) a.textContent = u.display_name || u.username || "—";
    var r = $("ustRol");
    if (r) {
      // Rol rozeti uydurma bir "PRO" değil: sunucunun verdiği rolün kendisi.
      r.textContent = u.role === "admin" ? "YÖNETİCİ" : (u.role || "üye").toUpperCase();
    }
  }

  /* ------------------------------------------------------- veri kaynağı kutusu
     VX.railStatus'un yeni gövdesi. Eski sürüm artık var olmayan
     railDataDot/railEngineDot kimliklerini arıyordu ve sessizce hiçbir şey
     yapmıyordu — üst navigasyona geçerken düşmüş, kimse fark etmemişti. */
  VX.railStatus = function (o) {
    o = o || {};
    if (o.data !== undefined) {
      var demo = o.data === "demo";
      var n = $("rayDNokta"), b = $("rayDBaslik");
      if (n) n.className = "ray__dnokta " + (demo ? "is-demo" : "is-canli");
      if (b) b.textContent = demo ? "DEMO veri" : "Binance canlı";
      var d = $("rayDurum");
      if (d) d.title = demo
        ? "Sentetik veri — gerçek piyasa değil. Ayarlar > Binance'ten anahtar bağlayabilirsin."
        : "Binance canlı verisi";
    }
    if (o.engine !== undefined || o.open !== undefined) {
      var alt = $("rayDAlt");
      if (alt) {
        var p = [];
        if (o.engine !== undefined) p.push("motor " + (o.engine ? "açık" : "kapalı"));
        if (o.open !== undefined) p.push(o.open + " açık");
        if (p.length) alt.textContent = p.join(" · ");
      }
    }
  };

  /* ---------------------------------------------------- açık pozisyon listesi */
  function para(x) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    return (Math.abs(x) >= 1000 ? Math.round(x).toLocaleString("tr-TR")
                                : Number(x).toFixed(2).replace(".", ",")) + " $";
  }

  function pozCiz(d) {
    var kutu = $("rayAcik"), liste = $("rayAcikListe"), sayi = $("rayAcikSayi");
    if (!kutu || !liste) return;

    // Anahtar yoksa blok HİÇ gösterilmiyor: boş bir "Açık işlemler (0)"
    // başlığı, pozisyon olmadığını söylerdi. Oysa bilinmiyor.
    if (!d || d.bagli === false || (d.ok === false && d.sebep === "anahtar_yok")) {
      kutu.hidden = true;
      return;
    }
    var poz = (d.pozisyonlar || []);
    kutu.hidden = false;
    if (sayi) sayi.textContent = String(poz.length);

    if (!poz.length) {
      liste.innerHTML = '<div class="ray__bos">Açık pozisyon yok.</div>';
      return;
    }
    liste.innerHTML = poz.slice(0, 8).map(function (p) {
      var pnl = Number(p.pnl);
      var sinif = pnl > 0 ? "up" : pnl < 0 ? "down" : "";
      var isaret = pnl > 0 ? "+" : "";
      return '<a class="ray__p" href="/portfoy" title="' + VX.esc(p.symbol) + " " + VX.esc(p.yon || "") + '">'
        + '<span class="ray__pikon">' + VX.coinIcon(p.symbol, 20) + "</span>"
        + '<span class="ray__pyazi">'
        +   '<span class="ray__psem">' + VX.esc(VX.baseAsset(p.symbol)) + "</span>"
        +   '<span class="ray__ptut ' + sinif + '">' + isaret + para(pnl) + "</span>"
        + "</span></a>";
    }).join("");
  }

  function pozYukle() {
    if (!$("rayAcik")) return;
    VX.get("/api/mentor/portfoy").then(pozCiz).catch(function () {
      // Uç nokta cevap vermediyse listeyi DEĞİŞTİRMİYORUZ: eski doğru
      // veriyi silip yerine hiçbir şey koymamak, yanlış bilgi vermenin
      // sessiz hâli olurdu.
    });
  }

  /* ------------------------------------------------------------------ kurulum */
  function kur() {
    // Açık/kapalı katlama — tercih hatırlanıyor.
    var bas = $("rayAcikBas"), kutu = $("rayAcik");
    if (bas && kutu) {
      try {
        if (localStorage.getItem("vx.rayAcik") === "kapali") kutu.classList.add("is-kapali");
      } catch (e) {}
      bas.addEventListener("click", function () {
        var kapali = kutu.classList.toggle("is-kapali");
        bas.setAttribute("aria-expanded", String(!kapali));
        try { localStorage.setItem("vx.rayAcik", kapali ? "kapali" : "acik"); } catch (e) {}
      });
    }

    VX.get("/api/auth/state").then(function (s) {
      if (s && s.authenticated) kimlikYaz(s.user);
    }).catch(function () {});

    pozYukle();
    // 90 saniye: mentor_sync sunucuda zaten 90 saniyede bir senkron
    // yapıyor; daha sık sormak yeni bilgi getirmezdi.
    setInterval(pozYukle, 90000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", kur);
  } else {
    kur();
  }
})();
