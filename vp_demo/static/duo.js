'use strict';
const content=document.querySelector('#content'), notice=document.querySelector('#notice');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e=esc; // Shared, locally bundled statement formatter uses the same HTML escaping as the workspace.
function browseQuery({q='',page=1,onlyEligible=false}={}){
 const p=new URLSearchParams();if(q)p.set('q',q);if(page>1)p.set('page',page);if(onlyEligible)p.set('eligible','1');
 return p.size?'?'+p:'';
}
const pageQuery=()=>browseQuery(room?.candidatePage);
const labels={WAITING:'选场与准备',RUNNING:'比赛进行中',SETTLING:'等待成员同步结算',FINISHED:'已结算',CANCELLED:'已取消'};
const badge=(mode)=>`<span class="pill ${mode==='replay'?'replay':''}">${mode==='replay'?'合成事件回放':'真实训练 · CF'}</span>`;
const clockText=n=>[Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(i=>String(i).padStart(2,'0')).join(':');
let room=null, uid=null, seq=0, busy=false, polling=false, signature='', clockBase=0, received=0;
function say(text){notice.hidden=!text;notice.textContent=text||'';}
async function api(path,data){
 const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},
  ...(data===undefined?{}:{body:JSON.stringify(data)})});
 const body=await r.json();
 if(r.status===401){signedOut();throw Error('会话已过期，请登录。');}
 if(!r.ok){
  const error=new Error(body.error||'请求失败');
  error.status=r.status;
  throw error;
 }
 return body;
}
function signedOut(){
 uid=null;room=null;seq++;
 const destination=new URLSearchParams(location.search);
 destination.set('next','duo');
 location.replace('/?'+destination);
}
async function lobby(){
 const token=++seq;room=null;signature='';say('');
 const data=await api('/api/rooms');if(seq!==token)return;
 history.replaceState(null,'','/duo');
 content.innerHTML=`<section class="hero"><div><h1>VP 训练室</h1><p>1～${data.maxMembers} 人选同一场比赛，各自计分、各自补题。</p></div></section>
 <div class="grid"><section class="box"><h2>创建房间</h2><form id="create">
 <label>训练方式<select name="mode"><option value="replay">离线回放 · 可快进看榜</option><option value="training">CF 实战 · 自动同步房间提交</option></select></label>
 <label id="live-handle" hidden>CF 用户名<input name="handle" maxlength="40" value="${esc(data.handle)}" placeholder="你在 Codeforces 提交时使用的名字" ${data.handle?'readonly':''} disabled></label>
 <details><summary>时长预算 · <span id="create-minutes">120</span> 分钟</summary><label>可用分钟数<input name="maxMinutes" type="number" min="1" max="1440" value="120" required></label></details>
 <button class="primary" type="submit">创建回放房</button></form></section>
 <section class="box"><h2>加入队友</h2><form id="join"><label>房间码<input name="code" required minlength="10" maxlength="10" autocomplete="off" placeholder="输入十位房间码"></label>
 <p class="sub quiet">如果收到了邀请链接，可以直接在本机的另一个浏览器会话打开。</p><button>加入房间</button></form></section></div>
 <section class="box"><h2>我的房间</h2>${data.rooms.length?data.rooms.map(r=>`<div class="tile row between"><div>${badge(r.mode)}　<b>${esc(r.name)}</b><br><small>${esc(labels[r.phase])} · ${esc(r.inviteCode)}</small></div><button data-open="${esc(r.id)}">打开房间</button></div>`).join(''):'<p class="sub">还没有参加过房间。</p>'}</section>`;
}
async function invitation(code, joinRequested=false){
 code=String(code||'').trim().toUpperCase();
 if(!/^[A-F0-9]{10}$/.test(code))throw Error('请输入十位房间码');
 const info=await api('/api/rooms/invite?code='+encodeURIComponent(code));
 if(info.memberRoomId)return show(await api('/api/rooms/'+info.memberRoomId),true);
 if(info.issue)throw Error(info.issue);
 const needsHandle=info.mode==='training'&&!info.boundHandle;
 if(joinRequested&&!needsHandle)return show(await api('/api/rooms/join',{code}),true);
 room=null;signature='';
 history.replaceState(null,'','/duo?join='+code);
 content.innerHTML=`<section class="box invitation"><h1>加入队友的房间</h1>
 <p>${badge(info.mode)}　${info.memberCount} / ${info.maxMembers} 人　·　房间码 <b>${code}</b></p>
 <form id="accept-invite"><input type="hidden" name="code" value="${code}">
 ${needsHandle?'<label>CF 用户名<input name="handle" required maxlength="40" autocomplete="off"></label>':
 `<p>你将以 <b>${esc(info.boundHandle||document.querySelector('#who').textContent)}</b> 参赛。</p>`}
 <button class="primary">加入房间</button> <button type="button" data-do="lobby">返回大厅</button></form></section>`;
}
function syncTimestamp(value){
 return value?new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'尚无成功记录';
}
function memberSyncState(m){
 if(m.syncDelayed)return {label:'同步延迟',className:'warning'};
 if(m.syncing)return {label:'同步中',className:'syncing'};
 return {label:m.syncedAt?'已同步':'等待首轮',className:m.syncedAt?'':'muted'};
}
function memberPanel(r){
 const activeSync=r.mode==='training'&&['RUNNING','SETTLING'].includes(r.phase);
 return `<section class="box members-panel"><div class="row between"><h2>参赛成员</h2><small>${r.members.length} / ${r.maxMembers}</small></div>${r.members.map(m=>{
  const status=activeSync?memberSyncState(m):{label:r.phase==='WAITING'?(m.ready?'已确认':'待确认'):'已结束',className:''};
  return `<div class="member-entry"><div class="row between"><div class="member-identity"><span class="member-avatar" aria-hidden="true">${esc(String(m.handle).slice(0,1).toUpperCase())}</span><div><b>${esc(m.handle)}</b> ${m.isMe?'<span class="quiet">· 我</span>':''}<br><small>席位 ${esc(m.slot)} · ${m.slot===1?'房主':'队友'}</small></div></div><span class="pill ${status.className}">${status.label}</span></div>
  ${r.mode==='training'&&r.phase!=='WAITING'?`<p class="member-sync quiet">最后成功：<span>${esc(syncTimestamp(m.syncedAt))}</span>${activeSync&&m.syncDelayed?' · 已超过 90 秒':''}</p>`:''}
  ${m.syncError?`<p class="sync-error quiet">${esc(m.syncError)} · 保留上次成功成绩</p>`:''}</div>`;
 }).join('')}
 ${r.phase==='WAITING'&&r.members.length<r.maxMembers?'<p class="sub quiet">新成员入房后，全员按新名单重新选场确认。</p>':''}</section>`;
}
function recommendationPanel(r){
 const recommendations=(r.recommendations||[]).slice(0,3);
 return `<section class="box recommendations" id="recommendations" aria-labelledby="recommendations-title"><div class="row between"><div><p class="section-kicker">TEAM MATCH</p><h2 id="recommendations-title">推荐给本房间</h2></div><span class="pill">${recommendations.length} 场推荐</span></div>
 <p class="sub quiet">${esc(r.recommendationNote||'先满足时长与接触记录条件，再结合成员水平进行规则推荐。推荐不显示题目内容。')}</p>
 ${recommendations.length?`<div class="recommendation-grid">${recommendations.map((c,i)=>`<article class="recommendation-card" data-recommendation="${esc(c.contestId)}"><div class="row between"><span class="recommendation-order">0${i+1}</span>${c.fitLabel?`<span class="recommendation-fit">${esc(c.fitLabel)}</span>`:''}</div><p class="contest-num">CF #${esc(c.contestId)}</p><h3>${esc(c.name)}</h3><p class="recommendation-meta">${esc(c.durationSeconds/60)} 分钟 <span>·</span> ${esc(c.problemCount)} 题</p><p class="recommendation-reason">${esc(c.reason)}</p><div class="recommendation-action">${r.selected?.contestId===c.contestId?'<span class="pill">已选本场</span>':r.isOwner?`<button class="${i===0?'primary':''}" data-recommend-pick="${esc(c.contestId)}">选这场并确认 <span aria-hidden="true">↗</span></button>`:'<small>由房主选场</small>'}</div></article>`).join('')}</div>`:'<div class="recommendation-empty"><b>暂时没有合适的比赛</b><p class="quiet">可以在下方浏览赛库，或由房主调整时长与重复练习条件。</p></div>'}
 </section>`;
}
function waiting(r){
 const mine=r.members.find(m=>m.isMe), g=r.candidatePage;
 const pending=r.members.filter(m=>!m.ready).map(m=>m.isMe?'你':m.handle);
 function pickButton(c){
  if(!r.isOwner)return '';
  if(r.selected?.contestId===c.contestId)return '<span class="pill">已选本场</span>';
  if(c.extendToMinutes)return `<button data-pick="${c.contestId}" data-minutes="${c.extendToMinutes}">用 ${c.extendToMinutes} 分钟选场并确认</button>`;
  return `<button data-pick="${c.contestId}" ${c.eligible?'':'disabled'}>${c.eligible?'选场并确认':'不可选'}</button>`;
 }
 const options=r.candidates.map(c=>`<div class="tile candidate" data-contest="${c.contestId}"><div><small class="contest-num">CF #${c.contestId}</small><br><b>${esc(c.name)}</b><br><small>${c.durationSeconds/60} 分钟 · ${c.problemCount} 题 · ${esc(c.reason)}</small></div>
 ${pickButton(c)}</div>`).join('');
 let guidance='房主选好比赛后，请在这里确认参赛。';
 if(r.isOwner)guidance=r.members.length===1?'可以选场单人开赛，或先邀请队友。':'成员已到齐后，选一场适合大家的比赛。';
 const readyStatus=pending.length?'等待'+esc(pending.join('、'))+'确认':(r.isOwner?'成员已全部确认，可以开赛。':'你已确认，等待房主开赛。');
 return `${recommendationPanel(r)}<div class="grid"><div><section class="box"><h2>准备开赛</h2>
 <p id="ready-status" aria-live="polite" tabindex="-1">${r.selected?readyStatus:guidance}</p>
 ${r.selected&&r.mode==='replay'?'<p class="sub quiet">回放事件已就绪。</p>':''}
 <details id="room-rules"><summary>房间计分规则</summary><p class="sub quiet">先比过题数，再比罚时。通过题计首次 AC 的分钟数，加上此前每次计罚失败的 20 分钟。编译错误和未通过题不加罚时。截止当秒的提交不计入。</p></details>
 ${r.selected?`<div class="actions">${mine.ready?'':`<button class="primary" data-do="ready">确认参赛</button>`}${r.isOwner?`<button class="primary" data-do="start" ${pending.length?'disabled':''}>${r.members.length===1?'单人开赛':'开始 VP（'+r.members.length+' 人）'}</button>`:''}</div>`:''}</section>${memberPanel(r)}</div>
 <section class="box" id="catalog"><h2>选择历史赛</h2><p id="catalog-count">历史赛库 <strong>${g.total.toLocaleString('en-US')}</strong> 场 · 当前房间可选 <strong>${g.eligible.toLocaleString('en-US')}</strong> 场</p><p class="sub quiet">${r.isOwner?'选择比赛同时确认自己的参赛意愿和房间计分规则。':'你可以浏览赛库，比赛由房主选定。'}</p>
 ${r.isOwner?`<details id="prefs"><summary>选场条件 · 最多 ${r.maxMinutes} 分钟</summary><form id="preferences"><label>时长预算（分钟）<input type="number" name="maxMinutes" min="1" max="1440" required value="${r.maxMinutes}"></label><label><input type="checkbox" name="allowSeen" ${r.allowSeen?'checked':''}> 允许已有提交的比赛</label><button>更新条件并重新确认</button></form></details>`:`<p>时长预算：${r.maxMinutes} 分钟 · ${r.allowSeen?'允许重复练习':'排除已知接触'}</p>`}
 <form id="contest-search"><label for="contest-query">比赛编号或名称</label><div class="search-line"><input id="contest-query" name="q" type="search" maxlength="100" value="${esc(g.q)}" placeholder="如：1985、Div. 4"><button>搜索</button></div><label class="quiet"><input type="checkbox" name="eligible" ${g.onlyEligible?'checked':''}> 只看当前房间可选的比赛</label></form>
 <div class="row between"><small id="matches" aria-live="polite">${g.matched.toLocaleString('en-US')} 场匹配${g.pages?' · 第 '+g.page+' / '+g.pages+' 页':''}</small>${g.q||g.onlyEligible?'<button class="text-btn" data-clear="1">清除搜索</button>':''}</div>
 ${options}
 ${!g.matched?'<p class="banner">没有匹配结果。可更换编号或赛名，或清除搜索条件。</p>':''}
 ${!g.eligible?'<p class="banner">当前房间没有可选比赛，房主可调整时长或允许重复练习。</p>':''}
 ${g.pages>1?`<nav class="pager" aria-label="赛库分页"><button data-page="${g.page-1}" ${g.page===1?'disabled':''}>上一页</button><span>${g.page} / ${g.pages}</span><button data-page="${g.page+1}" ${g.page===g.pages?'disabled':''}>下一页</button></nav>`:''}
 ${r.isOwner&&r.mode==='replay'&&r.selected?'<details id="events"><summary>替换回放事件</summary><p class="quiet">回放事件只能引用当前房内的席位。更换事件后全员须重新确认。</p><label>事件文件<input id="event-file" type="file" accept=".json,application/json"></label></details>':''}
 </section></div>`;
}
function problemLink(r,p,label){
 return r.offlineReading?`<button class="text-btn" data-read="${esc(p.index)}" title="读取 ${esc(p.title)}">${esc(label)}</button>`:
  `<a class="plink" target="_blank" rel="noopener noreferrer" href="${esc(p.url)}">${esc(label)} ↗</a>`;
}
async function readProblem(idx){
 const p=await api('/api/rooms/'+room.id+'/statement?index='+encodeURIComponent(idx));
 const dialog=document.querySelector('#reading'), link=room.problems.find(x=>x.index===idx);
 const fields=[['题意',p.body],['输入格式',p.input_format],['输出格式',p.output_format],['交互说明',p.interaction_format]];
 const section=([title,value])=>value?`<section><h3>${title}</h3>${renderStatementText(value)}</section>`:'';
 const sample=(type,value)=>`<div><h4>${type}</h4>${value?`<pre>${esc(value)}</pre>`:'<small>快照中的此栏为空，请对照原题。</small>'}</div>`;
 document.querySelector('#read-body').innerHTML=`<h2 id="reading-title">${esc(idx)}. ${esc(p.name)}</h2>
 <p class="sub">CF #${p.contestId} · 离线题面 · <a class="plink" target="_blank" rel="noopener noreferrer" href="${esc(link.url)}">在 Codeforces 打开原题 ↗</a></p>
 ${[p.time_limit,p.memory_limit].filter(Boolean).map(x=>'<span class="pill">'+esc(x)+'</span>').join(' ')}
 ${fields.map(section).join('')}<h3>公开样例</h3>
 ${p.example_source==='ronin'?'<p class="banner quiet">此来源的部分样例缺少原始换行；运行前请对照原题。</p>':''}
 ${p.examples.map((x,i)=>`<section class="sample-pair"><small>样例 ${i+1}</small><div>${sample('输入',x.input)}${sample('输出',x.output)}</div></section>`).join('')}
 ${section(['备注',p.note])}<p class="sub quiet">正文来自 ${esc(p.body_source)}，样例来自 ${esc(p.example_source)}。图示和交互细节可查看原题。</p>`;
 document.querySelector('#read-actions').innerHTML=readingTaskForm(room,idx);
 dialog.showModal();document.querySelector('#read-body').scrollTop=0;
}
function readingTaskForm(r,idx){
 const task=r.upsolves.find(t=>t.index===idx);
 if(!task||r.phase!=='FINISHED')return '';
 if(task.done)return '<p class="banner">你已完成这道补题。</p>';
 const real=r.mode==='training';
 return `<form class="reading-task" id="finish-reading" data-index="${esc(idx)}">
 ${real?'<label>完成方式<select name="completionMode" required><option value="">请选择</option><option value="independent">独立完成</option><option value="hint">参考提示</option><option value="editorial">阅读题解</option></select></label>':''}
 <button class="primary">${real?'保存补题完成状态':'标记演示完成'}并返回清单</button>
 <p class="read-error" role="status"></p></form>`;
}
document.querySelector('#read-actions').addEventListener('submit',async ev=>{
 ev.preventDefault();
 const form=ev.target;
 if(form.id!=='finish-reading'||busy)return;
 const task=room.upsolves.find(t=>t.index===form.dataset.index);
 if(!task||task.done)return;
 const data=new FormData(form);
 const submit=form.querySelector('button');
 submit.disabled=true;
 await action(async()=>{
  if(room.mode==='replay'){
   await postRoom('demo-task',{index:task.index,done:true});
  }else{
   await api('/api/tasks/update',{id:task.taskId,done:true,completionMode:data.get('completionMode')});
   await browse({});
  }
  document.querySelector('#reading').close();
  const list=document.querySelector('#upsolves');
  list?.focus({preventScroll:true});list?.scrollIntoView({block:'start'});
 },message=>{form.querySelector('.read-error').textContent=message;});
 submit.disabled=false;
});
function elapsedText(seconds){
 const value=Math.max(0,Math.floor(Number(seconds)||0));
 return `${Math.floor(value/60)} 分 ${String(value%60).padStart(2,'0')} 秒`;
}
function roomReport(r){
 if(r.phase!=='FINISHED'||!r.report)return '';
 const report=r.report, ranks=new Map();
 report.participants.forEach(p=>ranks.set(p.rank,(ranks.get(p.rank)||0)+1));
 return `<section class="box room-report" id="room-report" tabindex="-1" aria-labelledby="report-title"><div class="report-heading"><div><p class="section-kicker">THE ROOM RECAP</p><h2 id="report-title">赛后战报</h2><p class="report-contest">${esc(report.contestName)}</p></div><div class="report-actions"><span class="pill">仅房间成员可见</span><button data-do="print-report">打印战报</button></div></div>
 <div class="report-meta"><span>CF #${esc(report.contestId)}</span><span>${esc(report.participantCount)} 位参赛成员</span><span>${esc(report.durationSeconds/60)} 分钟</span><span>${r.mode==='replay'?'合成事件回放':'CF 实战'}</span></div>
 <div class="report-summary"><span class="report-summary-mark" aria-hidden="true">↗</span><p>${esc(report.summary)}</p></div>
 <div class="report-people">${report.participants.map(p=>`<article class="report-person ${p.rank===1?'report-leader':''}"><div class="row between"><span class="report-rank">${ranks.get(p.rank)>1?'并列第':'第'} ${esc(p.rank)} 名</span><span class="member-avatar" aria-hidden="true">${esc(String(p.handle).slice(0,1).toUpperCase())}</span></div><h3>${esc(p.handle)}</h3><div class="report-score"><strong>${esc(p.solved)}<small> 题</small></strong><span>罚时 <b>${esc(p.penalty)}</b> 分钟</span></div><div class="report-first"><span>首次通过</span><b>${p.firstAccepted?`${esc(p.firstAccepted.indices?.length?p.firstAccepted.indices.join(' / '):p.firstAccepted.index)} · ${elapsedText(p.firstAccepted.elapsedSeconds)}`:'暂无通过'}</b></div></article>`).join('')}</div>
 <div class="report-first-solves"><h3>每题首通</h3>${report.firstSolves?.length?`<div class="first-solve-list">${report.firstSolves.map(p=>`<div class="first-solve"><span class="problem-letter">${esc(p.index)}</span><div><b>${esc(p.handles.join('、'))}</b><small>${elapsedText(p.elapsedSeconds)}${p.handles.length>1?' · 并列首通':''}</small></div></div>`).join('')}</div>`:'<p class="sub quiet">本场尚无通过记录。</p>'}</div>
 <p class="report-footnote">按已结算的房间榜生成 · 同分同罚时并列 · ${r.mode==='replay'?'回放不代表真实 CF 成绩':'房间计分不代表 CF 正式成绩'}</p></section>`;
}
function autoSyncPanel(r){
 if(r.mode!=='training'||!['RUNNING','SETTLING'].includes(r.phase))return '';
 const delayed=r.members.filter(m=>m.syncDelayed), failed=r.members.filter(m=>m.syncError);
 const state=delayed.length||failed.length?'warning':'';
 return `<div class="auto-sync-panel ${state}" role="status"><div class="auto-sync-heading"><span class="sync-dot" aria-hidden="true"></span><b>${delayed.length?'部分成员同步延迟':'房间自动同步已开启'}</b></div><p>后台轮流获取所有成员的 CF 提交，成功后两端榜单自动更新。${delayed.length?'超过 90 秒未成功的成员已标记，榜单保留上次结果。':failed.length?'当前请求未成功，榜单保留上次结果并等待重试。':'无需每位成员手动同步。'}</p><div class="row between"><small>页面每 3 秒刷新本地榜单</small><button data-do="sync">重试我的同步</button></div></div>`;
}
function competing(r){
 const board=`<section class="box"><div class="row between"><h2>房间榜单</h2>${badge(r.mode)}</div><div class="scroll"><table><thead><tr><th>名次</th><th>选手</th><th>过题</th><th>罚时</th>${r.problems.map(p=>`<th>${problemLink(r,p,p.index)}</th>`).join('')}</tr></thead><tbody>
 ${r.standings.map(s=>`<tr><td class="rank">${s.rank}</td><td>${esc(s.handle)}</td><td>${s.solved}</td><td>${s.penalty}</td>${r.problems.map(p=>{const c=s.cells[p.index];return `<td class="${c.acSeconds!==null?'good':c.wrong?'bad':''}">${c.acSeconds!==null?'AC '+Math.floor(c.acSeconds/60)+'′':c.wrong?'−'+c.wrong:'—'}${c.acSeconds!==null&&c.wrong?' (+'+c.wrong+' 罚)':''}${c.pending?' · 待判':''}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div>
 <p class="sub quiet">${r.offlineReading?'点击题号可离线阅读题面与公开样例。':'点击题号可打开 CF 原题。'}并列同名次；榜单每 3 秒从本地服务刷新。</p>
 ${autoSyncPanel(r)}
 ${r.phase==='SETTLING'?'<p class="banner">比赛时间已到，后台正在补齐截止前的提交。所有成员同步完整且没有待判后，会自动结算并生成战报。</p>':''}
 ${r.mode==='replay'&&r.phase==='RUNNING'?`<p class="banner">合成提交回放 · 不访问 CF，不写入个人真实成绩。</p>${r.isOwner?`<div class="actions"><button data-advance="${Math.min(r.selected.durationSeconds,r.elapsedSeconds+300)}">推进 5 分钟</button><button data-advance="${Math.min(r.selected.durationSeconds,Math.max(r.elapsedSeconds,1800))}">推进至 30 分钟</button><button class="primary" data-advance="${r.selected.durationSeconds}">推进至终场并结算</button></div>`:'<small>进度由房主推进。</small>'}`:''}</section>`;
 const tasks=r.phase==='FINISHED'?`<section class="box" id="upsolves" tabindex="-1"><div class="row between"><h2>我的赛后补题</h2><span class="pill">已补 ${r.upsolves.filter(t=>t.done).length} / ${r.upsolves.length}</span></div><p class="sub quiet">${r.mode==='replay'?'回放练习清单':'已加入个人补题计划'}。${r.offlineReading?'点击题名可读题、记录完成情况。':''}</p>
 ${r.upsolves.length?r.upsolves.map(t=>`<div class="tile row between"><div><b>${esc(t.index)}　${problemLink(r,t,t.title)}</b></div>${r.mode==='replay'?`<button data-task="${esc(t.index)}" data-done="${!t.done}">${t.done?'已完成 · 撤销':'标记演示完成'}</button>`:`<span class="pill">${t.done?'自报完成':'待补'}</span>`}</div>`).join(''):'<p>没有待补题目。</p>'}
 ${r.mode==='training'?'<div class="actions"><a class="btn primary" href="/?tab=tasks">打开我的补题计划 →</a></div>':''}</section>`:'';
 return roomReport(r)+board+tasks+memberPanel(r);
}
function rememberRoomUI(){
 const active=document.activeElement;
 let focus=null;
 if(active&&content.contains(active)){
  const actionAttribute=['data-do','data-pick','data-recommend-pick','data-page','data-task','data-read'].find(key=>active.hasAttribute(key));
  focus={id:active.id,name:active.name,formId:active.form?.id,
   actionAttribute,actionValue:actionAttribute?active.getAttribute(actionAttribute):null,
   summary:active.tagName==='SUMMARY'?active.parentElement.id:null};
 }
 return {details:Array.from(content.querySelectorAll('details[id][open]'),d=>d.id),focus};
}
function restoreRoomUI(saved){
 if(!saved)return;
 for(const id of saved.details){const d=document.getElementById(id);if(d&&content.contains(d))d.open=true;}
 const f=saved.focus;if(!f)return;
 const target=Array.from(content.querySelectorAll('button,input,select,summary,a')).find(el=>
  f.id?el.id===f.id:f.actionAttribute?el.getAttribute(f.actionAttribute)===f.actionValue:
  f.name?el.name===f.name&&el.form?.id===f.formId:f.summary?el.tagName==='SUMMARY'&&el.parentElement.id===f.summary:false);
 if(target&&!target.disabled)target.focus({preventScroll:true});
}
function show(r,force=false){
 const sameRoom=!force&&room?.id===r.id&&room.phase===r.phase;
 const sameWaiting=sameRoom&&r.phase==='WAITING';
 room=r;clockBase=r.elapsedSeconds;received=performance.now();
 const key=JSON.stringify({...r,serverNow:null,elapsedSeconds:r.mode==='replay'?r.elapsedSeconds:0});
 if(key===signature&&!force){tick();return;}
 const savedUI=sameRoom?rememberRoomUI():null;
 const oldInput=sameWaiting&&document.querySelector('#contest-query');
 const draft=oldInput?{q:oldInput.value,checked:document.querySelector('#contest-search [name=eligible]').checked,
  focus:document.activeElement===oldInput,start:oldInput.selectionStart,end:oldInput.selectionEnd}:null;
 const prefs=sameWaiting&&document.querySelector('#prefs[open]')&&document.querySelector('#preferences');
 const edits=prefs?{minutes:prefs.elements.maxMinutes.value,allow:prefs.elements.allowSeen.checked}:null;
 signature=key;history.replaceState(null,'','/duo?room='+encodeURIComponent(r.id));
 content.innerHTML=`<section class="hero"><div><h1>${esc(r.selected?.name||'等待选场')}</h1>${badge(r.mode)} <span class="pill">${esc(labels[r.phase])}</span></div><div>${r.phase==='WAITING'?`<p>房间码</p><span class="code" id="invite-code">${esc(r.inviteCode)}</span> <button data-do="copy">复制邀请链接</button>
 <input id="invite-link" readonly aria-label="本机邀请链接" value="${esc(location.origin+'/duo?join='+r.inviteCode)}" hidden>`:`<p>${r.mode==='replay'?'回放时间':'比赛剩余时间'}</p><span class="clock" id="clock"></span>`}</div></section>
 ${r.phase==='CANCELLED'?'<section class="box">房主已取消本次训练。</section>':r.phase==='WAITING'?waiting(r):competing(r)}
 <div class="actions"><button data-do="lobby">返回房间列表</button><button data-do="refresh">刷新房间</button>${r.isOwner&&r.phase==='WAITING'?'<button data-do="cancel">取消房间</button>':''}</div>`;
 restoreRoomUI(savedUI);
 if(draft){const input=document.querySelector('#contest-query');input.value=draft.q;document.querySelector('#contest-search [name=eligible]').checked=draft.checked;
  if(draft.focus){input.focus({preventScroll:true});input.setSelectionRange(draft.start,draft.end);}}
 if(edits){document.querySelector('#prefs').open=true;const p=document.querySelector('#preferences');p.elements.maxMinutes.value=edits.minutes;p.elements.allowSeen.checked=edits.allow;}
 tick();
}
function tick(){
 const el=document.querySelector('#clock');if(!el||!room?.selected)return;
 const d=room.selected.durationSeconds;if(room.mode==='replay'){el.textContent=clockText(clockBase)+' / '+clockText(d);return;}
 el.textContent=clockText(Math.max(0,d-clockBase-(room.phase==='RUNNING'?Math.floor((performance.now()-received)/1000):0)));
}
async function action(fn,onError=say){
 if(busy)return;busy=true;seq++;say('');content.inert=true;content.setAttribute('aria-busy','true');
 try{await fn();}catch(err){onError(err.message);}finally{busy=false;content.inert=false;content.removeAttribute('aria-busy');}
}
async function postRoom(route,body={}){
 const path='/api/rooms/'+room.id, query=pageQuery();
 try{
  const updated=await api(path+'/'+route+query,{version:room.version,...body});
  show(updated,true);
 }catch(error){
  if(error.status===409)show(await api(path+query),true);
  throw error;
 }
}
async function browse(fields){show(await api('/api/rooms/'+room.id+browseQuery(fields)),true);}
content.addEventListener('submit',ev=>{
 ev.preventDefault();const form=ev.target, f=new FormData(form);
 action(async()=>{
 if(form.id==='create')show(await api('/api/rooms',{mode:f.get('mode'),handle:f.get('handle'),maxMinutes:Number(f.get('maxMinutes'))}),true);
 else if(form.id==='join')await invitation(f.get('code'),true);
 else if(form.id==='accept-invite')show(await api('/api/rooms/join',{code:f.get('code'),handle:f.get('handle')}),true);
 else if(form.id==='contest-search')await browse({q:f.get('q').trim(),onlyEligible:f.has('eligible')});
 else if(form.id==='preferences')await postRoom('preferences',{maxMinutes:Number(f.get('maxMinutes')),allowSeen:f.has('allowSeen')});});
});
content.addEventListener('change',ev=>{
 if(ev.target.name==='maxMinutes'&&ev.target.form?.id==='create')
  document.querySelector('#create-minutes').textContent=ev.target.value||'…';
 if(ev.target.name==='mode'&&ev.target.form?.id==='create'){
  const real=ev.target.value==='training',form=ev.target.form;
  document.querySelector('#live-handle').hidden=!real;
  form.elements.handle.disabled=!real;form.elements.handle.required=real;
  form.querySelector('[type=submit]').textContent=real?'创建实战房':'创建回放房';
  return;
 }
 if(ev.target.name==='eligible'&&ev.target.form?.id==='contest-search'){ev.target.form.requestSubmit();return;}
 if(ev.target.id==='event-file'&&ev.target.files[0]){const f=ev.target.files[0];action(async()=>{if(f.size>256000)throw Error('回放文件过大');let data;try{data=JSON.parse(await f.text());}catch{throw Error('请选择有效的 JSON 事件文件');}await postRoom('replay-file',{events:data.events??data});say('回放事件已更换，请全员重新确认。');});}});
content.addEventListener('click',ev=>{
 const b=ev.target.closest('button');if(!b||b.disabled||b.type==='submit'&&b.form)return;
 action(async()=>{
 if(b.dataset.open){show(await api('/api/rooms/'+b.dataset.open),true);return;}
 if(b.dataset.read)return readProblem(b.dataset.read);
 if(b.dataset.page)return browse({...room.candidatePage,page:Number(b.dataset.page)});
 if(b.dataset.clear)return browse({});
 if(b.dataset.pick||b.dataset.recommendPick){
  const choice={contestId:Number(b.dataset.pick||b.dataset.recommendPick),confirmOwner:true};
  if(b.dataset.minutes)choice.extendToMinutes=Number(b.dataset.minutes);
  await postRoom('select',choice);
  const status=document.querySelector('#ready-status');
  status.focus({preventScroll:true});status.scrollIntoView({block:'nearest'});
  return;
 }
 if(b.dataset.advance){await postRoom('replay',{elapsedSeconds:Number(b.dataset.advance)});return;}
 if(b.dataset.task){await postRoom('demo-task',{index:b.dataset.task,done:b.dataset.done==='true'});return;}
 const act=b.dataset.do;
 if(act==='print-report'){window.print();return;}
 if(act==='lobby')return lobby();
 if(act==='refresh')return show(await api('/api/rooms/'+room.id+pageQuery()),true);
 if(act==='copy'){
  const field=document.querySelector('#invite-link');
  try{await navigator.clipboard.writeText(field.value);say('邀请链接已复制，可在本机的另一个浏览器会话打开。');}
  catch{field.hidden=false;say('请复制房间码下方的邀请链接。');}
  return;
 }
 if(act==='cancel'&&!window.confirm('取消这个等待中的房间？'))return;
 if(['ready','start','sync','cancel'].includes(act)){if(act==='sync')say('正在向 CF 同步本人的本场记录…');await postRoom(act);say('');}
 });
});
setInterval(tick,500);
async function refreshVisibleRoom(event){
 const resumed=event?.type==='focus'||event?.type==='visibilitychange';
 if(!uid||!room||busy||polling||document.hidden||room.phase==='CANCELLED'||(room.phase==='FINISHED'&&!resumed))return;
 const token=seq,id=room.id;polling=true;
 try{const r=await api('/api/rooms/'+id+pageQuery());if(seq===token&&!busy&&room?.id===id)show(r);}
 catch(err){if(seq===token)say(err.message);}finally{polling=false;}
}
setInterval(refreshVisibleRoom,3000);
document.addEventListener('visibilitychange',refreshVisibleRoom);
window.addEventListener('focus',refreshVisibleRoom);
(async()=>{try{const m=await api('/api/me');if(!m.user)return signedOut();uid=m.user.id;document.querySelector('#who').textContent=m.user.username;
 const params=new URLSearchParams(location.search), id=params.get('room'), code=params.get('join');
 if(code){try{return await invitation(code);}catch(err){if(!uid)return;await lobby();say(err.message);return;}}
 if(id&&/^[a-f0-9]{20}$/.test(id)){try{return show(await api('/api/rooms/'+id),true);}catch(err){if(!uid)return;await lobby();say(err.message);return;}}
 await lobby();}catch(err){say(err.message);}})();
