'use strict';
const smartUI={data:null,seq:0,timer:null,busy:false,error:'',poolSyncedJob:null};
function resetSmart(){clearTimeout(smartUI.timer);smartUI.seq++;smartUI.data=null;smartUI.busy=false;smartUI.error='';smartUI.poolSyncedJob=null;}
function smartRunning(){return ['running','cancelling'].includes(smartUI.data?.job?.status);}
function smartSettings(){
 const d=smartUI.data;if(!d||!$('#smart-controls'))return;
 const p=d.settings.preferences;
 $('#smart-controls').innerHTML=`<section class="panel smart-preference"><span class="eyebrow">A PROBLEM FOR YOUR GOAL</span><h2>这次，你想练什么？</h2>
 <form id="smart-preference"><label for="smart-preference-text">用自己的话描述训练偏好</label><textarea id="smart-preference-text" name="preference" rows="3" maxlength="800" minlength="2" required placeholder="例如：我想做一些比较在意细节的题目">${e(d.job.preference||d.recommendation?.preference||'')}</textarea>
 <div class="smart-examples"><button type="button" class="text-button" data-smart-example="我想要做一些练习代码能力的题目">练习代码能力</button><button type="button" class="text-button" data-smart-example="我想要做数据结构和思维巧妙结合的题目">数据结构与巧思</button><button type="button" class="text-button" data-smart-example="我想做一些比较在意细节的题目">练习细节处理</button></div>
 <div class="form-actions"><button id="smart-generate" type="submit" class="primary">生成推荐</button><button id="smart-cancel" type="button" class="secondary" data-smart="cancel" hidden>停止推荐</button></div>
 <p class="help">按本次需求检索并核对原文；涉及题面内容时，搜索全部符合条件的可用题面。资料不全的题也可推荐；新增资料卡至少 80% 同时有题解和代码。使用 DeepSeek，每轮最多 9 次付费调用；直接复用结果不产生新调用。</p></form>
 <details class="smart-settings"><summary>候选范围 · 独立设置</summary><form id="smart-settings"><p class="help">CF 参考水平 ${d.settings.reference}${d.settings.ratingKnown?'':'（暂无 Rating，默认 1800）'}。默认范围为参考水平上下各 800。</p>
 <label>难度范围<select name="difficultyMode"><option value="default" ${p.difficultyMode==='default'?'selected':''}>默认，跟随 CF Rating</option><option value="manual" ${p.difficultyMode==='manual'?'selected':''}>手动范围</option></select></label>
 <div class="field-row"><label>手动下限<input name="minRating" type="number" min="0" max="5000" required value="${p.minRating}"></label><label>手动上限<input name="maxRating" type="number" min="0" max="5000" required value="${p.maxRating}"></label></div>
 <label class="inline-check"><input type="checkbox" name="includeAttempted" ${p.includeAttempted?'checked':''}>包含以前尝试过但未 AC 的题</label><button type="submit" class="secondary">保存范围</button></form></details>
 <div id="smart-notice" role="status"></div></section>`;
}
function drawSmartResult(){
 if(state.tab!=='intelligent'||!smartUI.data||!$('#smart-result'))return;
 const d=smartUI.data,j=d.job,r=d.recommendation,running=smartRunning();
 const materialBusy=intelligentUI.busy||intelligentUI.job?.status==='running';
 const btn=$('#smart-generate');if(btn){btn.disabled=running||smartUI.busy||materialBusy||!d.configured;btn.textContent=running?'正在为你选题…':'生成推荐';}
 const cancel=$('#smart-cancel');if(cancel){cancel.hidden=!running;cancel.disabled=j.status==='cancelling'||smartUI.busy;}
 $('#smart-settings')?.querySelectorAll('input,select,button').forEach(x=>x.disabled=running||smartUI.busy||materialBusy);
 const notice=$('#smart-notice');if(notice)notice.innerHTML=`${smartUI.error?`<p class="error" role="alert">${e(smartUI.error)}</p>`:''}${!d.configured?'<p class="notice neutral">先在“数据与账号”配置 DeepSeek 密钥。<button class="text-button" data-tab="settings">前往配置 →</button></p>':!intelligentUI.data?.retrievalReady&&!running?'<p class="help">可以直接生成推荐。下方资料池尚未备齐，不影响按需求检索其他题目。</p>':''}`;
 const statuses={running:'正在推荐',cancelling:'正在停止',completed:'已完成',cancelled:'已停止',failed:'未完成',interrupted:'服务已重启'};
 $('#smart-result').innerHTML=`${j.status!=='idle'?`<section class="panel smart-progress" role="status"><b>${statuses[j.status]||e(j.status)}</b><p>${e(j.message||'正在处理…')}</p>${j.cardsTotal?`<progress max="${j.cardsTotal}" value="${j.cardsReady||0}" aria-label="学习卡准备进度"></progress>`:''}<span class="help">本轮请求 ${j.calls||0} 次${j.totalTokens?` · ${j.usageComplete?'':'已返回部分用量 '}${j.totalTokens} tokens`:''}</span></section>`:''}
 ${r?`<section class="panel smart-choice"><div class="panel-head"><span class="eyebrow">AI PICK · #${r.id}</span><span class="help">${stamp(r.createdAt)}</span></div><p class="help">本次偏好：${e(r.preference)}</p>
 ${r.stale?'<p class="notice neutral">这是之前条件下的推荐，当前提交、设置或资料已变化。请整理资料后重新生成。</p>':''}
 ${r.searchedProblemCount!=null?`<p class="help">已检索 ${r.searchedProblemCount} 份符合当前范围的可用题面。</p>`:''}
 ${r.problem?`<h2>${e(r.problem.problemKey)} · ${e(r.problem.title)}</h2><div class="training-facts">${rating(r.problem.rating)}<span>题目 Rating</span></div><ul>${r.reasons.map(s=>`<li>${e(s)}</li>`).join('')}</ul><div class="form-actions"><a class="primary" href="${e(r.problem.url)}" target="_blank" rel="noopener">前往 CF 做题 ↗</a><button class="text-button" data-intelligent="material" data-key="${e(r.problemId)}">查看题面与资料 →</button></div>`:'<h2>本轮暂无合适推荐</h2>'}
 <p class="help">${e(r.message)}</p>${r.problem?`<p class="help">本题依据：基础信息${(r.materialKinds||[]).includes('statement')?'、题面':''}${(r.materialKinds||[]).includes('editorial')?'、题解':''}${r.referenceCodeAvailable?'、参考代码':''}。${r.outsideCache?'来自本轮扩展候选。':''}</p>`:''}${r.cardWarnings?`<p class="help">${r.cardWarnings} 张资料卡未通过检查，未用于补充结论。</p>`:''}<p class="help">${r.candidateCount} 道候选 → 审查 ${r.reviewedCount} 道 → ${r.qualifiedCount} 道明确匹配。新增 ${r.admittedCards||0} 张资料卡，结果已保存。</p></section>`:''}`;
}
async function loadSmart(){
 const seq=smartUI.seq;
 try{const d=await api('/api/intelligent/recommendation');if(seq!==smartUI.seq||!state.user||state.tab!=='intelligent')return;smartUI.data=d;smartSettings();drawSmartResult();if(smartRunning())smartUI.timer=setTimeout(pollSmart,2500);}
 catch(err){if(seq===smartUI.seq&&state.tab==='intelligent')$('#smart-controls').innerHTML=`<div class="panel"><p class="error">${e(err.message)}</p><button class="secondary" data-intelligent="reload">重新加载</button></div>`;}
}
async function pollSmart(){
 clearTimeout(smartUI.timer);const seq=smartUI.seq;
 try{const d=await api('/api/intelligent/recommendation');if(seq!==smartUI.seq||!state.user||state.tab!=='intelligent')return;smartUI.data=d;drawSmartResult();drawIntelligent();
  if(d.job.id&&!smartRunning()&&smartUI.poolSyncedJob!==d.job.id){
   const poolSeq=intelligentUI.seq,pool=await api('/api/intelligent/pool');
   if(seq!==smartUI.seq||poolSeq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;
   intelligentUI.data=pool;smartUI.poolSyncedJob=d.job.id;drawIntelligent();
  }
 }
 catch(err){if(seq===smartUI.seq&&state.tab==='intelligent'){smartUI.error=err.message;drawSmartResult();}}
 finally{if(seq===smartUI.seq&&state.user&&state.tab==='intelligent'&&smartRunning())smartUI.timer=setTimeout(pollSmart,3000);}
}
async function smartAction(action, data){
 if(smartUI.busy)return;const seq=smartUI.seq;smartUI.busy=true;smartUI.error='';drawSmartResult();
 try{
  const result=await api('/api/intelligent/'+action,data);
  if(seq!==smartUI.seq||!state.user||state.tab!=='intelligent')return;
  if(action==='settings'){
   smartUI.data.settings=result;
   await requestIntelligent('/api/intelligent/pool');
   if(seq!==smartUI.seq)return;
   const text=$('#smart-preference-text')?.value;smartSettings();if(text!==undefined)$('#smart-preference-text').value=text;
   smartUI.error='';
  }else smartUI.data=result;
 }catch(err){if(seq===smartUI.seq)smartUI.error=err.message;}
 finally{if(seq===smartUI.seq){smartUI.busy=false;drawSmartResult();drawIntelligent();pollSmart();}}
}
document.addEventListener('submit',ev=>{
 if(ev.target.id==='smart-preference'){ev.preventDefault();smartAction('recommendation',{preference:ev.target.elements.preference.value});}
 if(ev.target.id==='smart-settings'){ev.preventDefault();const f=ev.target.elements;smartAction('settings',{difficultyMode:f.difficultyMode.value,minRating:Number(f.minRating.value),maxRating:Number(f.maxRating.value),includeAttempted:f.includeAttempted.checked});}
});
document.addEventListener('click',ev=>{
 const sample=ev.target.closest('[data-smart-example]');if(sample&&$('#smart-preference-text'))$('#smart-preference-text').value=sample.dataset.smartExample;
 if(ev.target.closest('[data-smart="cancel"]'))smartAction('recommendation/cancel',{});
});
