// Existing source/review workflows run inside an isolated root while Vben owns navigation.
// No iframe, eval or remote scripts: assets are generated from the maintained local modules.
let host:HTMLElement; let ready:Promise<void>;
export async function mountWorkspace(target:HTMLElement){
 if(!host){host=document.createElement('div');const root=host.attachShadow({mode:'open'});
 ready=(async()=>{const response=await fetch('/workspace/index.html');if(!response.ok)throw new Error('工作台资源加载失败');
 const html=await response.text();root.innerHTML=html;
 const native=document;
 (window as any).vpDocument=new Proxy(root,{get(obj,key){if(key==='hidden')return native.hidden; const owner=key in obj?obj:native;const value=Reflect.get(owner,key);return typeof value==='function'?value.bind(owner):value;}});
 for(const file of ['vendor/katex/katex.min.js','workspace/helper.js','workspace/statement-format.js','workspace/statement.js','workspace/review.js','workspace/source.js','workspace/training.js','workspace/smart.js','workspace/intelligent.js','workspace/app.js']){
 await new Promise<void>((resolve,reject)=>{const script=native.createElement('script');script.src='/'+file;script.onload=()=>resolve();script.onerror=()=>reject(new Error('加载失败：'+file));native.head.append(script);});
 }
 await (window as any).vpReady;
 })();}
 target.append(host);await ready;return (window as any).vpNavigate as (tab:string)=>void;
}
