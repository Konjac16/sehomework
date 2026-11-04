'use strict';
// Format stored text only. Never insert HTML obtained from CF into the page.
function renderStatementText(text){
 let failed=0;
 function inline(value){
  const pattern=/\$\$\$[\s\S]*?\$\$\$|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)/g;
  let result='',cursor=0;
  for(const match of value.matchAll(pattern)){
   result+=e(value.slice(cursor,match.index));const raw=match[0];
   const width=raw.startsWith('$$$')?3:2,formula=raw.slice(width,-width);
   try{
    if(typeof katex==='undefined'||formula.length>5000)throw new Error('Formula unavailable');
    result+=katex.renderToString(formula,{displayMode:raw.startsWith('\\[')||(raw.startsWith('$$')&&!raw.startsWith('$$$')),throwOnError:true,trust:false,strict:'ignore',maxExpand:500,maxSize:12,output:'htmlAndMathml'});
   }catch{failed++;result+=`<code class="statement-math-fallback" title="此公式未能排版，保留原文">${e(raw)}</code>`;}
   cursor=match.index+raw.length;
  }
  return result+e(value.slice(cursor));
 }
 const lines=String(text).replace(/\r\n?/g,'\n').split('\n');let i=0,html='';
 const skip=()=>{while(i<lines.length&&!lines[i].trim())i++;};skip();
 if(/^[A-Z]\d*\.\s/.test(lines[i]||'')){html+=`<h3 class="statement-title">${inline(lines[i++].trim())}</h3>`;}
 const labels={'time limit per test':'时间限制','memory limit per test':'内存限制','input':'输入','output':'输出'};
 const facts=[];
 // These labels only denote metadata when followed by a nonblank value near the title.
 while(facts.length<4){skip();const label=lines[i]?.trim().toLowerCase();
  if(!labels[label]||(!facts.length&&label!=='time limit per test'))break;
  i++;skip();if(i>=lines.length)break;
  facts.push(`<div><dt>${labels[label]}</dt><dd>${e(lines[i++].trim())}</dd></div>`);
 }
 if(facts.length)html+=`<dl class="statement-facts">${facts.join('')}</dl>`;
 let paragraph=[],sample=false,sampleLines=null;
 const flushParagraph=()=>{if(paragraph.length){html+=`<p>${inline(paragraph.join('\n'))}</p>`;paragraph=[];}};
 const flushSample=()=>{if(sampleLines!==null){while(sampleLines.length&&!sampleLines[0].trim())sampleLines.shift();while(sampleLines.length&&!sampleLines.at(-1).trim())sampleLines.pop();html+=`<pre class="statement-sample"><code>${e(sampleLines.join('\n'))}</code></pre>`;sampleLines=null;}};
 for(;i<lines.length;i++){
  const line=lines[i],heading=line.trim();
  if(/^(Input|Output|Examples?|Notes?|Interaction|Scoring|Explanation)$/i.test(heading)){
   flushParagraph();flushSample();
   if(/^Examples?$/i.test(heading))sample=true;
   else if(!/^(Input|Output)$/i.test(heading))sample=false;
   html+=`<h4 class="statement-section-title">${e(heading)}</h4>`;
   if(sample&&/^(Input|Output)$/i.test(heading))sampleLines=[];
  }else if(sampleLines!==null)sampleLines.push(line);
  else if(!heading)flushParagraph();
  else paragraph.push(line);
 }
 flushParagraph();flushSample();
 const warning=failed?'<p class="statement-render-note">部分公式未能排版，已保留原文供核对。</p>':'';
 return `<article class="statement-document">${warning}${html}</article>`;
}
