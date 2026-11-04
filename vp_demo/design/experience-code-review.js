'use strict';
// Design examples only. No model calls, network access or durable storage.
let codeProblem='rectangle',codeMode='diff',codeStep=0,codeSubmission='B';
let analysisScope='pair',journeyCase='complete',drawerOpen=false,evidenceSelected=false;
const analysisCache=new Set();
const problems={
 rectangle:{title:'矩形面积',statement:'输入两个不超过 10⁹ 的正整数 w、h，输出面积。w、h 声明为 int。',submissions:[{id:'A',verdict:'WA'},{id:'B',verdict:'AC'}],changes:[{before:'A',after:'B',title:'在乘法前扩大类型',old:'long long area = w * h;',next:'long long area = 1LL * w * h;',oldLine:18,newLine:18,fact:'新增 1LL，使乘法使用 long long，而不是先用两个 int 相乘。',hypothesis:'原表达式中间乘积可能溢出。赋值给 long long 不会改变右侧运算的类型；AC 也不是对全部根因的独立证明。',test:'候选测试：w = h = 50000，数学结果为 2500000000。尚未执行，不预测有符号溢出时的具体输出。'}]},
 range:{title:'多次区间求和',statement:'静态数组，回答 q 次闭区间 [l, r] 的元素和。示例版本依次修正边界、查询状态和复杂度。',submissions:[{id:'J1',verdict:'WA'},{id:'J2',verdict:'WA'},{id:'J3',verdict:'TLE'},{id:'J4',verdict:'AC'}],changes:[
 {before:'J1',after:'J2',title:'纳入右端点，仍然 WA',old:'for (int i = l; i < r; ++i) sum += a[i];',next:'for (int i = l; i <= r; ++i) sum += a[i];',oldLine:12,newLine:12,fact:'循环现在包含第 r 个元素，与闭区间语义一致。',hypothesis:'可能修正端点遗漏，但仍然 WA 不代表修改无效，也不能说明其他错误已修复。',test:'候选测试：l = r，期望等于 a[l]。尚未运行。'},
 {before:'J2',after:'J3',title:'每次查询重置状态，判题变为 TLE',old:'long long sum = 0;  // 查询循环外',next:'long long sum = 0;  // 每次查询开始处',oldLine:8,newLine:10,fact:'初始化从查询循环外移动到循环内，不再沿用上一条查询的累计值。',hypothesis:'WA → TLE 是判题变化，不能据此断言已经通过所有正确性检查。',test:'候选测试：连续两次查询相同区间，期望得到相同答案。尚未运行。'},
 {before:'J3',after:'J4',title:'改为前缀和，首次获得 AC',old:'for (int i = l; i <= r; ++i) sum += a[i];',next:'sum = pref[r] - pref[l - 1];',oldLine:12,newLine:15,fact:'J4 新增 pref[i] = pref[i - 1] + a[i] 预处理，以常数次运算回答查询。',hypothesis:'对静态数组，总复杂度从 O(nq) 降至 O(n + q)，可能解决重复扫描问题。AC 不自动验证此前每条原因推测。',test:'候选测试：首元素、末元素、整段查询，并检查 pref[0] = 0。尚未运行。'}]}
};
function visibleSubmissions(){return problems[codeProblem].submissions.filter(s=>journeyCase!=='no_ac'||s.verdict!=='AC');}
function currentPair(){if(codeMode==='diff')return problems[codeProblem].changes[codeStep];const i=problems[codeProblem].submissions.findIndex(s=>s.id===codeSubmission);return i>0?problems[codeProblem].changes[i-1]:null;}
function currentContext(){const p=currentPair();return analysisScope==='pair'?`${codeProblem}:pair:${p?.before||'none'}:${p?.after||codeSubmission}:${journeyCase}`:`${codeProblem}:first_ac:${journeyCase}`;}
function canAnalyze(){const p=currentPair();return analysisScope==='first_ac'?journeyCase==='complete':Boolean(p)&&!(journeyCase==='partial'&&[p.before,p.after].includes('J2'));}
function hasAnalysis(){return canAnalyze()&&analysisCache.has(currentContext());}
function selectAnalysis(scope){
 // A saved experience reopens its exact example and saved analysis, not a new request.
 analysisScope=scope==='first_ac'?'first_ac':'pair';codeProblem=analysisScope==='first_ac'?'range':'rectangle';codeMode='diff';codeStep=0;codeSubmission=codeProblem==='range'?'J2':'B';journeyCase='complete';drawerOpen=true;evidenceSelected=false;analysisCache.add(currentContext());navigate('code');
}
function resetCodeContext(problem){codeProblem=problem;codeStep=0;journeyCase='complete';codeSubmission=codeMode==='diff'?problems[problem].changes[0].after:problems[problem].submissions.at(-1).id;evidenceSelected=false;}
function renderCodeView(){
 const p=problems[codeProblem],pair=currentPair();
 $('#code-title').textContent='示例题 · '+p.title;$('#code-workspace').classList.toggle('with-analysis',drawerOpen);
 $('#code-controls').innerHTML=`<label>题目<select id="code-problem"><option value="rectangle" ${codeProblem==='rectangle'?'selected':''}>矩形面积</option><option value="range" ${codeProblem==='range'?'selected':''}>多次区间求和</option></select></label><div class="code-view-switch"><button class="filter ${codeMode==='single'?'active':''}" data-code-mode="single" aria-pressed="${codeMode==='single'}">单份源码</button><button class="filter ${codeMode==='diff'?'active':''}" data-code-mode="diff" aria-pressed="${codeMode==='diff'}">代码对比</button></div>${codeMode==='single'?`<label>提交版本<select id="code-submission">${visibleSubmissions().map(s=>`<option value="${s.id}" ${s.id===codeSubmission?'selected':''}>${s.id} · ${s.verdict}</option>`).join('')}</select></label>`:`<label>对比版本<select id="code-pair">${p.changes.filter(s=>journeyCase!=='no_ac'||s.after!=='J4').map((s,i)=>`<option value="${i}" ${i===codeStep?'selected':''}>${s.before} → ${s.after}</option>`).join('')}</select></label>`}<button id="open-code-ai" class="primary" data-open-ai aria-controls="ai-drawer" aria-expanded="${drawerOpen}">✧ ${codeMode==='diff'?'AI 分析这次修改':'AI 分析'}</button>`;
 $('#code-evidence').innerHTML=`<div class="source">${esc(p.statement)}</div><div class="submission-strip">${visibleSubmissions().map((s,i)=>`<button data-code-submission="${s.id}" class="submission-node ${codeSubmission===s.id?'active':''}"><b>${s.id}</b><span class="badge ${s.verdict==='AC'?'':'amber'}">${s.verdict}</span><small>${i===0?'首次提交':s.verdict==='AC'?'首次 AC':'中间尝试'}${journeyCase==='partial'&&s.id==='J2'?' · 源码缺失':''}</small></button>`).join('')}</div><section class="code-surface ${evidenceSelected?'evidence-selected':''}" id="visible-code" tabindex="-1"><div class="code-surface-head"><b>${codeMode==='diff'?`${pair.before} → ${pair.after} · 代码差异`:`${codeSubmission} · 源码`}</b><span>C++ · 示例片段</span></div>${codeHTML()}<p class="code-note">虚构示例的相关片段；正式分析使用所选版本的完整源码。${evidenceSelected?' 已定位分析引用的修改步骤。':''}</p></section><div class="code-bottom-note">分析中的“定位代码”会在这里打开对应版本。${codeProblem==='range'?' 完整记录中，首次 AC 范围为 J1 → J2 → J3 → J4。':''}</div>`;
 $('#ai-drawer').hidden=!drawerOpen;if(drawerOpen)renderAnalysisDrawer();
}
function codeHTML(){
 const p=currentPair(),changes=problems[codeProblem].changes;
 if(journeyCase==='partial'&&(codeMode==='single'?codeSubmission==='J2':[p.before,p.after].includes('J2')))return '<div class="empty">J2 源码缺失，暂时无法显示这个版本。<br>正式版提供同步缺失源码的入口。</div>';
 if(codeMode==='diff')return `<div class="diff visible-diff"><div class="minus">− ${p.oldLine}　${esc(p.old)}</div><div class="plus">+ ${p.newLine}　${esc(p.next)}</div></div>`;
 const lines=codeProblem==='rectangle'?[[6,'int w, h;'],[7,'cin >> w >> h;'],[18,codeSubmission==='A'?changes[0].old:changes[0].next],[19,'cout << area << "\\n";']]:codeSubmission==='J4'?[[6,'pref[i] = pref[i - 1] + a[i];'],[15,changes[2].next],[16,'cout << sum << "\\n";']]:[[codeSubmission==='J3'?10:8,codeSubmission==='J3'?changes[1].next:changes[1].old],[12,codeSubmission==='J1'?changes[0].old:changes[0].next],[13,'cout << sum << "\\n";']];
 return `<div class="source-lines">${lines.map(([n,line])=>`<div><span>${n}</span><code>${esc(line)}</code></div>`).join('')}</div>`;
}
function renderAnalysisDrawer(){
 const full=analysisScope==='first_ac',p=currentPair(),ready=hasAnalysis();
 const range=full?`${problems[codeProblem].submissions[0].id} → ${visibleSubmissions().at(-1).id}${journeyCase==='no_ac'?' · 尚未 AC':journeyCase==='partial'?' · 源码不完整':' · 首次 AC'}`:p?`${p.before} → ${p.after}`:'当前为首次提交，没有上一份版本';
 $('#ai-drawer').innerHTML=`<header class="drawer-heading"><div><span class="eyebrow">REVIEW BESIDE YOUR CODE</span><h2 id="drawer-title">AI 分析</h2></div><button class="close" data-close-ai aria-label="收起 AI 分析窗口">×</button></header><div class="drawer-modes" aria-label="分析范围"><button class="filter ${!full?'active':''}" data-drawer-scope="pair" aria-pressed="${!full}">两次提交</button><button class="filter ${full?'active':''}" data-drawer-scope="first_ac" aria-pressed="${full}">首次 AC 全过程</button></div><div class="analysis-range"><b>${esc(problems[codeProblem].title)}</b><span>${esc(range)}</span><small>${ready?'已有分析示例 · 直接读取，无需再次生成':'尚未分析 · 打开窗口不会发起模型调用'}</small></div>${full&&codeProblem==='range'?`<label class="scenario-switch">设计状态演示<select id="journey-case"><option value="complete" ${journeyCase==='complete'?'selected':''}>完整记录</option><option value="partial" ${journeyCase==='partial'?'selected':''}>J2 源码缺失</option><option value="no_ac" ${journeyCase==='no_ac'?'selected':''}>尚未 AC</option></select></label>`:''}<div id="analysis-result" aria-live="polite">${ready?resultHTML():analysisPreviewHTML()}</div><div class="drawer-footnote">设计稿：结果为预置示例，不连接 DeepSeek、不消耗额度。正式版仅在点击“开始分析”时调用模型。</div>`;
}
function analysisPreviewHTML(){
 if(!canAnalyze())return `<div class="note" style="margin-top:18px">${analysisScope==='pair'&&!currentPair()?'这是首次提交，没有上一份代码。可以选择其他版本或切换全过程范围。':journeyCase==='no_ac'?'尚未 AC，只能进行阶段复盘，不能显示首次 AC 结论。本原型暂不生成阶段分析。':'中间源码缺失，不能生成完整首次 AC 复盘。正式版可补齐源码或明确选择片段复盘。'}</div><button class="primary drawer-start" disabled>当前范围无法开始完整分析</button>`;
 return `<div class="analysis-preview"><h3>本次会分析什么？</h3><ul><li>${analysisScope==='pair'?'当前选中的两份源码与差异':'该题首次提交到首次 AC 的完整提交序列'}</li><li>本题题面、约束与判题记录</li><li>由你选择的相关个人经验</li></ul><p class="muted">不会发送 CF 凭证或无关题目的源码。这里仅演示分析窗口的交互。</p><button class="primary drawer-start" data-start-analysis>开始分析（演示）</button></div>`;
}
function resultHTML(){
 const full=analysisScope==='first_ac',problem=problems[codeProblem],pair=currentPair();
 const facts=(full?problem.changes:[pair]).map(c=>`<section class="drawer-section"><span class="badge">${c.before} → ${c.after}</span><h3>${esc(c.title)}</h3><p>${esc(c.fact)}</p><button class="text-btn" data-code-evidence="${problem.changes.indexOf(c)}">↖ ${c.before}:${c.oldLine} → ${c.after}:${c.newLine} · 定位代码</button><div class="note">${esc(c.hypothesis)}</div><p class="muted">${esc(c.test)}</p></section>`).join('');
 const candidateId=codeProblem==='rectangle'&&!full?2:codeProblem==='range'&&full?5:null;
 let candidate='';if(candidateId){const c=cards.find(c=>c.id===candidateId);candidate=`<section class="drawer-section"><span class="eyebrow">SAVE WHAT YOU LEARNED</span><h3>可提炼的经验</h3><div class="candidate"><b>${esc(c.title)}</b><p>${esc(c.rule)}</p><small>来源：${full?'本题完整提交链及上述各步骤':'当前两次提交'}</small><button class="primary" data-collect="${candidateId}">${c.deleted?'加入待确认（演示）':c.review==='pending'?'已在待确认 · 查看卡片':'已确认 · 查看卡片'}</button></div></section>`;}
 return `${full?`<section class="drawer-section"><h3>全过程概览</h3><p>${problem.submissions.map(s=>`${s.id}（${s.verdict}）`).join(' → ')}</p><p class="muted">首次 AC 后的提交不在范围内。不推测思考时间，不把判题变化当作原因证明。</p></section>`:''}${facts}${full&&codeProblem==='range'?'<section class="drawer-section"><h3>跨步骤总结</h3><p>边界、状态与复杂度需要分开核对。仍然 WA 的修改可能有价值，TLE 也不是正确性证明。</p></section>':''}${candidate}${!candidate?'<div class="notice">此组合未预设经验卡。正式版会从当前分析提取候选，不套用其他范围的经验。</div>':''}`;
}
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.analysisScope){selectAnalysis(b.dataset.analysisScope);return;}
 if(b.dataset.codeMode){if(b.dataset.codeMode==='single'&&codeMode==='diff')codeSubmission=currentPair().after;if(b.dataset.codeMode==='diff'&&codeMode==='single')codeStep=Math.max(0,problems[codeProblem].submissions.findIndex(s=>s.id===codeSubmission)-1);codeMode=b.dataset.codeMode;evidenceSelected=false;renderCodeView();return;}
 if(b.dataset.codeSubmission){codeSubmission=b.dataset.codeSubmission;codeMode='single';evidenceSelected=false;renderCodeView();return;}
 if(b.hasAttribute('data-open-ai')){drawerOpen=true;analysisScope='pair';renderCodeView();$('#ai-drawer').focus();return;}
 if(b.hasAttribute('data-close-ai')){drawerOpen=false;renderCodeView();$('#open-code-ai').focus();return;}
 if(b.dataset.drawerScope){analysisScope=b.dataset.drawerScope;renderCodeView();return;}
 if(b.hasAttribute('data-start-analysis')&&canAnalyze()){analysisCache.add(currentContext());renderCodeView();return;}
 if(b.dataset.codeEvidence!==undefined){codeStep=Number(b.dataset.codeEvidence);codeSubmission=problems[codeProblem].changes[codeStep].after;codeMode='diff';evidenceSelected=true;renderCodeView();$('#visible-code').focus();return;}
 if(b.dataset.collect){const c=cards.find(c=>c.id===Number(b.dataset.collect));if(!c.deleted){navigate(c.review==='pending'?'pending':'library');selected=c.id;render();return;}c.deleted=false;renderCodeView();notify('已加入待确认；代码与分析窗口仍保留在这里。');}
});
document.addEventListener('change',event=>{
 const t=event.target;
 if(t.id==='code-problem')resetCodeContext(t.value);
 else if(t.id==='code-submission'){codeSubmission=t.value;evidenceSelected=false;}
 else if(t.id==='code-pair'){codeStep=Number(t.value);codeSubmission=problems[codeProblem].changes[codeStep].after;evidenceSelected=false;}
 else if(t.id==='journey-case'){journeyCase=t.value;codeStep=0;codeSubmission=codeMode==='diff'?problems[codeProblem].changes[0].after:visibleSubmissions().at(-1).id;evidenceSelected=false;}
 else return;renderCodeView();
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&view==='code'&&drawerOpen&&!$('#editor').open){drawerOpen=false;renderCodeView();$('#open-code-ai').focus();}});
