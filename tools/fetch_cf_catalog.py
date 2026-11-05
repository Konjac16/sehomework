"""Fetch two public Codeforces API snapshots; no account, standings rows or source code."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API = "https://codeforces.com/api/"
ENDPOINTS = {"contests": "contest.list?gym=false", "problemset": "problemset.problems"}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def snapshot(out):
    out.mkdir(parents=True, exist_ok=True)
    if (out / "sources.json").exists():
        raise ValueError("This snapshot already exists. Use a new directory for a new capture.")
    sources, last_start = {}, 0.0
    for name, endpoint in ENDPOINTS.items():
        url = API + endpoint
        for attempt in range(4):
            time.sleep(max(0, 2.2 - (time.monotonic() - last_start)))
            last_start = time.monotonic()
            try:
                req = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "VPLab-course-catalog/1.0"})
                with urlopen(req, timeout=60) as response:
                    blob = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        blob = gzip.decompress(blob)
                result = json.loads(blob)
                if result.get("status") != "OK":
                    if "limit" in str(result.get("comment", "")).lower():
                        raise URLError("CF rate limit")
                    raise ValueError("CF API failed: " + str(result.get("comment", ""))[:180])
                break
            except (HTTPError, URLError, TimeoutError) as err:
                if isinstance(err, HTTPError) and err.code not in (429, 500, 502, 503, 504):
                    raise
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        file = name + ".json.gz"
        (out / file).write_bytes(gzip.compress(blob, compresslevel=9, mtime=0))
        payload = result["result"]
        counts = {"contests": len(payload)} if name == "contests" else {key: len(payload[key]) for key in ("problems", "problemStatistics")}
        sources[name] = dict(url=url, fetchedAt=utc(), file=file, sha256Decoded=hashlib.sha256(blob).hexdigest(), bytesDecoded=len(blob), counts=counts)
        print(name, counts, flush=True)
    (out / "sources.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    return sources


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    snapshot(args.out.resolve())
