'use strict';
const jobKey=id=>'capture:'+id;
function sameProblem(url,expected){try{const a=new URL(url),b=new URL(expected);return a.origin==='https://codeforces.com'&&a.origin===b.origin&&a.pathname.replace(/\/$/,'')===b.pathname;}catch{return false;}}
async function sendResult(job,payload){
 const response=await fetch(job.base+'/api/browser/result',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json','X-VPLab-Token':job.token},body:JSON.stringify({id:job.id,...payload}),signal:AbortSignal.timeout(25000)});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'VP Lab 未接受题面。');return result;
}
async function handle(message,sender){
 if(message.type==='START'){
  const origin=new URL(sender.url||'');
  if(origin.protocol!=='http:'||origin.hostname!=='127.0.0.1'||sender.frameId!==0)throw new Error('请从本地 VP Lab 发起。');
  const j=message.job;
  if(!j||!/^https:\/\/codeforces\.com\/(contest|gym)\/[1-9]\d*\/problem\/[A-Za-z][A-Za-z0-9]*$/.test(j.url)||!/^[-\w]{20,80}$/.test(j.token)||!/^[-\w]{10,60}$/.test(j.id)||j.expiresAt*1000<=Date.now()||j.expiresAt*1000>Date.now()+660000)throw new Error('获取任务无效或已过期。');
  // Store before navigating so the CF content script can immediately find its job.
  const tab=await chrome.tabs.create({url:'about:blank',active:true});
  const job={id:j.id,token:j.token,url:j.url,expiresAt:j.expiresAt,base:origin.origin};
  await chrome.storage.session.set({[jobKey(tab.id)]:job});
  await chrome.tabs.update(tab.id,{url:j.url+'?locale=en'});
  return {ok:true};
 }
 const id=sender.tab?.id;
 if(id==null||sender.frameId!==0)throw new Error('没有可用采集任务。');
 const key=jobKey(id),job=(await chrome.storage.session.get(key))[key];
 if(!job)return {ok:false,inactive:true};
 if(job.expiresAt*1000<=Date.now()){await chrome.storage.session.remove(key);return {ok:false,error:'任务已过期，请回到 VP Lab 重新获取。'};}
 if(!sameProblem(sender.url,job.url))throw new Error('当前页面与任务原题不一致。');
 if(message.type==='GET_JOB')return {ok:true,url:job.url,expiresAt:job.expiresAt};
 if(message.type==='CAPTURE'){
  if(typeof message.html!=='string'||new TextEncoder().encode(message.html).length>1000000)throw new Error('题面过大，未回传。');
  await sendResult(job,{url:sender.url,html:message.html,formulaWarning:message.formulaWarning===true});
  await chrome.storage.session.remove(key);
  return {ok:true};
 }
 throw new Error('未知助手操作。');
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
 handle(message,sender).then(reply).catch(error=>reply({ok:false,error:error.message||'助手请求失败，请返回 VP Lab 重试。'}));return true;
});
chrome.tabs.onRemoved.addListener(async id=>{
 const key=jobKey(id),job=(await chrome.storage.session.get(key))[key];
 if(job){await chrome.storage.session.remove(key);try{await sendResult(job,{failure:true});}catch{/* The local service may already be closed. */}}
});
