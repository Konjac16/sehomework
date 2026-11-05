"""Index public standings into the local SQLite catalog. --follow exits after this download."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from cf_archive_catalog import add_backfill, attach_text, compact, initialize, read_json, sha_file
from fetch_cf_standings import atomic, read_receipts, stamp

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO if (REPO / "official/sources.json").is_file() else REPO.parent / "CF公开数据" / "2026-11-05"


def copy_snapshot(source, root):
    official = root / "official"
    if source.resolve() != official.resolve():
        official.mkdir(exist_ok=True)
        for name in ("contests.json.gz", "problemset.json.gz", "sources.json"):
            dest = official / name
            if dest.exists():
                if sha_file(dest) != sha_file(source / name):
                    raise ValueError("Use a separate directory for a new snapshot")
            else:
                shutil.copyfile(source / name, dest)
    return official


def database(root, snapshot):
    path = root / "catalog.sqlite3"
    if not path.is_file():
        fd, filename = tempfile.mkstemp(dir=root, prefix="building-catalog-", suffix=".sqlite3")
        os.close(fd)
        try:
            db = sqlite3.connect(filename)
            db.row_factory = sqlite3.Row
            try:
                initialize(db, root, snapshot)
            finally:
                db.close()
            os.replace(filename, path)
        finally:
            Path(filename).unlink(missing_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for name, src in read_json(snapshot / "sources.json").items():
        row = db.execute("SELECT value FROM meta WHERE key=?", (name + "_sha256",)).fetchone()
        if not row or row[0] != src["sha256Decoded"]:
            raise ValueError("Catalog does not correspond to this snapshot")
    return db


def ledger_entries(root):
    return read_receipts(root / "standings-index.jsonl")


def ingest(db, root, entry):
    cid = entry["contestId"]
    filename = f"standings/{cid}.json.gz"
    roster, frozen = [], None
    if entry["status"] == "ok":
        if filename != entry["file"]:
            raise ValueError(f"Unexpected archive path for contest {cid}")
        packed = (root / filename).read_bytes()
        if len(packed) != entry["compressedBytes"] or hashlib.sha256(packed).hexdigest() != entry["sha256"]:
            raise ValueError(f"Damaged compressed scoreboard {cid}")
        envelope = json.loads(gzip.decompress(packed))
        result = envelope["result"]
        roster, rows = result["problems"], result["rows"]
        if (envelope["status"] != "OK" or result["contest"]["id"] != cid
                or len(roster) != entry["problems"] or len(rows) != entry["rows"]
                or any(len(row["problemResults"]) != len(roster) for row in rows)):
            raise ValueError(f"Invalid scoreboard dimensions for {cid}")
        frozen = int(result["contest"]["frozen"]) if "frozen" in result["contest"] else None
    elif entry["status"] != "failed":
        raise ValueError("Unknown ledger status")
    with db:
        db.execute("DELETE FROM contest_problems WHERE contest_id=?", (cid,))
        db.execute("INSERT OR REPLACE INTO standings_files VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cid, entry["status"], entry.get("file"), entry.get("rows"), entry.get("problems"),
                    entry.get("sha256"), entry["checkedAt"], entry["url"], entry.get("error"), frozen))
        for position, problem in enumerate(roster, start=1):
            idx, name = problem["index"], problem["name"]
            db.execute("INSERT INTO contest_problems VALUES(?,?,?,?,?,?,?,?)",
                       (cid, position, idx, name, problem["type"], problem.get("points"),
                        problem.get("rating"), compact(problem["tags"])))
            attach_text(db, cid, idx, name)
        db.execute("""DELETE FROM problem_text WHERE contest_id=?
                      AND NOT EXISTS (SELECT 1 FROM problems p WHERE p.contest_id=problem_text.contest_id AND p.idx=problem_text.idx)
                      AND NOT EXISTS (SELECT 1 FROM contest_problems s WHERE s.contest_id=problem_text.contest_id AND s.idx=problem_text.idx)""", (cid,))


def counts(db, relation):
    row = db.execute(f"""
        SELECT count(*) AS total, count(body) AS withBody,
               sum(example_count>0) AS withExamples,
               sum(body IS NOT NULL AND example_count>0) AS withBoth,
               sum(rating IS NULL) AS withoutRating FROM {relation}
    """).fetchone()
    return {k: (v or 0) for k, v in dict(row).items()}


def update_report(db, root, live):
    finished = db.execute("SELECT count(*) FROM contests WHERE phase='FINISHED'").fetchone()[0]
    score = db.execute("""SELECT count(*) AS downloaded, coalesce(sum(rows),0) AS scoreboardRows,
                          coalesce(sum(problem_count),0) AS contestProblemSlots, coalesce(sum(frozen),0) AS frozen,
                          coalesce(sum(rows=0),0) AS empty FROM standings_files WHERE status='ok'""").fetchone()
    failed = db.execute("SELECT count(*) FROM standings_files WHERE status='failed'").fetchone()[0]
    coverage = dict(builtAt=stamp(), snapshot=root.name,
                    phases=dict(db.execute("SELECT phase,count(*) FROM contests GROUP BY phase")),
                    canonicalProblems=counts(db, "problem_details"), verifiedSlots=counts(db, "contest_problem_details"),
                    standings={**dict(score), "targetFinished": finished, "failed": failed,
                               "pending": finished - score["downloaded"] - failed, "collectorStatus": live.get("status")},
                    statementRevision=dict(db.execute("SELECT key,value FROM meta WHERE key IN ('openr1_revision','ronin_revision','deepmind_revision','icpc_revision')")))
    coverage['verifiedSlots']['absentAsDirectProblemsetKey'] = db.execute("""SELECT count(*) FROM contest_problems s
        LEFT JOIN problems p USING(contest_id,idx) WHERE p.idx IS NULL""").fetchone()[0]
    coverage['contestsWithEverySlotHavingBodyAndExample'] = db.execute("""SELECT count(*) FROM (
        SELECT contest_id FROM contest_problem_details GROUP BY contest_id HAVING min(body IS NOT NULL AND example_count>0)=1
    )""").fetchone()[0]
    # Store the short, readable lists alongside the complete raw receipts.
    gaps = []
    for row in db.execute("""SELECT c.contest_id, c.phase, s.status, s.error FROM contests c
                             LEFT JOIN standings_files s USING(contest_id)
                             WHERE c.phase!='FINISHED' OR coalesce(s.status,'pending')!='ok'
                             ORDER BY c.contest_id"""):
        status = "not_finished_at_snapshot" if row["phase"] != "FINISHED" else (row["status"] or "pending")
        gaps.append({"kind": "standings", "contestId": row["contest_id"], "status": status, "error": row["error"]})
    for row in db.execute("""SELECT m.*, (p.idx IS NOT NULL) AS in_problemset, (s.idx IS NOT NULL) AS in_roster,
                             p.rating IS NULL AND p.idx IS NOT NULL AS lacks_catalog_rating
                             FROM problem_text m LEFT JOIN problems p USING(contest_id,idx)
                             LEFT JOIN contest_problems s USING(contest_id,idx)
                             WHERE m.body_id IS NULL OR m.sample_id IS NULL OR (p.idx IS NOT NULL AND p.rating IS NULL)
                             ORDER BY m.contest_id,m.idx"""):
        gaps.append(dict(kind="problem", contestId=row["contest_id"], index=row["idx"], name=row["name"],
                         officialUrl=row["url"], inProblemset=bool(row["in_problemset"]), inRoster=bool(row["in_roster"]),
                         missingBody=row["body_id"] is None, noVerifiedSample=row["sample_id"] is None,
                         missingCatalogRating=bool(row["lacks_catalog_rating"]), matchRule=row["match_rule"]))
    atomic(root / "gaps.jsonl.gz", gzip.compress(("\n".join(map(compact, gaps)) + "\n").encode("utf-8"), mtime=0))
    missing = [f"{g['contestId']}/{g['index']}  {g['name']}  {g['matchRule']}  {g['officialUrl']}"
               for g in gaps if g['kind'] == 'problem' and g['inProblemset'] and g['missingBody']]
    header = f"官方题库中尚未匹配正文的 {len(missing)} 道题。更新于 {coverage['builtAt']}\n"
    atomic(root / "缺题面清单.txt", (header + "\n".join(missing) + "\n").encode("utf-8-sig"))
    atomic(root / "coverage.json", (json.dumps(coverage, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return coverage


def sync(db, root, *, follow):
    last_count = None
    while True:
        progress_file = root / "standings-progress.json"
        live = read_json(progress_file) if progress_file.exists() else {}
        entries = ledger_entries(root)
        current = {r[0]: tuple(r[1:]) for r in db.execute("SELECT contest_id,status,sha256,checked_at FROM standings_files")}
        for cid, entry in entries.items():
            version = (entry["status"], entry.get("sha256"), entry["checkedAt"])
            if current.get(cid) != version:
                ingest(db, root, entry)
        checkpoint = (len(entries), live.get("status"), sum(e["status"] == "failed" for e in entries.values()))
        if checkpoint != last_count:
            report = update_report(db, root, live)
            print(report["builtAt"], report["standings"], flush=True)
            last_count = checkpoint
        terminal = live.get("status") in {"complete", "complete_with_gaps", "partial_limit", "paused_by_operator", "paused_low_disk"}
        if not follow or terminal:
            break
        time.sleep(45)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--snapshot", type=Path, help="defaults to the official/ snapshot within --out")
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    root = args.out.resolve()
    snapshot = copy_snapshot(args.snapshot or root / "official", root)
    db = database(root, snapshot)
    try:
        add_backfill(db, root)
        sync(db, root, follow=args.follow)
    finally:
        db.close()


if __name__ == "__main__":
    main()
