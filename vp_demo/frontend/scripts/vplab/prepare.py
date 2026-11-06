"""Prepare maintained VP modules for the Vben workspace; no remote runtime assets."""
from pathlib import Path
import re, shutil
root=Path(__file__).resolve().parents[3]
public=root/'frontend/apps/web-antd/public'
out=public/'workspace';out.mkdir(parents=True,exist_ok=True)
static=root/'static'
html=(static/'index.html').read_text().split('<body>',1)[1].split('</body>',1)[0]
html=re.sub(r'<script[\s\S]*?</script>','',html)
links=''.join(f'<link rel="stylesheet" href="/workspace/{name}">' for name in ['style.css','review.css','training.css','intelligent.css','theme.css'])+'<link rel="stylesheet" href="/vendor/katex/katex.min.css">'
(out/'index.html').write_text(links+html.strip()+'\n')
for name in ['style.css','review.css','training.css','intelligent.css']:
 s=(static/name).read_text().replace(':root',':host').replace('body {',':host {')
 (out/name).write_text(s)
for name in ['helper','statement-format','statement','review','source','training','smart','intelligent','app']:
 s=re.sub(r'\bdocument(?=\s*[.)])','window.vpDocument',(static/(name+'.js')).read_text())
 if name=='app':
  s=re.sub(r'startApplication\(\);\s*$','',s)
  s+='''\nconst vpOriginalNavigate=navigate;
window.vpNavigate=(tab)=>{if(state.tab!==tab||!$('#main').innerHTML){state.tab=tab;if(state.data)render();}};
navigate=(tab)=>{vpOriginalNavigate(tab);window.dispatchEvent(new CustomEvent('vp:navigate',{detail:tab}));};
window.vpReady=startApplication();
'''
 (out/(name+'.js')).write_text(s)
shutil.copytree(static/'vendor',public/'vendor',dirs_exist_ok=True)
(public/'vp-logo.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40"><rect width="40" height="40" rx="10" fill="#0960bd"/><path d="m10 12 7 16 13-16" fill="none" stroke="white" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>')
shutil.copyfile(Path(__file__).with_name('theme.css'),out/'theme.css')
