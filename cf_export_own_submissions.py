#!/usr/bin/env python3
"""Export one account owner's Codeforces submissions via the official API.

The handle must belong to the account whose API key/secret are entered locally.
Credentials are prompted for at runtime and are never written to the output file.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_BASE = "https://codeforces.com/api/"
PAGE_SIZE = 1000
MIN_INTERVAL_SECONDS = 2.1


def call_api(method: str, params: dict[str, str], api_key: str, api_secret: str) -> object:
    signed_params = {**params, "apiKey": api_key, "time": str(int(time.time()))}
    ordered = sorted(signed_params.items(), key=lambda pair: (pair[0], pair[1]))
    canonical = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in ordered)
    rand = secrets.token_hex(3)
    digest = hashlib.sha512(f"{rand}/{method}?{canonical}#{api_secret}".encode("utf-8")).hexdigest()
    signed_params["apiSig"] = rand + digest

    url = API_BASE + method + "?" + urlencode(signed_params)
    request = Request(url, headers={"User-Agent": "cf-own-submissions-export/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Codeforces API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codeforces API returned invalid JSON") from exc

    if payload.get("status") != "OK":
        raise RuntimeError(f"Codeforces API error: {payload.get('comment', 'unknown error')}")
    return payload["result"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export source and metadata for the API-key owner's own Codeforces submissions."
    )
    parser.add_argument("--handle", required=True, help="Your own Codeforces handle (must match the API key owner).")
    parser.add_argument("--output", help="Output JSONL path; defaults to codeforces_<handle>_submissions.jsonl")
    parser.add_argument("--from-index", type=int, default=1, help="1-based first submission (default: 1)")
    parser.add_argument("--count", type=int, help="Maximum submissions to export (default: all available)")
    args = parser.parse_args()

    if args.from_index < 1 or (args.count is not None and args.count < 1):
        parser.error("--from-index and --count must be positive")

    output = args.output or f"codeforces_{args.handle}_submissions.jsonl"
    api_key = getpass.getpass("Codeforces API key (input hidden): ").strip()
    api_secret = getpass.getpass("Codeforces API secret (input hidden): ").strip()
    if not api_key or not api_secret:
        parser.error("API key and secret are required")

    exported = 0
    without_source = 0
    next_index = args.from_index
    previous_request_at: float | None = None
    try:
        with open(output, "w", encoding="utf-8") as out:
            while args.count is None or exported < args.count:
                if previous_request_at is not None:
                    delay = MIN_INTERVAL_SECONDS - (time.monotonic() - previous_request_at)
                    if delay > 0:
                        time.sleep(delay)

                batch_count = PAGE_SIZE if args.count is None else min(PAGE_SIZE, args.count - exported)
                previous_request_at = time.monotonic()
                result = call_api(
                    "user.status",
                    {
                        "handle": args.handle,
                        "from": str(next_index),
                        "count": str(batch_count),
                        "includeSources": "true",
                    },
                    api_key,
                    api_secret,
                )
                if not isinstance(result, list) or not result:
                    break

                retrieved_at = datetime.now(timezone.utc).isoformat()
                for submission in result:
                    if not submission.get("source"):
                        without_source += 1
                    record = {
                        "handle": args.handle,
                        "retrievedAt": retrieved_at,
                        "submission": submission,
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    exported += 1

                next_index += len(result)
                print(f"Exported {exported} submissions…", file=sys.stderr)
                if len(result) < batch_count:
                    break
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Saved {exported} submissions to {output}")
    if without_source:
        print(
            f"Warning: {without_source} records had no source field. The API only returns source for the account owner.",
            file=sys.stderr,
        )
    print("The API export includes problem metadata such as name, index, tags, and rating when available.")
    print("Problem statement text is not included in this API export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
