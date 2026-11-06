"""OJ boundary: platform-native data is normalized here, never in the UI."""
import base64
import hashlib
import html
import json
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Protocol
from problem_statement import fetch_cf_statement


def normalize_api_credential(value, label):
    # Accept formatting introduced by copying from a rich-text page. Never echo
    # the original value in validation errors or persist it in diagnostic state.
    value = html.unescape(str(value)).strip().strip("*`").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise ValueError(f"{label} 格式不正确，请只粘贴凭证内容，不要包含字段名或中间的空格。")
    return value


def network_error_message(exc):
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError):
        return "CF HTTPS 连接校验失败，请检查系统时间和网络代理。"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "连接 CF API 超时，请检查网络或代理后重试；已同步的数据会保留。"
    if isinstance(reason, ConnectionRefusedError):
        if urllib.request.getproxies().get("https"):
            return "连接代理被拒绝，请确认代理正在运行，或关闭旧服务后从桌面启动器重新启动。"
        return "CF API 连接被拒绝，请检查网络后重试。"
    if isinstance(reason, socket.gaierror):
        return "无法解析 CF API 或代理的域名，请检查网络和 DNS 设置。"
    return "无法连接 CF API，请检查网络和代理后重试；已同步的数据会保留。"


class PlatformAdapter(Protocol):
    platform: str
    capabilities: dict

    def normalize(self, record: dict) -> dict: ...
    def sync(self, handle: str) -> dict: ...
    def fetch_statement(self, record: dict) -> dict: ...
    def problem_catalog(self) -> list[dict]: ...


class CodeforcesAdapter:
    platform = "codeforces"
    capabilities = {"submissions": True, "tags": True, "difficulty": True,
                    "sourceImport": True, "sourceSync": True, "problemStatement": True}
    _lock = threading.Lock()
    _last_call = 0.0

    def fetch_statement(self, record):
        return fetch_cf_statement(record)

    def problem_catalog(self):
        data = self.call("problemset.problems")
        problems = []
        for p in data["problems"]:
            cid, index, rating = p.get("contestId"), p.get("index"), p.get("rating")
            if not isinstance(cid, int) or not isinstance(rating, int) or not re.fullmatch(r"[A-Za-z0-9]+", str(index or "")):
                continue
            if p.get("type", "PROGRAMMING") != "PROGRAMMING":
                continue
            problems.append(dict(platform=self.platform, externalId=f"{cid}{index}", problemKey=f"{cid}{index}",
                contestId=cid, index=index, title=p.get("name", f"{cid}{index}"), rating=rating,
                tags=sorted(set(p.get("tags") or [])),
                url=f"https://codeforces.com/{'gym' if cid >= 100000 else 'contest'}/{cid}/problem/{index}"))
        if not problems:
            raise ValueError("CF 暂未返回有难度信息的题目，请稍后重试")
        return problems

    def normalize(self, record):
        s = record.get("submission", record)
        p = s.get("problem") or {}
        cid = p.get("contestId", s.get("contestId"))
        index = p.get("index")
        if not isinstance(s.get("id"), int) or cid is None or not index:
            raise ValueError("提交必须包含 id、contestId 和 problem.index")
        author = s.get("author") or {}
        members = author.get("members") or []
        handle = record.get("handle") or (members[0].get("handle") if members else "")
        if not handle:
            raise ValueError("提交中缺少账号 handle")
        source = s.get("source") or ""
        if not source and s.get("sourceBase64"):
            try:
                source = base64.b64decode(s["sourceBase64"], validate=True).decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                raise ValueError("sourceBase64 不是有效的源码编码")
        rating = p.get("rating")
        if rating is not None and (not isinstance(rating, int) or rating < 0):
            raise ValueError("题目 rating 格式错误")
        tags = p.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("题目 tags 必须是字符串数组")
        timestamp = s.get("creationTimeSeconds")
        if not isinstance(timestamp, int) or timestamp <= 0:
            raise ValueError("提交时间缺失或无效")
        key = f"{cid}{index}"
        return dict(platform=self.platform, handle=handle, externalId=str(s["id"]),
                    problemKey=key, contestId=cid, index=index, title=p.get("name", key),
                    rating=rating, difficultyScale="codeforces", tags=tags,
                    url=f"https://codeforces.com/{'gym' if int(cid) >= 100000 else 'contest'}/{cid}/problem/{urllib.parse.quote(index)}",
                    verdict=s.get("verdict") or "PENDING", submittedAt=timestamp,
                    participantType=author.get("participantType", "UNKNOWN"),
                    participantMembers=([str(m['handle']) for m in members]
                        if isinstance(author.get('members'), list) and members
                        and all(isinstance(m, dict) and isinstance(m.get('handle'), str) for m in members) else None),
                    partyStartTime=(author.get('startTimeSeconds')
                        if type(author.get('startTimeSeconds')) is int and author['startTimeSeconds'] > 0 else None),
                    teamId=author.get('teamId'), submissionContestId=s.get('contestId', cid),
                    language=s.get("programmingLanguage", ""), source=source,
                    timeMs=s.get("timeConsumedMillis"), memoryBytes=s.get("memoryConsumedBytes"))

    @staticmethod
    def signed_params(method, params, api_key, api_secret):
        signed = {**params, "apiKey": api_key, "time": str(int(time.time()))}
        canonical = "&".join(f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
                             for k, v in sorted(signed.items()))
        nonce = secrets.token_hex(3)
        digest = hashlib.sha512(f"{nonce}/{method}?{canonical}#{api_secret}".encode()).hexdigest()
        return {**signed, "apiSig": nonce + digest}

    def call(self, method, *, api_key=None, api_secret=None, **params):
        # Sign AFTER the shared throttle, so queued calls receive a fresh timestamp.
        with self._lock:
            time.sleep(max(0, 2.1 - (time.monotonic() - self._last_call)))
            type(self)._last_call = time.monotonic()
            if api_key and api_secret:
                params = self.signed_params(method, params, api_key, api_secret)
            req = urllib.request.Request("https://codeforces.com/api/" + method + "?" + urllib.parse.urlencode(params),
                                         headers={"User-Agent": "VPLab-course-demo/0.1", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                # CF may return its structured error with a non-2xx HTTP status.
                # Decode only JSON; never expose the signed URL in HTTPError text.
                try:
                    data = json.loads(exc.read(65536))
                except Exception:
                    raise ValueError(f"CF API 返回 HTTP {exc.code}，请检查网络或稍后重试。") from None
            except json.JSONDecodeError:
                raise ValueError("CF API 未返回有效 JSON，请检查网络是否跳转到验证页面。") from None
            except Exception as exc:
                raise ValueError(network_error_message(exc)) from None
            if not isinstance(data, dict):
                raise ValueError("CF API 返回格式错误，请稍后重试。")
            if data.get("status") != "OK":
                comment = str(data.get("comment", "未知错误"))
                for secret in (api_key, api_secret, params.get("apiSig")):
                    if secret:
                        comment = comment.replace(secret, "[已隐藏]")
                raise ValueError("CF 返回错误：" + comment[:200])
            return data["result"]

    def source_page(self, handle, api_key, api_secret, start=1, count=200):
        rows = self.call("user.status", api_key=api_key, api_secret=api_secret,
                         handle=handle, includeSources="true", count=count, **{"from": start})
        if not isinstance(rows, list):
            raise ValueError("CF 返回了无效的提交列表")
        return [self.normalize({"handle": handle, "submission": row}) for row in rows]

    def sync(self, handle):
        users = self.call("user.info", handles=handle)
        profile = users[0]
        canonical = profile["handle"]
        # Bounded sync is deliberately labelled as recent, not a complete history.
        rows = self.call("user.status", handle=canonical, count=1000, **{"from": 1})
        normalized = [self.normalize({"handle": canonical, "submission": s}) for s in rows]
        return {"records": normalized, "handle": canonical,
                "profile": {k: profile.get(k) for k in ("handle", "rating", "maxRating", "rank")},
                "scope": "最近最多 1000 次提交（非完整历史）"}


ADAPTERS = {"codeforces": CodeforcesAdapter()}


def parse_import(text):
    try:
        stripped = text.lstrip("\ufeff \r\n\t")
        raw = json.loads(stripped) if stripped.startswith("[") else [json.loads(line) for line in stripped.splitlines() if line.strip()]
        if not isinstance(raw, list) or not raw or len(raw) > 20000:
            raise ValueError("请导入 1～20000 条记录")
        rows = [ADAPTERS["codeforces"].normalize(r) for r in raw]
        handles = {r["handle"].casefold() for r in rows}
        if len(handles) != 1:
            raise ValueError("一次只能导入一个 CF 账号的数据")
        return rows
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        raise ValueError("文件格式错误，请使用 CF JSONL 导出文件或原始提交 JSON 数组")
