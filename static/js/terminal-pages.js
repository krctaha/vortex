(() => {
  const $=id=>document.getElementById(id),esc=VX.esc,page=document.querySelector('main').dataset.page;
  const fail=e=>{$('pageError').hidden=false;$('pageError').textContent=e.message;};
  VX.live.connect();
  const clock=()=>{if($('clock'))$('clock').textContent=new Date().toLocaleTimeString('tr-TR');};clock();setInterval(clock,1000);
  const num=x=>x==null?'—':Number(x).toLocaleString('tr-TR',{maximumFractionDigits:4});
  function table(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;}
  async function journal(){
    const d=await VX.get('/api/terminal/journal'),states={pending:'Giriş bekliyor',open:'Simülasyon açık',target:'Hedef',stop:'Stop',timeout:'Süre sonu',ambiguous:'Sıra belirsiz',expired:'Süresi doldu',invalidated:'Geçersiz',data_gap:'Veri boşluğu'};
    const counts=new Map(d.summary.map(r=>[r.state,r.count]));
    $('journalStats').innerHTML=[['Bekleyen',counts.get('pending')||0],['Açık simülasyon',counts.get('open')||0],['Hedef / stop',`${counts.get('target')||0} / ${counts.get('stop')||0}`],['Belirsiz / veri boşluğu',(counts.get('ambiguous')||0)+(counts.get('data_gap')||0)]].map(([label,value])=>`<article><span>${label}</span><strong>${value}</strong></article>`).join('');
    $('journalRows').innerHTML=d.items.length?table(['Coin','Yön','Durum','Giriş','Stop','Hedef','Brüt R','Maliyet sonrası R*','Oluşturulma'],d.items.map(r=>`<tr><td>${esc(r.symbol)}</td><td>${esc(r.side)}</td><td>${esc(states[r.state]||r.state)}</td><td>${num(r.entry)}</td><td>${num(r.stop)}</td><td>${num(r.target)}</td><td>${num(r.result_r)}</td><td>${num(r.cost_r)}</td><td>${VX.fmtTime(r.created_at)}</td></tr>`)):'<div class="dash-empty">Henüz ileriye dönük kayıt yok. Hazır plan oluştuğunda takip otomatik başlar.</div>';
    $('journalNote').textContent=d.note;
  }
  async function cross(){
    const d=await VX.get('/api/terminal/cross');
    $('crossWatch').innerHTML=d.instruments.map(s=>`<div><b>${esc(s)}</b><small>Veri sağlayıcısı bekleniyor</small></div>`).join('');
    $('crossRows').innerHTML=d.items.length?table(['Varlık','Yön','Giriş','Miktar','Çıkış','Fark × birim','Durum'],d.items.map(r=>`<tr><td>${esc(r.asset)}</td><td>${esc(r.side)}</td><td>${num(r.entry)}</td><td>${num(r.quantity)}</td><td>${num(r.exit)}</td><td>${r.exit==null?'—':num((r.exit-r.entry)*r.quantity*(r.side==='LONG'?1:-1))}</td><td>${r.closed_at?'Kapandı':`<button type="button" data-close="${r.id}">Kapanış gir</button>`}</td></tr>`)):'<div class="dash-empty">Manuel kayıt yok.</div>';
  }
  async function connections(){
    const d=await VX.get('/api/system/status');
    VX.railStatus({data:d.data_mode==='demo'?'demo':'live'});
    $('connectionStatus').textContent=`Binance: ${d.data_mode} · İşlem WebSocket: ${d.ws?.trades_connected?'bağlı':'bağlı değil'} · RSS kontrolü 60 sn · Ekonomik takvim 30 dk · Forex/metaller: bağlı değil`;
  }
  if(page==='journal'){$('journalRefresh').onclick=()=>journal().catch(fail);journal().catch(fail);setInterval(()=>journal().catch(fail),60000);}
  if(page==='cross'){
    cross().catch(fail);
    $('crossForm').onsubmit=async e=>{e.preventDefault();try{const d=Object.fromEntries(new FormData(e.target));d.entry=Number(d.entry);d.quantity=Number(d.quantity);await VX.post('/api/terminal/cross',d);e.target.reset();await cross();}catch(err){fail(err);}};
    $('crossRows').onclick=async e=>{const b=e.target.closest('[data-close]');if(!b)return;const price=prompt('Manuel kapanış fiyatı (ondalık için nokta):');if(price===null)return;if(!Number.isFinite(Number(price))||Number(price)<=0)return VX.toast('Geçerli pozitif fiyat gir','warn');try{await VX.post(`/api/terminal/cross/${b.dataset.close}/close`,{exit:Number(price)});await cross();}catch(err){fail(err);}};
  }
  if(page==='settings'){
    if($('usersPanel')){
      let users=[],deleteTarget=null;
      const error=e=>{$('usersError').hidden=false;$('usersError').textContent=e.message;};
      const usageLabels={trades:'işlem',signals:'sinyal',push:'bildirim aboneliği',cross_manual:'manuel piyasa kaydı',mentor_trades:'eski takip kaydı',mentor_reviews:'analiz kaydı',watchlist:'izleme kaydı',notification_log:'bildirim günlüğü'};
      async function loadUsers(){
        const d=await VX.get('/api/users');users=d.items;
        $('usersRows').innerHTML=table(['Kullanıcı','Yetki','Durum','Son giriş','İşlemler'],users.map(u=>`<tr><td><b>${esc(u.username)}</b>${u.is_self?' · sen':''}<br><small>${esc(u.display_name||'')}</small></td><td>${u.role==='admin'?'Yönetici':'Kullanıcı'}</td><td>${u.is_active?'Aktif':'Pasif'}</td><td>${u.last_login?VX.fmtTime(u.last_login):'Henüz giriş yok'}</td><td><div class="user-actions"><button class="btn" type="button" data-user-active="${u.id}"${u.is_self?' disabled':''}>${u.is_active?'Pasife al':'Etkinleştir'}</button><button class="btn" type="button" data-user-delete="${u.id}"${u.is_self?' disabled':''}>Sil</button></div></td></tr>`));
      }
      $('usersRefresh').onclick=()=>loadUsers().catch(error);loadUsers().catch(error);
      $('userCreateForm').onsubmit=async e=>{
        e.preventDefault();const f=e.target,b=f.querySelector('button[type=submit]');b.disabled=true;$('usersError').hidden=true;
        try{const data=Object.fromEntries(new FormData(f));data.username=data.username.trim().toLowerCase();data.role='user';await VX.post('/api/users',data);f.reset();await loadUsers();VX.toast('Kullanıcı oluşturuldu');}catch(err){error(err);}finally{b.disabled=false;}
      };
      $('usersRows').onclick=async e=>{
        const active=e.target.closest('[data-user-active]'),del=e.target.closest('[data-user-delete]');
        if(active){const u=users.find(x=>x.id===Number(active.dataset.userActive));if(!u||u.is_self)return;if(!u.is_active||confirm(`${u.username} hesabının erişimini kesmek istiyor musun?`)){active.disabled=true;try{await VX.post(`/api/users/${u.id}/active`,{is_active:!u.is_active});await loadUsers();VX.toast('Hesap erişimi güncellendi');}catch(err){error(err);active.disabled=false;}}}
        if(del){deleteTarget=users.find(x=>x.id===Number(del.dataset.userDelete));if(!deleteTarget||deleteTarget.is_self)return;
          const usage=Object.entries(deleteTarget.usage||{}).filter(([,n])=>n>0),f=$('userDeleteForm');f.reset();$('userDeleteError').hidden=true;
          $('userDeleteDescription').textContent=`${deleteTarget.username} hesabı kalıcı silinecek. `+(usage.length?usage.map(([k,n])=>`${n} ${usageLabels[k]||k}`).join(', ')+' de silinir.':'Bağlı kişisel kayıt bulunmuyor.')+' Ortak SMC araştırma kayıtları korunur.';
          $('userPurgeLabel').hidden=!usage.length;f.elements.purge.required=!!usage.length;$('userDeleteDialog').showModal();f.elements.username_confirm.focus();
        }
      };
      $('userDeleteCancel').onclick=()=>$('userDeleteDialog').close();
      $('userDeleteForm').onsubmit=async e=>{
        e.preventDefault();const f=e.target,b=f.querySelector('button[type=submit]');if(!deleteTarget)return;
        if(f.elements.username_confirm.value!==deleteTarget.username){$('userDeleteError').hidden=false;$('userDeleteError').textContent='Kullanıcı adı eşleşmiyor.';return;}
        b.disabled=true;$('userDeleteError').hidden=true;
        try{await VX.del(`/api/users/${deleteTarget.id}${f.elements.purge.checked?'?purge=true':''}`);$('userDeleteDialog').close();deleteTarget=null;await loadUsers();VX.toast('Hesap ve bağlı kişisel kayıtlar kalıcı silindi');}catch(err){$('userDeleteError').hidden=false;$('userDeleteError').textContent=err.message;}finally{b.disabled=false;}
      };
    }
    async function loadPreferences(){
      const d=await VX.get('/api/terminal/preferences'),f=$('autoNotificationForm');
      for(const name of ['interval_minutes','symbol_cooldown_hours','min_score'])f.elements[name].value=d.notifications[name];
      f.elements.enabled.checked=d.notifications.enabled;
      f.elements.instant_enabled.checked=d.notifications.instant_enabled;
      $('notificationForm').elements.telegram_chat_id.value=d.telegram.chat_id||'';
      $('telegramState').textContent=d.telegram.configured?'Bot ve hedef kayıtlı. Teslimatı test düğmesiyle doğrula.':'Bot token veya Chat ID eksik.';
      const s=d.notification_state;
      $('notificationHistory').textContent=(s.last_sent?'Son özet: '+VX.fmtTime(s.last_sent):'Henüz özet yok.')+(s.last_instant_sent?' · Son giriş bildirimi: '+VX.fmtTime(s.last_instant_sent):' · Yeni giriş bildirimi bekleniyor.')+(s.error||s.instant_error?' · Hata: '+(s.error||s.instant_error):'');
      if($('directionForm')){$('directionForm').elements.direction.value=d.direction;$('directionState').textContent=d.direction==='AUTO'?'Otomatik':`Yalnızca ${d.direction}`;}
    }
    async function saveConnection(){
      const f=$('notificationForm'),d=Object.fromEntries([...new FormData(f)].map(([k,v])=>[k,v.trim()]).filter(([k,v])=>v));
      if(Object.keys(d).length)await VX.post('/api/auth/profile',d);
      f.elements.telegram_token.value='';
    }
    loadPreferences().catch(fail);
    if($('directionForm'))$('directionForm').onsubmit=async e=>{e.preventDefault();try{await VX.post('/api/terminal/direction',{direction:e.target.elements.direction.value});await loadPreferences();VX.toast('Yön tercihi uygulandı');}catch(err){fail(err);}};
    $('autoNotificationForm').onsubmit=async e=>{e.preventDefault();try{const f=e.target;await VX.post('/api/terminal/notifications',{enabled:f.elements.enabled.checked,instant_enabled:f.elements.instant_enabled.checked,interval_minutes:Number(f.elements.interval_minutes.value),symbol_cooldown_hours:Number(f.elements.symbol_cooldown_hours.value),min_score:Number(f.elements.min_score.value)});await loadPreferences();VX.toast('Otomatik bildirim tercihi kaydedildi');}catch(err){fail(err);}};
    VX.get('/api/auth/state').then(d=>{const f=$('accountForm');$('settingsUsername').value=d.user.username;f.elements.display_name.value=d.user.display_name||'';f.elements.email.value=d.user.email||'';}).catch(fail);
    $('accountForm').onsubmit=async e=>{e.preventDefault();try{const d=Object.fromEntries(new FormData(e.target));if(!d.new_password){delete d.new_password;delete d.current_password;}await VX.post('/api/auth/profile',d);e.target.elements.current_password.value='';e.target.elements.new_password.value='';VX.toast('Hesap güncellendi');}catch(err){fail(err);}};
    $('notificationForm').onsubmit=async e=>{e.preventDefault();try{await saveConnection();await loadPreferences();VX.toast('Bağlantı kaydedildi');}catch(err){fail(err);}};
    $('testTelegram').onclick=async()=>{const b=$('testTelegram');b.disabled=true;try{await saveConnection();const r=await VX.post('/api/telegram/test',{});if(!r.ok||!r.delivered)throw Error(r.error||'Mesaj teslimatı doğrulanamadı');await loadPreferences();$('pageError').hidden=true;$('telegramState').textContent=`@${r.bot} · Test mesajı Telegram tarafından kabul edildi.`;VX.toast('Test mesajı gönderildi');}catch(err){fail(err);$('telegramState').textContent='Test başarısız: '+err.message;}finally{b.disabled=false;}};
    $('reduceMotion').checked=localStorage.getItem('vx.reduceMotion')==='1';
    $('reduceMotion').onchange=e=>localStorage.setItem('vx.reduceMotion',e.target.checked?'1':'0');
    $('testConnections').onclick=()=>connections().catch(fail);connections().catch(fail);
    if($('scanToggle')){
      const paint=s=>{$('scanToggle').dataset.enabled=String(s.enabled);$('scanToggle').textContent=s.enabled?'Taramayı durdur':'Taramayı başlat';};
      VX.get('/api/engine/smc/status').then(paint).catch(fail);
      $('scanToggle').onclick=()=>VX.post('/api/engine/premium/toggle',{enabled:$('scanToggle').dataset.enabled!=='true'}).then(paint).catch(fail);
    }
    $('signOut').onclick=()=>VX.post('/api/auth/logout',{}).then(()=>location.href='/giris').catch(fail);
  }
})();
