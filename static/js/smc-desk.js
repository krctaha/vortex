(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => VX.esc(String(value ?? "—"));
  const price = value => value == null ? "—" : VX.fmtPrice(value);
  const phases = {
    HTF_BIAS_BEKLE: "Üst zaman diliminde yön bekleniyor",
    LIKIDITE_BEKLE: "Likidite süpürmesi bekleniyor",
    MSS_BEKLE: "Displacement ve yapı kırılımı bekleniyor",
    TEYIT_BEKLE: "CISD / dengesizlik teyidi bekleniyor",
    RR_YETERSIZ: "Yapısal hedef veya R:R yetersiz",
    HAZIR: "Kurulum tamam · giriş bekleniyor",
    KURULUM_GECERSIZ: "Kurulum eskidi veya stop seviyesi aşıldı",
    GIRIS_GECMIS: "Giriş bölgesi daha önce ziyaret edildi",
    GECERSIZ_STOP: "Stop geometrisi geçersiz",
    FUNDING_BEKLE: "Funding kalabalığı var",
    VERI_BAYAT: "Güncel mum verisi bekleniyor",
  };
  let requestId = 0;
  function detail(row) {
    const phase = phases[row.phase] || row.phase;
    $("smcDetail").innerHTML = `
      <div class="smc-desk__selection"><h2>${esc(row.symbol)}</h2><span class="tag">${esc(row.side)}</span></div>
      <p>${esc(phase)}</p>
      <div class="smc-flow__steps">${(row.steps || []).map(step => `<div class="smc-flow__step ${step.ok ? "is-ok" : "is-wait"}"><i>${step.ok ? "✓" : "○"}</i><span>${esc(step.label)}</span></div>`).join("")}</div>
      <div class="smc-flow__summary">
        <div><span>Planlanan giriş</span><b>${price(row.entry)}</b></div>
        <div><span>Yapısal stop</span><b>${price(row.stop)}</b></div>
        <div><span>Likidite hedefi</span><b>${price(row.target)}</b></div>
        <div><span>Brüt R:R</span><b>${Number(row.rr || 0).toFixed(2)}R</b></div>
      </div>
      <p class="workspace__muted">${row.demo ? "DEMO · Sentetik veri. " : ""}Model: ${esc(row.model)} · Koşul skoru ${esc(row.score)}/100. Komisyon, kayma ve funding maliyeti bu R:R içinde değildir.</p>
      <a class="btn" href="/analiz?symbol=${encodeURIComponent(row.symbol)}&interval=15m">Grafiği aç</a>`;
  }
  function render(data) {
    const rows = data.candidates || [];
    $("smcCounts").textContent = `${data.scanned || 0} / ${rows.filter(r => r.ready).length}`;
    $("smcTime").textContent = data.last_scan_at ? new Date(data.last_scan_at).toLocaleString("tr-TR") : "Henüz tarama yok";
    $("smcData").textContent = `${data.demo ? "DEMO · Sentetik veri" : "Emir yetkisi kapalı"}${data.errors ? " · " + data.errors + " sembolde veri hatası" : ""}`;
    $("smcCandidates").innerHTML = rows.length ? rows.map((row, i) => `
      <button class="smc-desk__candidate" data-index="${i}" type="button">
        <span><b>${esc(row.symbol)}</b><small>${esc(phases[row.phase] || row.phase)}</small></span>
        <span><b>${esc(row.side)}</b><small>${row.score}/100</small></span>
      </button>`).join("") : '<div class="empty">Teyit eşiğini geçen kurulum yok.</div>';
    $("smcCandidates").querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
      requestId++;
      const row = rows[Number(button.dataset.index)];
      $("smcSymbol").value = row.symbol;
      detail(row);
    }));
  }
  $("smcScan").addEventListener("click", async () => {
    const button = $("smcScan");
    button.disabled = true; button.textContent = "Taranıyor…";
    try { render(await VX.post("/api/engine/smc/scan-now", {})); }
    catch (error) { $("smcCandidates").textContent = "Tarama tamamlanamadı: " + error.message; }
    finally { button.disabled = false; button.textContent = "Piyasayı tara"; }
  });
  $("smcForm").addEventListener("submit", async event => {
    event.preventDefault();
    const id = ++requestId;
    $("smcDetail").textContent = "Kapalı mumlar inceleniyor…";
    try {
      const row = await VX.get("/api/market/smc?symbol=" + encodeURIComponent($("smcSymbol").value.trim().toUpperCase()));
      if (id === requestId) detail(row);
    } catch (error) { if (id === requestId) $("smcDetail").textContent = error.message; }
  });
  VX.get("/api/engine/smc/status").then(render).catch(error => { $("smcCandidates").textContent = error.message; });
  VX.get("/api/system/status").then(status => {
    const demo = status.data_mode === "demo";
    if ($("modeText")) $("modeText").textContent = demo ? "DEMO veri" : status.data_mode === "live" ? "Binance canlı" : "Veri bekleniyor";
    VX.railStatus({data: status.data_mode});
  }).catch(() => {});
  const tick = () => { if ($("clock")) $("clock").textContent = new Date().toLocaleTimeString("tr-TR"); };
  tick(); setInterval(tick, 1000);
})();
