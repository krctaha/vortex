const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const login=await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))});
  if(!login.ok())throw Error('Login failed');
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/ayarlar');
  await page.waitForFunction(()=>document.querySelector('#autoNotificationForm').elements.interval_minutes.value!=='');
  if(base.includes('127.0.0.1')){
   await page.locator('#directionForm select').selectOption('SHORT');
   await page.locator('#directionForm button').click();
   await page.locator('#directionState').filter({hasText:'Yalnızca SHORT'}).waitFor();
   await page.locator('#directionForm select').selectOption('AUTO');
   await page.locator('#directionForm button').click();
   await page.locator('#directionState').filter({hasText:'Otomatik'}).waitFor();
  }
  await page.screenshot({path:'qa/matte-settings-desktop.png'});
  await page.locator('#notifications').scrollIntoViewIfNeeded();
  await page.route('**/api/telegram/test',route=>route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({detail:'Test: hedef kanal yetkisi yok'})}));
  // Prevent the UI test from writing any connection fields on production.
  await page.route('**/api/auth/profile',route=>route.fulfill({contentType:'application/json',body:'{"ok":true}'}));
  await page.locator('#testTelegram').click();
  await page.locator('#telegramState').filter({hasText:'Test başarısız'}).waitFor();
  if(await page.getByText('Test mesajı gönderildi',{exact:true}).count())throw Error('False Telegram success');
  await page.screenshot({path:'qa/matte-telegram-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await page.waitForFunction(()=>document.querySelector('#autoNotificationForm').elements.interval_minutes.value!=='');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Settings mobile overflow');
  await page.screenshot({path:'qa/matte-settings-mobile.png'});
  await page.goto(base+'/');
  await page.locator('#priceChart svg').waitFor({timeout:60000});
  await page.setViewportSize({width:1440,height:1100});
  await page.screenshot({path:'qa/matte-dashboard-desktop.png'});
  const color=await page.locator('.dash-panel').first().evaluate(el=>getComputedStyle(el).backgroundColor);
  if(color!=='rgb(18, 18, 18)')throw Error('Matte surface not applied: '+color);
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: settings, Telegram error truthfulness, matte desktop and mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
