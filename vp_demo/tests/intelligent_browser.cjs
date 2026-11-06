// Offline acceptance: real HTTP/UI with a temporary DB and explicitly synthetic contest data.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require(process.env.VP_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vplab-pool-'));
const db=path.join(tmp,'test.sqlite3'),pack=path.join(tmp,'fixture.zip');
const evidence=process.argv[2]?path.resolve(process.argv[2]):null;
let child,browser;
const raw=(id,verdict='WRONG_ANSWER',type='PRACTICE',at=150)=>({handle:'fixture_handle',submission:{
 id,contestId:1,creationTimeSeconds:at,verdict,problem:{contestId:1,index:'A1',name:'Fixture A1',rating:1800,tags:['hidden-algorithm']},
 author:{participantType:type,members:[{handle:'fixture_handle'}]},programmingLanguage:'C++',source:'private fixture source'}});
async function boot(){
 const fixture=spawnSync(process.env.VP_TEST_PYTHON||'python',['-c',
  'import sys;from pathlib import Path;sys.path.insert(0,"tests");from test_intelligent import write_archive,seed_catalog;write_archive(Path(sys.argv[1]),count=13);seed_catalog(Path(sys.argv[2]),Path(sys.argv[1]))',pack,db],
  {cwd:root,windowsHide:true,encoding:'utf8'});
 assert.equal(fixture.status,0,fixture.stderr);
 child=spawn(process.env.VP_TEST_PYTHON||'python',['-u','-c','import sys;sys.path.insert(0,"tests");import server;from test_smart_recommendation import FakeProvider,FakeRetriever;server.SMART.provider=FakeProvider();server.SMART.retriever=FakeRetriever();server.SMART.key_loader=lambda uid:"fixture-only";server.main()','--port','0'],{cwd:root,windowsHide:true,
  env:{...process.env,VPLAB_DB:db,VPLAB_INTELLIGENT_ARCHIVE:pack},stdio:['ignore','pipe','pipe']});
 return new Promise((resolve,reject)=>{
  let output='',errors='';const timer=setTimeout(()=>reject(Error('Startup timeout '+errors)),15000);
  child.on('error',e=>{clearTimeout(timer);reject(e);});
  child.stderr.on('data',s=>errors+=s);
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Exited '+code+' '+errors));});
  child.stdout.on('data',s=>{output+=s;const m=output.match(/VP Lab: (http:\/\/127\.0\.0\.1:\d+)/);
   if(m){clearTimeout(timer);resolve(m[1]);}});
 });
}
(async()=>{
 try{
  const base=await boot();
  browser=await chromium.launch({headless:true,executablePath:process.env.VP_BROWSER_EXE});
  const context=await browser.newContext({viewport:{width:1380,height:980}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  await page.goto(base);
  await page.locator('#auth-form [name=username]').fill('pool_browser_owner');
  await page.locator('#auth-form [name=password]').fill('pool-fixture-password');
  await page.locator('#auth-submit').click();await page.locator('#shell').waitFor({state:'visible'});
  const imported=await context.request.post(base+'/api/import',{data:{text:JSON.stringify([raw(1)])}});
  assert.equal(imported.status(),200);
  await context.request.post(base+'/api/intelligent/settings',{data:{includeAttempted:true}});
  await page.locator('[data-tab=intelligent]').first().click();
  assert.equal(await page.locator('#intelligent-base').getAttribute('open'),null);
  await page.locator('#intelligent-base > summary').click();
  await page.locator('[data-intelligent=prepare]').click();
  await page.getByRole('heading',{name:'基础资料池 · 13 道'}).waitFor();
  assert.equal(await page.locator('[data-metric=available]').innerText(),'13');
  assert.equal(await page.locator('[data-metric=statements]').innerText(),'13');
  assert.equal(await page.locator('[data-metric=cards]').innerText(),'0');
  assert.equal(await page.locator('.intelligent-table tbody tr').count(),10);
  assert(! (await page.locator('#intelligent-content').innerText()).includes('hidden-algorithm'));
  await page.locator('[data-intelligent=next]').click();assert.equal(await page.locator('.intelligent-table tbody tr').count(),3);
  await page.locator('[data-intelligent=prev]').click();
  const seeded=spawnSync(process.env.VP_TEST_PYTHON||'python',['-c',
    'import sys;sys.path.insert(0,"tests");from test_editorials import seed_public_editorials;seed_public_editorials(sys.argv[1])',db],
    {cwd:root,windowsHide:true,encoding:'utf8'});
  assert.equal(seeded.status,0,seeded.stderr);
  await page.locator('[data-intelligent=editorials]').click();
  await page.getByRole('status').filter({hasText:'题解准备：已完成'}).waitFor();
  assert((await page.locator('#intelligent-content [role=status]').innerText()).includes('已满足80%'));
  const preparedPool=await (await context.request.get(base+'/api/intelligent/pool')).json();
  assert.equal(preparedPool.materialCoverage.completeCount,13);
  assert.equal(preparedPool.retrievalReady,true);
  assert(!(await page.locator('#intelligent-content').innerText()).includes('invariant'));
  await page.locator('#smart-preference-text').fill('我想练习细节处理');
  await page.locator('#smart-generate').click();
  await page.locator('.smart-choice').waitFor();
  const saved=await (await context.request.get(base+'/api/intelligent/recommendation')).json();
  assert.equal(saved.job.status,'completed');assert(saved.recommendation.problem);
  await page.waitForFunction(n=>document.querySelector('[data-metric=cards]')?.textContent===String(n),saved.recommendation.admittedCards);
  assert(!(await page.locator('#smart-result').innerText()).includes('Synthetic hidden solution'));
  assert(!(await page.locator('#smart-result').innerText()).includes('hidden-algorithm'));
  await page.goto(base+'/?tab=intelligent');
  await page.locator('.smart-choice').waitFor();
  assert.equal(await page.locator('#intelligent-base').getAttribute('open'),null);
  assert.equal((await (await context.request.get(base+'/api/intelligent/recommendation')).json()).recommendation.id,saved.recommendation.id);
  await page.locator('#smart-generate').click();
  await page.getByText('已复用相同条件的推荐，无新增模型调用。',{exact:true}).waitFor();
  assert.equal((await (await context.request.get(base+'/api/intelligent/recommendation')).json()).job.calls,0);

  if(evidence){fs.mkdirSync(evidence,{recursive:true});await page.screenshot({path:path.join(evidence,'pool-desktop.png'),fullPage:true});}
  await page.locator('#intelligent-base > summary').click();
  await page.locator('.intelligent-table [data-intelligent=material]').first().click();
  await page.locator('#intelligent-material').waitFor({state:'visible'});
  assert((await page.locator('#intelligent-material-body').innerText()).includes('Describe an independent public problem'));
  await page.getByText('展开题解与附带代码（包含解法）',{exact:true}).click();
  assert((await page.locator('#intelligent-material-body').innerText()).includes('invariant'));
  await page.getByText('附带代码 1',{exact:true}).click();
  assert((await page.locator('#intelligent-material-body').innerText()).includes('<script>not HTML</script>'));
  assert.equal(await page.locator('#intelligent-material-body script').count(),0);
  await page.locator('#intelligent-material form button').click();
  await page.locator('#intelligent-material').waitFor({state:'hidden'});
  await page.locator('#intelligent-base > summary').click();
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(evidence)await page.screenshot({path:path.join(evidence,'pool-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1380,height:980});
  await page.locator('#intelligent-base > summary').click();
  await context.request.post(base+'/api/import',{data:{text:JSON.stringify([raw(2,'OK','PRACTICE',250)])}});
  await page.locator('[data-intelligent=prepare]').click();
  await page.getByRole('heading',{name:'基础资料池 · 12 道'}).waitFor();
  assert.equal(await page.locator('[data-metric=available]').innerText(),'12');
  assert.equal(await page.locator('.intelligent-table [data-key="cf:1:A1"]').count(),0);
  const other=await browser.newContext();
  assert.equal((await other.request.post(base+'/api/register',{data:{username:'pool_browser_other',password:'pool-fixture-password'}})).status(),200);
  assert.equal((await other.request.get(base+'/api/intelligent/material?id=cf:1:A1')).status(),404);
  assert.equal((await (await other.request.get(base+'/api/intelligent/pool')).json()).prepared,false);
  await page.locator('#open-duo').click();await page.locator('#create').waitFor();
  await page.locator('a.back').click();await page.locator('#shell').waitFor({state:'visible'});
  await page.locator('[data-tab=intelligent]').first().click();await page.locator('[data-metric=available]').waitFor();
  assert.equal(await page.locator('[data-metric=available]').innerText(),'12');
  await page.locator('#logout').click();await page.locator('#auth').waitFor({state:'visible'});
  assert.equal(await page.evaluate(()=>intelligentUI.data),null);
  assert.equal(await page.evaluate(()=>smartUI.data),null);
  assert.deepEqual(errors,[]);
  console.log('Pool browser PASS: prepare, pagination, cached statement, mobile, AC refresh, account isolation, VP navigation, logout.');
 }finally{
  if(browser)await browser.close();
  if(child&&child.exitCode===null&&child.signalCode===null){const done=once(child,'exit');child.kill();await done;}
  for(const file of [pack,db,db+'-wal',db+'-shm',db+'-journal'])if(fs.existsSync(file))fs.unlinkSync(file);
  fs.rmdirSync(tmp);
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
