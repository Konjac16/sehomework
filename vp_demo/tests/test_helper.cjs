// Run our extension logic against Chrome API mocks; never drive a browser.
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const stored={},opened=[],deliveries=[];let onMessage,onRemoved;
const chrome={
 runtime:{onMessage:{addListener(fn){onMessage=fn;}}},
 storage:{session:{async set(obj){Object.assign(stored,obj);},async get(key){return {[key]:stored[key]};},async remove(key){delete stored[key];}}},
 tabs:{async create(options){opened.push(options);return {id:77};},async update(id,options){assert(stored['capture:'+id]);opened.push(options);},onRemoved:{addListener(fn){onRemoved=fn;}}}
};
const context=vm.createContext({chrome,URL,TextEncoder,AbortSignal,Date,
 fetch:async(url,options)=>{deliveries.push({url,options});return {ok:true,json:async()=>({job:{status:'succeeded'}})};}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser_helper/background.js'),'utf8'),context);
const send=(message,sender)=>new Promise(resolve=>onMessage(message,sender,resolve));
async function run(){
 const job={id:'example-job-12345678',token:'example-token-12345678901234567890',url:'https://codeforces.com/contest/1900/problem/A',expiresAt:Math.floor(Date.now()/1000)+600};
 const local={url:'http://127.0.0.1:8766/',frameId:0,tab:{id:1}};
 assert.equal((await send({type:'START',job},{...local,url:'https://example.com/'})).ok,false);
 assert.equal((await send({type:'START',job:{...job,url:'https://evil.example/'}},local)).ok,false);
 assert.equal((await send({type:'START',job},local)).ok,true);
 assert.equal(opened.at(-1).url,job.url+'?locale=en');
 const cf={url:job.url+'?locale=en',frameId:0,tab:{id:77}};
 const info=await send({type:'GET_JOB'},cf);assert.equal(info.ok,true);assert(!('token' in info));
 assert.equal((await send({type:'GET_JOB'},{...cf,tab:{id:88}})).inactive,true);
 assert.equal((await send({type:'CAPTURE',html:'bad'},{...cf,url:job.url+'1'})).ok,false);
 assert.equal(deliveries.length,0);
 assert.equal((await send({type:'CAPTURE',html:'<div class="problem-statement">fixture</div>',formulaWarning:true},cf)).ok,true);
 assert.equal(deliveries[0].url,'http://127.0.0.1:8766/api/browser/result');
 assert.equal(deliveries[0].options.headers['X-VPLab-Token'],job.token);
 assert.equal(deliveries[0].options.credentials,'omit');
 assert.equal(JSON.parse(deliveries[0].options.body).formulaWarning,true);
 assert.equal(stored['capture:77'],undefined);
 await send({type:'START',job},local);await onRemoved(77);
 assert.equal(JSON.parse(deliveries.at(-1).options.body).failure,true);

 // Exercise math-source and sample-line transformations using a small DOM double.
 let mathReplacement='',samples='',removed=false;
 const script={textContent:'x^2 + y',replaceWith(node){mathReplacement=node.textContent;}};
 const pre={querySelectorAll(){return [{textContent:'2'},{textContent:'3 4'},{textContent:''}];},set textContent(v){samples=v;}};
 const clone={outerHTML:'<div>serialized fixture</div>',querySelector(){return null;},querySelectorAll(selector){
  if(selector==='script[type^="math/tex"]')return [script];
  if(selector.startsWith('.MathJax,'))return [{remove(){removed=true;}}];
  if(selector==='pre')return [pre];return [];
 }};
 const extractContext=vm.createContext({document:{createTextNode(textContent){return {textContent};}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser_helper/extract.js'),'utf8'),extractContext);
 extractContext.root={cloneNode(){return clone;},querySelector(){return script;}};
 vm.runInContext('extractVPLabStatement(root)',extractContext);
 assert.equal(mathReplacement,' $$$x^2 + y$$$ ');assert.equal(samples,'2\n3 4\n');assert(removed);
 console.log('Browser helper checks passed: task-bound tabs, no token exposure to CF, local relay, cleanup, math and samples.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
