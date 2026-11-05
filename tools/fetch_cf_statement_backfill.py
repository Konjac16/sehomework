"""Fill older statement/sample gaps using only the public columns of DeepMind CodeContests."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import json
from pathlib import Path
import sqlite3
import sys
from urllib.request import Request, urlopen

from build_cf_archive import DEFAULT_ROOT
from cf_archive_catalog import compact, key_parts, sha_file
from fetch_cf_standings import atomic, js, stamp
from fetch_cf_statements_bulk import RangeFile

REPO = "deepmind/code_contests"
REV = "802411c3010cb00d1b05bad57ca77365a3c699d6"


def extract(file, contest_ids, complete):
    import pyarrow.parquet as pq
    remote = RangeFile(f"https://huggingface.co/datasets/{REPO}/resolve/{REV}/{file['path']}", file["size"])
    pf = pq.ParquetFile(remote, pre_buffer=False)
    output = []
    for n in range(pf.num_row_groups):
        rows = pf.read_row_group(n, columns=["cf_contest_id", "cf_index"]).to_pylist()
        keys = [(r["cf_contest_id"], r["cf_index"]) for r in rows]
        wanted = [i for i, key in enumerate(keys) if key[0] in contest_ids and key not in complete]
        if not wanted:
            continue
        selected = pf.read_row_group(n, columns=["name", "description", "public_tests"]).to_pylist()
        for i in wanted:
            cid, idx = keys[i]
            r = selected[i]
            prefix = f"{cid}_{idx}. "
            if not r["name"].startswith(prefix):
                continue
            tests = r.get("public_tests") or {}
            pairs = zip(tests.get("input") or [], tests.get("output") or [], strict=True)
            output.append(dict(key=f"{cid}/{idx}", title=r["name"][len(prefix):], description=r["description"],
                               examples=[dict(input=a, output=b) for a, b in pairs], file=file["path"], rowGroup=n, row=i))
    return output, remote.transferred


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--libs", type=Path)
    args = ap.parse_args()
    if args.libs:
        sys.path.insert(0, str(args.libs.resolve()))
    root = args.out.resolve()
    with sqlite3.connect((root / "catalog.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        contest_ids = {r[0] for r in db.execute("SELECT contest_id FROM contests")}
        complete = set(db.execute("""SELECT k.contest_id,k.idx FROM text_keys k JOIN texts t USING(text_id)
                                     GROUP BY k.contest_id,k.idx
                                     HAVING max(length(trim(t.body)))>0 AND max(t.example_count)>0"""))
        complete.difference_update(db.execute("SELECT contest_id,idx FROM problem_text WHERE body_id IS NULL OR sample_id IS NULL"))
    api = f"https://huggingface.co/api/datasets/{REPO}/tree/{REV}/data"
    req = Request(api, headers={"User-Agent": "VPLab-course-archive/1.0"})
    with urlopen(req, timeout=30) as response:
        files = [f for f in json.load(response) if f["path"].endswith(".parquet")]
    if len(files) != 41:
        raise ValueError("Incomplete listing of the fixed public release")
    result, total, errors = [], 0, []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(extract, f, contest_ids, complete): f["path"] for f in files}
        for n, future in enumerate(as_completed(jobs), start=1):
            try:
                rows, amount = future.result()
                result.extend(rows)
                total += amount
            except Exception as exc:
                errors.append({"file": jobs[future], "message": str(exc)[:180]})
            if n % 5 == 0:
                print(f"{n}/{len(jobs)} shards, {len(result)} candidate records", flush=True)
    out = root / "statements" / "deepmind"
    out.mkdir(parents=True, exist_ok=True)
    result.sort(key=lambda r: (key_parts(r["key"]), r["file"], r["rowGroup"], r["row"]))
    path = out / "candidates.jsonl.gz"
    atomic(path, gzip.compress(b"".join((compact(r) + "\n").encode("utf-8") for r in result), mtime=0))
    manifest = dict(provider=REPO, revision=REV, lookup=api, records=len(result), bytesTransferred=total,
                    columns=["cf_contest_id", "cf_index", "name", "description", "public_tests"],
                    scannedShards=len(files)-len(errors), totalShards=len(files), errors=errors,
                    file=path.name, sha256=sha_file(path), fetchedAt=stamp())
    atomic(out / "manifest.json", js(manifest))
    print(manifest, flush=True)
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
