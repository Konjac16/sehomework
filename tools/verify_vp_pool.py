"""Cross-check the shipped room pack against the source SQLite, including every actual roster."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from build_cf_archive import DEFAULT_ROOT, REPO
from cf_archive_catalog import sha_file


def verify(source, pool):
    with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True)) as db, ZipFile(pool) as z:
        db.row_factory = sqlite3.Row
        index = json.loads(z.read("catalog.json"))
        ready = {r["contest_id"]: r for r in db.execute("SELECT * FROM vp_ready_contests")}
        ids = sorted(c["contestId"] for c in index["contests"])
        if ids != sorted(ready) or z.testzip() is not None:
            raise ValueError("比赛编号集合或压缩包 CRC 不匹配")
        keys = {p["problemKey"]: p for p in index["problems"]}
        slots = samples = 0
        for c in index["contests"]:
            cid = c["contestId"]
            if (c["name"], c["durationSeconds"]) != (ready[cid]["name"], ready[cid]["duration_seconds"]):
                raise ValueError(f"场名或时长不符：{cid}")
            statement = json.loads(z.read(f"statements/{cid}.json"))
            official = list(db.execute("SELECT * FROM contest_problem_details WHERE contest_id=? ORDER BY ordinal", (cid,)))
            expected = [r["idx"] for r in official]
            if list(statement) != expected or [k.rsplit(":", 1)[1] for k in c["problemKeys"]] != expected:
                raise ValueError(f"题序不符：{cid}")
            for r in official:
                p, key = statement[r["idx"]], keys[f"cf:{cid}:{r['idx']}"]
                if (key["name"] != r["name"] or p["name"] != r["name"] or p["body"] != r["body"]
                    or p["examples"] != json.loads(r["examples_json"]) or not p["examples"]):
                    raise ValueError(f"题名、题面或样例不符：{cid}/{r['idx']}")
                slots += 1
                samples += len(p["examples"])
        if slots != len(index["problems"]) or len(keys) != slots:
            raise ValueError("题位数不符")
    encoded = json.dumps(ids, separators=(",", ":")).encode()
    return dict(result="PASS", verifiedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                contests=len(ids), slots=slots, samplePairs=samples, idsSha256=hashlib.sha256(encoded).hexdigest(),
                catalogSha256=sha_file(pool), sourceSqliteSha256=sha_file(source))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_ROOT/"catalog.sqlite3")
    ap.add_argument("--pool", type=Path, default=REPO/"vp_demo/data/vp-ready.zip")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    report = json.dumps(verify(args.db, args.pool), indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    print(report, end="")
