// All workflow mutations use the visible UI. Two rejected HTTP requests additionally test server-side guards.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const catalogPack=root=>path.resolve(process.env.VP_TEST_CATALOG || path.join(root,'data','vp-ready.zip'));
const idle=p=>p.locator('#content:not([inert])').waitFor();
async function query(p,term){
 await p.locator('#contest-query').fill(term);await p.getByRole('button',{name:'搜索',exact:true}).click();await idle(p);
}
async function checkTrainingTask({a,b,base,root,db,confirmReady,shot}){
 for(const p of [a,b])await p.getByRole('button',{name:'返回房间列表'}).click();
 await a.locator('#create [name=mode]').selectOption('training');
 await a.locator('#create [name=handle]').fill('vp_fixture_a');
 await a.getByRole('button',{name:'创建实战房'}).click();
 const code=await a.locator('#invite-code').innerText();
 await b.locator('#join [name=code]').fill(code);await b.locator('#join button').click();
 const guestHandle=b.locator('#accept-invite [name=handle]');
 await guestHandle.waitFor();assert.equal(await guestHandle.evaluate(e=>e.required),true);
 await guestHandle.fill('vp_fixture_b');await b.locator('#accept-invite .primary').click();
 await a.bringToFront();await a.getByText('席位 2　vp_fixture_b',{exact:true}).waitFor();
 await query(a,'4');await a.locator('[data-pick="4"]').click();await a.locator('[data-do=start]:disabled').waitFor();
 await b.bringToFront();await confirmReady(b);await a.bringToFront();await a.locator('[data-do=start]:enabled').click();
 await a.getByRole('button',{name:'同步我的 CF 提交'}).waitFor();
 assert.equal(await a.locator('[data-advance]').count(),0);
 const id=new URL(a.url()).searchParams.get('room');
 const fixture=spawnSync(process.env.VP_TEST_PYTHON||'python',['-X','utf8',path.join(root,'tests','settle_browser_training.py'),db,id],{env:{...process.env,VPLAB_CONTESTS:catalogPack(root)},encoding:'utf8',windowsHide:true});
 assert.equal(fixture.status,0,fixture.stderr);
 await a.locator('#upsolves').waitFor();await b.bringToFront();await b.locator('#upsolves').waitFor();await a.bringToFront();
 await a.locator('#upsolves [data-read=B]').click();
 const form=a.locator('#finish-reading');await form.waitFor();
 assert.equal(await form.evaluate(f=>f.checkValidity()),false);
 await form.locator('select').selectOption('hint');
 const desktop=a.viewportSize();
 await a.setViewportSize({width:390,height:780});
 const footerInView=await form.evaluate(f=>{
  const button=f.querySelector('button').getBoundingClientRect(), dialog=document.querySelector('#reading').getBoundingClientRect();
  return button.left>=0 && button.right<=innerWidth && button.top>=dialog.top && button.bottom<=Math.min(innerHeight,dialog.bottom);
 });
 assert.equal(footerInView,true);
 await a.setViewportSize(desktop);
 await shot('08-training-task',a);
 await form.getByRole('button').click();await form.waitFor({state:'hidden'});
 const dashboards=await Promise.all([a,b].map(async p=>(await p.request.get(base+'/api/dashboard')).json()));
 const [host,guest]=dashboards.map(d=>d.tasks.find(t=>t.problem_key==='4B'));
 assert.ok(host&&guest);assert.notEqual(host.id,guest.id);
 assert.equal(Boolean(host.done),true);assert.equal(host.completion_mode,'hint');assert.equal(Boolean(guest.done),false);
 return {...JSON.parse(fixture.stdout),hostTask:'4B',hostCompletionMode:host.completion_mode,guestDone:Boolean(guest.done),footerVisibleAt390px:footerInView};
}
module.exports=async({a,b,base,root,db,evidence,auth,createReplay,confirmReady,tableOf,queueOf,
                      restart,browserVersion,errors,assetErrors})=>{
 const steps=[],http=[],external=[];
 const step=(id,observed)=>steps.push({id,at:new Date().toISOString(),observed});
 if(evidence)fs.mkdirSync(evidence,{recursive:true});
 const shot=async(name,page,selector)=>{if(!evidence)return;
  if(!selector)await page.evaluate(()=>window.scrollTo(0,0));
  await (selector?page.locator(selector):page).screenshot({path:path.join(evidence,name+'.png')});};
 const get=async(p,url)=>(await p.request.get(base+url)).json();
 for(const [who,p] of [['host',a],['guest',b]]){
  await p.context().route('**/*',route=>{
   const u=new URL(route.request().url());if(u.protocol.startsWith('http')&&u.hostname!=='127.0.0.1'){
    external.push(u.origin);return route.abort();
   }return route.continue();
  });
  p.on('response',r=>{const u=new URL(r.url());if(u.pathname.startsWith('/api/rooms')||['/api/register','/api/login'].includes(u.pathname)){
   // The invite code, session cookies, passwords, and request bodies are intentionally not recorded.
   if(u.searchParams.has('code'))u.searchParams.set('code','REDACTED');
   http.push({who,method:r.request().method(),path:u.pathname,query:u.search,status:r.status()});}});
 }
 const names=['alpha','beta'];
 await auth(a,base,names[0]);
 assert.equal(await a.locator('#create [name=handle]').isVisible(),false);
 const code=await createReplay(a);
 const id=new URL(a.url()).searchParams.get('room'), api='/api/rooms/'+id;
 await a.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:base});
 await a.bringToFront();await a.getByRole('button',{name:'复制邀请链接'}).click();
 await a.getByText('邀请链接已复制',{exact:false}).waitFor();
 const inviteUrl=new URL(await a.evaluate(()=>navigator.clipboard.readText()));
 assert.equal(inviteUrl.origin,base);assert.equal(inviteUrl.searchParams.get('join'),code);
 await auth(b,base,names[1],false,inviteUrl.pathname+inviteUrl.search,'#accept-invite');
 assert.equal(await b.locator('#accept-invite [name=handle]').count(),0);
 await shot('00-invite',b);
 const identities=await Promise.all([get(a,'/api/me'),get(b,'/api/me')]);
 assert.deepEqual(identities.map(x=>x.user.username),names);assert.notEqual(identities[0].user.id,identities[1].user.id);
 step('独立账号注册',identities.map(x=>x.user));
 const outsider=await b.request.get(base+api+'/statement?index=B');assert.equal(outsider.status(),404);
 await b.locator('#accept-invite').getByRole('button',{name:'加入房间'}).click();
 await b.locator('#invite-code').waitFor();
 await a.bringToFront();await a.getByText('席位 2　beta',{exact:true}).waitFor();
 step('邀请跨过注册页',{invitePath:inviteUrl.pathname,afterRegistration:'接受邀请',guestName:identities[1].user.username});
 const initial=await get(a,api);
 assert.equal(initial.candidatePage.total,1996);assert.equal(initial.candidatePage.eligible,1342);
 assert.equal(initial.members.length,2);assert.equal(initial.candidates.length,8);
 assert.equal(await a.locator('[data-contest]').count(),8);assert.deepEqual(initial.problems,[]);
 assert.ok(!/Creating Words|Maximum Multiple Sum|"tags"|"rating"/.test(JSON.stringify(initial)));
 const statusBefore=await a.request.get(base+api+'/statement?index=B');assert.equal(statusBefore.status(),409);
 const ids=p=>p.locator('[data-contest]').evaluateAll(rs=>rs.map(x=>Number(x.dataset.contest)));
 const page1=await ids(a);await shot('01-catalog',a);
 await a.getByRole('button',{name:'下一页'}).click();await idle(a);const page2=await ids(a);
 assert.equal(page2.length,8);assert.equal(page2.some(cid=>page1.includes(cid)),false);
 const all=[];
 for(let page=1;page<=initial.candidatePage.pages;page++){
  const r=await get(a,api+'?page='+page);assert.equal(r.candidatePage.page,page);
  all.push(...r.candidates.map(c=>c.contestId));
 }
 assert.equal(all.length,1996);assert.equal(new Set(all).size,1996);
 const idsSha256=crypto.createHash('sha256').update(JSON.stringify(all.sort((a,b)=>a-b))).digest('hex');
 const poolPath=catalogPack(root);
 const sourceCheck=JSON.parse(fs.readFileSync(path.join(path.dirname(poolPath),'vp-ready.manifest.json')));
 assert.equal(idsSha256,sourceCheck.idsSha256);assert.equal(hash(poolPath),sourceCheck.catalogSha256);
 step('房间赛库与分页',{...initial.candidatePage,first:page1,second:page2,uniqueAcrossAllPages:all.length,idsSha256,nonMemberStatus:outsider.status(),preStartStatementStatus:statusBefore.status()});
 await query(a,'Div. 4');const named=await get(a,api+'?q=Div.%204');
 assert.ok(named.candidatePage.matched>1&&named.candidates.every(c=>c.name.includes('Div. 4')));
 await query(a,'2274');assert.equal(await a.locator('[data-contest]').count(),0);
 const unready=await a.request.post(base+api+'/select',{data:{version:initial.version,contestId:2274}});assert.equal(unready.status(),409);
 await query(a,'#1985');
 assert.equal(await a.locator('[data-pick="1985"]').getAttribute('data-minutes'),'150');
 const rejected=await a.request.post(base+api+'/select',{data:{version:initial.version,contestId:1985}});assert.equal(rejected.status(),409);
 await shot('02-budget',a);step('赛名搜索与超时拦截',{namedMatches:named.candidatePage.matched,visible:await a.locator('[data-contest]').innerText(),directSelectStatus:rejected.status(),outOfPool2274:unready.status()});
 await a.locator('#contest-search [name=eligible]').check();await idle(a);
 assert.equal(await a.locator('[data-contest]').count(),0);await a.getByText('没有匹配结果。',{exact:false}).waitFor();
 await a.locator('#contest-search [name=eligible]').uncheck();await idle(a);
 await a.getByRole('button',{name:'用 150 分钟选场并确认'}).click();
 await a.locator('[data-do=start]:disabled').waitFor();
 assert.equal(await a.locator('h1').innerText(),'Codeforces Round 952 (Div. 4)');
 const fixture=path.join(root,'data','replays','1985-two-player.json');
 const selected=await get(a,api);assert.equal(selected.maxMinutes,150);
 assert.deepEqual(selected.members.map(m=>m.ready),[true,false]);
 const missingGuest=await a.request.post(base+api+'/start',{data:{version:selected.version}});assert.equal(missingGuest.status(),409);
 await b.bringToFront();await confirmReady(b);await a.bringToFront();await a.locator('[data-do=start]:enabled').waitFor();
 const extraSetup=http.filter(r=>r.method==='POST'&&(r.path.endsWith('/preferences')||r.path.endsWith('/replay-file')||
  (r.who==='host'&&r.path.endsWith('/ready'))));assert.deepEqual(extraSetup,[]);
 await shot('03-ready',a);step('两人确认开赛',{contest:1985,originalDurationMinutes:150,roomBudgetMinutes:selected.maxMinutes,afterOneSelection:selected.members.map(m=>[m.handle,m.ready]),startWithoutGuest:missingGuest.status(),extraSetupRequests:extraSetup.length,ready:(await get(a,api)).members.map(m=>[m.handle,m.ready]),builtInReplay:path.basename(fixture),sha256:hash(fixture)});
 await a.locator('[data-do=start]').click();await a.getByRole('heading',{name:'房间榜单'}).waitFor();
 await b.bringToFront();await b.getByRole('heading',{name:'房间榜单'}).waitFor();await a.bringToFront();
 const zero=await tableOf(a);assert.deepEqual(zero,[['1','alpha','0','0'],['1','beta','0','0']]);
 await a.getByRole('button',{name:'推进 5 分钟',exact:true}).click();await idle(a);
 const early=await get(a,api);assert.equal(early.elapsedSeconds,300);assert.equal(early.standings[0].cells.A.wrong,1);assert.equal(early.standings[0].penalty,0);
 await a.getByRole('button',{name:'推进至 30 分钟'}).click();await idle(a);await b.bringToFront();
 await b.waitForFunction(()=>document.querySelector('#clock')?.textContent.startsWith('00:30:00'));
 const expected=[['1','beta','1','30'],['2','alpha','1','40']];
 assert.deepEqual(await tableOf(a),expected);assert.deepEqual(await tableOf(b),expected);
 await a.bringToFront();await shot('04-board',a);step('从空榜到第30分钟',{start:zero,minute5:{wrongA:1,solved:early.standings[0].solved,penalty:early.standings[0].penalty},host:await tableOf(a),guest:await tableOf(b)});
 await a.getByRole('button',{name:'推进至终场并结算'}).click();await a.getByRole('heading',{name:'我的赛后补题'}).waitFor();
 await b.bringToFront();await b.getByRole('heading',{name:'我的赛后补题'}).waitFor();await a.bringToFront();
 const hq=await queueOf(a),gq=await queueOf(b);
 assert.deepEqual(hq,['B','C','D','E','F','G','H1','H2']);assert.deepEqual(gq,['A','B','C','E','F','G','H1','H2']);
 const end=await get(b,api);assert.equal(end.phase,'FINISHED');assert.equal(end.elapsedSeconds,9000);assert.deepEqual(await tableOf(b),expected);
 const beta=end.standings.find(r=>r.handle==='beta');assert.equal(beta.cells.A.acSeconds,null);
 step('终场与分别生成补题',{phase:end.phase,elapsed:end.elapsedSeconds,cutoffAC:beta.cells.A.acSeconds,hostUpsolves:hq,guestUpsolves:gq});
 await a.getByRole('button',{name:'Maximum Multiple Sum',exact:true}).click();await a.locator('dialog[open]').waitFor();
 assert.equal(await a.locator('#reading-title').innerText(),'B. Maximum Multiple Sum');
 const actual=(await a.locator('#read-body .sample-pair pre').allTextContents());assert.deepEqual(actual,['2\n3\n15','3\n2']);
 const result=spawnSync(process.env.VP_TEST_PYTHON||'python',[path.join(root,'tests','acceptance_1985b.py')],{input:actual[0],encoding:'utf8',windowsHide:true});
 assert.equal(result.status,0,result.stderr);const stdout=result.stdout.trim().replace(/\r\n/g,'\n');assert.equal(stdout,actual[1]);
 await a.locator('.sample-pair').scrollIntoViewIfNeeded();await shot('05-statement-sample',a);
 await a.getByRole('button',{name:'标记演示完成并返回清单'}).click();
 await a.locator('dialog[open]').waitFor({state:'hidden'});
 await a.locator('[data-task=B][data-done=false]').waitFor();assert.equal(await b.locator('[data-task=B]').getAttribute('data-done'),'true');
 await shot('06-host-upsolves',a,'section.box:has([data-task])');await shot('07-guest-upsolves',b,'section.box:has([data-task])');
 step('从补题页读真题并运行样例',{contest:1985,index:'B',input:actual[0],published:actual[1],actual:stdout,exitCode:result.status,hostMarked:true,guestMarked:false});
 base=await restart();await Promise.all([auth(a,base,names[0],true,'/duo?room='+id,'#upsolves'),auth(b,base,names[1],true,'/duo?room='+id,'#upsolves')]);
 assert.equal(await a.locator('[data-task=B]').getAttribute('data-done'),'false');
 assert.equal(await b.locator('[data-task=B]').getAttribute('data-done'),'true');
 assert.deepEqual(await tableOf(a),expected);assert.deepEqual(await tableOf(b),expected);
 for(const p of [a,b]){const d=await get(p,'/api/dashboard');assert.equal(d.stats.submissions,0);assert.equal(d.tasks.length,0);}
 step('重启、重登后复查',{hostB:await a.locator('[data-task=B]').innerText(),guestB:await b.locator('[data-task=B]').innerText(),bothBoards:expected,realSubmissions:[0,0],personalPlanCount:[0,0]});
 const trainingTask=await checkTrainingTask({a,b,base,root,db,confirmReady,shot});
 step('实战补题表单（受控结算）',trainingTask);
 assert.deepEqual(errors,[]);assert.deepEqual(assetErrors,[]);assert.deepEqual(external,[]);
 return {result:'PASS',browserVersion,verifiedAt:new Date().toISOString(),roomId:id,
  mode:'实际本地服务、两个独立浏览器会话、真实归档比赛；判题为合成回放，补题样例在本机运行，未向 CF 提交',
  catalogSha256:hash(poolPath),steps,http,uncaughtPageErrors:errors,failedAssets:assetErrors,externalBrowserRequests:external};
};
