'use strict';
window.addEventListener('message',async event=>{
 if(event.source!==window||event.origin!==location.origin||event.data?.channel!=='vplab-helper-request')return;
 if(!document.querySelector('#boot-status')||!document.querySelector('#shell'))return;
 const {nonce,type,job}=event.data;if(typeof nonce!=='string'||nonce.length>80)return;
 let result;
 try{result=type==='PING'?{ok:true,version:chrome.runtime.getManifest().version}:type==='START'?await chrome.runtime.sendMessage({type,job}):{ok:false,error:'未知操作'};}
 catch{result={ok:false,error:'助手已更新或断开，请刷新 VP Lab 页面。'};}
 window.postMessage({channel:'vplab-helper-response',nonce,...result},location.origin);
});
