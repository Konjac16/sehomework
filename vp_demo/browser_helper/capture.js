'use strict';
(async()=>{
 let job;
 try{job=await chrome.runtime.sendMessage({type:'GET_JOB'});}catch{return;}
 if(!job?.ok)return; // Ordinary CF tabs are never collected.
 const badge=document.createElement('div');
 Object.assign(badge.style,{position:'fixed',bottom:'18px',right:'18px',zIndex:'2147483647',background:'#263d2f',color:'#fff',padding:'14px 18px',borderRadius:'10px',maxWidth:'380px',font:'14px/1.6 sans-serif',boxShadow:'0 4px 20px #0003'});
 badge.textContent='VP Lab：等待题面。若出现人机验证，请手动完成；正常显示后自动回传。';
 document.documentElement.appendChild(badge);
 let sending=false;
 async function scan(){
  if(sending)return;
  if(Date.now()>job.expiresAt*1000){badge.textContent='VP Lab：等待已超时，请返回训练空间重新获取。';return;}
  const root=document.querySelector('.problem-statement');
  if(!root){setTimeout(scan,1800);return;}
  sending=true;badge.textContent='VP Lab：正在回传题面…';
  try{
   const result=await chrome.runtime.sendMessage({type:'CAPTURE',...extractVPLabStatement(root)});
   if(!result?.ok)throw new Error(result?.error||'任务已结束，请返回 VP Lab 重新获取。');
   badge.textContent='VP Lab：题面已保存。可以返回训练空间查看和进行 AI 分析。';
  }catch(error){
   badge.textContent='VP Lab：'+error.message+' ';const retry=document.createElement('button');retry.textContent='重试回传';retry.onclick=()=>{sending=false;scan();};badge.appendChild(retry);
  }
 }
 setTimeout(scan,800);
})();
