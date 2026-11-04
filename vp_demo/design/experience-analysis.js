'use strict';
// Fabricated review scenarios for the design prototype; no model or network calls.
let analysisScope = 'pair';
let journeyCase = 'complete';
let journeyStep = 0;
const journeySubmissions = [
  {id:'J1', verdict:'WA', label:'第一次提交', change:'初始版本'},
  {id:'J2', verdict:'WA', label:'第二次提交', change:'修正右端点'},
  {id:'J3', verdict:'TLE', label:'第三次提交', change:'重置查询状态'},
  {id:'J4', verdict:'AC', label:'首次 AC', change:'改用前缀和'}
];
const journeyChanges = [
  {before:'J1',after:'J2',title:'修正区间右端点，但这道题仍然 WA',old:'for (int i = l; i < r; ++i) sum += a[i];',next:'for (int i = l; i <= r; ++i) sum += a[i];',fact:'循环开始包含第 r 个元素；闭区间 [l, r] 的边界与示例题意一致。',hypothesis:'这个改动可能消除了端点遗漏，但不能解释全部错误。两次结果都是 WA，也不意味着这次修改没有价值。',test:'检查 l = r 的单点查询。数学结果应等于 a[l]；该测试尚未执行。',oldLine:12,newLine:12},
  {before:'J2',after:'J3',title:'每次查询重新初始化累加器，判题变为 TLE',old:'long long sum = 0;  // 在所有查询之外初始化',next:'long long sum = 0;  // 移到每次查询开始时初始化',fact:'sum 的初始化位置从查询循环外移到循环内，避免沿用上一条查询的累计值。',hypothesis:'观察到 WA → TLE；这不是“已证明正确”。TLE 只说明该次提交在评测中超过时间限制。',test:'连续两次查询同一区间，结果应相同。还需实际运行验证；不能从 TLE 推断所有正确性问题都已修复。',oldLine:8,newLine:10},
  {before:'J3',after:'J4',title:'把逐段遍历改为前缀和查询，首次获得 AC',old:'for (int i = l; i <= r; ++i) sum += a[i];',next:'sum = pref[r] - pref[l - 1];',fact:'示例版本 J4 增加了前缀和预处理 pref[i] = pref[i - 1] + a[i]，并以常数次运算回答区间和。',hypothesis:'在本示例的静态数组场景中，总复杂度由 O(nq) 降至 O(n + q)，可能解决重复扫描的性能问题；AC 不自动证明前面所有推测。',test:'核对 pref[0] = 0，并测试首元素、末元素及整段查询。这里只展示候选测试，未执行源码。',oldLine:12,newLine:15}
];

function selectAnalysis(scope){
  analysisScope=scope==='first_ac'?'first_ac':'pair';
  if(analysisScope==='first_ac'){journeyCase='complete';journeyStep=0;}
  navigate('analysis');
}

function renderAnalysis(){
  const full=analysisScope==='first_ac';
  $('#analysis-title').textContent=full?'从第一次尝试，到第一次 AC。':'这次修改，值得留下什么？';
  $('#analysis-description').textContent=full?'回看完整修改链，再提取跨步骤的经验。示例不代表你的真实提交。':'聚焦同题两次提交，解释具体差异。全部分析文字均为设计示例。';
  $('#analysis-modes').innerHTML=[['pair','01','分析这次修改','选择两次提交，理解一次局部改动。'],['first_ac','02','复盘首次 AC 过程','第一次提交 → 首次 AC，保留中间每次尝试。']].map(([scope,n,title,desc])=>`<button class="review-mode ${analysisScope===scope?'active':''}" data-analysis-scope="${scope}" aria-pressed="${analysisScope===scope}"><span>${n}</span><div><strong>${title}</strong><small>${desc}</small></div><b>${analysisScope===scope?'✓':'↗'}</b></button>`).join('');
  $('#analysis-content').innerHTML=full?journeyAnalysisHTML():pairAnalysisHTML();
}

function providerNotice(){return '<div class="notice">首个候选模型：DeepSeek-V4.1-Flash · deepseek-flash<br>此处为静态设计示例，不含密钥、不发送请求。</div>';}
function pairAnalysisHTML(){return `<div class="steps"><span class="step">1　选中两次提交</span>→<span class="step">2　分析并核对</span>→<span class="step">3　选择候选经验</span>→<span>4　编辑后确认</span></div><div class="analysis-grid"><div><section class="panel"><span class="eyebrow">PAIR / LOCAL CHANGE</span><h2>示例题 · 矩形面积</h2><p class="muted">示例提交 A（WA） → 示例提交 B（AC） · C++</p><div class="source" style="margin-top:16px">示例题意：输入两个不超过 10⁹ 的正整数 w、h，输出面积。两个变量在代码中声明为 int。正式分析使用选中的两份完整源码；此处仅展示片段。</div><div class="diff"><div class="minus">− 18　long long area = w * h;</div><div class="plus">+ 18　long long area = <mark>1LL * </mark>w * h;</div></div><span class="badge">代码可直接核对</span><p style="margin-top:10px">乘法表达式新增了 <code>1LL</code>，使中间运算采用 long long。</p></section><section class="panel"><h2>先解释，再下结论。</h2><div class="fact"><span class="badge amber">可能原因 · 待验证</span><b>中间乘积可能超出 int 范围</b><p>赋值给 long long，并不会改变右侧两个 int 相乘时的计算类型。对于较大输入，原表达式可能发生有符号整数溢出。</p></div><div class="fact"><span class="badge amber">候选测试 · 尚未运行</span><b>w = 50000，h = 50000</b><p>数学结果为 2500000000，超过 32 位有符号整数上限。需要实际执行以记录行为，不能预言有符号溢出一定输出某个值。</p></div><div class="note">两次提交只解释这两个版本的差异；中间若还有提交，不在本次分析范围内。</div></section></div><div><section class="panel"><span class="eyebrow">ONE CHANGE, ONE LESSON</span><h2>单次修改经验</h2><div class="candidate"><span class="badge amber">AI 候选 · 示例</span><h3>先扩大操作数类型，再做乘法。</h3><p>适用于两个 int 的乘积可能超出范围时。先估算上界，再检查中间表达式类型。</p>${tagHTML(['C++','整数溢出','数据范围'])}<button class="primary" data-collect="2">加入待确认（演示）</button><p class="muted" style="font-size:12px">来源：提交 A → B，第 18 行。保留“单次修改复盘”来源类型。</p></div></section>${providerNotice()}</div></div>`;}

function journeyAnalysisHTML(){
  const complete=journeyCase==='complete',partial=journeyCase==='partial';
  const nodes=partial?journeySubmissions.filter(s=>s.id!=='J2'):journeySubmissions.slice(0,complete?4:3);
  const count=nodes.length;
  const label=complete?'首次 AC 全过程':partial?'片段复盘 · 过程不完整':'阶段复盘 · 尚未 AC';
  const explanation=complete?'本示例提供完整历史：从最早提交 J1 到首次 AC J4，共 4 次提交、3 次相邻修改。首次 AC 之后的提交不纳入。':partial?'示例中缺少 J2 的源码，不能还原 J1 → J2 → J3。可以对比 J1 与 J3，但必须标注“非相邻、跨越缺失版本”。':'当前仅观察到 J1～J3，没有 AC。本次只能总结已观察到的尝试，不能展示“首次 AC 结论”。';
  const active=complete?journeyChanges[journeyStep]:null;
  const timeline=nodes.map((s,i)=>`<div class="attempt ${s.verdict==='AC'?'accepted':''}"><span class="attempt-dot">${i+1}</span><strong>${s.id} <span class="badge ${s.verdict==='AC'?'':'amber'}">${s.verdict}</span></strong><small>${s.label}</small><p>${s.change}</p></div>`).join('');
  const stepPanel=active?`<div class="step-picker" aria-label="选择相邻修改">${journeyChanges.map((s,i)=>`<button class="filter ${i===journeyStep?'active':''}" data-journey-step="${i}" aria-pressed="${i===journeyStep}">${s.before} → ${s.after}</button>`).join('')}</div><h3>${esc(active.title)}</h3><div class="diff"><div class="minus">− ${active.oldLine}　${esc(active.old)}</div><div class="plus">+ ${active.newLine}　${esc(active.next)}</div></div><div class="fact"><span class="badge">可观察事实</span><p>${esc(active.fact)}</p></div><div class="fact"><span class="badge amber">解释与限制</span><p>${esc(active.hypothesis)}</p></div><div class="note">${esc(active.test)}</div>`:`<div class="note">${explanation}</div><p class="muted" style="margin-top:16px">原型此状态不生成全过程经验。正式版本允许用户明确选择片段或阶段复盘，但输出和经验来源必须保留该范围标记。</p>`;
  return `<div class="panel"><div class="journey-header"><div><span class="eyebrow">JOURNEY / FIRST ACCEPTED</span><h2>示例题 · 多次区间求和</h2><p class="muted">静态数组，回答 q 次闭区间 [l, r] 的元素和。</p></div><label class="scenario-switch">查看数据状态示例<select id="journey-case"><option value="complete" ${complete?'selected':''}>完整历史，已首次 AC</option><option value="partial" ${partial?'selected':''}>中间源码缺失</option><option value="no_ac" ${journeyCase==='no_ac'?'selected':''}>尚未 AC</option></select></label></div><div class="journey-scope"><span class="badge ${complete?'':'amber'}">${label}</span><span>${count} 份可见代码 · ${complete?'终点：J4 首次 AC':partial?'缺失：J2':'终点：当前 J3'}</span></div><p class="muted">${explanation}</p><div class="attempts">${timeline}</div></div><div class="analysis-grid"><div><section class="panel"><span class="eyebrow">STEP BY STEP</span><h2>逐次修改，逐次核对。</h2>${stepPanel}</section>${complete?'<section class="panel"><span class="eyebrow">ACROSS THE WHOLE JOURNEY</span><h2>把三个局部改动放在一起看</h2><ol class="journey-summary"><li><b>边界问题：</b>J1 → J2 纳入右端点，仍然 WA；不把“仍 WA”等同于“改动无效”。</li><li><b>状态问题：</b>J2 → J3 每次查询重置 sum，判题变为 TLE；不把 TLE 当作正确性证明。</li><li><b>复杂度问题：</b>J3 → J4 用预处理减少重复扫描，观察到首次 AC。</li></ol><div class="note">过程总结描述可见修改，不推测你当时的想法、是否看题解，也不把提交间隔当作思考耗时。</div></section>':''}</div><div><section class="panel"><span class="eyebrow">A LESSON FROM THE PROCESS</span><h2>${complete?'全过程经验':'保留分析范围'}</h2>${complete?`<div class="candidate"><span class="badge amber">AI 候选 · 示例</span><h3>分开检查边界、状态和复杂度。</h3><p>在多次静态区间查询中，先确认区间语义与每次查询的状态，再判断重复扫描是否需要预处理。</p>${tagHTML(['前缀和','调试过程','复杂度'])}<p class="candidate-evidence">整体来源：J1 → J2 → J3 → J4<br>证据：端点修正、状态重置、查询重写<br>终点：本题首次 AC</p><button class="primary" data-collect="5">加入待确认（演示）</button></div>`:`<div class="empty">${label}<br>不显示完整首次 AC 经验。</div><button class="secondary" data-restore-journey style="margin-top:16px">切回完整示例</button>`}</section>${providerNotice()}</div></div>`;
}

document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.dataset.analysisScope){selectAnalysis(b.dataset.analysisScope);return;}
  if(b.dataset.journeyStep!==undefined){journeyStep=Number(b.dataset.journeyStep);renderAnalysis();return;}
  if(b.hasAttribute('data-restore-journey')){journeyCase='complete';journeyStep=0;renderAnalysis();return;}
  if(b.dataset.collect){const c=cards.find(c=>c.id===Number(b.dataset.collect));const existed=!c.deleted;c.deleted=false;navigate(c.review==='pending'?'pending':'library');selected=c.id;render();notify(existed?'这条示例候选已存在，未重复创建。':'已加入待确认，保留本次复盘范围与证据（演示）。');}
});
document.addEventListener('change',event=>{if(event.target.id==='journey-case'){journeyCase=event.target.value;journeyStep=0;renderAnalysis();}});
