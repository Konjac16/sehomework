"""Explicit, pinned download for the optional local recommendation encoder."""
import os
import hashlib
from pathlib import Path
import urllib.request

REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
BASE = f'https://huggingface.co/intfloat/multilingual-e5-small/resolve/{REVISION}/'
HASHES = {'model.onnx':'dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88',
          'tokenizer.json':'0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39'}


def verify(path, name):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=HASHES[name]:
        raise ValueError(f'{name} does not match the pinned model checksum')


def main():
    target=Path(os.environ.get('VPLAB_EMBEDDING_MODEL',Path(__file__).parent/'.local/models/multilingual-e5-small'))
    target.mkdir(parents=True,exist_ok=True)
    for name,remote in [('model.onnx','onnx/model_qint8_avx512_vnni.onnx'),('tokenizer.json','tokenizer.json')]:
        path=target/name
        if path.is_file():
            verify(path,name)
            print(f'Keeping existing {name}',flush=True)
            continue
        temporary=path.with_suffix(path.suffix+'.download')
        print(f'Downloading {name} from pinned revision {REVISION}',flush=True)
        try:
            with urllib.request.urlopen(BASE+remote,timeout=90) as response,temporary.open('wb') as output:
                while chunk:=response.read(1024*1024):output.write(chunk)
            verify(temporary,name)
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    print(f'Model ready in {target}',flush=True)


if __name__=='__main__':main()
