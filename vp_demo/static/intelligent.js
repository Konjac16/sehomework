'use strict';
const intelligentUI={data:null,busy:false,seq:0,page:1,error:'',job:null,timer:null,baseOpen:false};
function resetIntelligent(){clearTimeout(intelligentUI.timer);resetSmart();intelligentUI.job=null;intelligentUI.seq++;intelligentUI.data=null;intelligentUI.busy=false;intelligentUI.error='';intelligentUI.page=1;intelligentUI.baseOpen=false;$('#intelligent-material')?.close();}
function intelligentView(){return heading('YOUR NEXT CHALLENGE','智能推荐','按本次偏好阅读候选资料，寻找一道值得练习的题。')+'<div id="smart-controls"></div><div id="smart-result" aria-live="polite"></div>'+
 '<div id="intelligent-content" aria-live="polite"><div class="panel">正在读取候选池…</div></div>';}
function drawIntelligent(){
 if(state.tab!=='intelligent'||!intelligentUI.data)return;
 const d=intelligentUI.data,busy=intelligentUI.busy,job=intelligentUI.job,running=job?.status==='running',disabled=busy||running||smartRunning()?'disabled':'';
 const labels={accepted:'已有 AC',in_daily_training:'日常训练中',judging:'仍在评测',unrated:'难度待判断',outside_difficulty:'不在难度范围内',attempted:'以前尝试过',cooldown:'当天冷却中'};
 const rows=d.candidates||[],pages=Math.max(1,Math.ceil(rows.length/10));intelligentUI.page=Math.min(intelligentUI.page,pages);
 $('#intelligent-content').innerHTML=`<section class="panel intelligent-intro"><div><span class="eyebrow">可用题目与资料</span><h2>推荐范围与资料</h2><p>按难度和做题记录筛选；生成推荐时，再结合本次偏好寻找合适题目。</p></div><button class="text-button" data-intelligent="refresh-stats" ${busy?'disabled':''}>刷新统计</button></section>
 ${intelligentUI.error?`<p class="error" role="alert">${e(intelligentUI.error)}</p>`:''}
 ${d.needsRefresh?'<div class="notice">条件已更新，下方统计仍属于上次整理的范围。生成推荐时会自动更新，也可以展开基础资料池手动整理。</div>':''}
 ${d.overview?.archiveUnavailable?'<div class="notice">题面快照暂时无法读取，题面数量仅统计已缓存的资料；基础资料池仍可查看。</div>':''}
 ${d.prepared?`<div class="intelligent-metrics intelligent-overview"><article class="panel"><small>可选题目</small><b data-metric="available">${d.overview?.availableCount??'—'}</b><p>符合已整理范围，推荐不局限于基础池</p></article><article class="panel"><small>可检索题面</small><b data-metric="statements">${d.overview?.statementCount??'—'}</b><p>上述题目中已有可用题面原文的数量</p></article><article class="panel"><small>AI 资料卡</small><b data-metric="cards">${d.overview?.cardCount??'—'}</b><p>当前范围内已收录资料卡的题目，按需生成与复用</p></article></div>`:'<p class="help">尚未整理范围。可以直接生成推荐，或展开下方基础资料池进行准备。</p>'}
 <details id="intelligent-base" class="intelligent-base" ${intelligentUI.baseOpen?'open':''}><summary>基础资料池与维护 <span>${d.prepared?`${d.candidateCount} 道已准备`:'尚未整理'}${running?' · 资料准备中':''}</span></summary><div class="intelligent-base-body">
 <section class="panel intelligent-intro"><div><span class="eyebrow">预先准备的资料</span><h2>${d.prepared?`基础资料池 · ${d.candidateCount} 道`:'准备基础资料池'}</h2>
 <p>这里是预先准备的基础资料池；智能推荐可以按需求检索池外题目。整理资料不调用模型。</p></div><div class="form-actions"><button class="primary" data-intelligent="prepare" ${disabled}>${busy?'处理中…':d.prepared?'重新整理候选':'准备候选池'}</button><button class="secondary" data-intelligent="sync" ${disabled||(!d.handle?'disabled':'')}>同步 CF 并整理</button>${d.prepared?`<button class="secondary" data-intelligent="editorials" ${disabled}>补齐候选题解</button>`:''}</div></section>
 ${job&&job.status!=='idle'?`<div class="notice neutral" role="status">题解准备：${{running:'进行中',completed:'已完成',cancelled:'已停止',blocked:'暂时无法访问',failed:'已中断',insufficient:'资料仍不足'}[job.status]||e(job.status)} · ${job.processed||0}/${job.total||0}，新获取 ${job.acquired||0}，复用 ${job.cached||0}，部分资料 ${job.partial||0}，未取得 ${job.failed||0}，暂缓 ${job.deferred||0}。代码已取得 ${job.codeReady||0}，仅链接 ${job.linksOnly||0}。${job.current?`当前 ${e(job.current)}。`:''}${job.message?e(job.message):''}${running?`<button class="text-button" data-intelligent="cancel-editorials" ${busy?'disabled':''}>停止准备</button>`:''}</div>`:''}
 ${!d.handle?'<div class="notice neutral">绑定 CF 账号后，可以按你的真实记录排除已做题。<button class="text-button" data-tab="settings">前往绑定 →</button></div>':''}
 ${d.prepared?`<div class="intelligent-metrics"><article class="panel"><small>基础资料池</small><b>${d.candidateCount} <span>/ ${d.capacity}</span></b><p>池外另有 ${d.reserveCount} 道题，可按需检索</p></article><article class="panel"><small>参考水平</small><b>${d.referenceRating}</b><p>采用 CF Rating；无 Rating 时参考 1800</p></article><article class="panel"><small>基础池题面</small><b>${d.statementReady}</b><p>仅统计这 ${d.candidateCount} 道基础池题目</p></article><article class="panel"><small>基础池题解 / 代码</small><b>${d.editorialReady} / ${d.referenceCodeReady||0}</b><p>均为题目数量；另有 ${d.referenceCodeLinksOnly||0} 道仅取得代码链接，不计入代码数量</p></article></div>
 <section class="panel intelligent-quality"><h3>${d.retrievalReady?'资料覆盖已达标':'候选池仍在准备'}</h3><p>同时具备题解与代码：<strong>${d.materialCoverage?.completeCount||0} / ${d.materialCoverage?.total||0}</strong>。目标 ${d.materialCoverage?.targetCount||0} 题，至少 ${d.materialCoverage?.requiredCount||0} 题资料齐全（80%）。</p><p>${d.retrievalReady?'基础资料池覆盖已达标；推荐时仍会按需扩展。':d.needsRefresh?'训练条件已变化，请重新整理候选。':`还需 ${d.materialCoverage?.shortfall||0} 题同时具备题解与代码${d.materialCoverage?.candidateShortfall?`，另有 ${d.materialCoverage.candidateShortfall} 个候选空位`:''}。不影响上方生成推荐。批量准备基础池时，完整资料先入池；缺资料最多保留 ${d.incompleteLimit??20} 道，超过就跳过，继续读下一道。`}</p></section>
 <section class="panel"><div class="panel-head"><div><h3>基础池题目</h3><p>只列出预先准备的基础池；当前顺序不代表推荐排名。</p></div><span class="badge">共 ${d.candidateCount} 道</span></div>
 ${rows.length?`<div class="table-wrap"><table class="intelligent-table"><thead><tr><th>题目</th><th>来源比赛</th><th>难度</th><th>资料</th><th></th></tr></thead><tbody>${rows.slice((intelligentUI.page-1)*10,intelligentUI.page*10).map(item=>`<tr><td><a href="${e(item.problem.url)}" target="_blank" rel="noopener">${e(item.problem.problemKey)} · ${e(item.problem.title)}</a><div class="help">${{easy:'轻松',normal:'正常',challenge:'挑战',manual:'人工区间'}[item.level]||'候选'}</div></td><td>${e(item.sources[0].name)}</td><td>${rating(item.problem.rating)}</td><td><span class="badge ${item.materials.statement==='ready'?'':'gray'}">${item.materials.statement==='ready'?'题面已缓存':'题面待补充'}</span><div class="help">${item.materials.editorial==='ready'?'题解已缓存':item.materials.editorial==='partial'?'题解正文待补充':item.materials.editorial==='failed'?'题解暂未取得':'题解待补充'}</div><div class="help">${item.materials.referenceCode==='ready'?'代码已缓存':item.materials.referenceCode==='links_only'?'已找到代码链接':'代码待补充'}</div></td><td><button class="text-button" data-intelligent="material" data-key="${e(item.id)}" ${disabled|| (item.materials.statement==='ready'||item.materials.editorial!=='missing'?'':'disabled')}>查看资料 →</button></td></tr>`).join('')}</tbody></table></div><div class="table-bottom"><span>第 ${intelligentUI.page} / ${pages} 页</span><div><button class="secondary small" data-intelligent="prev" ${disabled||(intelligentUI.page===1?'disabled':'')}>上一页</button> <button class="secondary small" data-intelligent="next" ${disabled||(intelligentUI.page===pages?'disabled':'')}>下一页</button></div></div>`:d.reserveCount?empty('尚未筛入候选','点击“补齐候选题解”，逐题检查储备资料并入池。'):empty('暂时没有合适候选','可以先同步 CF；不会用过难、过易或已通过题目凑满题池。')}
 </section><section class="panel intelligent-coverage"><h3>覆盖与待补资料</h3><p>候选来自 CF 题库，不要求参加过来源比赛。已做题判断范围：${e(d.historyScope)}；同步：${stamp(d.lastSync)}。</p>
 <p>当前难度范围 ${d.range[0]}～${d.range[1]}；${d.preferences.includeAttempted?'包含以前尝试过的题':'排除以前尝试过的题'}。采用本模块的独立难度设置。可在页面上方修改。</p>
 <div class="intelligent-counts">${Object.entries(d.excluded).filter(([,n])=>n).map(([k,n])=>`<span>${e(labels[k]||k)}：${n} 题</span>`).join('')}</div>
 <p class="help">候选范围：${d.catalogScope==='cf_problemset'?'CF 题库接口及其缓存':'本地题库快照（在线题库暂不可用）'}。题面快照 ${e(d.catalogSnapshot||'未知')}。逐题检查资料后入池：题解和代码齐全的直接接收，缺资料的最多保留20%；超出名额就跳过，继续检查储备，直到填满。已有资料保留，标签与解法不在选题阶段显示。</p></section>`:''}</div></details>`;
}
async function requestIntelligent(path,data){
 if(intelligentUI.busy)return;const seq=++intelligentUI.seq;intelligentUI.busy=true;drawIntelligent();
 try{const result=await api(path,data);if(seq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;intelligentUI.data=result;intelligentUI.error='';}
 catch(err){if(seq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;intelligentUI.error=err.message;
  if(!intelligentUI.data)$('#intelligent-content').innerHTML=`<div class="panel"><p class="error">${e(err.message)}</p><button class="secondary" data-intelligent="reload">重试</button></div>`;}
 finally{if(seq===intelligentUI.seq){intelligentUI.busy=false;drawIntelligent();drawSmartResult();}}
}
async function loadIntelligent(){resetIntelligent();await requestIntelligent('/api/intelligent/pool');await loadSmart();if(state.user&&state.tab==='intelligent')pollIntelligentEditorials();}
async function pollIntelligentEditorials(){
 clearTimeout(intelligentUI.timer);const seq=intelligentUI.seq;
 try{
  const [status,pool]=await Promise.all([api('/api/intelligent/editorials'),api('/api/intelligent/pool')]);
  if(seq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;
  intelligentUI.job=status.job;intelligentUI.data=pool;drawIntelligent();drawSmartResult();
  if(status.job.status==='running')intelligentUI.timer=setTimeout(pollIntelligentEditorials,3000);
 }catch(err){if(seq===intelligentUI.seq&&state.user&&state.tab==='intelligent'){intelligentUI.error=err.message;drawIntelligent();}}
}
async function controlIntelligentEditorials(cancel=false){
 if(intelligentUI.busy)return;const seq=intelligentUI.seq;intelligentUI.busy=true;drawIntelligent();
 try{const d=await api('/api/intelligent/editorials'+(cancel?'/cancel':''),{});
  if(seq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;
  intelligentUI.job=d.job;intelligentUI.error='';
 }catch(err){if(seq===intelligentUI.seq)intelligentUI.error=err.message;}
 finally{if(seq===intelligentUI.seq){intelligentUI.busy=false;drawIntelligent();drawSmartResult();if(state.user&&state.tab==='intelligent')pollIntelligentEditorials();}}
}
async function openIntelligentMaterial(key){
 const seq=intelligentUI.seq;
 try{const d=await api('/api/intelligent/material?id='+encodeURIComponent(key));if(seq!==intelligentUI.seq||!state.user||state.tab!=='intelligent')return;
  const m=d.material||{statement:{},warnings:[]},s=m.statement;
  const sections=[['题意',s.body],['输入格式',s.input_format],['输出格式',s.output_format],['交互说明',s.interaction_format],['备注',s.note]];
  $('#intelligent-material-body').innerHTML=`<h2>${e(d.problem.problemKey)} · ${e(d.problem.title)}</h2><p><a href="${e(d.problem.url)}" target="_blank" rel="noopener">查看 CF 原题 ↗</a></p><p class="help">题面来源 ${e(m.bodyProvider||'未知')}。</p>${m.warnings.map(w=>`<p class="notice neutral">${e(w)}</p>`).join('')}${sections.filter(([,v])=>v).map(([label,value])=>`<h3>${label}</h3>${renderStatementText(value)}`).join('')}<h3>公开样例</h3>${(s.examples||[]).map(x=>`<div class="intelligent-sample"><div><b>输入</b><pre>${e(x.input)}</pre></div><div><b>输出</b><pre>${e(x.output)}</pre></div></div>`).join('')}`;
  const ed=d.editorial;
  $('#intelligent-material-body').insertAdjacentHTML('beforeend',ed?`<details class="panel"><summary>展开题解与附带代码（包含解法）</summary><p><a href="${e(ed.sourceUrl)}" target="_blank" rel="noopener">题解来源 ↗</a>${ed.pdfPages?` · PDF 第 ${ed.pdfPages.join('、')} 页`:''}</p>${(ed.warnings||[]).map(w=>`<p class="notice neutral">${e(w)}</p>`).join('')}${ed.text?renderStatementText(ed.text):'<p class="notice neutral">题解正文暂未取得，已找到的代码或链接保留在下方。</p>'}${(ed.codeBlocks||[]).map((c,i)=>`<details><summary>附带代码 ${i+1}</summary><p class="help">来源附带代码，未在本地评测。${c.sourceUrl?` <a href="${e(c.sourceUrl)}" target="_blank" rel="noopener">代码来源 ↗</a>`:""}</p><pre>${e(c.text)}</pre></details>`).join('')}${(ed.codeLinks||[]).length?`<h3>题解提供的代码链接</h3><ul>${ed.codeLinks.map(ref=>`<li><a href="${e(ref.url)}" target="_blank" rel="noopener">打开提交 ↗</a> · ${{ready:'源码已缓存',found:'已找到链接，源码待获取',access_restricted:'源码访问受限',unavailable:'暂未取得源码'}[ref.status]||'已找到链接'}${ref.message?`<p class="help">${e(ref.message)}</p>`:''}</li>`).join('')}</ul>`:''}</details>`:`<p class="notice neutral">${e(d.editorialAttempt?.message||'题解尚未准备，可返回候选池补齐题解。')}</p>`);
  $('#intelligent-material').showModal();
 }catch(err){if(seq===intelligentUI.seq&&state.user&&state.tab==='intelligent'){intelligentUI.error=err.message;drawIntelligent();}}
}
document.addEventListener('toggle',ev=>{if(ev.target.id==='intelligent-base'&&ev.target===$('#intelligent-base'))intelligentUI.baseOpen=ev.target.open;},true);
document.addEventListener('click',ev=>{
 const b=ev.target.closest('[data-intelligent]');if(!b||b.disabled||intelligentUI.busy)return;
 const action=b.dataset.intelligent;
 if(action==='refresh-stats'){requestIntelligent('/api/intelligent/pool');return;}
 if(action==='editorials'||action==='cancel-editorials'){controlIntelligentEditorials(action==='cancel-editorials');return;}
 if(action==='material'){openIntelligentMaterial(b.dataset.key);return;}
 if(action==='prev'||action==='next'){intelligentUI.page+=action==='next'?1:-1;drawIntelligent();return;}
 if(action==='reload'){loadIntelligent();return;}
 if(action==='prepare'||action==='sync')requestIntelligent('/api/intelligent/'+action,{});
});
