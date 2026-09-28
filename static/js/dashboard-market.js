/* Shared pure market-table model: no orders, no extra market requests. */
(function (root) {
  "use strict";
  const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  function rows(board, radar, options = {}) {
    const rsi = new Map((radar?.all || []).map((row) => [row.symbol, row]));
    const unique = new Map();
    for (const row of [...(board?.top_volume || []), ...(board?.gainers || []), ...(board?.losers || [])]) {
      if (!row.symbol || unique.has(row.symbol)) continue;
      const value = rsi.get(row.symbol)?.rsi;
      unique.set(row.symbol, { ...row, rsi: finite(value) ? Number(value) : null });
    }
    const query = String(options.search || "").trim().toUpperCase();
    const list = [...unique.values()].filter((row) => row.symbol.toUpperCase().includes(query));
    const key = options.sort?.startsWith("rsi") ? "rsi" : ["gainers", "losers"].includes(options.sort) ? "change_pct" : "quote_volume";
    const direction = ["losers", "rsi-low"].includes(options.sort) ? 1 : -1;
    return list.sort((a, b) => {
      if (!finite(a[key]) && !finite(b[key])) return a.symbol.localeCompare(b.symbol);
      if (!finite(a[key])) return 1;
      if (!finite(b[key])) return -1;
      return direction * (Number(a[key]) - Number(b[key])) || a.symbol.localeCompare(b.symbol);
    });
  }
  function table(list, vx) {
    if (!list.length) return '<div class="empty">Gösterilecek kontrat yok. Aramayı veya veri bağlantısını kontrol et.</div>';
    const body = list.map((row) => {
      const rsi = row.rsi;
      const zone = rsi === null ? "Veri yok" : rsi >= 70 ? "Aşırı alım" : rsi <= 30 ? "Aşırı satım" : "Normal aralık";
      const rsiclass = rsi === null ? "" : rsi >= 70 ? "is-high" : rsi <= 30 ? "is-low" : "";
      return `<tr>
        <th scope="row"><span class="sym">${vx.coinIcon(row.symbol, 22)}<span><b>${vx.esc(vx.baseAsset(row.symbol))}</b><small>${vx.esc(row.symbol)}</small></span></span></th>
        <td class="right num" data-price="${vx.esc(row.symbol)}">${vx.fmtPrice(row.price)}</td>
        <td class="right delta ${vx.deltaClass(row.change_pct)}">${vx.fmtPct(row.change_pct)}</td>
        <td class="right num">${finite(row.quote_volume) ? vx.fmtCompact(row.quote_volume) : "—"}</td>
        <td class="right"><span class="rsi-number ${rsiclass}" title="${zone}">${rsi === null ? "—" : rsi.toFixed(1)}</span></td>
        <td class="market-watch__zone">${zone}</td>
        <td class="right"><a class="btn btn--sm" href="/analiz?symbol=${encodeURIComponent(row.symbol)}" aria-label="${vx.esc(row.symbol)} analiz yap">Analiz yap ↗</a></td>
      </tr>`;
    }).join("");
    return `<table class="tbl market-watch__table"><thead><tr><th scope="col">Coin</th><th scope="col" class="right">Fiyat · USDT</th><th scope="col" class="right">24s %</th><th scope="col" class="right">Hacim · 24s USDT</th><th scope="col" class="right">RSI · 1s</th><th scope="col">RSI bölgesi</th><th scope="col">Analiz</th></tr></thead><tbody>${body}</tbody></table>`;
  }
  const api = { rows, table };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VortexMarket = api;
})(typeof window !== "undefined" ? window : globalThis);
