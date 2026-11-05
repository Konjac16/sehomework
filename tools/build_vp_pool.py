"""Export the searchable room catalog and public reading material; no accounts or standings."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from build_cf_archive import DEFAULT_ROOT, REPO
from query_cf_archive import vp_catalog


def write_member(z, name, data):
    entry = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    entry.compress_type = ZIP_DEFLATED
    blob = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    z.writestr(entry, blob)


def build(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        ready = {r["contest_id"]: r for r in db.execute("SELECT * FROM vp_ready_contests ORDER BY contest_id")}
        if not ready:
            raise ValueError("归档没有可用于 VP 的比赛")
        catalog = vp_catalog(db, ready)
        catalog["snapshot"] = source.parent.name
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
        os.close(fd)
        try:
            with ZipFile(tmp, "w") as pack:
                for c in catalog["contests"]:
                    cid = c["contestId"]
                    c["startTimeSeconds"] = ready[cid]["start_time_seconds"]
                    rows = db.execute("""SELECT idx, name, body, input_format, output_format, interaction_format,
                        note, time_limit, memory_limit, body_source, example_source, examples_json
                        FROM contest_problem_details WHERE contest_id=? ORDER BY ordinal""", (cid,))
                    statements = {}
                    for row in rows:
                        p = dict(row)
                        idx = p.pop("idx")
                        p["examples"] = json.loads(p.pop("examples_json"))
                        if not p["body"].strip() or not p["examples"] or not all(
                            isinstance(x["input"], str) and isinstance(x["output"], str)
                            and (x["input"].strip() or x["output"].strip()) for x in p["examples"]
                        ):
                            raise ValueError(f"正文或样例不完整：{cid}/{idx}")
                        statements[idx] = p
                    if (len(statements) != ready[cid]["problem_count"] or
                            list(statements) != [k.rsplit(":", 1)[1] for k in c["problemKeys"]]):
                        raise ValueError(f"已核验题序与选场目录不一致：{cid}")
                    write_member(pack, f"statements/{cid}.json", statements)
                write_member(pack, "catalog.json", catalog)
                # Git may check out Markdown with CRLF on Windows; keep the bundle reproducible.
                sources = (REPO / "vp_demo/data/VP赛库来源.md").read_text(encoding="utf-8")
                write_member(pack, "SOURCES.md", sources.encode("utf-8"))
                write_member(pack, "Ronin-LICENSE.txt", (source.parent / "statements/ronin/LICENSE").read_bytes())
            os.replace(tmp, target)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return {"contests": len(ready), "slots": len(catalog["problems"]), "bytes": target.stat().st_size}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_ROOT/"catalog.sqlite3")
    ap.add_argument("--out", type=Path, default=REPO/"vp_demo/data/vp-ready.zip")
    args = ap.parse_args()
    print(json.dumps(build(args.db, args.out)))
