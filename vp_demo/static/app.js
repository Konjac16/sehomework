'use strict';
const $ = (s, root = document) => root.querySelector(s);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escapeHtml;
const names = {overview:'训练概览', training:'日常练习', portrait:'能力画像', problems:'题目与代码', predict:'概率实验室', tasks:'补题计划', settings:'数据与账号', history:'提交历史', compare:'代码对比',experiences:'个人经验库'};
let state = {user:null, data:null, tab:'overview', page:1, filter:{q:'',tag:'',band:'',status:''}, registering:true, detail:[], prediction:null};
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,6000); }
async function api(path, data) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),['/api/sync','/api/training/refresh','/api/recommendations'].includes(path)?90000:30000);
  try {
  const response = await fetch(path, {...(data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),signal:controller.signal});
  const result = await response.json();
  if (!response.ok) { if(response.status===401){ showAuth(); } throw new Error(result.error || '请求失败'); }
  return result;
  } catch(error) {
    if(error.name==='AbortError')throw new Error('本地服务响应超时，请检查启动终端，然后重试。');
    throw error;
  } finally {clearTimeout(timer);}
}
function date(ts) { return ts ? new Date(ts*1000).toLocaleDateString('zh-CN') : '—'; }
function stamp(ts) { return ts ? new Date(ts*1000).toLocaleString('zh-CN',{hour12:false}) : '尚未同步'; }
function tags(values, limit=3) { return `<span class="tags">${values.slice(0,limit).map(t=>`<span class="tag">${e(t)}</span>`).join('')}${values.length>limit?`<span class="tag">+${values.length-limit}</span>`:''}</span>`; }
function rating(v) { return v == null ? '<span class="badge gray">未评级</span>' : `<span class="badge ${v>=2400?'amber':''}">${e(v)}</span>`; }
function heading(kicker,title,description,action='') {return `<div class="page-heading"><div><span class="eyebrow">${kicker}</span><h1>${title}</h1><p>${description}</p></div>${action}</div>`;}
function empty(title,description,button='') {return `<div class="empty"><b>${title}</b>${description}${button?`<div>${button}</div>`:''}</div>`;}
function dataNotice(){return state.data.onlyAccepted?'<div class="notice"><strong>当前数据仅包含 AC 提交。</strong> 以下展示已完成题目的积累；不据此计算真实通过率、失败次数或判定弱项。</div>':'<div class="notice neutral">统计仅覆盖已导入或同步的记录，不代表完整历史。不同题型可能重叠；自由练习与比赛表现尚未分开建模。</div>';}
function renderAuthMode(){
  const route=new URLSearchParams(location.search);
  const inRoomFlow=route.get('next')==='duo';
  const creating=state.registering;
  $('#auth').classList.toggle('duo-entry',inRoomFlow);
  $('#auth-title').textContent=inRoomFlow?(creating?'创建 VP 账号':'登录 VP 账号'):(creating?'开始你的训练档案':'欢迎回来');
  let description=creating?'注册本地账号，数据保存在这台电脑上。':'登录后继续查看你的记录和训练计划。';
  if(inRoomFlow){
    description=route.has('join')?'登录后继续接受队友的邀请。':route.has('room')?'登录后回到这间训练房。':'使用本地账号创建房间或加入队友。';
  }
  $('#auth-description').textContent=description;
  $('#auth-submit').textContent=inRoomFlow?(creating?'注册并继续':'登录并继续'):(creating?'注册并开始 ↗':'登录工作台 ↗');
  $('#auth-toggle').textContent=creating?'已有账号？登录':'还没有账号？注册';
  $('#auth-form input[name=password]').autocomplete=creating?'new-password':'current-password';
  $('#auth-error').textContent='';
}
function showAuth(){
  resetTraining();resetHelper();resetReviewUI();resetSourceUI();state.user=null;state.data=null;state.prediction=null;
  try{state.registering=localStorage.getItem('vp-has-account')!=='1';}catch{state.registering=true;}
  renderAuthMode();$('#auth').hidden=false;$('#shell').hidden=true;
}
async function load(){
  const generation=sourceUI.generation;
  const data=await api('/api/dashboard');
  if(generation!==sourceUI.generation||!state.user)return;
  state.data=data;
  $('#task-count').textContent=state.data.tasks.filter(t=>!t.done).length;
  render();
  watchSync(state.data.sourceSync);
}
async function enter(){
  const me=await api('/api/me');
  if(!me.user){showAuth();return;}
  state.user=me.user;$('#auth').hidden=true;$('#shell').hidden=false;
  const route = new URLSearchParams(location.search);
  if(route.get('next')==='duo'){
    const join = route.get('join') || '', roomId = route.get('room') || '';
    let target = '/duo';
    if (/^[a-f0-9]{10}$/i.test(join)) target += '?join=' + join.toUpperCase();
    else if (/^[a-f0-9]{20}$/.test(roomId)) target += '?room=' + roomId;
    location.replace(target);
    return;
  }
  if(new URLSearchParams(location.search).get('tab')==='tasks')state.tab='tasks';
  $('#username').textContent=me.user.username;$('#avatar').textContent=me.user.username[0].toUpperCase();
  await load();
  const sid=new URLSearchParams(location.search).get('submission');
  if(sid){try{await openSubmission(sid);}catch(error){toast(error.message);}}
}
function navigate(tab){state.tab=tab;render();window.scrollTo({top:0});}
function render(){
  if(!state.data)return;
  document.querySelectorAll('#navigation button').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));
  $('#page-label').textContent=names[state.tab];
  const views={overview,training:trainingView,portrait,problems,predict:predictionView,tasks,settings,history:historyView,compare:compareView,experiences:experienceView};
  $('#main').innerHTML=views[state.tab]();
  if(state.tab==='training')loadTraining();else resetTraining();
  if(state.tab==='history')renderHistory();
  if(state.tab==='compare'){renderDiff();syncReviewContext('compare');if(sourceUI.comparison&&!sourceUI.comparison.savedAnalysisId)loadStatementPanel(sourceUI.comparison.key,'compare-statement');}
  if(state.tab==='experiences')loadExperiences();
  if(state.tab==='settings'){syncStatus();loadAIConfig();checkHelper();}
  if(state.tab==='problems') renderProblemRows();
  if(state.tab==='predict' && state.prediction) {
    renderPrediction(state.prediction);
    const previous=state.predictionInput;
    if(previous) for(const [key,value] of Object.entries(previous)) {
      const field=$('#prediction-form').elements.namedItem(key);
      if(field)field.value=Array.isArray(value)?value.join(', '):(value??'');
    }
  }
}
function overview(){
  const d=state.data, s=d.stats;
  const metrics=[['已完成题目',s.solved,'按不同题目去重','↗'],['提交记录',s.submissions,'仅统计当前数据集','↻'],['包含源码',s.sources,'对应提交的已获取源码','⌘'],['待完成计划',d.tasks.filter(t=>!t.done).length,'补题与复习任务','☑']];
  const maxMonth=Math.max(1,...d.months.map(m=>m.count));
  return heading('YOUR TRAINING, IN FOCUS','每一次练习，都留下进步的线索。',`${e(d.settings.handle || state.user.username)} 的训练空间 · ${s.submissions?'最近同步 '+stamp(d.settings.lastSync):'从同步 CF 提交与源码开始'}`,`<button class="primary" data-tab="predict">探索下一道题 ↗</button>`)+
    `<section class="hero"><div class="hero-text"><span class="eyebrow">PRACTICE WITH INTENTION</span><h2>${s.solved?`你的 ${s.solved} 道题，值得重新看一遍。`:'先把你的训练记录带进来。'}</h2><p>${s.solved?'从题型与难度分布观察已有积累，再为下一步训练挑选值得投入的题目。这里的分析始终保留数据范围和依据。':'连接 CF 官方 API，直接获取历史提交、源码、标签和难度。逐次比较修改，回看从 WA 到 AC 的过程。'}</p><div class="hero-action"><button class="primary small" data-tab="${s.solved?'portrait':'settings'}">${s.solved?'查看训练画像':'连接 CF 获取数据'} →</button><span class="help">${s.solved?`${s.rated} 道题有难度记录`:'无需 OJ 密码'}</span></div></div><div class="hero-art" aria-hidden="true"><span class="caption">BUILD YOUR NEXT STEP ↗</span>${[38,61,84,112,144].map(h=>`<div class="bar" style="height:${h}px"></div>`).join('')}</div></section>`+
    `<div class="metric-grid">${metrics.map(m=>`<div class="metric"><div class="metric-head"><span>${m[0]}</span><span>${m[3]}</span></div><strong>${m[1]}</strong><small>${m[2]}</small></div>`).join('')}</div>`+
    dataNotice()+`<div class="columns"><section class="panel"><div class="panel-head"><div><h3>训练足迹</h3><p>数据集中最近 12 个有提交记录的月份 · UTC</p></div><span>提交次数</span></div>${d.months.length?`<div class="chart">${d.months.map(m=>`<div class="chart-col"><b>${m.count}</b><div class="stick" style="height:${Math.max(3,m.count/maxMonth*125)}px"></div><small>${m.month.slice(2)}</small></div>`).join('')}</div>`:empty('还没有训练记录','同步后，这里会展示提交时间分布。')}</section><section class="panel"><div class="panel-head"><div><h3>最常出现的标签</h3><p>已完成题目的标签分布，不等同于能力排名</p></div><button class="text-button" data-tab="portrait">全部 →</button></div>${tagBars(d.tags.slice(0,5))}</section></div>`+
    `<section class="panel"><div class="panel-head"><div><h3>最近的题目</h3><p>按最后提交时间排列</p></div><button class="text-button" data-tab="problems">打开题库 →</button></div>${s.problems?problemTable(d.problems.slice(0,5)):empty('第一道题，从这里开始','在数据与账号中直接同步 CF 提交和源码。')}</section>`;
}
function tagBars(list){const max=Math.max(1,...list.map(t=>t.solved));return list.length?list.map(t=>`<div class="tag-row"><span class="name" title="${e(t.name)}">${e(t.name)}</span><div class="track"><div class="fill" style="width:${t.solved/max*100}%"></div></div><b>${t.solved}</b></div>`).join(''):empty('暂无标签数据','导入有标签的题目后查看。');}
function problemTable(list){return `<div class="table-wrap"><table><thead><tr><th>题目</th><th>难度</th><th>标签</th><th>记录</th><th>最近提交</th><th></th></tr></thead><tbody>${list.map(p=>`<tr><td class="title-cell"><span class="problem-name">${e(p.title)}</span><span class="problem-id">CF · ${e(p.problemKey)}</span></td><td>${rating(p.rating)}</td><td>${tags(p.tags)}</td><td><span class="badge ${p.solved?'':'gray'}">${p.solved?'已通过':'未观察到 AC'}</span><div class="problem-id">${p.attempts} 次提交</div></td><td class="muted">${date(p.lastSubmittedAt)}</td><td><button class="link-button" data-detail="${e(p.problemKey)}">详情 ↗</button></td></tr>`).join('')}</tbody></table></div>`;}
function portrait(){
  const d=state.data;
  const bands=Object.keys(d.bands);
  const matrix=d.tags.slice(0,14).map(t=>({tag:t.name,cells:bands.map(b=>{const ps=d.problems.filter(p=>p.tags.includes(t.name)&&(p.rating===null?'未评级':`${Math.floor(p.rating/400)*400}–${Math.floor(p.rating/400)*400+399}`)===b);return {total:ps.length,solved:ps.filter(p=>p.solved).length};})}));
  return heading('OBSERVE BEFORE YOU CONCLUDE','训练能力画像','先看已有积累，再讨论下一步。点击题目列表可以核对每条记录。')+dataNotice()+
   `<div class="columns equal"><section class="panel"><div class="panel-head"><div><h3>难度分布</h3><p>按题目去重，难度采用 CF 原始 rating</p></div><span>${d.stats.problems} 道题</span></div>${bands.length?bands.map(b=>`<div class="tag-row"><span>${e(b)}</span><div class="track"><div class="fill" style="width:${d.bands[b].solved/Math.max(1,...Object.values(d.bands).map(x=>x.attempted))*100}%"></div></div><b>${d.bands[b].solved}</b></div>`).join(''):empty('暂无难度数据','请先导入或同步数据。')}</section><section class="panel"><div class="panel-head"><div><h3>题型积累</h3><p>一道题可拥有多个标签，因此数量有重叠</p></div><span>通过题数</span></div>${tagBars(d.tags.slice(0,8))}</section></div>`+
   `<section class="panel"><div class="panel-head"><div><h3>标签 × 难度</h3><p>每格：已通过题目 / 有提交记录的题目；这不是独立解题成功率。</p></div><span>前 14 个常见标签</span></div>${bands.length?`<div class="table-wrap"><table><thead><tr><th>算法标签</th>${bands.map(b=>`<th>${e(b)}</th>`).join('')}</tr></thead><tbody>${matrix.map(r=>`<tr><td>${e(r.tag)}</td>${r.cells.map(c=>`<td class="matrix-cell ${c.total?'matrix-good':'matrix-empty'}">${c.total?`<b>${c.solved} / ${c.total}</b>${c.total<5?'<div>样本较少</div>':''}`:'—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:empty('暂无画像','导入后自动生成。')}<p class="legend">“样本较少”的展示阈值暂设为 5 道，仅用于提醒。题目少不等于弱项；参考题解情况未知时，不评价独立能力。</p></section>`;
}
function problems(){
 const f=state.filter;
 return heading('YOUR PROBLEM ARCHIVE','题目与代码','浏览真实提交，按题型和难度筛选；源码仅对当前登录用户显示。', '<a class="secondary" href="/api/export">导出归档 ↓</a>')+
 `<section class="panel"><div class="filters"><input id="problem-search" aria-label="搜索题目" placeholder="搜索题号或题目名称…" value="${e(f.q)}"><select id="problem-tag" aria-label="筛选标签"><option value="">全部标签</option>${state.data.tags.map(t=>`<option ${f.tag===t.name?'selected':''}>${e(t.name)}</option>`).join('')}</select><select id="problem-band" aria-label="筛选难度"><option value="">全部难度</option>${Object.keys(state.data.bands).map(b=>`<option ${f.band===b?'selected':''}>${e(b)}</option>`).join('')}</select><select id="problem-status" aria-label="筛选状态"><option value="">全部状态</option><option value="solved" ${f.status==='solved'?'selected':''}>已通过</option><option value="unsolved" ${f.status==='unsolved'?'selected':''}>未观察到 AC</option></select></div><div id="problem-results"></div></section>`;
}
function filteredProblems(){const f=state.filter;return state.data.problems.filter(p=>(`${p.problemKey} ${p.title}`.toLowerCase().includes(f.q.toLowerCase()))&&(!f.tag||p.tags.includes(f.tag))&&(!f.status||(f.status==='solved'?p.solved:!p.solved))&&(!f.band||(p.rating==null?'未评级':`${Math.floor(p.rating/400)*400}–${Math.floor(p.rating/400)*400+399}`)===f.band));}
function renderProblemRows(){const ps=filteredProblems(), total=Math.max(1,Math.ceil(ps.length/15));state.page=Math.min(state.page,total);$('#problem-results').innerHTML=(ps.length?problemTable(ps.slice((state.page-1)*15,state.page*15)):empty('没有匹配的题目','试试其他关键词或清除筛选条件。'))+`<div class="table-bottom"><span>共 ${ps.length} 道题 · 第 ${state.page} / ${total} 页</span><div><button class="secondary small" data-page="-1" ${state.page<=1?'disabled':''}>上一页</button> <button class="secondary small" data-page="1" ${state.page>=total?'disabled':''}>下一页</button></div></div>`;}
function predictionView(){const ref=state.data.referenceRating;return heading('A SMALL EXPERIMENT, NOT A VERDICT','单题通过概率实验室','探索难度差带来的变化。当前是规则演示，不是经过验证的个人能力模型。')+
 `<div class="notice"><strong>未校准 · 规则估算 v0.1</strong> 固定假设为两小时 VP。结果仅由参考 Rating 和题目难度计算，标签与源码尚未进入模型，不应视为真实成功概率。</div><div class="columns equal"><section class="panel"><div class="panel-head"><h3>设置一道目标题</h3><span class="pill-info">CF 难度体系</span></div><form id="prediction-form"><label>题号（可选，用于检查是否做过）<input name="problemKey" placeholder="例如 1900A"></label><label>题目名称（可选）<input name="title" placeholder="例如：本次计划挑战的题目"></label><div class="field-row"><label>你的参考 Rating<input name="referenceRating" type="number" min="0" max="4000" required value="${ref??''}" placeholder="请填写，不能由 AC 记录推断"></label><label>题目难度<input name="problemRating" type="number" min="800" max="4000" step="100" required value="1800"></label></div><label>训练标签（逗号分隔，仅供补题计划使用）<input name="tags" placeholder="dp, graphs"></label><button class="primary wide">生成规则估算 ↗</button><p class="help">输入的参考 Rating 只用于本次试算。可在“数据与账号”中保存默认值。完整题面分析将作为后续功能。</p></form></section><section class="panel" id="prediction-output">${empty('先选一个目标，看看难度关系','填写左侧信息，生成估算及可核对的依据。')}<div class="notice neutral">已有 AC 记录的题目会被标记为“已做过”，不会返回虚假的 100% 首次通过概率。</div></section></div><section class="panel"><div class="panel-head"><h3>从估算到可信的预测</h3><span>后续迭代</span></div><div class="roadmap"><div><b>01 · 收集完整赛内样本</b><p>记录比赛开始前的特征，以及时间窗口内是否通过。</p></div><span class="badge gray">待建设</span></div><div class="roadmap"><div><b>02 · 训练与校准</b><p>按时间划分验证集，比较基础模型与加入题型特征的模型。</p></div><span class="badge gray">待建设</span></div><div class="roadmap"><div><b>03 · 留下预测快照</b><p>赛前记录预测，赛后比较实际结果，持续验证概率。</p></div><span class="badge gray">待建设</span></div></section>`;}
function renderPrediction(r){const el=$('#prediction-output');if(!el)return;if(r.status!=='estimated'){el.innerHTML=empty(r.status==='already_solved'?'这道题你已经通过了':'暂时不能估算',e(r.reason));return;}
el.innerHTML=`<div class="panel-head"><h3>本次规则估算</h3><span class="badge amber">未校准</span></div><div class="probability-result"><span class="eyebrow">HEURISTIC ESTIMATE</span><div class="prob-number">${Math.round(r.probability*100)}<span>%</span></div><p class="muted">假设：两小时 VP · 首次面对该题</p><div class="prob-track"><div style="width:${r.probability*100}%"></div></div><small>数值来自人为设定的规则，不是实测成功率</small></div><div class="result-facts"><div>用户参考 Rating<strong>${r.referenceRating}</strong></div><div>题目难度<strong>${r.problemRating}</strong></div></div><p class="help">${e(r.reason)}</p><p class="help">附近 ±200 难度内，已观察 ${r.nearbyObserved} 道题、通过 ${r.nearbySolved} 道。${e(r.evidence)}。这项统计仅作背景展示，不参与概率计算。</p><button class="secondary wide" data-prediction-task style="margin-top:18px">加入我的训练计划 ＋</button>`;}
function tasks(){const pending=state.data.tasks.filter(t=>!t.done),done=state.data.tasks.filter(t=>t.done);return heading('TURN INSIGHT INTO ACTION','补题与复习计划','按照明确规则排序，也可以手动置顶。完成后记录学习方式。','<button class="primary" data-new-task>添加题目 ＋</button>')+
 `<div class="notice neutral">排序依据：手动置顶 → 相对参考 Rating 的难度层次 → 同层次的训练标签匹配 → 加入时间。无难度时标为“待判断”。当前完成状态由你手动确认。</div><div class="metric-grid"><div class="metric"><div class="metric-head">待完成</div><strong>${pending.length}</strong><small>保持清晰、可执行的训练清单</small></div><div class="metric"><div class="metric-head">已经完成</div><strong>${done.length}</strong><small>完成方式保留在记录中</small></div><div class="metric"><div class="metric-head">参考 Rating</div><strong>${state.data.referenceRating??'—'}</strong><small>${state.data.settings.referenceRating!=null?'手动设置':'来自同步资料或尚未设置'}</small></div><div class="metric"><div class="metric-head">关注的标签</div><strong>${(state.data.settings.focusTags||[]).length}</strong><small>可在数据与账号中调整</small></div></div><div class="task-list">${state.data.tasks.length?state.data.tasks.map(t=>`<article class="task-card ${t.done?'done':''}"><span class="task-icon">${t.done?'✓':'↗'}</span><div class="task-body"><h3>${e(t.title)} ${rating(t.rating)} <span class="badge ${t.level==='挑战'?'amber':t.level==='待判断'?'gray':''}">${t.level}</span></h3><p>${e(t.reason)}</p>${tags(t.tags,8)}${t.done?`<p>完成方式：${{independent:'独立完成',hint:'参考提示',editorial:'阅读题解'}[t.completion_mode]||'未记录'}</p>`:''}${t.note?`<p class="note-text">笔记：${e(t.note)}</p>`:''}${t.url?`<a class="text-button" target="_blank" rel="noopener" href="${e(t.url)}">前往 CF 题目 ↗</a>`:''}</div><div class="task-actions"><button class="text-button" data-pin="${t.id}">${t.pinned?'取消置顶':'置顶'}</button><button class="secondary small" data-edit-task="${t.id}">${t.done?'查看 / 修改':'记录进展'}</button></div></article>`).join(''):empty('给下一次训练留一个明确目标','可以手动添加待补题，也可以从题库加入一道需要复习的题。','<button class="primary" data-new-task>添加第一道题 ＋</button>')}</div>`;}
function settings(){const d=state.data,s=d.settings;return heading('YOUR DATA, YOUR SPACE','数据与账号','第一版支持 Codeforces；账号归属不验证。数据与源码保存在本机，按本平台账号隔离。')+
 onlineSettings()+helperSettingsView()+aiSettingsView()+
 `<div class="columns equal"><section class="panel"><div class="panel-head"><h3>训练偏好</h3><span class="muted">用于估算与计划排序</span></div><form id="settings-form"><label>手动参考 Rating<input name="referenceRating" type="number" min="0" max="4000" value="${s.referenceRating??''}" placeholder="留空则使用同步得到的 Rating"></label><p class="help">CF 最新资料：${s.profile?.rating??'未获取'}；最高 Rating：${s.profile?.maxRating??'未获取'}。我们不会根据仅 AC 的数据反推出你的水平。</p><label style="margin-top:20px">关注的算法标签（最多 8 个）</label><div class="chip-grid">${[...new Set(['dp','graphs','greedy','math','data structures','trees','strings','binary search',...d.tags.slice(0,12).map(t=>t.name)])].map(t=>`<label><input name="focus" type="checkbox" value="${e(t)}" ${(s.focusTags||[]).includes(t)?'checked':''}>${e(t)}</label>`).join('')}</div><button class="primary">保存偏好</button></form></section><section class="panel"><div class="panel-head"><h3>平台接入进度</h3><span>适配器架构</span></div><div class="roadmap"><div><b>Codeforces</b><p>官方 API 源码同步 / 提交历史 / 两次提交差异对比</p></div><span class="badge">已实现</span></div><div class="roadmap"><div><b>AtCoder · QOJ · 洛谷</b><p>统一的数据接口已预留，适配器待实现</p></div><span class="badge gray">后续版本</span></div><div class="roadmap"><div><b>VP 房间与比赛推荐</b><p>本版先验证数据、画像和补题体验</p></div><span class="badge gray">后续版本</span></div><p class="help">本版运行在本机 127.0.0.1。注册账号、记录和任务持久化到 SQLite；服务重启后需重新登录。</p><a class="secondary small" href="/api/export" style="display:inline-block;margin-top:20px">导出当前账号的数据与源码 ↓</a></section></div>`;}
async function detail(key, selectedId=null){const generation=sourceUI.generation;const r=await api('/api/problem?key='+encodeURIComponent(key));if(generation!==sourceUI.generation)return;state.detail=r.records;const p=r.records[0];$('#detail-content').innerHTML=`<div class="single-review-layout"><div id="single-review-code"><div class="dialog-head"><div><span class="eyebrow">CODEFORCES · ${e(p.problemKey)}</span><h2>${e(p.title)}</h2></div><button class="close-dialog" data-close aria-label="关闭">×</button></div><div class="detail-facts">${rating(p.rating)}${tags(p.tags,20)}</div><div class="dialog-controls"><select id="submission-select" aria-label="选择提交">${r.records.map((x,i)=>`<option value="${i}">#${e(x.externalId)} · ${e(x.verdict)} · ${stamp(x.submittedAt)}</option>`).join('')}</select><button class="secondary small" id="copy-source">复制代码</button><button class="secondary small" id="save-source">保存代码</button></div><div class="form-actions">${originalProblemLink(p)}<button class="primary small" data-single-review>✧ AI 分析</button><button class="secondary small" data-record-problem>记录经验</button><button class="secondary small" data-detail-compare>与上次提交对比</button><a id="source-new-tab" target="_blank" rel="noopener" class="text-button">在新标签页打开 ↗</a></div>${statementPanel(p,'single-statement')}<div id="source-meta" class="help"></div><pre id="source-code"></pre><div class="form-actions"><a href="${e(p.url)}" target="_blank" rel="noopener" class="primary small">查看原题 ↗</a><button class="secondary small" data-add-problem="${e(p.problemKey)}">加入补题 / 复习计划 ＋</button><button class="text-button" data-estimate-problem="${e(p.problemKey)}">查看概率适用性 →</button></div><p class="help">程序运行时间与内存是评测指标，不代表选手的思考时间。</p></div><aside class="review-side" id="single-review-side" tabindex="-1" aria-label="AI 分析" hidden></aside></div>`;const selected=Math.max(0,r.records.findIndex(x=>x.externalId===String(selectedId)));$('#submission-select').value=String(selected);updateSource(selected);loadStatementPanel(p.problemKey,'single-statement');if(!$('#detail').open)$('#detail').showModal();}
function updateSource(i){const r=state.detail[i];$('#source-new-tab').href='/?submission='+encodeURIComponent(r.externalId);$('#source-code').textContent=r.source||'这次提交尚未获取源码。请到“数据与账号”使用该 CF 账号的 API Key / Secret 同步源码。';$('#source-meta').textContent=`${r.language} · ${r.timeMs??'—'} ms · ${r.memoryBytes==null?'—':(r.memoryBytes/1024/1024).toFixed(1)} MB · ${r.participantType}`;$('#copy-source').disabled=!r.source;$('#save-source').disabled=!r.source;syncReviewContext('single');}
function taskDialog(task=null, seed={}){closeReview();const s=task||seed;const editing=Boolean(task);$('#detail-content').innerHTML=`<div class="dialog-head"><div><span class="eyebrow">PRACTICE PLAN</span><h2>${editing?'记录学习进展':'添加一道训练题'}</h2></div><button class="close-dialog" data-close aria-label="关闭">×</button></div><form id="task-form" data-id="${task?.id||''}">${editing?`<h3>${e(task.title)}</h3><label style="margin-top:18px">状态<select name="done"><option value="0">待完成</option><option value="1" ${task.done?'selected':''}>已完成</option></select></label><label>完成方式<select name="completionMode"><option value="">尚未完成</option>${[['independent','独立完成'],['hint','参考提示'],['editorial','阅读题解']].map(([v,n])=>`<option value="${v}" ${task.completion_mode===v?'selected':''}>${n}</option>`).join('')}</select></label><label>个人笔记<textarea name="note" rows="4" maxlength="2000" placeholder="记录卡点、收获或下次需要注意的地方">${e(task.note)}</textarea></label>`:`<label>题目名称<input name="title" required maxlength="160" value="${e(s.title||'')}"></label><div class="field-row"><label>CF 题号（可选）<input name="problemKey" value="${e(s.problemKey||'')}" placeholder="例如 1900A"></label><label>题目难度（可选）<input name="rating" type="number" min="800" max="4000" step="100" value="${s.rating??''}"></label></div><label>算法标签（逗号分隔）<input name="tags" value="${e((s.tags||[]).join(', '))}" placeholder="dp, trees"></label><label>CF 题目链接（可选）<input name="url" type="url" value="${e(s.url||'')}" placeholder="https://codeforces.com/contest/…"></label>`}<button class="primary wide">${editing?'保存进展':'加入计划'}</button><p class="help">已通过的题也可以作为复习任务加入。手动完成标记不会伪造 AC 提交或改变题库统计。</p></form>`;if(!$('#detail').open)$('#detail').showModal();}
async function submitHandler(event){
 if(event.target.id.startsWith('training-'))return;
 event.preventDefault();const form=event.target,button=form.querySelector('button[type=submit],button.primary');if(!button)return;const original=button.innerHTML;button.disabled=true;button.textContent='处理中…';
 try {const f=new FormData(form);
 if(form.id==='auth-form'){
  await api(state.registering?'/api/register':'/api/login',{username:f.get('username'),password:f.get('password')});
  try{localStorage.setItem('vp-has-account','1');}catch{}
  form.reset();await enter();
 }
 if(form.id==='import-form'){const file=f.get('file');if(file.size>24*1024*1024)throw new Error('文件大于 24 MB');const r=await api('/api/import',{text:await file.text()});await load();toast(`导入完成：收到 ${r.received} 条，新增 ${r.added} 条`);}
 if(form.id==='sync-form'){const r=await api('/api/sync',{handle:f.get('handle')});await load();toast(`同步成功：新增 ${r.added} 条记录`);}
 if(form.id==='settings-form'){await api('/api/settings',{referenceRating:f.get('referenceRating')===''?null:Number(f.get('referenceRating')),focusTags:f.getAll('focus')});await load();toast('训练偏好已保存');}
 if(form.id==='prediction-form'){const input={problemKey:f.get('problemKey').trim(),referenceRating:Number(f.get('referenceRating')),problemRating:Number(f.get('problemRating'))};state.prediction=await api('/api/predict',input);state.predictionInput={...input,title:f.get('title').trim(),tags:f.get('tags').split(/[,，]/).map(s=>s.trim()).filter(Boolean)};renderPrediction(state.prediction);}
 if(form.id==='task-form'){if(form.dataset.id){await api('/api/tasks/update',{id:Number(form.dataset.id),done:f.get('done')==='1',completionMode:f.get('completionMode'),note:f.get('note')});}else{await api('/api/tasks/create',{title:f.get('title'),problemKey:f.get('problemKey').trim(),rating:f.get('rating')===''?null:Number(f.get('rating')),tags:f.get('tags').split(/[,，]/).map(s=>s.trim()).filter(Boolean),url:f.get('url').trim()});}$('#detail').close();await load();toast('训练计划已保存');}
 }catch(error){if(form.id==='auth-form')$('#auth-error').textContent=error.message;else toast(error.message);}finally{button.disabled=false;button.innerHTML=original;}
}
document.addEventListener('submit',submitHandler);
document.addEventListener('input',event=>{if(event.target.id==='problem-search'){state.filter.q=event.target.value;state.page=1;renderProblemRows();}});
document.addEventListener('change',event=>{const t=event.target;if(t.id==='submission-select')updateSource(Number(t.value));const fields={'problem-tag':'tag','problem-band':'band','problem-status':'status'};if(fields[t.id]){state.filter[fields[t.id]]=t.value;state.page=1;renderProblemRows();}});
document.addEventListener('click',async event=>{
 const b=event.target.closest('button');if(!b || b.disabled)return;
 try{
 if(b.dataset.tab){navigate(b.dataset.tab);return;}
 if(b.id==='open-duo'){location.assign('/duo');return;}
 if(b.id==='auth-toggle'){state.registering=!state.registering;renderAuthMode();}
 if(b.id==='logout'){await api('/api/logout',{});state.data=null;state.prediction=null;state.tab='overview';showAuth();}
 if(b.id==='import-sample'){b.disabled=true;b.textContent='正在导入…';try{const r=await api('/api/import-sample',{});await load();toast(`已导入 ${r.received} 条记录，新增 ${r.added} 条`);}finally{b.disabled=false;b.textContent='导入这份本地样本 →';}}
 if(b.dataset.detail)await detail(b.dataset.detail);
 if(b.dataset.page){state.page+=Number(b.dataset.page);renderProblemRows();}
 if(b.hasAttribute('data-close'))$('#detail').close();
 if(b.hasAttribute('data-new-task'))taskDialog();
 if(b.dataset.editTask)taskDialog(state.data.tasks.find(t=>t.id===Number(b.dataset.editTask)));
 if(b.dataset.pin){const task=state.data.tasks.find(t=>t.id===Number(b.dataset.pin));await api('/api/tasks/update',{id:task.id,pinned:!task.pinned});await load();}
 if(b.dataset.addProblem){const p=state.data.problems.find(p=>p.problemKey===b.dataset.addProblem);taskDialog(null,p);}
 if(b.hasAttribute('data-prediction-task')){const p=state.predictionInput;taskDialog(null,{problemKey:p.problemKey,title:p.title||p.problemKey||`CF ${p.problemRating} 难度训练题`,rating:p.problemRating,tags:p.tags});}
 if(b.dataset.estimateProblem){const p=state.data.problems.find(p=>p.problemKey===b.dataset.estimateProblem);$('#detail').close();state.predictionInput={problemKey:p.problemKey,referenceRating:state.data.referenceRating,problemRating:p.rating,title:p.title,tags:p.tags};state.prediction=await api('/api/predict',state.predictionInput);navigate('predict');$('#prediction-form input[name=problemKey]').value=p.problemKey;$('#prediction-form input[name=title]').value=p.title;$('#prediction-form input[name=problemRating]').value=p.rating??'';}
 if(b.id==='copy-source'){await navigator.clipboard.writeText(state.detail[Number($('#submission-select').value)].source);toast('代码已复制');}
 if(b.id==='save-source'){const r=state.detail[Number($('#submission-select').value)], url=URL.createObjectURL(new Blob([r.source],{type:'text/plain;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`${r.problemKey}-${r.externalId}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 }catch(error){toast(error.message);}
});
$('#detail').addEventListener('click',event=>{if(event.target===$('#detail'))$('#detail').close();});
function showStartupError(error){
  $('#auth').hidden=true;$('#shell').hidden=true;
  $('#boot-status').hidden=false;$('#boot-message').textContent=error.message;
}
async function startApplication(){
  try{
    if(typeof resetSourceUI!=='function'||typeof resetReviewUI!=='function'||typeof ensureStatement!=='function'||typeof resetHelper!=='function')throw new Error('新版源码模块未加载。请关闭旧版 VP Lab 服务，再重新运行 StartDemo.cmd 并刷新页面。');
    const health=await api('/api/health');
    if(health.app!=='vplab'||health.apiVersion!==10)throw new Error('页面与服务版本不匹配，请重启 VP Lab 服务。');
    await enter();
    $('#boot-status').hidden=true;
  }catch(error){showStartupError(error);}
}
startApplication();
