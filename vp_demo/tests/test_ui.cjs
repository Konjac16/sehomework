// Template and asynchronous view tests, without driving a browser.
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const listeners = {};
function element(key) {
  if (!elements.has(key)) elements.set(key, {innerHTML:'',textContent:'',value:'',hidden:false,open:false,
    addEventListener(){},close(){this.open=false;},showModal(){this.open=true;},classList:{toggle(){}},querySelector(){return null;}});
  return elements.get(key);
}
const context = vm.createContext({console,URLSearchParams,setTimeout,clearTimeout,assert,
  location:{search:''}, window:{scrollTo(){}},
  document:{querySelector:element,querySelectorAll:()=>[],addEventListener(type,fn){(listeners[type]??=[]).push(fn);}}});
vm.runInContext(readFileSync(join(__dirname,'../static/source.js'),'utf8'),context);
const appScript=readFileSync(join(__dirname,'../static/app.js'),'utf8').replace(/startApplication\(\);\s*$/,'');
vm.runInContext(appScript,context);
async function run(){
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
  vm.runInContext(`api=async(path)=>path==='/api/health'?{app:'vplab',apiVersion:2}:{user:null};`,context);
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
  console.log('UI template checks passed: sync entry, adjacent selection, diff escaping, empty/error states, logout cleanup.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
