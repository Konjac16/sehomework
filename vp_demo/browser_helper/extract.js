'use strict';
function extractVPLabStatement(root){
 const clone=root.cloneNode(true);let formulaWarning=false;
 // MathJax 2 retains its original TeX scripts; prefer these to rendered glyphs.
 clone.querySelectorAll('script[type^="math/tex"]').forEach(node=>node.replaceWith(document.createTextNode(' $$$'+node.textContent+'$$$ ')));
 clone.querySelectorAll('mjx-container').forEach(node=>{
  const tex=node.querySelector('annotation[encoding="application/x-tex"]')?.textContent||node.getAttribute('data-tex');
  if(tex)node.replaceWith(document.createTextNode(' $$$'+tex+'$$$ '));
  else{formulaWarning=true;node.replaceWith(document.createTextNode(' [公式可见文本：'+node.textContent+'] '));}
 });
 // Remove rendered MathJax 2 duplicates only when the original TeX exists.
 if(root.querySelector('script[type^="math/tex"]'))clone.querySelectorAll('.MathJax,.MathJax_Display,.MathJax_Preview,.MathJax_SVG,.MathJax_SVG_Display').forEach(node=>node.remove());
 else if(clone.querySelector('.MathJax,.MathJax_SVG'))formulaWarning=true;
 clone.querySelectorAll('script,style,button,.input-output-copier').forEach(node=>node.remove());
 clone.querySelectorAll('pre').forEach(pre=>{
  const lines=[...pre.querySelectorAll('.test-example-line')];
  if(lines.length)pre.textContent=lines.map(line=>line.textContent).join('\n');
 });
 return {html:clone.outerHTML,formulaWarning};
}
