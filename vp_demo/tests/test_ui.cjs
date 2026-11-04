// Template and asynchronous view tests, without driving a browser.
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const listeners = {};
function element(key) {
  if (!elements.has(key)) elements.set(key, {innerHTML:'',textContent:'',value:'',hidden:false,open:false,
    addEventListener(){},close(){this.open=false;},showModal(){this.open=true;},setAttribute(){},focus(){},scrollIntoView(){},classList:{toggle(){},remove(){},add(){}},querySelector(){return null;}});
  return elements.get(key);
}
const context = vm.createContext({console,URLSearchParams,setTimeout,clearTimeout,assert,
  location:{search:'',origin:'http://127.0.0.1:8765'}, window:{scrollTo(){},addEventListener(){}},
  document:{querySelector:element,querySelectorAll:()=>[],addEventListener(type,fn){(listeners[type]??=[]).push(fn);}}});
vm.runInContext(readFileSync(join(__dirname,'../static/helper.js'),'utf8'),context);
vm.runInContext(readFileSync(join(__dirname,'../static/statement-format.js'),'utf8'),context);
vm.runInContext(readFileSync(join(__dirname,'../static/statement.js'),'utf8'),context);
vm.runInContext(readFileSync(join(__dirname,'../static/review.js'),'utf8'),context);
vm.runInContext(readFileSync(join(__dirname,'../static/source.js'),'utf8'),context);
vm.runInContext(readFileSync(join(__dirname,'../static/training.js'),'utf8'),context);
const appScript=readFileSync(join(__dirname,'../static/app.js'),'utf8').replace(/startApplication\(\);\s*$/,'');
vm.runInContext(appScript,context);
async function run(){
  vm.runInContext(`const practiceProblem={platform:'codeforces',externalId:'1A',problemKey:'1A',title:'<unsafe>',rating:1800,tags:['secret-tag'],url:'https://codeforces.com/contest/1/problem/A'};
    assert(!trainingProblem(practiceProblem).includes('secret-tag'));
    assert.equal((trainingReason().match(/<option /g)||[]).length,5);
    assert(trainingProblem(practiceProblem).includes('&lt;unsafe&gt;'));
    const feedbackHtml=trainingFeedback({id:1,problem:practiceProblem,acId:'123'});
    assert.equal((feedbackHtml.match(/required/g)||[]).length,3);
    assert(!feedbackHtml.includes('data-training="skip"'));
    assert(!feedbackHtml.includes('secret-tag'));
    state.user={id:1};state.tab='overview';
    let finishTrainingRequest;api=()=>new Promise(resolve=>finishTrainingRequest=resolve);
    let pendingTrainingRequest=trainingRequest('/api/training');resetTraining();
    finishTrainingRequest({recommendation:practiceProblem,active:null,pending:[]});`,context);
  await vm.runInContext('pendingTrainingRequest',context);
  vm.runInContext('assert.equal(trainingUI.data,null);',context);
  vm.runInContext(`state.user={id:1,username:'tester'};state.data={settings:{},tags:[],tasks:[],sampleAvailable:false};
    assert(onlineSettings().includes('id="source-sync-form"'));
    assert(onlineSettings().indexOf('id="source-sync-form"')<onlineSettings().indexOf('id="import-form"'));
    assert(historyView().includes('history-results'));
    assert(compareView().includes('从一道题的提交开始'));
    const fixture={externalId:'1',problemKey:'1900A',title:'<script>bad()</script>',submittedAt:1,verdict:'WRONG_ANSWER',language:'C++',source:'x'};
    api=async()=>({records:[{...fixture,externalId:'2',submittedAt:2,verdict:'OK'},fixture]});`,context);
  await vm.runInContext(`openComparison('1900A','2')`,context);
  vm.runInContext(`assert.equal(sourceUI.comparison.before,'1');assert.equal(sourceUI.comparison.after,'2');
    assert(compareView().includes('&lt;script&gt;'));assert(!compareView().includes('<script>bad'));
    api=async()=>({identical:false,removed:1,added:1,before:fixture,after:{...fixture,externalId:'2'},hunks:[[{kind:'replace',oldLine:1,newLine:1,old:[['<script>',true]],new:[['safe',true]]}]]});`,context);
  await vm.runInContext('renderDiff()',context);
  assert(element('#diff-output').innerHTML.includes('<mark>&lt;script&gt;</mark>'));
  assert(element('#diff-output').innerHTML.includes('removed'));
  assert(element('#diff-output').innerHTML.includes('added'));
  vm.runInContext(`sourceUI.comparison.before='';`,context);
  await vm.runInContext('renderDiff()',context);
  assert(element('#diff-output').innerHTML.includes('需要两次提交'));
  vm.runInContext(`sourceUI.comparison.before='1';api=async()=>{throw new Error('<unsafe>');};`,context);
  await vm.runInContext('renderDiff()',context);
  assert(element('#diff-output').innerHTML.includes('&lt;unsafe&gt;'));
  vm.runInContext(`api=async()=>({records:[fixture]});`,context);
  await vm.runInContext(`openComparison('1900A','1')`,context);
  vm.runInContext(`assert.equal(sourceUI.comparison.before,'');resetSourceUI();assert.equal(sourceUI.comparison,null);assert.equal(state.detail.length,0);`,context);
  assert.equal(element('#detail').open,false);
  vm.runInContext(`api=async(path)=>path==='/api/health'?{app:'vplab',apiVersion:5}:{user:null};`,context);
  await vm.runInContext('startApplication()',context);
  assert.equal(element('#boot-status').hidden,true);
  assert.equal(element('#auth').hidden,false);
  vm.runInContext(`api=async()=>({app:'vplab',apiVersion:1});`,context);
  await vm.runInContext('startApplication()',context);
  assert.equal(element('#boot-status').hidden,false);
  assert(element('#boot-message').textContent.includes('版本不匹配'));
  const missingModule=vm.createContext({document:context.document,console,setTimeout,clearTimeout});
  vm.runInContext(appScript,missingModule);
  await vm.runInContext('startApplication()',missingModule);
  assert(element('#boot-message').textContent.includes('源码模块未加载'));
  assert.equal(element('#shell').hidden,true);
  // Opening the code-side review only previews; an explicit Start is required.
  vm.runInContext(`state.user={id:1};state.tab='compare';state.data={settings:{},tasks:[]};
    sourceUI.comparison={key:'1900A',before:'1',after:'2',rows:[fixture,{...fixture,externalId:'2',submittedAt:2,verdict:'OK'}],request:0};
    let calls=[];api=async(path)=>{calls.push(path);if(path==='/api/ai/config')return {configured:true,model:'test'};
    if(path.startsWith('/api/experiences'))return {rows:[]};
    if(path==='/api/problem/statement')return {statement:{text:'',status:'unavailable',message:'HTTP 403'}};
    if(path==='/api/analyses/preview')return {preview:{fingerprint:'abc',workflow:{type:'pure_ai_two_stage',callCount:2},problem:{textOrigin:'missing'},timeline:[],warnings:[],blockers:[],coverageState:'pair',inputCharacters:10}};
    throw new Error('Unexpected model request: '+path);};`, context);
  await vm.runInContext(`openReview('compare')`,context);
  vm.runInContext(`assert(!calls.includes('/api/analyses/create'));assert.equal(reviewUI.preview.fingerprint,'abc');`,context);
  assert(element('#review-side').innerHTML.includes('data-start-review'));
  assert(element('#review-side').innerHTML.includes('自动完成两轮'));
  vm.runInContext(`const savedWorkflow=reviewUI.preview.workflow;delete reviewUI.preview.workflow;drawReview();`,context);
  assert(element('#review-side').innerHTML.includes('服务更新待生效'));
  assert(/data-start-review\s+disabled/.test(element('#review-side').innerHTML));
  vm.runInContext(`reviewUI.preview.workflow=savedWorkflow;drawReview();`,context);
  vm.runInContext(`const running={id:8,status:'running',stage:'distill',context:{workflow:{type:'pure_ai_two_stage'}}};
    assert(reviewResult(running).includes('第 2 / 2 轮'));
    assert(reviewResult({...running,stage:'analyze'}).includes('第 1 / 2 轮'));`,context);
  vm.runInContext(`assert.equal(originalProblemUrl({problemKey:'1900A'}),'https://codeforces.com/contest/1900/problem/A');
    assert.equal(originalProblemUrl({problemKey:'100001A1'}),'https://codeforces.com/gym/100001/problem/A1');
    assert.equal(originalProblemUrl({problemKey:'javascript:alert(1)'}),'');
    assert(compareView().includes('查看原题'));
    assert(statementBody({text:'<script>alert(1)</script>',textOrigin:'codeforces'},'1900A').includes('&lt;script&gt;'));
    assert(!statementBody({text:'<script>alert(1)</script>'},'1900A').includes('<script>'));`,context);
  vm.runInContext(`const unsafe={id:3,status:'succeeded',model:'test',completedAt:1,result:{summary:'<script>alert(1)</script>',steps:[],limitations:[],candidates:[{key:'0',title:'<img>',rule:'rule',tags:[],evidenceValid:false}]}};
    const html=reviewResult(unsafe);assert(html.includes('&lt;script&gt;'));assert(!html.includes('<script>'));
    assert(html.includes('data-candidate="0" disabled'));closeReview();assert.equal(reviewUI.open,false);`,context);
  vm.runInContext(`const optional={...unsafe,context:{workflow:{type:'pure_ai_two_stage'}},usage:{complete:false},result:{...unsafe.result,
    steps:[{before:'1',after:'2',adjacent:true,observation:'change',hypothesis:'reason',test:'',evidence:[]}],
    candidates:[{...unsafe.result.candidates[0],incident:'<script>incident</script>',scenario:'<img>',checklist:['<svg>check</svg>']}]}};
    const optionalHtml=reviewResult(optional);assert(!optionalHtml.includes('可选自检建议'));
    assert(optionalHtml.includes('两轮用量未完整返回'));assert(optionalHtml.includes('&lt;svg&gt;'));
    assert(!optionalHtml.includes('<script>'));`,context);
  // Saved results compare their immutable snapshots, even if live records changed.
  vm.runInContext(`sourceUI.comparison.savedAnalysisId=12;
    api=async(path)=>{calls.push(path);return {identical:true,removed:0,added:0,hunks:[],before:fixture,after:{...fixture,externalId:'2'}};};`,context);
  await vm.runInContext('renderDiff()',context);
  vm.runInContext(`assert(calls.at(-1).startsWith('/api/analysis/diff?'));`,context);
  assert(element('#diff-output').innerHTML.includes('data-review-evidence="12"'));
  // A request that finishes after logout cannot return another user's data to a view.
  vm.runInContext(`let finishRequest;api=()=>new Promise(resolve=>finishRequest=resolve);let pendingRequest=reviewApi('/api/experience?id=1');resetReviewUI();finishRequest({card:{title:'private'}});`,context);
  await assert.rejects(vm.runInContext('pendingRequest',context),/账号已切换/);
  vm.runInContext(`assert.equal(libraryUI.selected,null);assert.equal(reviewUI.config,null);`,context);
  console.log('UI checks passed: source/diff views, AI preview without model calls, escaped results, snapshot comparison, logout isolation.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
