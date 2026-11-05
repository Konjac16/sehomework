"""Read public statement/example columns from a pinned Open-R1 Parquet dataset with HTTP ranges.
PyArrow is needed only for collection. No official/generated tests, solutions, or editorials are selected.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from fetch_cf_standings import atomic, js, stamp

COLUMNS = ("id","aliases","contest_id","index","title","description","input_format","output_format",
           "interaction_format","note","examples","time_limit","memory_limit")
REPOSITORY = "open-r1/codeforces"
REVISION = "fbe3f6e903ee854eec2e69e9d96d0306cde59baf"


def shard_list(root):
    path = root / "statement-shards.json"
    if path.is_file():
        source = json.loads(path.read_text("utf-8"))
        if (source["repository"], source["revision"]) != (REPOSITORY, REVISION):
            raise ValueError("Unexpected dataset or revision in the shard list")
        return source
    url = f"https://huggingface.co/api/datasets/{REPOSITORY}/tree/{REVISION}/data"
    request = Request(url, headers={"User-Agent": "VPLab-course-archive/1.0"})
    with urlopen(request, timeout=30) as response:
        listing = json.load(response)
    files = [{"path": f["path"], "size": f["size"]} for f in listing if f["path"].endswith(".parquet")]
    if len(files) != 12:
        raise ValueError("Incomplete list for pinned release")
    source = {"repository": REPOSITORY, "revision": REVISION, "source": url, "files": files}
    atomic(path, js(source))
    return source


class RangeFile(io.RawIOBase):
    def __init__(self,url,size):
        super().__init__()
        self.url,self.size,self.pos=url,size,0
        self.segments=[]
        self.transferred=0
        self.calls=0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self.pos=(0 if whence==0 else self.pos if whence==1 else self.size)+offset
        if self.pos<0:raise ValueError("Invalid seek")
        return self.pos
    def _range(self,start,end):
        for lo,hi,data in self.segments:
            if lo<=start and end<=hi:return data[start-lo:end-lo]
        for attempt in range(4):
            try:
                req=Request(self.url,headers={"User-Agent":"VPLab-course-archive/1.0","Range":f"bytes={start}-{end-1}","Accept-Encoding":"identity"})
                with urlopen(req,timeout=45) as r:
                    cr=r.headers.get("Content-Range","")
                    if r.status!=206 or cr!=f"bytes {start}-{end-1}/{self.size}":
                        raise ValueError(f"Exact HTTP byte range unavailable: status={r.status}, range={cr}")
                    data=r.read(end-start+1)
                if len(data)!=end-start:raise OSError("short range read")
                self.calls+=1;self.transferred+=len(data);self.segments.append((start,end,data))
                return data
            except (OSError,URLError,HTTPError) as exc:
                if attempt==3:raise RuntimeError("Public Parquet range request failed: "+str(getattr(exc,'code',type(exc).__name__))) from None
                time.sleep(2**(attempt+1))
    def read(self,n=-1):
        end=self.size if n<0 else min(self.size,self.pos+n)
        if end<=self.pos:return b""
        start=self.pos;self.pos=end
        return self._range(start,end)
    def readinto(self,b):
        data=self.read(len(b));b[:len(data)]=data;return len(data)


def extract(root,source,item):
    import pyarrow.parquet as pq
    filename=Path(item["path"]).stem+".jsonl.gz"
    output=root/"statements"/"openr1"/filename
    meta=output.with_suffix(".meta.json")
    if output.is_file() and meta.is_file():
        record=json.loads(meta.read_text("utf-8"))
        if record.get("revision")==source["revision"] and hashlib.sha256(output.read_bytes()).hexdigest()==record["sha256"]:
            return record
    output.parent.mkdir(parents=True,exist_ok=True)
    url=f"https://huggingface.co/datasets/{source['repository']}/resolve/{source['revision']}/{item['path']}"
    remote=RangeFile(url,item["size"])
    pf=pq.ParquetFile(remote,pre_buffer=False)
    columns=[c for c in COLUMNS if c in pf.schema_arrow.names]
    for required in ("id","description","examples","contest_id","index"):
        if required not in columns:raise ValueError("Missing required statement column: "+required)
    temp=output.with_suffix(".gz.part")
    count=0;footer=list(remote.segments)
    with temp.open("wb") as raw, gzip.GzipFile(fileobj=raw,mode="wb",compresslevel=6,mtime=0) as target:
        for n in range(pf.num_row_groups):
            remote.segments=list(footer)
            group=pf.metadata.row_group(n)
            spans=[]
            for i in range(group.num_columns):
                col=group.column(i)
                if col.path_in_schema.split('.')[0] not in columns:continue
                start=min(v for v in (col.data_page_offset,col.dictionary_page_offset) if v is not None and v>=0)
                spans.append((start,start+col.total_compressed_size))
            merged=[]
            for start,end in sorted(spans):
                if merged and start<=merged[-1][1]+32:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
                else:merged.append((start,end))
            for lo,hi in merged:remote._range(lo,hi)
            for row in pf.read_row_group(n,columns=columns,use_threads=False).to_pylist():
                target.write(js(row)+b"\n");count+=1
    if count!=pf.metadata.num_rows:raise ValueError("Row count mismatch")
    os.replace(temp,output)
    result=dict(provider=source["repository"],revision=source["revision"],url=url,upstreamFile=item["path"],
                file=str(output.relative_to(root)).replace(os.sep,"/"),rows=count,columns=columns,
                upstreamBytes=item["size"],transferredBytes=remote.transferred,rangeRequests=remote.calls,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest(),fetchedAt=stamp())
    atomic(meta,js(result));return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument("--out",required=True,type=Path)
    ap.add_argument("--libs",type=Path);ap.add_argument("--jobs",type=int,default=2)
    args=ap.parse_args();root=args.out.resolve()
    if args.libs:sys.path.insert(0,str(args.libs.resolve()))
    source=shard_list(root)
    files=source["files"];records=[];errors=[]
    def progress(status):
        atomic(root/"statements-openr1-progress.json",js(dict(status=status,updatedAt=stamp(),target=len(files),
            files=len(records),rows=sum(r["rows"] for r in records),transferredBytes=sum(r["transferredBytes"] for r in records),errors=errors)))
    progress("running")
    with ThreadPoolExecutor(max_workers=min(3,max(1,args.jobs))) as pool:
        futures={pool.submit(extract,root,source,x):x["path"] for x in files}
        for f in as_completed(futures):
            try:
                r=f.result();records.append(r);print(f"{r['upstreamFile']}: {r['rows']} rows; {r['transferredBytes']} bytes fetched",flush=True)
            except Exception as exc:
                errors.append(dict(file=futures[f],error=str(exc)[:230]));print(errors[-1],flush=True)
            progress("running")
    atomic(root/"statements-openr1-manifest.json",js({"source":source,"shards":sorted(records,key=lambda x:x["file"]),"errors":errors}))
    progress("complete_with_gaps" if errors else "complete")
    raise SystemExit(bool(errors))


if __name__=="__main__":main()
