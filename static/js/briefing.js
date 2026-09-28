(() => {
  const esc=VX.esc;
  function paint(data) {
    if(data.type==='scanner'){window.dispatchEvent(new CustomEvent('vortex:scanner',{detail:data.scanner}));return;}
    if(data.heartbeat)return;
    window.dispatchEvent(new CustomEvent('vortex:briefing',{detail:data}));
    const box=document.getElementById('newsTicker');
    if(!box)return;
    const headlines=(data.items||[]).slice(0,18).map(n=>{
      const safe=/^https?:\/\//i.test(n.link||'');
      return `<span>${n.important?'<b class="news-alert" title="Ekonomik anahtar kelime eşleşmesi">!</b> ':''}${safe?`<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>`:esc(n.title)} <small>${esc(n.source)} · ${VX.fmtAgo(n.published)}</small></span>`;
    });
    const events=(data.events||[]).filter(e=>e.time>Date.now()-3600000&&e.time<Date.now()+48*3600000).slice(0,12).map(e=>`<span>${e.impact==='High'?'<b class="news-alert">!</b> ':''}TAKVİM · ${esc(e.currency)} ${esc(e.title)} · ${VX.fmtTime(e.time)} · ${esc(e.impact_tr)}</span>`);
    box.innerHTML=[...events,...headlines].join('')||'Haber/takvim kaynağı bekleniyor. Bu akış RSS tabanlıdır.';
    box.title=`RSS kontrolü 60 sn; takvim 30 dk. Son haber kontrolü: ${VX.fmtTime(data.news_fetched)}. Yayın gecikmesi kaynağa bağlıdır.`;
  }
  let socket, stopped=false, retry=1000;
  function connect(){
    socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/briefing`);
    socket.onopen=()=>{retry=1000;};
    socket.onmessage=e=>{try{paint(JSON.parse(e.data));}catch(_){}};
    socket.onclose=()=>{if(!stopped)setTimeout(connect,retry);retry=Math.min(retry*2,30000);};
  }
  document.getElementById('tickerPause')?.addEventListener('click',e=>{const paused=document.body.classList.toggle('tickers-paused');e.currentTarget.textContent=paused?'▶':'Ⅱ';e.currentTarget.setAttribute('aria-label',paused?'Şeritleri oynat':'Şeritleri durdur');});
  if(localStorage.getItem('vx.reduceMotion')==='1')document.body.classList.add('tickers-paused');
  window.addEventListener('pagehide',()=>{stopped=true;socket?.close();});
  connect();
})();
