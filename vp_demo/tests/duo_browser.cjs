// Optional browser acceptance test. Application runtime does not require Node or Playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require(process.env.VP_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname,'..'), tmp = fs.mkdtempSync(path.join(os.tmpdir(),'vplab-duo-'));
const db = path.join(tmp,'test.sqlite3');
const fullCatalog = process.argv[2] === '--catalog';
const evidenceArg = process.argv[fullCatalog?3:2];
const evidence = evidenceArg ? path.resolve(evidenceArg) : null;
let child, browser;
async function boot(){
 const env={...process.env,VPLAB_DB:db,PYTHONDONTWRITEBYTECODE:'1'};
 if(fullCatalog){
  if(process.env.VP_TEST_CATALOG)env.VPLAB_CONTESTS=path.resolve(process.env.VP_TEST_CATALOG);
  else delete env.VPLAB_CONTESTS;
  delete env.VPLAB_CF_ARCHIVE;
 }else env.VPLAB_CONTESTS=path.join(root,'data','contests.json');
 child=spawn(process.env.VP_TEST_PYTHON || 'python',['-u','server.py','--port','0'],{
  cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
 return new Promise((ok,fail)=>{
  let text='',err=''; const timeout=setTimeout(()=>fail(Error('Service startup timeout: '+err)),12000);
  child.stderr.on('data',s=>err+=s);child.on('error',e=>{clearTimeout(timeout);fail(e);});
  child.once('exit',c=>{clearTimeout(timeout);fail(Error('Service exited '+c+' '+err));});
  child.stdout.on('data',s=>{text+=s;const m=text.match(/VP Lab: (http:\/\/127\.0\.0\.1:\d+)/);
   if(m){clearTimeout(timeout);ok(m[1]);}});
 });
}
async function stop(){
 if(child&&child.exitCode===null&&child.signalCode===null){const end=once(child,'exit');child.kill();await end;}child=null;
}
async function auth(page,base,user,login=false,entry='/duo',after='#create'){
 await page.goto(base+entry);
 try{await page.locator('#auth-form').waitFor({state:'visible',timeout:16000});}
 catch{throw Error('Login startup: '+JSON.stringify(await page.evaluate(()=>({
  url:location.href,text:document.body.innerText.slice(0,500),state:document.readyState,
  js:typeof startApplication,scripts:Array.from(document.scripts).map(s=>s.src),
  resources:performance.getEntriesByType('resource').map(e=>[e.name,Math.round(e.duration)])
 }))));}
 const showingLogin=(await page.locator('#auth-form [name=password]').getAttribute('autocomplete'))==='current-password';
 if(showingLogin!==login)await page.locator('#auth-toggle').click();
 await page.locator('#auth-form [name=username]').fill(user);
 await page.locator('#auth-form [name=password]').fill('duo-synthetic-123');
 await page.locator('#auth-submit').click();
 await page.locator(after).waitFor();
}
async function refresh(page){await page.locator('[data-do=refresh]').click();await page.locator('#content:not([inert])').waitFor();}
async function createReplay(page){
 await page.getByRole('button',{name:'创建回放房'}).click();
 return page.locator('#invite-code').innerText();
}
async function joinRoom(page,code){
 await page.locator('#join [name=code]').fill(code);
 await page.locator('#join button').click(); await page.locator('#invite-code').waitFor();
}
async function confirmReady(page){
 const button=page.locator('[data-do=ready]');
 await button.click();await button.waitFor({state:'detached'});
}
async function finishReplay(host){
 await host.locator('[data-do=start]').click();await host.getByRole('heading',{name:'房间榜单'}).waitFor();
 await host.getByRole('button',{name:'推进至终场并结算'}).click();await host.getByRole('heading',{name:'我的赛后补题'}).waitFor();
}
const tableOf=page=>page.locator('tbody tr').evaluateAll(rs=>rs.map(r=>Array.from(r.cells).slice(0,4).map(c=>c.textContent)));
const queueOf=page=>page.locator('[data-task]').evaluateAll(bs=>bs.map(b=>b.dataset.task));
async function solo(page){
 await createReplay(page);await page.locator('[data-pick="1"]').click();
 await page.getByRole('button',{name:'单人开赛'}).waitFor();await finishReplay(page);
 const ranking=await tableOf(page),upsolves=await queueOf(page);
 assert.deepEqual(ranking,[['1','alpha','1','40']]);assert.deepEqual(upsolves,['B','C']);
 await page.locator('[data-do=lobby]').click();return {ranking,upsolves};
}
async function trio(a,b,c){
 const code=await createReplay(a);await joinRoom(b,code,'beta');
 await refresh(a);await a.locator('[data-pick="1"]').click();await refresh(b);await confirmReady(b);
 await joinRoom(c,code,'gamma');await refresh(a);
 await a.getByRole('heading',{name:'等待选场'}).waitFor();
 await a.locator('[data-pick="1"]').click();await refresh(b);await confirmReady(b);await refresh(a);
 assert.equal(await a.locator('[data-do=start]').isDisabled(),true);
 await refresh(c);await confirmReady(c);await refresh(a);await finishReplay(a);await refresh(c);
 const ranking=await tableOf(a),thirdUpsolves=await queueOf(c);
 assert.deepEqual(ranking,[['1','beta','1','30'],['1','gamma','1','30'],['3','alpha','1','40']]);
 assert.deepEqual(thirdUpsolves,['A','C']);
 if(evidence)await a.screenshot({path:path.join(evidence,'three-account-replay.png'),fullPage:true});
 return {ranking,thirdUpsolves,reselectedAfterJoin:true,allThreeConfirmed:true};
}
async function round(host,guest,alternate){
 const code=await createReplay(host);await joinRoom(guest,code,'beta');
 await refresh(host);await host.locator('[data-pick="1"]').click();
 await host.locator('[data-do=start]:disabled').waitFor();
 if(alternate){
  const saved=host.waitForResponse(r=>r.url().endsWith('/replay-file')&&r.status()===200);
  await host.locator('#event-file').setInputFiles(path.join(root,'data/replays/02-tie.json'));
  await saved;await host.locator('#content:not([inert])').waitFor();
  await confirmReady(host);
 }
 assert.equal(await host.locator('[data-do=start]').isDisabled(),true);
 await refresh(guest);await confirmReady(guest);await refresh(host);await finishReplay(host);
 await refresh(guest);await guest.getByRole('heading',{name:'我的赛后补题'}).waitFor();
 const table=await tableOf(host);
 const expected=alternate?[['1','alpha','1','40'],['1','beta','1','40']]:[['1','beta','1','30'],['2','alpha','1','40']];
 assert.deepEqual(table,expected);
 assert.deepEqual(await queueOf(host),['B','C']);
 const guestQueue=await queueOf(guest);
 assert.deepEqual(guestQueue,alternate?['A','C']:['B','C']);
 await guest.locator('[data-task]').first().click();
 await guest.getByRole('button',{name:'已完成 · 撤销'}).waitFor();
 await refresh(host);assert.equal(await host.getByRole('button',{name:'已完成 · 撤销'}).count(),0);
 if(evidence&&!alternate){fs.mkdirSync(evidence,{recursive:true});await host.screenshot({path:path.join(evidence,'two-account-replay.png'),fullPage:true});}
 return {table,guestQueue,id:new URL(host.url()).searchParams.get('room')};
}
(async()=>{
 try{
  let base=await boot();const localEdge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  browser=await chromium.launch({headless:true,executablePath:process.env.VP_BROWSER_EXE||(fs.existsSync(localEdge)?localEdge:undefined)});
  const contexts=await Promise.all([browser.newContext({viewport:{width:1230,height:880}}),browser.newContext({viewport:{width:1180,height:850}})]);
  const [a,b]=await Promise.all(contexts.map(c=>c.newPage())); const errors=[], assetErrors=[];
  const monitor=(page)=>{page.setDefaultTimeout(18000);page.on('pageerror',e=>{errors.push(e.message);console.error('Page error: '+e.message);});
   page.on('requestfailed',req=>{const reason=req.failure()?.errorText||'unknown';
    if(['script','stylesheet'].includes(req.resourceType())&&!reason.includes('ABORTED'))assetErrors.push([req.url(),reason]);
    console.error('Request failed: '+req.url()+' '+reason);});
   page.on('console',msg=>{if(msg.type()==='error')console.error('Browser: '+msg.text());});};
  monitor(a);monitor(b);
  if(fullCatalog){
   const run=require('./catalog_walkthrough.cjs');
   const result=await run({a,b,base,root,db,evidence,auth,createReplay,confirmReady,tableOf,queueOf,
    restart:async()=>{await stop();return boot();},browserVersion:browser.version(),errors,assetErrors});
   if(evidence)fs.writeFileSync(path.join(evidence,'browser-results.json'),JSON.stringify(result,null,2)+'\n');
   console.log('Full catalog / two browser accounts: PASS. '+result.steps.length+' checkpoints.');return;
  }
  await Promise.all([auth(a,base,'alpha'),auth(b,base,'beta')]);
  const onePerson=await solo(a);
  const one=await round(a,b,false);
  await Promise.all([a.locator('[data-do=lobby]').click(),b.locator('[data-do=lobby]').click()]);
  const two=await round(a,b,true);
  await Promise.all([a.locator('[data-do=lobby]').click(),b.locator('[data-do=lobby]').click()]);
  const c=await (await browser.newContext({viewport:{width:1180,height:850}})).newPage();monitor(c);
  await auth(c,base,'gamma');const threePeople=await trio(a,b,c);
  await stop();base=await boot();await auth(b,base,'beta',true);
  await b.goto(base+'/duo?room='+two.id);await b.getByRole('button',{name:'已完成 · 撤销'}).waitFor();
  assert.deepEqual(errors,[]);assert.deepEqual(assetErrors,[]);
  const result={method:'Headless Chromium; separate browser contexts; synthetic replay; no CF or LLM calls',
   solo:onePerson,standard:{ranking:one.table,guestUpsolves:one.guestQueue},changedEvents:{ranking:two.table,guestUpsolves:two.guestQueue},trio:threePeople,
   secondAccountMustConfirm:true,privateCompletionIsolated:true,completionSurvivedServerRestart:true,uncaughtPageErrors:errors,failedRequiredAssets:assetErrors};
  if(evidence)fs.writeFileSync(path.join(evidence,'browser-results.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));
 }finally{
  if(browser)await browser.close();await stop();
  for(const suffix of ['','-wal','-shm','-journal'])if(fs.existsSync(db+suffix))fs.unlinkSync(db+suffix);
  fs.rmdirSync(tmp);
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
