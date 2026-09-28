const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const login=await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))});
  if(!login.ok())throw Error('Login failed');
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/');
  await page.waitForFunction(()=>document.querySelector('#todayPlanSummary').textContent.includes('bugün'));
  for(const name of ['Yeni giriş planları','Açık takipler','Günün sonuçları']){
   if(!await page.getByRole('heading',{name,exact:false}).count())throw Error('Missing lifecycle section: '+name);
  }
  console.log('Open positions:',await page.locator('#openPositionCount').innerText());
  await page.screenshot({path:'qa/opportunity-board-desktop.png'});
  await page.locator('#openPositions').scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/opportunity-board-tracking.png'});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await page.waitForFunction(()=>document.querySelector('#todayPlanSummary').textContent.includes('bugün'));
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile overflow');
  await page.screenshot({path:'qa/opportunity-board-mobile.png'});
  await page.goto(base+'/ayarlar');
  await page.locator('input[name=instant_enabled]').waitFor();
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: entry/open/today sections, mobile, instant notification setting');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
