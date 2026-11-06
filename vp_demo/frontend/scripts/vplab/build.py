"""Build and publish the Vben frontend. Run from any working directory."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

frontend = Path(__file__).resolve().parents[2]
subprocess.run([sys.executable, str(Path(__file__).with_name('prepare.py'))], check=True)
pnpm = os.environ.get('VPLAB_PNPM', 'pnpm')
command = [pnpm] if not pnpm.endswith('.cjs') else ['node', pnpm]
subprocess.run(command + ['-F', '@vben/web-antd', 'exec', 'vite', 'build', '--mode', 'production'], cwd=frontend, check=True)
source = frontend / 'apps/web-antd/dist'
target = frontend.parent / 'static/ui'
if not (source / 'index.html').is_file():
    raise RuntimeError('Vite did not generate index.html')
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
shutil.copyfile(frontend / 'LICENSE', target / 'VBEN-LICENSE.txt')
print(f'Published frontend to {target}')
