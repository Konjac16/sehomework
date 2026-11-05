'use strict';
const statementRequests=new Map();
function originalProblemUrl(record){
 const match=String(record.problemKey||'').match(/^([1-9]\d*)([A-Za-z][A-Za-z0-9]*)$/);
 if(!match)return '';
 return `https://codeforces.com/${Number(match[1])>=100000?'gym':'contest'}/${match[1]}/problem/${match[2]}`;
}
function originalProblemLink(record){const url=originalProblemUrl(record);return url?`<a class="text-button" href="${e(url)}" target="_blank" rel="noopener">查看原题 ↗</a>`:'';}
function statementPanel(record,id,snapshot=null){
 return `<section class="problem-statement-panel" id="${id}" data-problem-key="${e(record.problemKey)}" ${snapshot?'data-snapshot="1"':''}><div class="panel-head"><b>${snapshot?'分析时的题面快照':'原题题面'}</b>${originalProblemLink(record)}</div><div class="statement-content">${snapshot?statementBody({...snapshot,status:snapshot.text?'ready':'unavailable',message:snapshot.text?'':'这次历史分析没有保存题面。'},record.problemKey,true):helperControls(record.problemKey)+'<p class="help">正在尝试直接获取题面，源码可继续查看…</p>'}</div></section>`;
}
function statementBody(s,key,saved=false){
 const source=({user:'手动补充',codeforces_browser:'CF 原题 · 浏览器采集',codeforces:'Codeforces 原题',cf_archive:'本地公开题库'})[s.textOrigin] || '尚未取得';
 return `${s.message?`<p class="help">${e(s.message)}</p>`:''}${s.text?`<p class="help">来源：${source}${s.fetchedAt?' · '+stamp(s.fetchedAt):''}${s.status==='stale'?' · 使用缓存版本':''}。${saved?'':'AI 分析默认使用这份题面。'}</p><details><summary>展开题意、约束与样例</summary>${renderStatementText(s.text)}</details>`:'<p class="help">当前没有题面，AI 只能分析代码；可打开原题，或在分析窗口手动补充。</p>'}${(s.warnings||[]).map(w=>`<p class="help">${e(w)}</p>`).join('')}${saved?'':`${helperControls(key)}<button class="text-button" ${s.textOrigin==='codeforces_browser'?'data-browser-statement':'data-refresh-statement'}="${e(key)}">${s.textOrigin==='codeforces_browser'?'重新采集题面':s.text?'刷新题面':'重新获取题面'}</button><span class="help">${s.textOrigin==='codeforces_browser'?' · 将重新打开原题':' · 同题直接请求至少间隔 60 秒'}</span>`}`;
}
async function ensureStatement(key,refresh=false){
 const epoch=reviewUI.epoch,requestKey=`${epoch}:${key}`;
 if(statementRequests.has(requestKey))return statementRequests.get(requestKey);
 const promise=(async()=>{
  try{return (await reviewApi('/api/problem/statement',{platform:'codeforces',problemKey:key,refresh})).statement;}
  catch(error){return {text:'',status:'unavailable',message:error.message};}
 })();
 statementRequests.set(requestKey,promise);
 try{return await promise;}finally{if(statementRequests.get(requestKey)===promise)statementRequests.delete(requestKey);}
}
async function loadStatementPanel(key,id,refresh=false){
 const epoch=reviewUI.epoch,host=$('#'+id);if(!host)return;
 const content=host.querySelector('.statement-content');if(!content)return;const request=Number(host.dataset.request||0)+1;host.dataset.request=String(request);
 const s=await ensureStatement(key,refresh);
 if(epoch!==reviewUI.epoch||$('#'+id)!==host||host.dataset.request!==String(request))return;
 content.innerHTML=statementBody(s,key);
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('button[data-refresh-statement]');if(!button)return;
 button.disabled=true;const key=button.dataset.refreshStatement,host=button.closest('.problem-statement-panel');
 await loadStatementPanel(key,host.id,true);
 if(reviewUI.open&&reviewUI.key===key&&!reviewUI.analysis){readReviewFields();await prepareReview();}
});
