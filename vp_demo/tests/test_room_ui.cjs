// Exercise the room's actual rendering and focus restoration without a browser.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const elements = new Map();
const listeners = new Map();
function element(id) {
 if(!elements.has(id)) elements.set(id, {
  id:id.replace(/^#/,''),innerHTML:'',textContent:'',hidden:false,
  addEventListener(type,fn){listeners.set(id+':'+type,fn);},
  querySelectorAll(){return [];},contains(){return false;},
  setAttribute(){},removeAttribute(){},focus(){},scrollIntoView(){},
 });
 return elements.get(id);
}
const context = vm.createContext({assert,console,URLSearchParams,Date,Map,Array,
 performance:{now:()=>100},setInterval(){},
 history:{replaceState(){}},location:{origin:'http://127.0.0.1:8765',search:''},
 window:{addEventListener(){},print(){}},
 document:{querySelector:element,getElementById:id=>element('#'+id),
  querySelectorAll:()=>[],addEventListener(){},activeElement:null},
});
const source = readFileSync(join(__dirname,'../static/duo.js'),'utf8');
vm.runInContext(source.slice(0,source.lastIndexOf('(async()=>{try{const m=')),context);
vm.runInContext(`
const recommendation={contestId:42,name:'Round <unsafe>',durationSeconds:7200,
 problemCount:6,reason:'整场适合 <队友>，没有题目剧透',fitLabel:'均衡 <训练>',
 tags:['private-tag-sentinel'],problems:[{title:'private-problem-sentinel',rating:4000}]};
const waitingRoom={id:'room',isOwner:true,phase:'WAITING',selected:null,
 recommendations:[recommendation,{...recommendation,contestId:43},{...recommendation,contestId:44},{...recommendation,contestId:45}],
 recommendationNote:'依据 <记录> 推荐'};
const choices=recommendationPanel(waitingRoom);
assert.equal((choices.match(/data-recommendation=/g)||[]).length,3);
assert.equal((choices.match(/data-recommend-pick=/g)||[]).length,3);
assert(choices.includes('Round &lt;unsafe&gt;'));
assert(choices.includes('&lt;队友&gt;'));assert(choices.includes('&lt;记录&gt;'));
assert(!choices.includes('data-pick='));
assert(!choices.includes('private-tag-sentinel'));assert(!choices.includes('private-problem-sentinel'));
assert(!recommendationPanel({...waitingRoom,isOwner:false}).includes('data-recommend-pick='));
assert(recommendationPanel({...waitingRoom,recommendations:[recommendation]}).includes('1 场推荐'));
assert(recommendationPanel({...waitingRoom,recommendations:[]}).includes('暂时没有合适的比赛'));
assert(recommendationPanel({...waitingRoom,selected:{contestId:42}}).includes('已选本场'));

const member={slot:1,handle:'<alpha>',isMe:true,ready:true,syncedAt:1800000000,
 syncDelayed:true,syncing:false,syncError:'网络 <中断>'};
const liveRoom={mode:'training',phase:'RUNNING',members:[member],maxMembers:3};
const health=memberPanel(liveRoom);
assert(health.includes('同步延迟'));assert(health.includes('最后成功：'));
assert(health.includes('已超过 90 秒'));assert(health.includes('保留上次成功成绩'));
assert(health.includes('&lt;alpha&gt;'));assert(health.includes('&lt;中断&gt;'));
assert.equal(memberSyncState({...member,syncing:true}).label,'同步延迟');
assert.equal(memberSyncState({...member,syncDelayed:false,syncing:true}).label,'同步中');
assert.equal(memberSyncState({...member,syncDelayed:false,syncedAt:null}).label,'等待首轮');
assert(autoSyncPanel(liveRoom).includes('无需每位成员手动同步')===false);
assert(autoSyncPanel(liveRoom).includes('超过 90 秒未成功'));
assert(autoSyncPanel({...liveRoom,members:[{...member,syncDelayed:false,syncError:''}]}).includes('无需每位成员手动同步'));
assert.equal(autoSyncPanel({...liveRoom,mode:'replay'}),'');
assert.equal(autoSyncPanel({...liveRoom,phase:'FINISHED'}),'');

const firstAccepted={index:'A',indices:['A','B'],elapsedSeconds:1200};
const report={contestId:42,contestName:'Round <unsafe>',durationSeconds:7200,mode:'replay',
 settledAt:1800007200,participantCount:2,
 participants:[{slot:1,handle:'<alpha>',rank:1,solved:1,penalty:40,firstAccepted},
 {slot:2,handle:'beta',rank:1,solved:1,penalty:40,firstAccepted:{index:'C',elapsedSeconds:2400}}],
 summary:'<alpha> 与 beta 并列第一。',
 firstSolves:[{index:'A',elapsedSeconds:1200,handles:['<alpha>','beta']}]};
const recap=roomReport({phase:'FINISHED',mode:'replay',report});
assert.equal((recap.match(/并列第 1 名/g)||[]).length,2);
assert(recap.includes('A / B · 20 分 00 秒'));
assert(recap.includes('C · 40 分 00 秒'));
assert(recap.includes('罚时 <b>40</b> 分钟'));
assert(recap.includes('并列首通'));assert(recap.includes('仅房间成员可见'));
assert(recap.includes('&lt;alpha&gt;'));assert(!recap.includes('<alpha>'));
assert(recap.includes('回放不代表真实 CF 成绩'));
assert.equal(roomReport({phase:'RUNNING',report}),'');
assert.equal(roomReport({phase:'FINISHED'}),'');
const zero=roomReport({phase:'FINISHED',mode:'training',report:{...report,
 participants:[{slot:1,handle:'solo',rank:1,solved:0,penalty:0,firstAccepted:null}],firstSolves:[]}});
assert(zero.includes('暂无通过'));assert(zero.includes('本场尚无通过记录'));
assert(!zero.includes('class="report-rank">并列第'));
`,context);

// A background standings refresh must not close details or steal keyboard focus.
const content = element('#content'), details=element('#room-rules');
const oldButton={id:'',name:'',tagName:'BUTTON',dataset:{do:'sync'},
 hasAttribute:key=>key==='data-do',getAttribute:key=>key==='data-do'?'sync':null};
const nextButton={...oldButton,focus(options){this.focused=options.preventScroll;}};
content.contains=()=>true;
content.querySelectorAll=selector=>selector==='details[id][open]'?[details]:[oldButton];
context.document.activeElement=oldButton;
vm.runInContext('const savedRoomUI=rememberRoomUI();',context);
details.open=false;
content.querySelectorAll=()=>[nextButton];
vm.runInContext('restoreRoomUI(savedRoomUI);',context);
assert.equal(details.open,true);
assert.equal(nextButton.focused,true);

async function run(){
 // A recommendation uses the same guarded select request as a catalog choice.
 vm.runInContext(`
let selectedChoice,finishedClick;
action=fn=>{finishedClick=fn();};
postRoom=async(route,body)=>{selectedChoice={route,body};};
`,context);
 const choice={disabled:false,type:'button',dataset:{recommendPick:'42'}};
 listeners.get('#content:click')({target:{closest:()=>choice}});
 await vm.runInContext('finishedClick',context);
 vm.runInContext("assert.equal(selectedChoice.route,'select');assert.equal(selectedChoice.body.contestId,42);assert.equal(selectedChoice.body.confirmOwner,true);",context);
 // Older room requests cannot overwrite a different room after navigating away.
 vm.runInContext(`
uid=1;seq=5;busy=false;polling=false;room={id:'old-room',phase:'RUNNING'};
let finishOldRoom;api=()=>new Promise(resolve=>finishOldRoom=resolve);
const oldRefresh=refreshVisibleRoom();
room={id:'new-room',phase:'WAITING'};seq++;
finishOldRoom({id:'old-room',phase:'RUNNING'});
`,context);
 await vm.runInContext('oldRefresh',context);
 vm.runInContext("assert.equal(room.id,'new-room');assert.equal(polling,false);",context);
 console.log('Room UI checks passed: recommendations, escaping, report ties/first AC, sync health, focus and stale-request isolation.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
