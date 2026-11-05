"""Download original ICPC statement booklets and only the organizers' public sample files."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from build_cf_archive import DEFAULT_ROOT
from fetch_cf_standings import atomic, js, stamp

REPO = "icpc-apac-org/icpc-apc-problem-archive"
REV = "4d3abbdae53dd7e90776e728a45e5f69450fef73"
NERC = "https://neerc.ifmo.ru/past/"
BOOKLETS = {
    1938: "https://fileserv.icpc.jp/championship2024/tasks.pdf",
    2073: "https://fileserv.icpc.jp/championship2025/problemset.pdf",
    1089: NERC + "2018/neerc-2018-statement.pdf",
    1090: "https://neerc.ifmo.ru/school/archive/2018-2019/ru-olymp-team-russia-2018-problems-eng.pdf",
    1267: NERC + "2019/nerc-2019-statement.pdf",
    1510: NERC + "2020/nerc-2020-offline-statement.pdf",
    1666: NERC + "2021/nerc-2021-statement.pdf",
    1773: NERC + "2022/nef-2022-statement.pdf",
    1912: NERC + "2023/nef-2023-statement.pdf",
    2052: NERC + "2024/nef-2024-statement.pdf",
}
YEARS = {"2024":1938, "2025":2073}
OPEN_OI = {
    1939: ("2023/2024", 1, [14892, 14893, 14894, 14895]),
    1940: ("2023/2024", 2, [14896, 14897, 14898, 14899]),
    2079: ("2024/2025", 1, [13279, 13280, 13281, 13282]),
    2080: ("2024/2025", 2, [13283, 13284, 13285, 13286]),
}


def get(url, limit):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent":"VPLab-course-archive/1.0", "Accept-Encoding":"identity"})
            with urlopen(request, timeout=35) as response:
                data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Source exceeded the expected size")
            return data
        except (OSError, HTTPException) as exc:
            if attempt == 2 or isinstance(exc, HTTPError) and exc.code not in {429,500,502,503,504}:
                raise
            time.sleep(5 * (attempt + 1))


def inventory(out):
    path = out / "apac-tree.json"
    if not path.is_file():
        url = f"https://api.github.com/repos/{REPO}/git/trees/{REV}?recursive=1"
        tree = json.loads(get(url, 14 * 1024**2))
        files = [v for v in tree["tree"] if v["type"] == "blob" and "/data/sample/" in v["path"]]
        atomic(path, js({"sha":tree["sha"], "truncated":tree["truncated"], "files":files}))
    tree = json.loads(path.read_text("utf-8"))
    if tree["sha"] != REV or tree["truncated"]:
        raise ValueError("Incomplete or unpinned contest source tree")
    items = [dict(contestId=cid, file=f"booklets/{cid}.pdf", url=url) for cid, url in BOOKLETS.items()]
    for cid, (edition, day, ids) in OPEN_OI.items():
        for idx, qoj_id in zip("ABCD", ids, strict=True):
            items.append(dict(contestId=cid, index=idx, file=f"booklets/{cid}-{idx}.pdf",
                              collection="Open Olympiad in Informatics", edition=edition, day=day,
                              contestPage=f"https://qoj.ac/problems?tag=Open+OI+{edition.replace('/', '%2F')}+Day+{day}",
                              problemPage=f"https://qoj.ac/problem/{qoj_id}",
                              url=f"https://qoj.ac/download.php?id={qoj_id}&type=statement"))
    for item in tree["files"]:
        match = re.fullmatch(r"(202[45])/contest/([A-M])-[^/]+/data/sample/([\w.-]+\.(?:in|ans))", item["path"])
        if match:
            cid = YEARS[match[1]]
            items.append(dict(contestId=cid, index=match[2], file=f"samples/{cid}/{match[2]}/{match[3]}",
                              url=f"https://raw.githubusercontent.com/{REPO}/{REV}/{item['path']}",
                              gitBlob=item["sha"], size=item["size"]))
    return items


def download(out, item, previous):
    path = out / item["file"]
    if path.is_file() and item["file"] in previous:
        previous_row = previous[item["file"]]
        if previous_row["url"] == item["url"] and hashlib.sha256(path.read_bytes()).hexdigest() == previous_row["sha256"]:
            return previous_row
    data = get(item["url"], item.get("size", 20 * 1024**2))
    if "gitBlob" in item:
        git_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if git_hash != item["gitBlob"] or len(data) != item["size"]:
            raise ValueError("Unexpected sample blob")
    elif not data.startswith(b"%PDF-"):
        raise ValueError("Expected an official PDF booklet")
    atomic(path, data)
    return {**item, "bytes":len(data), "sha256":hashlib.sha256(data).hexdigest(), "checkedAt":stamp()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_ROOT)
    root = ap.parse_args().out
    out = root / "statements/icpc"
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "files-manifest.json"
    old = json.loads(manifest.read_text("utf-8"))["files"] if manifest.is_file() else []
    previous = {r["file"]:r for r in old}
    items = inventory(out)
    records, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(download, out, i, previous):i for i in items}
        for n, future in enumerate(as_completed(jobs), start=1):
            try:
                records.append(future.result())
            except Exception as exc:
                errors.append({**jobs[future], "error": str(exc)[:200]})
            if n % 20 == 0:
                print(f"{n}/{len(items)} files, {len(errors)} errors", flush=True)
    atomic(manifest, js(dict(revision=REV, files=sorted(records, key=lambda r:r["file"]),
                             errors=errors, checkedAt=stamp())))
    print(f"{len(records)} files, {len(errors)} errors, {sum(r['bytes'] for r in records)} bytes")
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
