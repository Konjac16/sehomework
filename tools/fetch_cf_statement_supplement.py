"""Acquire the public September 2026 supplemental CSV and its accompanying license, pinned to a commit."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from fetch_cf_standings import atomic,js,stamp

REVISION = "cdbad90365bba6ddba3fd28dc25b93fe8c596b23"


def get(url,limit):
    with urlopen(Request(url,headers={"User-Agent":"VPLab-course-archive/1.0","Accept-Encoding":"identity"}),timeout=120) as f:
        data=f.read(limit+1)
    if len(data)>limit:raise ValueError("Source exceeds expected size")
    return data


def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True,type=Path);p.add_argument("--revision", default=REVISION);args=p.parse_args()
    out=args.out/"statements"/"ronin";out.mkdir(parents=True,exist_ok=True);repo="Ronin5205/Codeforces-Problemset-Statements"
    revision=args.revision
    base=f"https://raw.githubusercontent.com/{repo}/{revision}/"
    manifest={"repository":repo,"revision":revision,"fetchedAt":stamp(),"files":{}}
    for name,limit in (("LICENSE",100000),("README.md",1000000),("problems.csv",160*1024**2)):
        data=get(base+name,limit)
        file=name+".gz" if name.endswith(".csv") else name
        atomic(out/file,gzip.compress(data,compresslevel=6,mtime=0) if file.endswith(".gz") else data)
        manifest["files"][name]={"url":base+name,"file":file,"bytes":len(data),"sha256Decoded":hashlib.sha256(data).hexdigest()}
    atomic(out/"manifest.json",js(manifest));print(json.dumps(manifest,indent=2))

if __name__=="__main__":main()
