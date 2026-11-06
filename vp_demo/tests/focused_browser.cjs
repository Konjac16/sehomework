// Real HTTP/UI, temporary database and synthetic CF fixtures. No OJ or model calls.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require(process.env.VP_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vplab-focused-'));
const db=path.join(tmp,'test.sqlite3'),evidence=process.argv[2]?path.resolve(process.argv[2]):null;
let child,browser;
const fixture=`import sys,json,time
sys.path.insert(0,'tests')
import server
from test_training import problem
server.init_db()
with server.connect() as c:
 c.execute("INSERT INTO training_catalog VALUES('codeforces',?,?)",(json.dumps([problem('1900A',1800,['dp']),problem('1900B',1800,['graphs']),problem('1900C',1800,['math']),problem('1900D',2200,['strings'])]),int(time.time())))
server.ADAPTERS['codeforces'].sync=lambda handle:{'records':[],'scope':'Offline browser fixture','profile':None}
server.main()
`;
async function boot(){
 child=spawn(process.env.VP_TEST_PYTHON||'python',['-u','-c',fixture,'--port','0'],{cwd:root,windowsHide:true,
  env:{...process.env,VPLAB_DB:db,DEEPSEEK_API_KEY:''},stdio:['ignore','pipe','pipe']});
 return new Promise((resolve,reject)=>{
  let out='',err='';const timer=setTimeout(()=>reject(Error('Startup timeout '+err)),15000);
  child.on('error',reject);child.stderr.on('data',s=>err+=s);
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Server exit '+code+' '+err));});
  child.stdout.on('data',s=>{out+=s;const m=out.match(/VP Lab: (http:\/\/127\.0\.0\.1:\d+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
 });
}
function record(id,index,verdict){return {handle:'focused_fixture',submission:{id,contestId:1900,
 creationTimeSeconds:Math.floor(Date.now()/1000),verdict,problem:{contestId:1900,index,name:'Fixture '+index,rating:1800,tags:['dp']},
 author:{participantType:'PRACTICE',members:[{handle:'focused_fixture'}]},programmingLanguage:'C++'}};}
(async()=>{
 try{
  const base=await boot();browser=await chromium.launch({headless:true,executablePath:process.env.VP_BROWSER_EXE});
  const context=await browser.newContext({viewport:{width:1380,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
  await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await page.goto(base+'/?tab=training');
  await page.locator('#auth-form [name=username]').fill('focused_browser');
  await page.locator('#auth-form [name=password]').fill('focused-fixture-password');
  await page.locator('#auth-submit').click();await page.locator('#shell').waitFor({state:'visible'});
  assert.equal((await context.request.post(base+'/api/import',{data:{text:JSON.stringify([record(1,'Z','WRONG_ANSWER')])}})).status(),200);
  await page.goto(base+'/?tab=training');await page.locator('[data-training-mode=daily][aria-pressed=true]').waitFor();
  await page.locator('[data-training-mode=focused]').click();await page.getByText('先选择这次想练的方向',{exact:true}).waitFor();
  const check=t=>page.locator(`#training-settings input[name=directions][value="${t}"]`);
  await check('dp').check();await check('graphs').check();await check('math').check();
  assert(await check('strings').isDisabled());
  await check('math').uncheck();assert(!(await check('strings').isDisabled()));
  await check('graphs').uncheck();
  await page.locator('#training-settings input[value=manual]').check();
  await page.locator('#training-settings input[name=minRating]').fill('1700');
  await page.locator('#training-settings input[name=maxRating]').fill('1900');
  await page.locator('#training-settings button[type=submit]').click();
  await page.locator('[data-training=start]').waitFor();
  assert(!(await page.locator('#training-content').innerText()).includes('动态规划'));
  let saved=await (await context.request.get(base+'/api/training')).json();
  assert.equal(saved.mode,'focused');assert.deepEqual(saved.range,[1700,1900]);assert.equal(saved.recommendation.problemKey,'1900A');
  assert(!Object.hasOwn(saved.recommendation,'tags'));
  await page.locator('[data-training-mode=daily]').click();
  await page.locator('[data-training-mode=daily][aria-pressed=true]').waitFor();
  assert(await page.locator('#training-settings input[value=default]').isChecked());
  assert.deepEqual((await (await context.request.get(base+'/api/training')).json()).range,[1600,2000]);
  await page.locator('[data-training-mode=focused]').click();
  await page.locator('[data-training-mode=focused][aria-pressed=true]').waitFor();
  assert(await check('dp').isChecked());assert(await page.locator('#training-settings input[value=manual]').isChecked());
  await check('dp').uncheck();await check('strings').check();
  await page.locator('#training-settings button[type=submit]').click();
  await page.getByText('当前条件下暂无可推荐题目',{exact:true}).waitFor();
  await check('strings').uncheck();await check('dp').check();
  await page.locator('#training-settings button[type=submit]').click();await page.locator('[data-training=start]').click();
  await page.locator('[data-training=skip]').waitFor();
  const active=(await (await context.request.get(base+'/api/training')).json()).active;
  assert.equal(active.mode,'focused');
  await page.locator('[data-training-mode=daily]').click();
  await page.locator('[data-training-mode=daily][aria-pressed=true]').waitFor();
  assert((await page.locator('#training-content').innerText()).includes('本题来自专项训练'));
  assert.equal((await (await context.request.get(base+'/api/training')).json()).active.id,active.id);
  assert.equal(await page.locator('[data-training=start]').count(),0);
  await page.locator('[data-training-mode=focused]').click();await page.locator('[data-training-mode=focused][aria-pressed=true]').waitFor();
  await page.reload();await page.locator('[data-training-mode=focused][aria-pressed=true]').waitFor();
  assert.equal((await (await context.request.get(base+'/api/training')).json()).active.id,active.id);
  if(evidence){fs.mkdirSync(evidence,{recursive:true});await page.screenshot({path:path.join(evidence,'focused-desktop.png'),fullPage:true});}
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(evidence)await page.screenshot({path:path.join(evidence,'focused-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1380,height:1000});
  await context.request.post(base+'/api/import',{data:{text:JSON.stringify([record(2,'A','OK')])}});
  await page.locator('[data-training-mode=daily]').click();
  await page.locator('.training-feedback').waitFor();
  assert((await page.locator('.training-feedback').innerText()).includes('本题归属专项训练'));
  await page.locator('.training-feedback input[value=independent]').check();
  await page.locator('.training-feedback button[type=submit]').click();
  await page.locator('.training-feedback').waitFor({state:'hidden'});
  const daily=await (await context.request.get(base+'/api/training?mode=daily')).json();
  const focused=await (await context.request.get(base+'/api/training?mode=focused')).json();
  assert.equal(daily.feedbackWindow,0);assert.equal(focused.feedbackWindow,1);
  assert.equal(focused.history.filter(r=>r.problem.problemKey==='1900A').length,1);
  const other=await browser.newContext();
  await other.request.post(base+'/api/register',{data:{username:'focused_other',password:'focused-fixture-password'}});
  const isolated=await (await other.request.get(base+'/api/training?mode=focused')).json();
  assert.equal(isolated.history.length,0);assert.deepEqual(isolated.preferences.directions,[]);
  await page.locator('#logout').click();await page.locator('#auth').waitFor({state:'visible'});
  assert.equal(await page.evaluate(()=>trainingUI.data),null);assert.deepEqual(errors,[]);
  console.log('Focused browser PASS: direction limits, independent settings, mode persistence, shared active, no spoilers, no scope expansion, AC attribution, mobile and isolation.');
 }finally{
  if(browser)await browser.close();
  if(child&&child.exitCode===null&&child.signalCode===null){const done=once(child,'exit');child.kill();await done;}
  for(const suffix of ['', '-wal','-shm','-journal'])if(fs.existsSync(db+suffix))fs.unlinkSync(db+suffix);
  fs.rmdirSync(tmp);
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
