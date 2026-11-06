'use strict';
const helperPending=new Map();
let helperJobs=new Map();
window.addEventListener('message',event=>{
 if(event.source!==window||event.origin!==location.origin||event.data?.channel!=='vplab-helper-response')return;
 const pending=helperPending.get(event.data.nonce);if(!pending)return;
 clearTimeout(pending.timer);helperPending.delete(event.data.nonce);pending.resolve(event.data);
});
function helperRequest(type,job){
 return new Promise((resolve,reject)=>{
  const nonce=crypto.randomUUID();const timer=setTimeout(()=>{helperPending.delete(nonce);reject(new Error('未检测到题面助手，请先安装扩展并刷新当前页面。'));},type==='PING'?2200:20000);
  helperPending.set(nonce,{resolve,reject,timer});window.postMessage({channel:'vplab-helper-request',nonce,type,job},location.origin);
 });
}
function resetHelper(){for(const j of helperJobs.values())clearTimeout(j.timer);helperJobs=new Map();for(const p of helperPending.values()){clearTimeout(p.timer);p.reject(new Error('账号已退出。'));}helperPending.clear();$('#helper-install')?.close();}
function helperControls(key){const j=helperJobs.get(key);return `<div class="helper-controls" data-helper-key="${e(key)}"><button class="primary small" data-browser-statement="${e(key)}" ${j?.status==='waiting'?'disabled':''}>浏览器获取题面 ↗</button><button class="text-button" data-helper-install>安装 / 检查题面助手</button><p class="help" data-helper-message>${e(j?.message||'自动打开 CF 原题，正常加载后回传；验证由你在浏览器完成。')}</p></div>`;}
function helperSettingsView(){return `<section class="panel"><div class="panel-head"><h3>浏览器题面助手</h3><span id="helper-connection" class="badge">尚未检查</span></div><p>当 CF 拒绝直接获取时，打开原题，通过浏览器助手将题意、公式与样例送回本地。</p><button class="primary small" data-helper-install>安装与使用说明</button><button class="text-button" data-helper-check>检查连接</button></section>`;}
async function checkHelper(){const el=$('#helper-install')?.open?$('#helper-install-connection'):$('#helper-connection');if(!el)return;el.textContent='正在检查…';try{const r=await helperRequest('PING');el.textContent=r.ok?'已连接 · '+r.version:r.error;}catch{el.textContent='未连接，请安装后刷新';}}
function installHelper(){
 $('#helper-install-content').innerHTML=`<div class="dialog-head"><h2>安装 VP Lab 题面助手</h2><button class="close-dialog" data-helper-close aria-label="关闭">×</button></div><ol class="helper-steps"><li><a href="/browser-helper.zip" class="text-button">下载题面助手 ZIP ↓</a>，右键“全部解压”。</li><li>Chrome 地址栏输入 <code>chrome://extensions</code>；Edge 输入 <code>edge://extensions</code>。</li><li>开启“开发者模式”，点击“加载已解压的扩展程序”，选择含 <code>manifest.json</code> 的文件夹。</li><li>在同一个浏览器刷新 VP Lab，点击代码旁的“浏览器获取题面”。</li><li>原题打开后，按需完成 CF 验证；出现题面后自动回传，无需复制粘贴。</li></ol><div class="notice neutral">助手只采集你主动指定的题目，不读取 CF Cookie、密码或提交代码。任务十分钟有效；题面图片目前只提示，不识别内容。</div><p class="help">也可直接加载项目里的 browser_helper 文件夹。安装后保留文件夹；扩展更新时点击扩展管理页的重新加载。</p><div id="helper-install-connection" class="badge">尚未检查</div><button class="secondary small" data-helper-check>检查连接</button>`;
 $('#helper-install').showModal();checkHelper();
}
function drawHelperJob(key){window.vpDocument.querySelectorAll('.helper-controls').forEach(host=>{if(host.dataset.helperKey===key)host.outerHTML=helperControls(key);});}
async function startBrowserStatement(key){
 const epoch=reviewUI.epoch;const connected=await helperRequest('PING');if(epoch!==reviewUI.epoch)return;if(!connected.ok)throw new Error(connected.error);
 const r=await reviewApi('/api/browser/create',{platform:'codeforces',problemKey:key});
 const {token,...publicJob}=r.job;clearTimeout(helperJobs.get(key)?.timer);helperJobs.set(key,publicJob);drawHelperJob(key);
 try{const opened=await helperRequest('START',{...publicJob,token});if(!opened.ok)throw new Error(opened.error);}
 catch(error){if(epoch!==reviewUI.epoch)return;helperJobs.set(key,{...publicJob,status:'failed',message:error.message});drawHelperJob(key);throw error;}
 if(epoch!==reviewUI.epoch)return;pollHelperJob(key,epoch);
}
function pollHelperJob(key,epoch){
 const job=helperJobs.get(key);if(!job||job.status!=='waiting'||epoch!==reviewUI.epoch)return;
 job.timer=setTimeout(async()=>{
  try{
   const r=await reviewApi('/api/browser/status?id='+encodeURIComponent(job.id));if(epoch!==reviewUI.epoch||helperJobs.get(key)!==job)return;
   Object.assign(job,r.job);drawHelperJob(key);
   if(job.status==='waiting')pollHelperJob(key,epoch);
   if(job.status==='succeeded'){
    statementRequests.delete(`${epoch}:${key}`);
    const targets=[...window.vpDocument.querySelectorAll('.problem-statement-panel')].filter(h=>h.dataset.problemKey===key&&!h.dataset.snapshot);
    await Promise.all(targets.map(h=>loadStatementPanel(key,h.id)));
    if(reviewUI.open&&reviewUI.key===key&&!reviewUI.analysis){readReviewFields();await prepareReview();}
    toast('题面已保存；后续 AI 分析会自动使用。');
   }
  }catch(error){if(epoch!==reviewUI.epoch)return;job.status='failed';job.message=error.message;drawHelperJob(key);}
 },1600);
}
window.vpDocument.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button||button.disabled)return;
 if(button.hasAttribute('data-helper-install'))installHelper();
 if(button.hasAttribute('data-helper-close'))$('#helper-install').close();
 if(button.hasAttribute('data-helper-check'))checkHelper();
 if(button.dataset.browserStatement){const epoch=reviewUI.epoch;button.disabled=true;try{await startBrowserStatement(button.dataset.browserStatement);}catch(error){if(epoch===reviewUI.epoch){toast(error.message);installHelper();}}finally{button.disabled=false;}}
});
