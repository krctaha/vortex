const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1100}});
    const login=await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))});
    if(!login.ok())throw Error('Login failed');
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const ready=async()=>{
      await page.locator('#chartScreenshotBtn:not([disabled])').waitFor({timeout:90000});
      if(await page.getByText('Sayfa yüklenemedi:',{exact:false}).count())throw Error('Chart boot toast');
      if(!await page.locator('canvas').count())throw Error('Chart missing');
    };
    await page.goto(base+'/takvim');
    await page.waitForFunction(()=>Boolean(window.VX));
    await page.evaluate(()=>{window.__navigationMarker=true;});
    await page.locator('#rayMenu a[href="/analiz"]').click();
    await ready();
    if(!await page.evaluate(()=>window.__navigationMarker))throw Error('Not an SPA navigation');
    await page.locator('#rayMenu a[href="/takvim"]').click();
    await page.locator('main[data-page="takvim"]').waitFor();
    await page.goBack();await ready();
    await page.reload();await ready();
    if(errors.length)throw Error(errors.join('\n'));
    console.log('PASS: calendar → chart (SPA), back navigation, direct reload; no page errors');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
