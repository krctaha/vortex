const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 let context,createdId;
 try{
  const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
  context=await browser.newContext({viewport:{width:1440,height:1100}});
  if(!(await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))})).ok())throw Error('Login failed');
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/ayarlar');await page.locator('#usersRows [data-user-delete]').first().waitFor();
  await page.waitForFunction(()=>document.querySelector('#modeChip').dataset.streamState==='live',null,{timeout:20000});
  const stale=await page.evaluate(()=>{const old=VX.live.lastFreshAt;VX.live.lastFreshAt=Date.now()-10000;VX.live.paintStatus();const text=document.querySelector('#modeText').textContent;VX.live.lastFreshAt=old;VX.live.paintStatus();return text;});
  if(stale!=='Veri bekleniyor')throw Error('Global stale status misleading');
  await page.evaluate(()=>VX.live.socket.close());
  await page.waitForFunction(()=>document.querySelector('#modeChip').dataset.streamState==='live',null,{timeout:20000});
  const listing=await (await context.request.get(base+'/api/users')).json(),self=listing.items.find(u=>u.is_self);
  if(!await page.locator(`[data-user-delete="${self.id}"]`).isDisabled())throw Error('Self deletion not disabled');
  if(base==='http://127.0.0.1:18765'){
   const username='qa-ui-'+Date.now(),password='Temporary-QA-password-123';
   await page.locator('#userCreateForm input[name=username]').fill(username);
   await page.locator('#userCreateForm input[name=password]').fill(password);
   await page.locator('#userCreateForm button[type=submit]').click();
   await page.getByRole('row').filter({hasText:username}).waitFor();
   const after=await (await context.request.get(base+'/api/users')).json();createdId=after.items.find(u=>u.username===username).id;
   const member=await browser.newContext();
   try{
    if(!(await member.request.post(base+'/api/auth/login',{data:{username,password}})).ok())throw Error('New user cannot log in');
    const mp=await member.newPage();await mp.goto(base+'/ayarlar');
    if(await mp.locator('#usersPanel').count())throw Error('Management visible to member');
    if((await member.request.get(base+'/api/users')).status()!==403)throw Error('Member can list users');
    if((await member.request.post(base+'/api/users',{data:{username:'blocked-member',password}})).status()!==403)throw Error('Member can create users');
   }finally{await member.close();}
   await page.locator(`[data-user-delete="${createdId}"]`).click();
   await page.locator('#userDeleteForm input[name=username_confirm]').fill(username);
   await page.locator('#userDeleteForm button[type=submit]').click();
   await page.waitForFunction(()=>!document.querySelector('#userDeleteDialog').open);
   if((await (await context.request.get(base+'/api/users')).json()).items.some(u=>u.id===createdId))throw Error('Delete did not remove account');
   createdId=null;
  }
  await page.locator('#usersPanel').scrollIntoViewIfNeeded();await page.screenshot({path:'qa/admin-users-desktop.png'});
  await page.goto(base+'/');
  await page.waitForFunction(()=>document.querySelectorAll('#rsiGrid .rsi-tile').length===200,null,{timeout:30000});
  await page.waitForFunction(()=>[...document.querySelectorAll('#rsiGrid .rsi-tile>b')].some(el=>el.textContent!=='—'),null,{timeout:180000});
  await page.evaluate(()=>{window.__rsiTicks=0;VX.live.onTick(batch=>{for(const tick of batch.values())if(Number.isFinite(tick.rsi_14)&&tick.rsi_14>=0&&tick.rsi_14<=100)window.__rsiTicks++;});});
  await page.waitForFunction(()=>window.__rsiTicks>0,null,{timeout:10000});
  await page.locator('#rsiSearch').fill('BTCUSDT');
  if(await page.locator('#rsiGrid .rsi-tile:visible').count()!==1)throw Error('RSI search failed');
  await page.locator('#rsiSearch').fill('');await page.locator('#rsiFilter').selectOption('high');
  const bad=await page.locator('#rsiGrid .rsi-tile:visible').evaluateAll(els=>els.some(el=>Number(el.querySelector('b').textContent.replace(',','.'))<70));
  if(bad)throw Error('RSI region filter failed');
  await page.locator('#rsiFilter').selectOption('all');await page.locator('.dash-rsi').scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/rsi-radar-desktop.png'});
  const data=await (await context.request.get(base+'/api/engine/smc/dashboard')).json();
  const miniCount=await page.locator('.trade-mini-chart').count();
  if(miniCount!==data.board.pending.length+data.board.open.length)throw Error('Missing tracked-position mini charts');
  if(data.board.open.length){await page.locator('#openPositions').scrollIntoViewIfNeeded();await page.screenshot({path:'qa/trade-minis-desktop.png'});}
  await page.setViewportSize({width:390,height:844});await page.goto(base+'/');
  await page.locator('#rsiGrid .rsi-tile').first().waitFor();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile overflow');
  await page.locator('.dash-rsi').scrollIntoViewIfNeeded();await page.screenshot({path:'qa/rsi-radar-mobile.png'});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: global live/reconnect status, admin-only user controls, streaming RSI and TP/SL mini charts');
 }finally{
  if(createdId&&context)await context.request.delete('http://127.0.0.1:18765/api/users/'+createdId+'?purge=true');
  await browser.close();
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
