(() => {
  "use strict";
  const $ = id => document.getElementById(id), esc = x => VX.esc(String(x ?? "—"));
  const num = (x, n = 2) => Number(x).toLocaleString("tr-TR", {maximumFractionDigits:n});
  const price = x => x == null ? "—" : VX.fmtPrice(x);
  const compact = x => new Intl.NumberFormat("tr-TR", {notation:"compact",maximumFractionDigits:1}).format(x);
  const pct = x => `${x > 0 ? "+" : ""}${num(x)}%`;
  const phase = r => ({HAZIR:"Teyit tamam",TAKIPTE:"Mevcut plan takipte",MALIYET_BEKLE:"Stop maliyete göre çok dar",HTF_BIAS_BEKLE:"Yön bekliyor",LIKIDITE_BEKLE:"Süpürme bekliyor",MSS_BEKLE:"Yapı kırılımı bekliyor",TEYIT_BEKLE:"Teyit bekliyor",RR_YETERSIZ:"R:R yetersiz",KURULUM_GECERSIZ:"Geçersiz kurulum",GIRIS_GECMIS:"Giriş geçmiş",GECERSIZ_STOP:"Stop geçersiz",FUNDING_BEKLE:"Funding teyidi yok",VERI_BAYAT:"Veri bayat"}[r?.phase] || "Tarama bekliyor");
  let state = null, chartRequest = 0, pending = false, lastTickAt=0, lastChartFetch=0;
  let chartData=null,lastDraw=0;
  const crossedEntries=new Set();
  let btcHistory=[],lastBtcDraw=0,btcHistoryBusy=false,btcHistoryLoadedAt=0;
  const btcTapeElement=$('btcTape');
  function drawBtcTape(){
    if(!btcTapeElement.isConnected||!btcHistory.length)return;
    const tick=VX.live.ticks.BTCUSDT,current=tick&&!tick.demo&&tick.source==='aggTrade'&&VX.live.age(tick)<5000;
    const end=Math.floor((Date.now()-(VX.live.serverOffsetMs||0))/60000)*60000,start=end-59*60000;
    const rows=btcHistory.filter(r=>r.ts>=start&&r.ts<=end).map(r=>({...r}));
    if(current){const bucket=Math.floor(tick.ts/60000)*60000,found=rows.find(r=>r.ts===bucket);if(found)found.price=tick.price;else if(bucket>=start&&bucket<=end)rows.push({ts:bucket,price:tick.price});}
    rows.sort((a,b)=>a.ts-b.ts);if(rows.length<2)return;
    const lo=Math.min(...rows.map(r=>r.price)),hi=Math.max(...rows.map(r=>r.price)),pad=Math.max((hi-lo)*.2,hi*.00015),min=lo-pad,max=hi+pad;
    const x=t=>8+(t-start)/(end-start)*344,y=p=>8+(max-p)/(max-min)*80;
    let line='',area='',segment=[];
    function flush(){if(!segment.length)return;const path=segment.map((r,i)=>`${i?'L':'M'}${x(r.ts).toFixed(2)},${y(r.price).toFixed(2)}`).join(' ');line+=path;area+=`${path} L${x(segment.at(-1).ts).toFixed(2)},91 L${x(segment[0].ts).toFixed(2)},91 Z `;segment=[];}
    for(const r of rows){if(segment.length&&r.ts-segment.at(-1).ts>60000)flush();segment.push(r);}flush();
    const latest=rows.at(-1),time=t=>new Date(t).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'});
    btcTapeElement.innerHTML=`<svg viewBox="0 0 420 116" preserveAspectRatio="none" role="img" aria-label="BTC gerçek bir dakikalık kapanış fiyatları; son mum canlı fiyatla güncellenir" data-points="${rows.length}" data-last-price="${latest.price}"><defs><linearGradient id="btcAreaFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#b9c1c7" stop-opacity=".2"/><stop offset="100%" stop-color="#b9c1c7" stop-opacity="0"/></linearGradient></defs>${[12,48,88].map(yy=>`<line x1="8" x2="352" y1="${yy}" y2="${yy}" stroke="#ffffff" stroke-opacity=".065"/><text x="363" y="${yy+4}" fill="#8b8b8b" font-size="10">${price(max-(yy-8)/80*(max-min))}</text>`).join('')}<path class="btc-area" d="${area}" fill="url(#btcAreaFill)"/><path class="btc-line" d="${line}" fill="none" stroke="#c2cbd1" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>${current?`<line x1="8" x2="352" y1="${y(latest.price)}" y2="${y(latest.price)}" stroke="#d6dde2" stroke-dasharray="3 5" stroke-opacity=".2"/><circle class="btc-live-dot" cx="${x(latest.ts)}" cy="${y(latest.price)}" r="3" fill="#d6dde2"/>`:''}<text x="8" y="111" fill="#808080" font-size="10">${time(start)}</text><text x="167" y="111" fill="#808080" font-size="10">${time(start+30*60000)}</text><text x="352" y="111" text-anchor="end" fill="#aaa" font-size="10">${time(end)}</text></svg>`;
    lastBtcDraw=performance.now();
  }
  async function loadBtcHistory(){
    if(btcHistoryBusy||!btcTapeElement.isConnected)return;btcHistoryBusy=true;
    try{const d=await VX.get('/api/market/klines?symbol=BTCUSDT&interval=1m&limit=60');if(!btcTapeElement.isConnected)return;if(d.demo)throw Error('Canlı mum verisi yok');
      btcHistory=d.candles.map(r=>({ts:r.time*1000,price:Number(r.close)})).filter(r=>Number.isFinite(r.price)&&r.price>0);btcHistoryLoadedAt=Date.now();drawBtcTape();
    }catch(e){if(btcTapeElement.isConnected&&!btcHistory.length)btcTapeElement.textContent='Fiyat grafiği yüklenemedi; canlı fiyat akışı devam ediyor.';}
    finally{btcHistoryBusy=false;}
  }
  function paintBtc(){
    const tick=VX.live.ticks.BTCUSDT,freshTick=tick&&!tick.demo&&tick.source==='aggTrade'&&VX.live.age(tick)<5000;
    $('btcPrice').textContent=freshTick?price(tick.price):'—';
    const row=state?.markets.find(r=>r.symbol==='BTCUSDT'),change=tick?.change_24h??row?.change;
    $('btcChange').textContent=change!=null?`${pct(change)} / 24s`:'—';$('btcChange').className=change>=0?'up':'down';
    $('btcPriceStatus').textContent=freshTick?`Son gerçekleşen işlem · ${Math.round(VX.live.age(tick))} ms yaş`:'Güncel BTC işlem fiyatı bekleniyor';
    $('connection').textContent=freshTick?'● BTC canlı':'BTC verisi bekleniyor';
    if(freshTick&&btcHistory.length){const bucket=Math.floor(tick.ts/60000)*60000,found=btcHistory.find(r=>r.ts===bucket);if(found)found.price=tick.price;else if(bucket>btcHistory.at(-1).ts){btcHistory.push({ts:bucket,price:tick.price});btcHistory=btcHistory.slice(-60);}}
    if(performance.now()-lastBtcDraw>250)drawBtcTape();
  }
  function paintBook(){
    const b=VX.live.orderbook,valid=b?.source==='depth20'&&!b.demo&&VX.live.age(b)<2000&&b.bids.length&&b.asks.length;
    document.querySelector('.btc-book').classList.toggle('is-stale',!valid);
    $('btcBookStatus').textContent=valid?'● Canlı':'Veri bekleniyor';
    if(!valid){$('btcBookRows').innerHTML='<div class="dash-empty">Güncel emir defteri bekleniyor…</div>';$('btcSpread').textContent='Spread —';$('btcBookBalance').textContent='20 kademe dengesi —';return;}
    const bids=b.bids.slice(0,8),asks=b.asks.slice(0,8),max=Math.max(...bids.map(r=>r[1]),...asks.map(r=>r[1]));
    $('btcBookRows').innerHTML=bids.map((bid,i)=>{const ask=asks[i];return `<div class="book-row"><div class="book-level bid" style="--depth:${bid[1]/max*100}%"><span class="book-qty">${num(bid[1],3)}</span><span>${price(bid[0])}</span></div><div class="book-level ask" style="--depth:${ask?ask[1]/max*100:0}%"><span>${ask?price(ask[0]):'—'}</span><span class="book-qty">${ask?num(ask[1],3):'—'}</span></div></div>`;}).join('');
    $('btcSpread').textContent=`Spread ${num(b.asks[0][0]-b.bids[0][0],2)} USDT`;
    const bv=b.bids.reduce((s,r)=>s+r[1],0),av=b.asks.reduce((s,r)=>s+r[1],0);
    $('btcBookBalance').textContent=`20 kademe · alış %${num(bv/(bv+av)*100,1)} / satış %${num(av/(bv+av)*100,1)}`;
  }
  const unbook=VX.live.onBook(paintBook),btcFreshness=setInterval(()=>{paintBtc();if(!VX.live.orderbook||VX.live.age(VX.live.orderbook)>=2000)paintBook();},500);
  paintBtc();paintBook();
  loadBtcHistory();
  const btcHistoryRefresh=setInterval(()=>{if(!document.hidden&&Date.now()-btcHistoryLoadedAt>60000)loadBtcHistory();},15000);
  const tradeState={pending:'Giriş bekliyor',open:'Giriş gerçekleşti · takipte',target:'Hedef',stop:'Stop',timeout:'Takip süresi doldu',expired:'Giriş olmadı · süre doldu',invalidated:'Geçersiz',ambiguous:'Mum içi sıra belirsiz',data_gap:'Veri boşluğu'};
  function trackingGraph(r){
    if(!['pending','open'].includes(r.state))return '—';
    const tick=VX.live.ticks[r.symbol],current=tick&&!tick.demo&&VX.live.age(tick)<10000?tick.price:null;
    const history=(state?.scanner.results.find(x=>x.symbol===r.symbol)?.sparkline||[]).slice(-16).filter(Number.isFinite);
    const values=[...history,...(current!=null?[current]:[])],levels=[r.stop,r.target,r.entry];
    const lo=Math.min(...levels,...values),hi=Math.max(...levels,...values),span=Math.max(hi-lo,hi*.00001);
    const y=p=>8+(hi-p)/span*48,x=i=>4+i/Math.max(1,values.length-1)*116;
    const points=values.map((p,i)=>`${x(i)},${y(p)}`).join(' ');
    const marks=[[r.target,'TP','#79bca5'],[r.stop,'SL','#dc8b93'],[r.entry,'Giriş','#858585']].map(([p,name,color])=>({p,name,color,labelY:y(p)+3})).sort((a,b)=>a.labelY-b.labelY);
    marks.forEach((m,i)=>{if(i)m.labelY=Math.max(m.labelY,marks[i-1].labelY+10);});
    for(let i=marks.length-1;i>=0;i--)marks[i].labelY=Math.min(marks[i].labelY,i===marks.length-1?61:marks[i+1].labelY-10);
    const label=`${r.symbol} · Son 16 kapalı 15M mum ve güncel fiyat; işlem açılışından itibaren tarihçe değildir. TP ${price(r.target)}, SL ${price(r.stop)}, giriş ${price(r.entry)}. ${current==null?'Güncel fiyat doğrulanamıyor.':'Güncel '+price(current)}`;
    return `<svg class="trade-mini-chart" viewBox="0 0 168 66" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>${marks.map(({p,name,color,labelY})=>`<line x1="3" x2="124" y1="${y(p)}" y2="${y(p)}" stroke="${color}" stroke-width=".7" stroke-dasharray="3 3" opacity=".7"/><line x1="124" x2="128" y1="${y(p)}" y2="${labelY-3}" stroke="${color}" stroke-width=".5" opacity=".5"/><text x="130" y="${labelY}" fill="${color}" font-size="8">${name}</text>`).join('')}${values.length>1?`<polyline points="${points}" fill="none" stroke="#c4c4c4" stroke-width="1.3"/>`:''}${current!=null?`<circle cx="${x(values.length-1)}" cy="${y(current)}" r="2.3" fill="#efefef"/>`:''}</svg>`;
  }
  function paintTrackingCharts(){
    const rows=new Map([...(state?.board?.pending||[]),...(state?.board?.open||[])].map(r=>[r.id,r]));
    document.querySelectorAll('[data-trade-chart]').forEach(cell=>{const r=rows.get(cell.dataset.tradeChart);if(!r)return;const markup=trackingGraph(r);if(cell.dataset.chart!==markup){cell.innerHTML=markup;cell.dataset.chart=markup;}});
  }
  function trackingTable(rows){
    if(!rows.length)return '<div class="dash-empty">Bu bölümde kayıt yok.</div>';
    return `<table><thead><tr><th>Coin / yön</th><th>Durum</th><th>TP / SL grafiği*</th><th>Giriş</th><th>Stop</th><th>Hedef</th><th>Son fiyat</th><th>Maliyet sonrası R*</th><th>Zaman</th></tr></thead><tbody>${rows.map(r=>`<tr><td><a href="/analiz?symbol=${encodeURIComponent(r.symbol)}">${esc(r.symbol)} · ${esc(r.side)}</a></td><td>${esc(r.entry_note||tradeState[r.state]||r.state)}</td><td data-trade-chart="${esc(r.id)}">${trackingGraph(r)}</td><td>${price(r.entry)}</td><td>${price(r.stop)}</td><td>${price(r.target)}</td><td data-live-price="${esc(r.symbol)}">${price(r.live_price)}</td><td>${r.cost_r==null?'—':num(r.cost_r)}</td><td>${new Date(r.closed_at||r.opened_at||r.created_at).toLocaleString('tr-TR')}</td></tr>`).join('')}</tbody></table>`;
  }
  function renderBoard(){
    const b=state?.board,s=state?.scanner;if(!b)return;
    const quotes=new Map(state.markets.map(r=>[r.symbol,r.price]));
    const actionable=b.pending.filter(r=>r.actionable&&Date.now()-b.updated_at<15000&&!crossedEntries.has(r.id)&&fresh(s)&&s.enabled&&(!s.direction||s.direction==='AUTO'||s.direction===r.side)&&quotes.has(r.symbol)&&(r.side==='LONG'?quotes.get(r.symbol)>r.entry&&quotes.get(r.symbol)<r.target:quotes.get(r.symbol)<r.entry&&quotes.get(r.symbol)>r.target));
    $('readyCount').textContent=actionable.length;
    const markup=actionable.length?planCards(actionable.map(r=>({...r,as_of:r.created_at}))):'<div class="dash-empty">Şu anda yeni girişe uygun teyitli plan yok.<br>Açık takipler ve günün sonuçları aşağıda görünmeye devam eder.</div>';
    if($('plans').dataset.markup!==markup){$('plans').innerHTML=markup;$('plans').dataset.markup=markup;}
    document.querySelectorAll('#plans .plan-card').forEach((card,index)=>{const r=actionable[index];if(!r)return;let mini=card.querySelector('[data-trade-chart]');if(!mini){mini=document.createElement('div');mini.dataset.tradeChart=r.id;card.querySelector('.plan-levels').after(mini);}mini.innerHTML=trackingGraph(r);});
    const version=`${b.updated_at}:${crossedEntries.size}:${actionable.length}`;
    if($('openPositions').dataset.version===version)return;
    $('openPositions').dataset.version=version;
    const waiting=b.pending.filter(r=>!actionable.some(a=>a.id===r.id)).map(r=>({...r,entry_note:crossedEntries.has(r.id)?'Giriş seviyesine temas — yeni giriş olarak gösterilmez':r.entry_note}));
    $('pendingRecordCount').textContent=`(${waiting.length})`;$('pendingRecords').innerHTML=trackingTable(waiting);
    $('openPositionCount').textContent=`(${b.open.length})`;$('openPositions').innerHTML=trackingTable(b.open);
    const trackingStale=!b.tracker?.last_checked||Date.now()-b.tracker.last_checked>180000;
    $('todayPlanSummary').textContent=`${b.today_date} · Türkiye saati · bugün ${b.new_today} yeni plan seçildi. Sonuçlar simülasyondur. R: ücret/kayma varsayımı dahil, funding hariç.${trackingStale?' Takip güncellemesi bekleniyor; durumlar gecikmiş olabilir.':''}`;
    $('todayPlanStats').innerHTML=Object.entries(b.today).map(([key,value])=>`<span>${esc(tradeState[key])}: <b>${value}</b></span>`).join('');
    $('todayPlanRows').innerHTML=trackingTable(b.closed_today);
  }
  function spark(values,up) {
    if(!values||values.length<2)return '<small>Grafik bekleniyor</small>';
    const lo=Math.min(...values),hi=Math.max(...values),span=Math.max(hi-lo,hi*.00001);
    const points=values.map((v,i)=>`${i/(values.length-1)*92},${29-(v-lo)/span*26}`).join(' ');
    return `<svg viewBox="0 0 94 32" role="img" aria-label="Son 32 kapalı 15 dakika mumu"><polyline points="${points}" fill="none" stroke="${up?'#79bca5':'#dc8b93'}" stroke-width="1.6"/></svg>`;
  }
  function movers(rows,results) {
    const bySymbol=new Map(results.map(r=>[r.symbol,r.sparkline]));
    for(const [id,direction] of [['gainers',1],['losers',-1]]) {
      $(id).innerHTML=rows.filter(r=>direction*r.change>0).sort((a,b)=>direction*(b.change-a.change)).slice(0,5).map(r=>`<button class="dash-mover" data-symbol="${esc(r.symbol)}"><span><b>${esc(r.symbol.replace('USDT',''))}</b><small data-live-price="${esc(r.symbol)}">${price(r.price)}</small></span>${spark(bySymbol.get(r.symbol),direction>0)}<span class="${direction>0?'up':'down'}">${pct(r.change)}</span></button>`).join('')||'<div class="dash-empty">Eşleşen piyasa yok.</div>';
    }
  }
  function planCards(rows) {
    return `<div class="plan-grid">${rows.map(r=>{const q=r.verification||{};return `<article class="plan-card"><div class="dash-panel-head"><button data-symbol="${esc(r.symbol)}">${esc(r.symbol)} <span class="${r.side==='LONG'?'up':'down'}">${esc(r.side)}</span></button><b class="plan-score" title="Doğrulama puanı, kazanma olasılığı değil">${num(q.score||0,1)}<small>/100</small></b></div><div class="plan-levels"><span>Giriş<b>${price(r.entry)}</b></span><span>Stop<b>${price(r.stop)}</b></span><span>Hedef<b>${price(r.target)}</b></span></div><p class="dash-note">${esc(r.model)} · ${num(r.rr)}R brüt · ${new Date(r.as_of).toLocaleTimeString('tr-TR')}</p><details><summary>Puanın gerekçesi</summary>${(q.components||[]).map(f=>`<div class="score-factor"><span>${esc(f.name)}</span><b>${num(f.points)}/${f.weight}</b></div>`).join('')}<p class="dash-note">Temel ${num(q.base||0)} + deneyim ${num(q.learning_delta||0)} · ${q.samples||0} tamamlanmış örnek. Olasılık değildir.</p></details></article>`;}).join('')}</div>`;
  }
  const fresh = s => s.last_scan_at && Date.now() - s.last_scan_at < 20 * 60_000;
  function renderRsi(){
    const source=state?.rsi_radar;if(!source)return;
    const grid=$('rsiGrid'),universe=source.items.map(r=>r.symbol).join(',');
    if(grid.dataset.universe!==universe){
      grid.innerHTML=source.items.map(r=>`<a class="rsi-tile" data-rsi-symbol="${esc(r.symbol)}" href="/analiz?symbol=${encodeURIComponent(r.symbol)}"><span>${esc(r.symbol.replace('USDT',''))}</span><b>—</b></a>`).join('');grid.dataset.universe=universe;
    }
    const filter=$('rsiFilter').value,search=$('rsiSearch').value.trim().toUpperCase();
    let high=0,low=0,normal=0,current=0;
    const values=new Map(source.items.map(r=>{
      const tick=VX.live.ticks[r.symbol],newer=tick&&tick.ts>=(r.ts||0)&&Object.hasOwn(tick,'rsi_14');
      const value=newer?tick.rsi_14:r.value,ts=newer?tick.ts:r.ts;
      const valid=value!=null&&Number.isFinite(Number(value))&&value>=0&&value<=100&&VX.live.age({ts:ts||0})<10000&&!(newer&&tick.demo);
      const zone=valid?(value>=70?'high':value<=30?'low':'normal'):'missing';
      if(valid){current++;if(zone==='high')high++;else if(zone==='low')low++;else normal++;}
      return [r.symbol,{value,valid,zone,ts}];
    }));
    for(const cell of grid.children){
      const symbol=cell.dataset.rsiSymbol,r=values.get(symbol);cell.hidden=!symbol.includes(search)||(filter!=='all'&&r.zone!==filter);
      cell.classList.toggle('rsi-high',r.zone==='high');cell.classList.toggle('rsi-low',r.zone==='low');cell.classList.toggle('rsi-missing',!r.valid);
      cell.querySelector('b').textContent=r.valid?num(r.value,1):'—';
      cell.title=r.valid?`${symbol} · RSI(14): ${num(r.value,2)} · 15M oluşan mum · ${new Date(r.ts).toLocaleTimeString('tr-TR')}`:`${symbol} · güncel RSI / işlem fiyatı bekleniyor`;
    }
    $('rsiCounts').innerHTML=`<span>Güncel <b>${current}/${source.items.length}</b></span><span class="rsi-zone-high">≥70 <b>${high}</b></span><span class="rsi-zone-low">≤30 <b>${low}</b></span><span>30–70 <b>${normal}</b></span>`;
    $('rsiStatus').textContent=current?`${current} coin için güncel RSI · mevcut WebSocket akışıyla yenilenir · hesaplama için yeni borsa isteği yapılmaz`:'RSI hazırlanıyor veya güncel işlem fiyatı bekleniyor. Kapalı mum geçmişi ilk taramada yüklenir.';
  }
  function scanInsights(s) {
    const valid=(s.results||[]).filter(r=>r.ok&&!r.demo);
    if(!fresh(s)||s.stale||s.demo||!valid.length){
      $('structureBias').innerHTML='<div class="dash-empty">Güncel yapısal yön verisi bekleniyor.</div>';
      $('structureBiasNote').textContent='Son tarama güncel değil; eski dağılım gösterilmez.';
      $('setupWatch').innerHTML='<div class="dash-empty">Güncel kurulum teyidi bekleniyor.</div>';return;
    }
    const counts=[['LONG','up',valid.filter(r=>r.side==='LONG').length],['SHORT','down',valid.filter(r=>r.side==='SHORT').length],['Nötr','neutral',valid.filter(r=>!['LONG','SHORT'].includes(r.side)).length]];
    $('structureBias').innerHTML=`<div class="bias-stack" aria-hidden="true">${counts.map(([,cls,n])=>`<i class="${cls}" style="width:${n/valid.length*100}%"></i>`).join('')}</div><div class="bias-breakdown">${counts.map(([label,cls,n])=>`<div><span class="${cls}">${label}</span><b>${n}<small>coin</small></b><em>%${num(n/valid.length*100,1)}</em></div>`).join('')}</div>`;
    $('structureBiasNote').textContent=`${valid.length} geçerli analiz · son tarama ${new Date(s.last_scan_at).toLocaleTimeString('tr-TR')} · manuel yön filtresinden bağımsız`;
    const eligible=new Set(['HTF_BIAS_BEKLE','LIKIDITE_BEKLE','MSS_BEKLE','TEYIT_BEKLE','FUNDING_BEKLE']);
    const score=r=>Number(r.verification?.score??r.score??0);
    const watch=valid.filter(r=>!r.ready&&eligible.has(r.phase)).sort((a,b)=>score(b)-score(a)||a.symbol.localeCompare(b.symbol)).slice(0,5);
    $('setupWatch').innerHTML=watch.length?watch.map(r=>`<a class="setup-watch-row" href="/analiz?symbol=${encodeURIComponent(r.symbol)}"><span><b>${esc(r.symbol.replace('USDT',''))}<small class="${r.side==='LONG'?'up':r.side==='SHORT'?'down':''}">${esc(r.side==='WAIT'?'Nötr':r.side)}</small></b><em>${esc(phase(r))}</em></span><strong>${num(score(r),1)}<small>/100</small></strong></a>`).join(''):'<div class="dash-empty">İzleme koşullarını karşılayan bekleyen kurulum yok.</div>';
  }
  function bars(id, rows, unit = "%") {
    const max = Math.max(1e-8, ...rows.map(r => Math.abs(r.value)));
    $(id).innerHTML = rows.length ? rows.map(r => `<div class="dash-bar"><span>${esc(r.label)}</span><div class="dash-bar-track"><div class="dash-bar-fill" style="width:${Math.max(0,Math.min(100,Math.abs(r.value)/max*100))}%;background:${r.value < 0 ? "#dc8b93" : "#79bca5"}"></div></div><b>${esc(num(r.value,unit === "%" ? 4 : 0))}${unit}</b></div>`).join("") : '<div class="dash-empty">Veri bekleniyor.</div>';
  }
  function table(rows, plans = false) {
    return `<table><thead><tr><th>Coin</th>${plans ? '<th>Yön</th><th>Giriş</th><th>Stop</th><th>Hedef</th><th>Brüt R:R</th><th>Mum kapanışı</th>' : '<th>Fiyat</th><th>24 saat</th><th>Hacim</th><th>Funding</th><th>Motor durumu</th>'}</tr></thead><tbody>${rows.map(r => `<tr><td><button type="button" data-symbol="${esc(r.symbol)}">${esc(r.symbol)}</button></td>${plans ? `<td class="${r.side === "LONG" ? "up" : "down"}">${esc(r.side)}</td><td>${price(r.entry)}</td><td>${price(r.stop)}</td><td>${price(r.target)}</td><td>${num(r.rr)}R</td><td>${new Date(r.as_of).toLocaleTimeString("tr-TR")}</td>` : `<td data-live-price="${esc(r.symbol)}">${price(r.price)}</td><td class="${r.change >= 0 ? "up" : "down"}">${pct(r.change)}</td><td>${compact(r.volume)}</td><td>${r.funding == null ? "—" : num(r.funding * 100,4) + "%"}</td><td>${esc(phase(r.analysis))}</td>`}</tr>`).join("")}</tbody></table>`;
  }
  function watchlist() {
    if (!state) return;
    const bySymbol = new Map((state.scanner.results || []).map(r => [r.symbol,r]));
    const query = $("marketSearch").value.toUpperCase().trim();
    const rows = state.markets.filter(r => r.symbol.includes(query)).map(r => ({...r,analysis:bySymbol.get(r.symbol)}));
    $("watchlist").innerHTML = rows.length ? table(rows) : '<div class="dash-empty">Eşleşen coin yok.</div>';
  }
  function render(data) {
    state = data;
    const rows = data.markets, s = data.scanner;
    if ($('directionNote')) $('directionNote').textContent=`Yön: ${s.direction==='LONG'?'yalnızca LONG':s.direction==='SHORT'?'yalnızca SHORT':'otomatik — her coinin yapısına göre'} · Ayarlar → Sinyal yönü`;
    paintBtc();
    VX.live.paintStatus();
    VX.railStatus({data:data.demo ? "demo" : "live"});
    $("marketCount").textContent = `${rows.length} / 200`;
    $("marketVolume").textContent = compact(rows.reduce((a,r) => a+r.volume,0));
    const up = rows.filter(r => r.change > 0).length, down = rows.filter(r => r.change < 0).length, flat = rows.length-up-down;
    $("marketBreadth").textContent = `${up} / ${down}`;
    const a = up / rows.length * 100, b = (up+down) / rows.length * 100;
    $("breadthChart").innerHTML = `<div class="dash-ring" style="background:conic-gradient(#79bca5 0 ${a}%,#dc8b93 ${a}% ${b}%,#54616e ${b}% 100%)"><div>${num(a,0)}%<small>yükselen coin oranı</small></div></div>`;
    $("breadthLegend").innerHTML = `<span class="up">● ${up} yükselen</span><span class="down">● ${down} düşen</span><span>● ${flat} yatay</span>`;
    movers(rows,s.results||[]);
    bars("fundingChart", rows.filter(r=>r.funding != null).sort((a,b)=>Math.abs(b.funding)-Math.abs(a.funding)).slice(0,5).map(r=>({label:r.symbol.replace("USDT",""),value:r.funding*100})));
    const results = s.results || [], groups = {};
    for (const r of results) groups[phase(r)] = (groups[phase(r)] || 0) + 1;
    bars("setupChart",Object.entries(groups).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([label,value])=>({label,value})),"");
    scanInsights(s);
    const quotes = new Map(rows.map(r=>[r.symbol,r.price]));
    const plans = fresh(s) && !data.demo ? (s.candidates || []).filter(r => r.ready && !r.demo && quotes.has(r.symbol) && (r.side === "LONG" ? quotes.get(r.symbol)>r.stop && quotes.get(r.symbol)<r.target : quotes.get(r.symbol)<r.stop && quotes.get(r.symbol)>r.target)) : [];
    $("readyCount").textContent = s.last_scan_at ? plans.length : "—";
    const selectedPlans=plans.filter(r=>r.selected).slice(0,10);
    const planMarkup=selectedPlans.length ? planCards(selectedPlans.slice(0,3))+(selectedPlans.length>3?`<details class="more-plans"><summary>Diğer ${selectedPlans.length-3} planı göster</summary>${planCards(selectedPlans.slice(3))}</details>`:'') : `<div class="dash-empty">${!s.last_scan_at ? "İlk tarama sürüyor. 200 piyasanın verileri yüklenirken lütfen bekleyin." : !fresh(s) ? "Tarama güncel değil. Yeni teyit gelene kadar plan gösterilmiyor." : "Şu anda tüm koşulları karşılayan canlı işlem planı yok."}<br>Motor, sırf liste dolsun diye sinyal üretmez.</div>`;
    if($("plans").dataset.markup!==planMarkup){$("plans").innerHTML=planMarkup;$("plans").dataset.markup=planMarkup;}
    $("scanStatus").textContent = `${s.scanning ? "Taranıyor " + (s.progress?.completed || 0) + "/" + (s.progress?.total || 200) : s.enabled ? "Motor aktif" : "Motor durduruldu"} · son tur ${s.scanned || 0} coin · ${s.errors || 0} veri hatası${s.last_error ? " · " + s.last_error : ""}`;
    $("scanNext").textContent = s.next_scan_at ? `Son: ${new Date(s.last_scan_at).toLocaleTimeString("tr-TR")} · sıradaki: ${new Date(s.next_scan_at).toLocaleTimeString("tr-TR")} · aynı mum tekrar hesaplanmaz` : "İlk yükleme API kotasına göre sırayla yapılır.";
    $("watchCount").textContent = `(${rows.length})`;
    $("dataTime").textContent = "Son veri: " + new Date(data.updated_at).toLocaleTimeString("tr-TR");
    const selected = $("chartSymbol").value;
    if ($("chartSymbol").options.length !== rows.length || !rows.some(r=>r.symbol === selected)) {
      $("chartSymbol").innerHTML = rows.map(r=>`<option>${esc(r.symbol)}</option>`).join("");
      if (rows.some(r=>r.symbol === selected)) $("chartSymbol").value = selected;
    }
    const quote = rows.find(r => r.symbol === $("chartSymbol").value);
    if (quote) $("priceSummary").innerHTML = `${price(quote.price)} <small class="${quote.change>=0?"up":"down"}">${pct(quote.change)} / 24s</small>`;
    watchlist();
    if(!$("priceTicker").children.length)$("priceTicker").innerHTML=rows.slice(0,30).map(r=>`<span>${esc(r.symbol)} <b data-live-price="${esc(r.symbol)}">${price(r.price)}</b></span>`).join('');
    VX.live.watch(rows.map(r=>r.symbol));
    renderBoard();
    renderRsi();
    paintTrackingCharts();
  }
  async function chart() {
    const id = ++chartRequest, symbol = $("chartSymbol").value;
    lastChartFetch=Date.now();
    try {
      const data = await VX.get("/api/engine/smc/chart?symbol="+encodeURIComponent(symbol));
      if (id !== chartRequest) return;
      chartData=data;drawChart(data);
    } catch(e) {if(id===chartRequest) $("priceChart").textContent="Grafik alınamadı: "+e.message;}
  }
  function drawChart(data) {
      const symbol=data.symbol;
      const mobile = innerWidth < 650;
      const rows = data.rows.slice(mobile ? -35 : -70), W=mobile ? 380 : 760,H=270,left=4,right=75,top=14,bottom=60;
      const low = Math.min(...rows.map(r=>+r[3])), high=Math.max(...rows.map(r=>+r[2])), span=Math.max(high-low,high*.0001);
      const y = p => top+(high-p)/span*(H-top-bottom), step=(W-right-left)/rows.length;
      const maxVol=Math.max(1,...rows.map(r=>+r[5]));
      let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(symbol)} 15 dakika mum ve hacim grafiği">`;
      for(let i=0;i<5;i++) {const v=low+span*i/4, yy=y(v);svg+=`<line x1="0" x2="${W-right}" y1="${yy}" y2="${yy}" stroke="#26313b"/><text x="${W-right+8}" y="${yy+4}" fill="#9ba8b6" font-size="10">${esc(price(v))}</text>`;}
      rows.forEach((r,i)=>{const x=left+i*step+step/2,c=+r[4]>=+r[1]?"#79bca5":"#dc8b93",height=Math.max(1,Math.abs(y(+r[1])-y(+r[4])));svg+=`<line x1="${x}" x2="${x}" y1="${y(+r[2])}" y2="${y(+r[3])}" stroke="${c}"/><rect x="${x-step*.3}" y="${Math.min(y(+r[1]),y(+r[4]))}" width="${step*.6}" height="${height}" fill="${c}"/><rect x="${x-step*.3}" y="${H-20-(+r[5]/maxVol)*32}" width="${step*.6}" height="${(+r[5]/maxVol)*32}" fill="${c}" opacity=".45"/>`;if(i%20===0)svg+=`<text x="${x}" y="${H-3}" fill="#9ba8b6" font-size="10">${new Date(+r[0]).toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit"})}</text>`;});
      $("priceChart").innerHTML=svg+"</svg>";
  }
  async function refresh() {
    if(pending || document.hidden) return;
    pending=true;
    try {render(await VX.get("/api/engine/smc/dashboard"));$("dashError").hidden=true;if(!chartData||chartData.symbol!==$("chartSymbol").value||Date.now()-lastChartFetch>60000)void chart();}
    catch(e){$("dashError").textContent="Canlı veri yenilenemedi. Önceki değerler güncel olmayabilir. "+e.message;$("dashError").hidden=false;$("connection").textContent="Bağlantı kesildi";$("plans").dataset.markup='';$("plans").innerHTML='<div class="dash-empty">Bağlantı yenilenene kadar işlem planları gizlendi.</div>';$("readyCount").textContent="—";}
    finally {pending=false;}
  }
  $("chartSymbol").addEventListener("change",()=>{if(state)render(state);chart();});
  $("marketSearch").addEventListener("input",watchlist);
  $('rsiSearch').addEventListener('input',renderRsi);$('rsiFilter').addEventListener('change',renderRsi);
  document.querySelector(".dashboard").addEventListener("click",event=>{const b=event.target.closest("button[data-symbol]");if(b){$("chartSymbol").value=b.dataset.symbol;render(state);chart();$("priceChart").scrollIntoView({behavior:"smooth",block:"center"});}});
  const timer=setInterval(refresh,10000), clock=setInterval(()=>{if($("clock"))$("clock").textContent=new Date().toLocaleTimeString("tr-TR");},1000);
  const unsubscribe=VX.live.onTick(batch=>{
    if(batch.has('BTCUSDT'))paintBtc();
    if(!state)return;
    const bySymbol=new Map(state.markets.map(r=>[r.symbol,r]));
    for(const [symbol,tick] of batch){
      if(tick.demo||tick.source!=='aggTrade'||VX.live.age(tick)>5000)continue;
      const row=bySymbol.get(symbol);if(!row)continue;
      row.price=tick.price;
      for(const p of state.board?.pending||[]){if(p.symbol===symbol&&(p.side==='LONG'?tick.price<=p.entry:tick.price>=p.entry))crossedEntries.add(p.id);}
      if(tick.change_24h!=null)row.change=tick.change_24h;
      if(tick.volume_24h!=null)row.volume=tick.volume_24h;
      if(tick.funding!=null)row.funding=tick.funding;
      document.querySelectorAll(`[data-live-price="${symbol}"]`).forEach(el=>el.textContent=price(tick.price));
      if(symbol===$("chartSymbol").value){
        $("priceSummary").innerHTML=`${price(tick.price)} <small class="${row.change>=0?'up':'down'}">${pct(row.change)} / 24s</small>`;
        if(chartData?.symbol===symbol){
          let bar=chartData.rows.at(-1),c=tick.candle;
          if(c&&+c[0]>=+bar[0]){if(+c[0]>+bar[0])chartData.rows.push([...c]);else chartData.rows[chartData.rows.length-1]=[...c];bar=chartData.rows.at(-1);}
          if(tick.ts>=+bar[0]&&tick.ts<=+bar[6]){bar[2]=Math.max(+bar[2],tick.price);bar[3]=Math.min(+bar[3],tick.price);bar[4]=tick.price;}
          if(performance.now()-lastDraw>150){drawChart(chartData);lastDraw=performance.now();}
        }
      }
      lastTickAt=Date.now();
      $("streamLatency").textContent=`WebSocket · son işlem yaşı ${Math.round(VX.live.age(tick))} ms · RSS 60 sn · takvim 30 dk`;
    }
  });
  const freshness=setInterval(()=>{if(!document.hidden&&state&&(!lastTickAt||Date.now()-lastTickAt>5000)){$("streamLatency").textContent='Son işlem akışı 5 saniyeyi aştı; fiyatlar canlı kabul edilmez. Bağlantı yenileniyor.';}},1000);
  window.addEventListener('vortex:scanner',e=>{if(state&&$("dashError").hidden){state.scanner=e.detail;render(state);}});
  const marketPaint=setInterval(()=>{
    if(!state||document.hidden)return;
    movers(state.markets,state.scanner.results||[]);
    if(!$("dashError").hidden)return;
    renderBoard();
    renderRsi();
    paintTrackingCharts();
    const rows=state.markets;
    $("marketVolume").textContent=compact(rows.reduce((a,r)=>a+r.volume,0));
    $("marketBreadth").textContent=`${rows.filter(r=>r.change>0).length} / ${rows.filter(r=>r.change<0).length}`;
  },500);
  window.addEventListener("pagehide",()=>{clearInterval(timer);clearInterval(clock);clearInterval(freshness);clearInterval(marketPaint);clearInterval(btcFreshness);clearInterval(btcHistoryRefresh);unsubscribe();unbook();});
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
  VX.live.connect();
  refresh();
})();
