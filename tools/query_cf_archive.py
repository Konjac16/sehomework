"""离线查询 CF 题库、导出一场比赛的成绩或生成 VP 选场文件。"""
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from build_cf_archive import DEFAULT_ROOT
from cf_archive_catalog import compact, sha_file


def lookup(db, cid):
    row = db.execute("SELECT * FROM contests WHERE contest_id=?", (cid,)).fetchone()
    if row is None:
        raise ValueError(f"本快照未收录比赛 {cid}")
    return dict(row)


def verified_scores(db, root, cid):
    row = db.execute("SELECT * FROM standings_files WHERE contest_id=? AND status='ok'", (cid,)).fetchone()
    if row is None:
        raise ValueError(f"比赛 {cid} 的榜单尚未归档，请查看 coverage.json")
    path = root / row["file"]
    if sha_file(path) != row["sha256"]:
        raise ValueError(f"榜单文件校验失败 {path}")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        result = json.load(stream)["result"]
    if len(result["rows"]) != row["rows"]:
        raise ValueError(f"榜单行数异常 {cid}")
    return result


def detail(db, cid, idx):
    row = db.execute("SELECT * FROM contest_problem_details WHERE contest_id=? AND idx=?", (cid, idx)).fetchone()
    if row is None:
        row = db.execute("SELECT * FROM problem_details WHERE contest_id=? AND idx=?", (cid, idx)).fetchone()
    if row is None:
        raise ValueError(f"本快照还没有题号 {cid}/{idx}")
    result = dict(row)
    result["tags"] = json.loads(result.pop("tags_json"))
    examples = result.pop("examples_json")
    result["examples"] = json.loads(examples) if examples is not None else None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='booklet_pages'").fetchone():
        booklet = db.execute("SELECT * FROM booklet_pages WHERE contest_id=? AND idx=?", (cid,idx)).fetchone()
        if booklet:
            result["officialBooklet"] = dict(booklet)
    return result


def contest_detail(db, cid):
    contest = lookup(db, cid)
    receipt = db.execute("SELECT * FROM standings_files WHERE contest_id=?", (cid,)).fetchone()
    contest["standings"] = dict(receipt) if receipt is not None else None
    if receipt and receipt["status"] == "ok":
        query = """SELECT ordinal,idx,name,rating,example_count,body IS NOT NULL AS has_body
                   FROM contest_problem_details WHERE contest_id=? ORDER BY ordinal"""
        contest["rosterSource"] = "contest.standings"
    else:
        query = """SELECT idx,name,rating,example_count,body IS NOT NULL AS has_body
                   FROM problem_details WHERE contest_id=? ORDER BY idx"""
        contest["rosterSource"] = "problemset.problems — 部分题号可能在另一场次名下"
    contest["problems"] = [dict(r) for r in db.execute(query, (cid,))]
    return contest


def export_results(db, root, cid, target):
    result = verified_scores(db, root, cid)
    if target.exists():
        raise ValueError(f"输出文件已经存在 {target}")
    if target.suffix != ".gz":
        raise ValueError("请使用 .jsonl.gz 输出文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix="export-", suffix=".part")
    os.close(fd)
    count = 0
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as output:
            for row in result["rows"]:
                party = row["party"]
                base = dict(contestId=cid, contestType=result["contest"]["type"], rank=row["rank"],
                            partyMembers=[m["handle"] for m in party["members"]],
                            participantType=party["participantType"], teamId=party.get("teamId"),
                            teamName=party.get("teamName"), totalPoints=row["points"],
                            penalty=row.get("penalty"))
                for problem, cell in zip(result["problems"], row["problemResults"], strict=True):
                    item = {**base, "index": problem["index"], "problemPoints": cell["points"],
                            "bestSubmissionTimeSeconds": cell.get("bestSubmissionTimeSeconds"),
                            "rejectedAttemptCount": cell["rejectedAttemptCount"],
                            "resultType": cell["type"]}
                    output.write(compact(item) + "\n")
                    count += 1
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"contestId": cid, "scoreboardRows": len(result["rows"]),
            "problemSlots": len(result["problems"]), "exportedRows": count, "file": str(target)}


def vp_catalog(db, ids):
    contests, problems = [], []
    for cid in dict.fromkeys(ids):
        c = lookup(db, cid)
        if c["phase"] != "FINISHED" or c["type"] not in {"CF", "ICPC"} or not 60 <= c["duration_seconds"] <= 86400:
            raise ValueError(f"比赛 {cid} 的赛制、状态或时长超出 VP 房间范围")
        rows = list(db.execute("SELECT * FROM contest_problems WHERE contest_id=? ORDER BY ordinal", (cid,)))
        if not rows:
            raise ValueError(f"比赛 {cid} 的实际题序尚未由榜单核实")
        if any(not re.fullmatch(r"[A-Z0-9]{1,6}", row["idx"]) or row["type"] != "PROGRAMMING" for row in rows):
            raise ValueError(f"比赛 {cid} 存在房间未支持的题号或题型")
        keys = []
        for row in rows:
            index = row["idx"]
            key = f"cf:{cid}:{index}"
            keys.append(key)
            problems.append({"problemKey": key, "contestId": cid, "index": index, "name": row["name"],
                             "rating": row["rating"], "tags": json.loads(row["tags_json"]),
                             "url": f"https://codeforces.com/contest/{cid}/problem/{index}"})
        contests.append({"contestId": cid, "name": c["name"], "durationSeconds": c["duration_seconds"],
                         "phase": c["phase"], "problemSetComplete": True, "problemKeys": keys,
                         "sourceUrl": f"https://codeforces.com/contest/{cid}"})
    return {"schemaVersion": "1.0", "mode": "real_snapshot", "source": "Codeforces contest.standings",
            "contests": contests, "problems": problems}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", type=Path, default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("contest", help="查看实际比赛题序、榜单位置及题面覆盖")
    c.add_argument("contest_id", type=int)
    problem = sub.add_parser("problem", help="读取题面、格式和公开样例")
    problem.add_argument("contest_id", type=int)
    problem.add_argument("index")
    find = sub.add_parser("find", help="在题面和样例齐全的历史 VP 比赛中按名称、编号和时长筛选")
    find.add_argument("query", nargs="?", default="")
    find.add_argument("--minutes", type=int, default=1440)
    find.add_argument("--limit", type=int, default=15)
    results = sub.add_parser("results", help="导出一场比赛所有参赛名单项及逐题成绩")
    results.add_argument("contest_id", type=int)
    results.add_argument("--out", required=True, type=Path)
    vp = sub.add_parser("vp", help="导出项目可加载的已核对选场目录")
    vp.add_argument("ids", type=int, nargs="+")
    vp.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    root = args.archive.resolve()
    with sqlite3.connect((root / "catalog.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        try:
            if args.action == "find":
                if not 1 <= args.limit <= 200 or not 1 <= args.minutes <= 1440:
                    raise ValueError("--limit 须为 1～200，--minutes 须为 1～1440")
                rows = db.execute("""SELECT * FROM vp_ready_contests WHERE duration_seconds<=?
                    AND (instr(lower(name),lower(?))>0 OR CAST(contest_id AS TEXT)=?)
                    ORDER BY start_time_seconds DESC, contest_id DESC LIMIT ?""",
                    (args.minutes*60, args.query, args.query, args.limit))
                value = [dict(row) for row in rows]
            elif args.action == "problem":
                value = detail(db, args.contest_id, args.index.upper())
            elif args.action == "contest":
                value = contest_detail(db, args.contest_id)
            elif args.action == "vp":
                value = vp_catalog(db, args.ids)
                with args.out.open("x", encoding="utf-8") as f:
                    f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
                value = {"contests": len(value["contests"]), "problems": len(value["problems"]), "file": str(args.out)}
            else:
                value = export_results(db, root, args.contest_id, args.out)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        except ValueError as exc:
            ap.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
