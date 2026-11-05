"""Serial, resumable archival of official public final standings; no user source code."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cf_archive_catalog import sha_stream


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        Path(tmp).unlink(missing_ok=True)


def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def acquire_slot(root):
    # Shared with other official-CF downloaders in this archive. Reserve actual start times.
    import sqlite3
    with sqlite3.connect(root / "rate.sqlite3",timeout=120) as con:
        con.execute("CREATE TABLE IF NOT EXISTS limiter(k INTEGER PRIMARY KEY, last REAL)")
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT last FROM limiter WHERE k=1").fetchone()
        wait = max(0.0, (row[0] if row else 0.0) + 2.2 - time.time())
        if wait: time.sleep(wait)
        con.execute("INSERT OR REPLACE INTO limiter VALUES(1,?)", (time.time(),))


def fetch(url, root, accept="application/json", max_bytes=192*1024*1024):
    for n in range(4):
        acquire_slot(root)
        try:
            req = Request(url, headers={"User-Agent":"VPLab-course-archive/1.0", "Accept":accept,
                                        "Accept-Encoding":"gzip"})
            with urlopen(req, timeout=60) as response:
                blob = response.read(max_bytes+1)
                if len(blob)>max_bytes: raise ValueError("response exceeds safe per-file limit")
                if response.headers.get("Content-Encoding","").lower()=="gzip":
                    blob = gzip.decompress(blob)
            if len(blob)>max_bytes: raise ValueError("decoded response exceeds per-file limit")
            if accept=="application/json" and blob[:1200].lower().find(b"call limit exceeded")>=0:
                raise URLError("CF rate limit")
            return blob
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException, EOFError, zlib.error) as exc:
            if isinstance(exc, HTTPError) and exc.code not in {429,500,502,503,504}: raise
            if n==3: raise
            delay=4*(2**n)
            if isinstance(exc,HTTPError) and exc.code==429:
                delay=max(30,delay,int(exc.headers.get("Retry-After","30")) if str(exc.headers.get("Retry-After","")).isdigit() else 30)
            time.sleep(min(120,delay))


def read_gz(path):
    with gzip.open(path,"rt",encoding="utf-8") as f: return json.load(f)


def read_receipts(ledger, *, repair_tail=False):
    if not ledger.is_file():
        return {}
    data = ledger.read_bytes()
    newline = data.rfind(b"\n") + 1
    tail = data[newline:]
    if tail and repair_tail:
        # The downloader holds the exclusive writer lock here. A complete but unflushed
        # line only needs a newline. Retain a torn last record for diagnosis and re-fetch it.
        try:
            json.loads(tail)
        except (ValueError, UnicodeError):
            atomic(ledger.with_suffix(".interrupted-tail"), tail)
            with ledger.open("r+b") as stream:
                stream.truncate(newline)
        else:
            with ledger.open("ab") as stream:
                stream.write(b"\n")
            data += b"\n"
            newline = len(data)
    entries = {}
    for line in data[:newline].splitlines():
        if line.strip():
            entry = json.loads(line)
            entries[entry["contestId"]] = entry
    return entries


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--limit",type=int);p.add_argument("--retry-failed",action="store_true")
    args=p.parse_args();root=args.out.resolve();root.mkdir(parents=True,exist_ok=True)
    guard=(root/"standings.worker.lock").open("a+b")
    if not guard.tell():guard.write(b"0");guard.flush()
    guard.seek(0)
    if os.name=="nt":
        import msvcrt
        msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    contests=[c for c in read_gz(args.snapshot/"contests.json.gz")["result"] if c["phase"]=="FINISHED"]
    preferred={1:0,4:1,2274:2,2273:3,2269:4,921:5}
    contests.sort(key=lambda c:(preferred.get(c["id"],10),-c.get("startTimeSeconds",0),c["id"]))
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be positive")
    target_count = len(contests)
    if args.limit: contests=contests[:args.limit]
    progress=root/"standings-progress.json";ledger=root/"standings-index.jsonl"
    entries = read_receipts(ledger, repair_tail=True)
    def save_progress(status, active=None):
        valid=[e for e in entries.values() if e.get("status")=="ok"]
        atomic(progress,js(dict(status=status,updatedAt=stamp(),target=target_count,activeContestId=active,
             downloaded=len(valid),scoreboardRows=sum(e["rows"] for e in valid),
             contestProblemSlots=sum(e["problems"] for e in valid),failed=sum(e.get("status")=="failed" for e in entries.values()))))
    for c in contests:
        cid=c["id"];rel=f"standings/{cid}.json.gz";f=root/rel;old=entries.get(cid,{})
        if (root/"PAUSE").exists():
            save_progress("paused_by_operator",cid);return
        if old.get("status") == "ok" and f.is_file() and f.stat().st_size == old["compressedBytes"]:
            with f.open("rb") as stream:
                if sha_stream(stream) == old["sha256"]:
                    continue
        if old.get("status")=="failed" and not args.retry_failed:continue
        if shutil.disk_usage(root).free<1536*1024**2:
            save_progress("paused_low_disk",cid);raise SystemExit("Free-space reserve reached; completed files kept.")
        url=f"https://codeforces.com/api/contest.standings?contestId={cid}"
        save_progress("running",cid)
        try:
            raw=fetch(url,root)
            doc=json.loads(raw)
            if doc.get("status")!="OK":raise ValueError(str(doc.get("comment","CF failed"))[:180])
            result=doc["result"];problems=result["problems"];rows=result["rows"]
            if result["contest"]["id"]!=cid or not isinstance(problems,list) or not isinstance(rows,list):
                raise ValueError("unexpected standings object")
            if len({x["index"] for x in problems})!=len(problems):raise ValueError("duplicate contest indices")
            if any(len(x.get("problemResults",[]))!=len(problems) for x in rows):raise ValueError("ranklist/roster dimension mismatch")
            packed=gzip.compress(raw,compresslevel=5,mtime=0);atomic(f,packed)
            item=dict(status="ok",contestId=cid,url=url,checkedAt=stamp(),file=rel,rows=len(rows),
                      problems=len(problems),compressedBytes=len(packed),sha256=hashlib.sha256(packed).hexdigest())
            del doc,result,rows,problems,raw,packed
        except (ValueError, KeyError, TypeError, HTTPError, URLError, TimeoutError, OSError, HTTPException, EOFError, zlib.error) as exc:
            reason = str(exc)
            if isinstance(exc, HTTPError):
                try:
                    reason += " " + str(json.loads(exc.read(4096)).get("comment", ""))
                except (ValueError, OSError, HTTPException):
                    pass
            item=dict(status="failed",contestId=cid,url=url,checkedAt=stamp(),error=f"{type(exc).__name__}: {reason[:260]}")
        with ledger.open("a",encoding="utf-8") as fh:fh.write(json.dumps(item,ensure_ascii=False)+"\n")
        entries[cid]=item
        if len(entries)<=6 or len(entries)%25==0 or item["status"]=="failed":
            print(f"{len(entries)}/{len(contests)} contest={cid} {item['status']} rows={item.get('rows','-')}",flush=True)
    if len(entries) < target_count:
        save_progress("partial_limit")
    else:
        save_progress("complete_with_gaps" if any(x["status"]!="ok" for x in entries.values()) else "complete")


if __name__=="__main__":
    main()
