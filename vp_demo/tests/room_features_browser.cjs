// Actual browser acceptance for A/B/C. Only the CF transport and clock are synthetic.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {randomBytes}=require('node:crypto');
const {chromium}=require(process.env.VP_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vplab-room-features-'));
const output=process.argv[2]?path.resolve(process.argv[2]):null;
const external=process.env.VP_PREVIEW_BASE,token=process.env.VP_ROOM_TEST_TOKEN||randomBytes(24).toString('hex');
let child,browser;
async function boot(){
 if(external)return external;
 child=spawn(process.env.VP_TEST_PYTHON||'python',['-u','tests/room_features_fixture.py','--port','0'],{
  cwd:root,windowsHide:true,env:{...process.env,VPLAB_DB:path.join(tmp,'test.sqlite3'),VP_ROOM_TEST_TOKEN:token},stdio:['ignore','pipe','pipe']});
 return new Promise((resolve,reject)=>{
  let out='',err='';const timer=setTimeout(()=>reject(Error('Fixture boot timed out: '+err)),12000);
  child.on('error',reject);child.once('exit',c=>{clearTimeout(timer);reject(Error('Fixture exited '+c+': '+err));});
  child.stderr.on('data',s=>err+=s);child.stdout.on('data',s=>{out+=s;const m=out.match(/VP Lab: (http:\/\/127\.0\.0\.1:\d+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
 });
}
async function post(context,base,route,data){
 const response=await context.request.post(base+route,{data});const body=await response.json();
 assert.equal(response.status(),200,JSON.stringify(body));return body;
}
async function control(context,base,data){
 const response=await context.request.post(base+'/__acceptance/control',{data,headers:{'X-Test-Token':token}});
 assert.equal(response.status(),200);
}
async function roomData(context,base,id){return (await context.request.get(base+'/api/rooms/'+id)).json();}
const tableOf=page=>page.locator('tbody tr').evaluateAll(rows=>rows.map(r=>Array.from(r.cells).slice(0,4).map(c=>c.textContent)));
async function waitForBoard(page){await page.waitForFunction(()=>{
 const rows=document.querySelectorAll('tbody tr');return rows.length===2&&rows[0].cells[1].textContent==='beta'&&rows[0].cells[3].textContent==='30'&&rows[1].cells[3].textContent==='40';
});}
async function screenshot(page,name,locator){if(output){fs.mkdirSync(output,{recursive:true});if(!locator)await page.evaluate(()=>window.scrollTo(0,0));await (locator?page.locator(locator):page).screenshot({path:path.join(output,name+'.png')});}}
async function noOverflow(page){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflows its viewport');}
(async()=>{
 try{
  const base=await boot(),exe='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  browser=await chromium.launch({headless:true,executablePath:process.env.VP_BROWSER_EXE||(fs.existsSync(exe)?exe:undefined)});
  const contexts=await Promise.all([1,2,3].map(()=>browser.newContext({viewport:{width:1440,height:1100}})));
  const [a,b,c]=await Promise.all(contexts.map(x=>x.newPage())),errors=[],manualSync=[];
  for(const page of [a,b,c]){page.setDefaultTimeout(18000);page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/\/api\/rooms\/[^/]+\/sync(?:\?|$)/.test(r.url()))manualSync.push(r.url());});}
  const accounts=['preview_alpha','preview_beta','preview_outsider'];
  for(let i=0;i<contexts.length;i++){
   await post(contexts[i],base,'/api/register',{username:accounts[i],password:'Preview-12345'});
   if(i<2)await post(contexts[i],base,'/api/settings',{referenceRating:i===0?1200:1600});
  }
  await a.goto(base+'/duo');await a.locator('#create').waitFor();
  await a.locator('#create [name=mode]').selectOption('training');
  await a.locator('#create [name=handle]').fill('alpha');
  await a.getByRole('button',{name:'创建实战房'}).click();
  const code=await a.locator('#invite-code').innerText();
  const joined=await post(contexts[1],base,'/api/rooms/join',{code,handle:'beta'}),id=joined.id;
  await b.goto(base+'/duo?room='+id);await b.locator('#recommendations').waitFor();
  await a.locator('[data-do=refresh]').click();await a.locator('#content:not([inert])').waitFor();
  const recommendations=(await roomData(contexts[0],base,id)).recommendations;
  assert.equal(recommendations.length,3);
  for(const r of recommendations)assert(!('rating'in r||'tags'in r||'problems'in r));
  await noOverflow(a);await screenshot(a,'B-room-recommendations');
  await a.setViewportSize({width:390,height:844});await noOverflow(a);await screenshot(a,'B-mobile-recommendations');
  await a.setViewportSize({width:1440,height:1100});
  await a.locator('[data-recommend-pick]').first().click();
  await a.locator('#ready-status').filter({hasText:'等待beta确认'}).waitFor();
  // Browse a known match for the hand-calculable score fixture.
  await a.locator('#contest-query').fill('1');await a.locator('#contest-search button').click();
  await a.locator('[data-pick="1"]').click();
  await b.locator('[data-do=refresh]').click();await b.locator('[data-do=ready]').click();
  await a.locator('[data-do=refresh]').click();await a.locator('[data-do=start]:not(:disabled)').click();
  await a.getByRole('heading',{name:'房间榜单'}).waitFor();
  await b.locator('[data-do=refresh]').click();await b.getByRole('heading',{name:'房间榜单'}).waitFor();
  await control(contexts[0],base,{submitted:true,advance:1900});
  // No browser calls /sync: the server worker must update both independent sessions.
  await Promise.all([waitForBoard(a),waitForBoard(b)]);
  assert.deepEqual(await tableOf(a),[['1','beta','1','30'],['2','alpha','1','40']]);
  assert.deepEqual(await tableOf(a),await tableOf(b));assert.equal(manualSync.length,0);
  await screenshot(a,'C-live-auto-sync');
  const before=await roomData(contexts[0],base,id);
  await control(contexts[0],base,{failure:true,advance:91});
  await Promise.all([a,b].map(p=>p.waitForFunction(()=>document.querySelectorAll('.members-panel .pill.warning').length===2&&document.querySelectorAll('.sync-error').length===2)));
  const offline=await roomData(contexts[0],base,id);
  assert.deepEqual(offline.standings,before.standings);
  assert.deepEqual(offline.members.map(m=>m.syncedAt),before.members.map(m=>m.syncedAt));
  await screenshot(a,'C-sync-delayed');
  await a.setViewportSize({width:390,height:844});await noOverflow(a);await screenshot(a,'C-mobile-delayed');
  await a.setViewportSize({width:1440,height:1100});
  await control(contexts[0],base,{failure:false,settle:id});
  await Promise.all([a,b].map(p=>p.locator('#room-report').waitFor()));
  const report=await (await contexts[0].request.get(base+'/api/rooms/'+id+'/report')).json();
  assert.deepEqual(report.participants.map(p=>[p.handle,p.rank,p.solved,p.penalty]),[['beta',1,1,30],['alpha',2,1,40]]);
  assert(report.summary.includes('beta以 30 比 40'));
  assert.deepEqual(report.participants.map(p=>p.firstAccepted.elapsedSeconds),[1800,1200]);
  assert.equal((await contexts[2].request.get(base+'/api/rooms/'+id+'/report')).status(),404);
  await c.goto(base+'/duo?room='+id);await c.locator('#create').waitFor();assert.equal(await c.locator('#room-report').count(),0);
  await noOverflow(a);await screenshot(a,'A-room-report','#room-report');
  await a.setViewportSize({width:390,height:844});await noOverflow(a);await screenshot(a,'A-mobile-report','#room-report');
  await a.setViewportSize({width:1440,height:1100});
  await a.emulateMedia({media:'print'});
  assert.equal(await a.locator('header').evaluate(el=>getComputedStyle(el).display),'none');
  assert.notEqual(await a.locator('#room-report').evaluate(el=>getComputedStyle(el).display),'none');
  await a.emulateMedia({media:'screen'});
  // Leave a second waiting room for the user to inspect recommendations separately.
  const waiting=await post(contexts[0],base,'/api/rooms',{mode:'replay',handle:'alpha',maxMinutes:120});
  await post(contexts[1],base,'/api/rooms/join',{code:waiting.inviteCode,handle:'beta'});
  assert.deepEqual(errors,[]);assert.equal(manualSync.length,0);
  const results={method:'Real Edge browser, independent sessions; synthetic CF transport and clock; isolated DB',
   reports:base+'/duo?room='+id,recommendations:base+'/duo?room='+waiting.id,
   ranking:await tableOf(a),recommendationCount:recommendations.length,automaticWithoutManualSync:true,
   outagePreservedScoresAndLastSuccess:true,automaticSettlement:true,outsiderReportStatus:404,
   desktopAnd390pxLayout:true,reportOnlyPrint:true,uncaughtPageErrors:errors,
   previewAccount:accounts[0],previewPassword:'Preview-12345'};
  if(output)fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify(results,null,2)+'\n');
  console.log(JSON.stringify(results,null,2));
 }finally{
  if(browser)await browser.close();
  if(child&&child.exitCode===null){const end=once(child,'exit');child.kill();await end;}
  const resolvedTmp=fs.realpathSync(tmp);
  assert.equal(path.dirname(resolvedTmp),fs.realpathSync(os.tmpdir()));
  assert(path.basename(resolvedTmp).startsWith('vplab-room-features-'));
  fs.rmSync(resolvedTmp,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
