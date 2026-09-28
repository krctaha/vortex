const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const base=process.env.VORTEX_TEST_URL||'http://127.0.0.1:18765';
  const context=await browser.newContext({viewport:{width:1600,height:1050}});
  if(!(await context.request.post(base+'/api/auth/login',{data:JSON.parse(fs.readFileSync(0,'utf8'))})).ok())throw Error('Login failed');
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/');
  await page.waitForFunction(()=>document.querySelector('#structureBias').children.length>0);
  const nav=await page.locator('#ray').boundingBox();
  if(nav.width<1500||nav.height>60)throw Error('Left rail remains');
  await page.screenshot({path:'qa/text-nav-desktop.png'});
  await page.locator('.dash-insights').scrollIntoViewIfNeeded();
  const panels=await page.locator('.dash-insights>section').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width}}));
  if(panels.length!==3||panels.some(p=>Math.abs(p.y-panels[0].y)>2))throw Error('Insights not a complete row');
  const data=await (await context.request.get(base+'/api/engine/smc/dashboard')).json();
  const valid=data.scanner.results.filter(r=>r.ok&&!r.demo);
  if(!data.scanner.stale&&valid.length){
   await page.waitForFunction(expected=>[...document.querySelectorAll('.bias-breakdown b')].reduce((n,el)=>n+parseInt(el.textContent),0)===expected,valid.length,{timeout:30000});
   const total=await page.locator('.bias-breakdown b').evaluateAll(els=>els.reduce((n,el)=>n+parseInt(el.textContent),0));
   if(total!==valid.length)throw Error('Structural counts do not match real scan');
  }
  await page.screenshot({path:'qa/dashboard-insights-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await page.waitForFunction(()=>document.querySelector('#structureBias').children.length>0);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile overflow');
  await page.screenshot({path:'qa/text-nav-mobile.png'});
  await page.locator('.dash-insights').scrollIntoViewIfNeeded();
  await page.screenshot({path:'qa/dashboard-insights-mobile.png'});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS: quiet top navigation, three filled insight panels, real scan counts and mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
