const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const katex=require('../static/vendor/katex/katex.min.js');
const e=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx=vm.createContext({katex,e});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/statement-format.js'),'utf8'),ctx);
function render(text){ctx.sample=text;return vm.runInContext('renderStatementText(sample)',ctx);}
const html=render('E2. N-MEX (Counting Version)\n\ntime limit per test\n2 seconds\n\nmemory limit per test\n256 megabytes\n\ninput\nstandard input\n\noutput\nstandard output\n\nCompute $$$0 \\leq b_i \\leq n$$$ and $$$\\sum_{i=1}^n a_i$$$.\n\nInput\nAn integer $$$n$$$.\n\nOutput\nPrint the answer.\n\nExample\nInput\n2\n3 4\n\nOutput\n7\n\nNote\nUse $$$n^2$$$.');
assert(html.includes('class="statement-facts"'));
assert.equal((html.match(/<dt>/g)||[]).length,4);
assert.equal((html.match(/class="katex"/g)||[]).length,4);
assert(html.includes('<pre class="statement-sample"><code>2\n3 4</code></pre>'));
assert(!html.includes('statement-render-note'));
assert(render('Hello <script>bad()</script> $$$x_1$$$').includes('&lt;script&gt;'));
assert(!render('$$$\\href{javascript:alert(1)}{click}$$$').includes('href="javascript:'));
assert(render('$$$\\notARealCommand{x}$$$').includes('statement-math-fallback'));
assert(render('$$\\frac{1}{2}$$').includes('katex-display'));
assert(render('\\(n^2\\)').includes('class="katex"'));
assert(render('Example\nInput\n$$$literal$$$\nOutput\n<unsafe>').includes('<code>$$$literal$$$</code>'));
// Every CSS font reference must exist in the offline distribution.
const css=fs.readFileSync(path.join(__dirname,'../static/vendor/katex/katex.min.css'),'utf8');
for(const match of css.matchAll(/url\(([^)]+)\)/g))assert(fs.existsSync(path.join(__dirname,'../static/vendor/katex',match[1])));
console.log('Statement render checks passed: real KaTeX, compact metadata, samples, escaping, fallback and offline fonts.');
