const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
  const login=await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))});
  if(!login.ok())throw Error('Login failed');
  const page=await context.newPage();
  let pendingSmc;
  await page.route('**/api/market/smc?**',route=>{pendingSmc=route;});
  page.on('pageerror',e=>console.log('PAGE ERROR',e.message));
  page.on('response',r=>{if(r.status()>=400)console.log('HTTP ERROR',r.status(),r.url());});
  await page.goto(base+'/analiz');
  await page.locator('#chartScreenshotBtn:not([disabled])').waitFor({timeout:20000});
  console.log('PASS: chart renders while SMC request is still pending');
  if(pendingSmc)await pendingSmc.abort().catch(()=>{});
  await page.unroute('**/api/market/smc?**');
  for (const width of [1440,390]) {
   await page.setViewportSize({width,height:1000});
   await page.locator('#analysisChart').scrollIntoViewIfNeeded();
   await page.screenshot({path:`qa/chart-live-${width}.png`,fullPage:true});
   console.log(JSON.stringify(await page.evaluate(()=>{
    const el=document.querySelector('#analysisChart');const r=el.getBoundingClientRect();
    return {viewport:innerWidth,width:r.width,height:r.height,canvases:[...el.querySelectorAll('canvas')].map(c=>({width:c.width,height:c.height})),bodyWidth:document.documentElement.scrollWidth};
   })));
  }
  await page.route('**/api/market/snapshot?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Test veri kesintisi'})}));
  await page.reload();
  await page.locator('#analysisRetry:visible').waitFor({timeout:20000});
  if(!(await page.locator('#analysisLoadText').innerText()).includes('Test veri kesintisi'))throw Error('Missing persistent chart error');
  await page.unroute('**/api/market/snapshot?**');
  await page.locator('#analysisRetry').click();
  await page.locator('#chartScreenshotBtn:not([disabled])').waitFor({timeout:20000});
  console.log('PASS: persistent error and retry recovery');
 } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
