"""Local-only course demo. Python standard library + SQLite, no install required."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import webbrowser
import zipfile

from adapters import ADAPTERS, parse_import, normalize_api_credential
from domain import summarize, predict, prioritize
from code_diff import compare_sources
from review_service import ReviewService, ReviewError, init_tables
from problem_statement import StatementService
from browser_bridge import BrowserBridge
from training_service import TrainingService
from duo_rooms import DuoRooms
from duo_rules import RoomError
from duo_sync import RoomAutoSync
from training_archive import TrainingArchive
from recommendation_pool import SmartRecommendationPool, PoolError
from editorial_service import EditorialService
from smart_recommendation import SmartRecommendationService

ROOT = Path(__file__).resolve().parent
API_VERSION = 17
DB = Path(os.environ.get("VPLAB_DB", str(ROOT / ".local" / "vplab.sqlite3")))
SAMPLE = Path(os.environ.get("VPLAB_SAMPLE", "E:/codeforces_submissions.jsonl"))
SESSIONS = {}
SESSION_LOCK = threading.Lock()
DB_LOCK = threading.RLock()
JOBS = {}
JOB_LOCK = threading.Lock()


def get_sync_job(uid):
    with JOB_LOCK:
        return dict(JOBS.get(uid, {}))


def update_sync_job(uid, **fields):
    with JOB_LOCK:
        JOBS[uid].update(fields)


def run_source_sync(uid, handle, api_key, api_secret, mode):
    """Keys live only in this worker's arguments, never in job state or SQLite."""
    try:
        adapter = ADAPTERS["codeforces"]
        profile = adapter.call("user.info", handles=handle)[0]
        handle = profile["handle"]
        compact_profile = {k: profile.get(k) for k in ("handle", "rating", "maxRating", "rank")}
        received = added = sources = missing = 0
        seen = set()
        # Pages may shift if new submissions arrive during synchronization. Deduplicate by ID.
        # Another full sync reconciles shifts; do not claim an immutable snapshot.
        for page in range(1, 501):
            if get_sync_job(uid).get("cancelRequested"):
                update_sync_job(uid, status="cancelled", message="已中止，已同步的数据已保留")
                return
            rows = adapter.source_page(handle, api_key, api_secret, start=(page - 1) * 200 + 1)
            if get_sync_job(uid).get("cancelRequested"):
                update_sync_job(uid, status="cancelled", message="已中止，已同步的数据已保留")
                return
            fresh = [r for r in rows if r["externalId"] not in seen]
            scope = "在线源码同步进行中（尚未完成）"
            saved = save_records(uid, fresh, scope, compact_profile)
            seen.update(r["externalId"] for r in fresh)
            received += len(fresh)
            added += saved["added"]
            sources += sum(bool(r["source"]) for r in fresh)
            missing += sum(not bool(r["source"]) for r in fresh)
            update_sync_job(uid, pages=page, received=received, added=added, sources=sources,
                            missing=missing, message=f"已读取 {received} 次提交，取得 {sources} 份源码")
            exhausted = len(rows) < 200
            if exhausted or (mode == "recent" and page >= 5):
                label = "在线源码同步：已遍历历史分页" if exhausted else "在线源码同步：最近最多 1000 次提交"
                save_records(uid, [], label, compact_profile)
                if exhausted and mode == "all":
                    with DB_LOCK, connect() as c:
                        settings = json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])
                        settings["historyCoverage"] = {"platform": "codeforces", "handle": handle,
                            "completeThrough": get_sync_job(uid).get("startedAt", int(time.time())) - 1,
                            "verifiedAt": int(time.time())}
                        c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(settings), uid))
                message = f"同步完成：{received} 次提交，取得 {sources} 份源码"
                if missing:
                    message += f"；{missing} 次未返回源码，请检查 API 凭证是否属于该 handle 或提交是否可访问"
                update_sync_job(uid, status="completed", message=message, exhausted=exhausted)
                return
            if rows and not fresh:
                raise ValueError("CF 重复返回相同分页，已停止；已同步的数据已保留")
        update_sync_job(uid, status="limited", message="达到本次 100000 条安全上限，历史尚未遍历完成")
    except Exception as exc:
        # Never return arbitrary network exception text, which may contain signed URLs.
        message = str(exc) if isinstance(exc, ValueError) else "同步中断，请重试；已同步的数据已保留"
        for credential in (api_key, api_secret):
            if credential:
                message = message.replace(credential, "[已隐藏]")
        update_sync_job(uid, status="failed", message=message[:300])
    finally:
        api_key = api_secret = None


def submission_metadata(row):
    return {k: v for k, v in row.items() if k != "source"} | {"hasSource": bool(row.get("source"))}


@contextmanager
def connect():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          salt TEXT NOT NULL, password_hash TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS submissions(user_id INTEGER NOT NULL REFERENCES users(id),
          platform TEXT NOT NULL, external_id TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(user_id,platform,external_id));
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          title TEXT NOT NULL, problem_key TEXT NOT NULL DEFAULT '', rating INTEGER, tags TEXT NOT NULL,
          url TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
          pinned INTEGER NOT NULL DEFAULT 0, completion_mode TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
        ''')
        init_tables(c)
        StatementService.init_tables(c)
        TrainingService.init_tables(c)
        DuoRooms.init_tables(c)
        SmartRecommendationPool.init_tables(c)
        SmartRecommendationService.init_tables(c)


def user_settings(uid):
    with connect() as c:
        return json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])


def user_records(uid):
    with connect() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT payload FROM submissions WHERE user_id=?", (uid,))]


def save_records(uid, records, scope, profile=None):
    handles = {r["handle"].casefold() for r in records}
    with DB_LOCK, connect() as c:
        settings = json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])
        handle = records[0]["handle"] if records else (profile or {}).get("handle", "")
        old = settings.get("handle", "")
        if old and handle and old.casefold() != handle.casefold():
            raise ValueError("此账号已绑定其他 CF handle；当前版本请另建本地账号，避免混合训练记录")
        if len(handles) > 1:
            raise ValueError("不能合并多个 CF 账号")
        before = c.execute("SELECT COUNT(*) FROM submissions WHERE user_id=?", (uid,)).fetchone()[0]
        for r in records:
            # A public sync must never overwrite an already imported source with an empty string.
            prev = c.execute("SELECT payload FROM submissions WHERE user_id=? AND platform=? AND external_id=?",
                             (uid, r["platform"], r["externalId"])).fetchone()
            if prev and not r["source"]:
                r = dict(r, source=json.loads(prev[0]).get("source", ""))
            c.execute("INSERT OR REPLACE INTO submissions VALUES(?,?,?,?)", (uid, r["platform"], r["externalId"], json.dumps(r, ensure_ascii=False)))
        after = c.execute("SELECT COUNT(*) FROM submissions WHERE user_id=?", (uid,)).fetchone()[0]
        settings.update(handle=handle or old, lastSync=int(time.time()), lastScope=scope)
        if profile:
            settings["profile"] = profile
        c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), uid))
        TRAINING.reconcile(uid, c)
    return {"received": len(records), "added": after - before, "total": after, "handle": handle}


STATEMENTS = StatementService(connect, ADAPTERS)
REVIEWS = ReviewService(connect, DB_LOCK, user_records, user_settings, STATEMENTS.cached_for)
BRIDGE = BrowserBridge(STATEMENTS)
TRAINING = TrainingService(connect, DB_LOCK, ADAPTERS)
INTELLIGENT = SmartRecommendationPool(connect, DB_LOCK, TrainingArchive(
    os.environ.get('VPLAB_INTELLIGENT_ARCHIVE', ROOT / 'data' / 'vp-ready.zip')), catalog_loader=TRAINING.catalog)
EDITORIALS = EditorialService(connect, DB_LOCK, pool=INTELLIGENT)

def smart_api_key(uid):
    with REVIEWS.lock:
        return REVIEWS.keys.get(uid) or os.environ.get('DEEPSEEK_API_KEY','')

SMART = SmartRecommendationService(connect, DB_LOCK, INTELLIGENT, smart_api_key, editorials=EDITORIALS)
_pool = ROOT / "data" / "vp-ready.zip"
ROOMS = DuoRooms(connect, DB_LOCK, ADAPTERS["codeforces"],
                 Path(os.environ.get("VPLAB_CONTESTS", _pool if _pool.is_file() else ROOT / "data" / "contests.json")))


class Handler(BaseHTTPRequestHandler):
    server_version = "VPLab/0.3"
    protocol_version = "HTTP/1.1"  # Reuse connections for the two simultaneous workstations.

    def log_message(self, format, *args):
        pass  # No credentials, submitted code, or import bodies in request logs.

    def send(self, data, status=200, content_type="application/json; charset=utf-8", extra=None):
        blob = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if urlparse(self.path).path == '/api/browser/result' and re.fullmatch(r'chrome-extension://[a-p]{32}', self.headers.get('Origin', '')):
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(blob)

    def uid(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["vplab_session"].value
            with SESSION_LOCK:
                uid, expires = SESSIONS[token]
                if expires < time.time():
                    del SESSIONS[token]
                    raise KeyError()
            return uid
        except (KeyError, ValueError):
            return None

    def do_GET(self):
        try:
            self.get_route()
        except (RoomError, PoolError) as exc:
            self.send({"error": str(exc)}, exc.status)
        except ReviewError as exc:
            self.send({"error": str(exc)}, exc.status)
        except (ValueError, TypeError, KeyError):
            self.send({"error": "请求参数无效"}, 400)
        except Exception:
            self.send({"error": "服务暂时不可用，请重试"}, 500)

    def get_route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        ui_root = (ROOT / "static" / "ui").resolve()
        if (ui_root / "index.html").is_file() and not path.startswith("/api/"):
            candidate = (ui_root / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            if candidate.is_relative_to(ui_root) and candidate.is_file():
                mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                return self.send(candidate.read_bytes(), content_type=mime)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/health":
            return self.send({"app": "vplab", "apiVersion": API_VERSION})
        if path in ("/duo", "/duo.js", "/duo.css"):
            name, mime = {"/duo": ("duo.html", "text/html"), "/duo.js": ("duo.js", "text/javascript"),
                          "/duo.css": ("duo.css", "text/css")}[path]
            return self.send((ROOT / "static" / name).read_bytes(), content_type=mime + "; charset=utf-8")
        if path.startswith('/vendor/katex/'):
            name = path.removeprefix('/vendor/katex/')
            if name not in ('katex.min.js', 'katex.min.css') and not re.fullmatch(r'fonts/[A-Za-z0-9_-]+\.(woff2|woff|ttf)', name):
                return self.send({'error': '不存在的资源'}, 404)
            file = ROOT / 'static' / 'vendor' / 'katex' / name
            if not file.is_file():
                return self.send({'error': '不存在的资源'}, 404)
            mime = {'.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf'}[file.suffix]
            return self.send(file.read_bytes(), content_type=mime)
        if path == '/browser-helper.zip':
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
                for file in sorted((ROOT / 'browser_helper').iterdir()):
                    if file.suffix in ('.js', '.json', '.txt'):
                        z.write(file, 'VP-Lab-Browser-Helper/' + file.name)
            return self.send(buf.getvalue(), content_type='application/zip', extra={'Content-Disposition': 'attachment; filename="VP-Lab-Browser-Helper.zip"'})
        if path in ("/", "/app.js", "/source.js", "/review.js", "/statement.js", "/statement-format.js", "/helper.js", "/training.js", "/training.css", "/intelligent.js", "/smart.js", "/intelligent.css", "/style.css", "/review.css"):
            name = "index.html" if path == "/" else path[1:]
            mime = "text/html" if path == "/" else "text/javascript" if path.endswith(".js") else "text/css"
            return self.send((ROOT / "static" / name).read_bytes(), content_type=mime + "; charset=utf-8")
        uid = self.uid()
        if path == "/api/me":
            if not uid:
                return self.send({"user": None})
            with connect() as c:
                username = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()[0]
            return self.send({"user": {"id": uid, "username": username}})
        if not uid:
            return self.send({"error": "请先登录"}, 401)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path == '/api/intelligent/pool':
            return self.send(INTELLIGENT.state(uid))
        if path == '/api/intelligent/recommendation':
            return self.send(SMART.state(uid))
        if path == '/api/intelligent/material':
            return self.send(INTELLIGENT.material(uid, q.get('id', '')))
        if path == '/api/intelligent/editorials':
            return self.send({'job': EDITORIALS.status(uid)})
        if path in ("/api/training", "/api/training/preferences"):
            return self.send(TRAINING.state(uid,q.get('mode')))
        if path == "/api/rooms" or path.startswith("/api/rooms/"):
            return self.send(ROOMS.handle("GET", path, uid, query=q))
        if path == '/api/browser/status':
            return self.send({'job': BRIDGE.status(uid, q.get('id', ''))})
        if path == "/api/ai/config":
            return self.send(REVIEWS.config(uid))
        if path == "/api/experiences":
            return self.send(REVIEWS.list_cards(uid, q))
        if path == "/api/experience":
            return self.send({"card": REVIEWS.get_card(uid, q.get("id", 0))})
        if path == "/api/analysis":
            return self.send({"analysis": REVIEWS.analysis(uid, q.get("id", 0))})
        if path == "/api/analysis/source":
            analysis = REVIEWS.analysis(uid, q.get("id", 0), True)
            record = next((r for r in analysis["snapshot"]["records"] if r["externalId"] == q.get("submissionId")), None)
            if not record:
                raise ReviewError("该分析快照中没有这份源码。", 404)
            return self.send({"record": record})
        if path == "/api/analysis/diff":
            analysis = REVIEWS.analysis(uid, q.get("id", 0), True)
            records = {r["externalId"]: r for r in analysis["snapshot"]["records"]}
            if any(q.get(side) not in records for side in ("before", "after")):
                raise ReviewError("所选版本不在这次分析的源码快照中。", 404)
            a, b = records[q["before"]], records[q["after"]]
            return self.send(compare_sources(a["source"], b["source"], q.get("full") == "1") |
                             {"before": submission_metadata(a), "after": submission_metadata(b)})
        if path == "/api/sync/source/status":
            return self.send({"job": get_sync_job(uid) or None})
        if path == "/api/diff":
            q = parse_qs(parsed.query)
            ids = [q.get(name, [""])[0] for name in ("before", "after")]
            records = []
            with connect() as c:
                for sid in ids:
                    row = c.execute("SELECT payload FROM submissions WHERE user_id=? AND platform='codeforces' AND external_id=?", (uid, sid)).fetchone()
                    if not row:
                        return self.send({"error": "提交不存在或不属于当前账号"}, 404)
                    records.append(json.loads(row[0]))
            a, b = records
            if a["problemKey"] != b["problemKey"]:
                raise ValueError("请选择同一道题的两次提交")
            if not a["source"] or not b["source"]:
                raise ValueError("有提交尚未获取源码，请先在数据与账号中同步")
            return self.send(compare_sources(a["source"], b["source"], q.get("full") == ["1"]) |
                             {"before": submission_metadata(a), "after": submission_metadata(b)})
        if path == "/api/submissions":
            q = parse_qs(parsed.query)
            search = q.get("q", [""])[0].casefold()
            verdict = q.get("verdict", [""])[0]
            source_filter = q.get("source", [""])[0]
            page = max(1, int(q.get("page", [1])[0]))
            rows = user_records(uid)
            verdicts = sorted({r["verdict"] for r in rows})
            rows = [r for r in rows if search in f"{r['problemKey']} {r['title']} {r['externalId']}".casefold()
                    and (not verdict or r["verdict"] == verdict)
                    and (source_filter != "yes" or bool(r["source"]))
                    and (source_filter != "no" or not r["source"])]
            rows.sort(key=lambda r: (r["submittedAt"], int(r["externalId"])), reverse=True)
            total = len(rows)
            page = min(page, max(1, (total + 24) // 25))
            return self.send({"rows": [submission_metadata(r) for r in rows[(page-1)*25:page*25]],
                              "total": total, "page": page, "pageSize": 25, "verdicts": verdicts})
        if path == "/api/submission":
            q = parse_qs(parsed.query)
            sid = q.get("id", [""])[0]
            platform = q.get("platform", ["codeforces"])[0]
            with connect() as c:
                row = c.execute("SELECT payload FROM submissions WHERE user_id=? AND platform=? AND external_id=?",
                                (uid, platform, sid)).fetchone()
            if not row:
                return self.send({"error": "提交不存在或不属于当前账号"}, 404)
            return self.send({"record": json.loads(row[0])})
        if path == "/api/dashboard":
            summary = summarize(user_records(uid))
            settings = user_settings(uid)
            reference = settings.get("referenceRating", (settings.get("profile") or {}).get("rating"))
            with connect() as c:
                tasks = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE user_id=?", (uid,))]
            for t in tasks:
                t["tags"] = json.loads(t["tags"])
                rank, level, reason = prioritize(t, reference, settings.get("focusTags", []))
                t.update(priority=rank, level=level, reason=reason)
            tasks.sort(key=lambda t: (t["priority"], -bool(set(t["tags"]) & set(settings.get("focusTags", []))), -t["created_at"]))
            return self.send(summary | {"settings": settings, "referenceRating": reference, "tasks": tasks,
                                        "capabilities": ADAPTERS["codeforces"].capabilities, "sampleAvailable": SAMPLE.exists(),
                                        "sourceSync": get_sync_job(uid) or None})
        if path == "/api/problem":
            key = parse_qs(parsed.query).get("key", [""])[0]
            records = [r for r in user_records(uid) if r["platform"] == "codeforces" and r["problemKey"] == key]
            if not records:
                return self.send({"error": "未找到题目"}, 404)
            return self.send({"records": sorted(records, key=lambda r: r["submittedAt"], reverse=True)})
        if path == "/api/export":
            buf = io.BytesIO()
            records = user_records(uid)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("records.normalized.json", json.dumps(records, ensure_ascii=False, indent=2))
                z.writestr("problems.json", json.dumps(summarize(records)["problems"], ensure_ascii=False, indent=2))
                with connect() as c:
                    lessons = [json.loads(r["payload"]) | {"id": r["id"], "version": r["version"], "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
                               for r in c.execute("SELECT * FROM experiences WHERE user_id=? AND deleted=0", (uid,))]
                    analyses = [dict(r) for r in c.execute("SELECT id,status,snapshot,result,model,created_at FROM analyses WHERE user_id=?", (uid,))]
                for analysis in analyses:
                    for field in ("snapshot", "result"):
                        analysis[field] = json.loads(analysis[field]) if analysis[field] else None
                z.writestr("experiences.json", json.dumps(lessons, ensure_ascii=False, indent=2))
                z.writestr("analyses.json", json.dumps(analyses, ensure_ascii=False, indent=2))
                for r in records:
                    if r["source"]:
                        # Export path uses only a validated integer submission ID.
                        z.writestr(f"sources/{int(r['externalId'])}.txt", r["source"])
            return self.send(buf.getvalue(), content_type="application/zip", extra={"Content-Disposition": 'attachment; filename="vplab-export.zip"'})
        self.send({"error": "不存在的地址"}, 404)

    def do_OPTIONS(self):
        if urlparse(self.path).path != '/api/browser/result' or not re.fullmatch(r'chrome-extension://[a-p]{32}', self.headers.get('Origin', '')):
            return self.send({'error': '请求来源不匹配'}, 403)
        return self.send({}, extra={'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type, X-VPLab-Token', 'Access-Control-Allow-Private-Network': 'true'})

    def do_POST(self):
        origin = self.headers.get("Origin")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 24 * 1024 * 1024:
                return self.send({"error": "文件过大，请控制在 24 MB 内"}, 413)
            body = self.rfile.read(length)
            # Drain the bounded body before replying; closing with unread bytes can
            # reset the connection on Windows and hide the useful 403 response.
            bridge_result = urlparse(self.path).path == '/api/browser/result'
            if bridge_result and length > 1_200_000:
                return self.send({'error': '题面过大'}, 413)
            extension_origin = bridge_result and re.fullmatch(r'chrome-extension://[a-p]{32}', origin or '')
            if origin and origin != "http://" + self.headers.get("Host", "") and not extension_origin:
                return self.send({"error": "请求来源不匹配"}, 403)
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式错误")
            self.post_route(urlparse(self.path).path, data)
        except (RoomError, PoolError) as exc:
            self.send({"error": str(exc)}, exc.status)
        except ReviewError as exc:
            self.send({"error": str(exc)}, exc.status)
        except (ValueError, TypeError, KeyError) as exc:
            self.send({"error": str(exc)[:250] or "请求参数错误"}, 400)
        except Exception:
            self.send({"error": "操作失败，已有数据已保留"}, 500)

    def post_route(self, path, data):
        if path == '/api/browser/result':
            return self.send({'job': BRIDGE.deliver(self.headers.get('X-VPLab-Token', ''), data)})
        if path in ("/api/register", "/api/login"):
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            if not re.fullmatch(r"[\w\-]{3,24}", username) or not 8 <= len(password) <= 128:
                raise ValueError("用户名需 3～24 个字母、数字、汉字或下划线；密码需 8～128 位")
            with DB_LOCK, connect() as c:
                row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                if path.endswith("register"):
                    if row:
                        raise ValueError("用户名已存在，请登录或更换名称")
                    salt = secrets.token_hex(16)
                    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240000).hex()
                    uid = c.execute("INSERT INTO users(username,salt,password_hash) VALUES(?,?,?)", (username, salt, digest)).lastrowid
                else:
                    if not row or not hmac.compare_digest(row["password_hash"], hashlib.pbkdf2_hmac("sha256", password.encode(), row["salt"].encode(), 240000).hex()):
                        raise ValueError("用户名或密码错误")
                    uid = row["id"]
            token = secrets.token_urlsafe(32)
            with SESSION_LOCK:
                SESSIONS[token] = (uid, time.time() + 86400)
            return self.send({"ok": True}, extra={"Set-Cookie": f"vplab_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"})
        uid = self.uid()
        if not uid:
            return self.send({"error": "请先登录"}, 401)
        if path == "/api/training/preferences":
            return self.send(TRAINING.preferences(uid, data))
        if path == '/api/training/mode':
            return self.send(TRAINING.select_mode(uid,data))
        if path == '/api/intelligent/settings':
            if uid in SMART.active or EDITORIALS.status(uid).get('status')=='running':
                raise PoolError('请先停止当前推荐或资料准备，再修改设置。',409)
            return self.send(INTELLIGENT.settings(uid,data))
        if path == '/api/intelligent/recommendation':
            with SMART.lock, EDITORIALS.lock:
                if any(j.get('status')=='running' for j in EDITORIALS.jobs.values()):
                    raise PoolError('资料仍在准备，请完成后再生成推荐。',409)
                result=SMART.start(uid,data)
            return self.send(result)
        if path == '/api/intelligent/recommendation/cancel':
            if data:raise PoolError('此操作不接受额外参数。')
            return self.send(SMART.cancel(uid))
        if path in ('/api/intelligent/editorials', '/api/intelligent/editorials/cancel'):
            if data:
                raise PoolError('题解准备只处理当前候选池，不接受外部地址。')
            with SMART.lock, EDITORIALS.lock:
                if SMART.active:raise PoolError('AI 推荐正在运行，请先停止推荐。',409)
                job = EDITORIALS.cancel(uid) if path.endswith('/cancel') else EDITORIALS.start(uid)
            return self.send({'job':job},202)
        if path in ('/api/intelligent/prepare', '/api/intelligent/sync'):
            if uid in SMART.active:raise PoolError('AI 推荐正在运行，请先停止推荐。',409)
            if data:
                raise PoolError('候选准备由系统管理，此接口不接受选题参数。')
            if path.endswith('/sync'):
                settings = user_settings(uid)
                if not settings.get('handle'):
                    raise PoolError('请先在数据与账号中绑定 CF 账号。')
                if get_sync_job(uid).get('status') == 'running':
                    raise PoolError('提交同步正在进行，请稍后整理候选。', 409)
                result = ADAPTERS['codeforces'].sync(settings['handle'])
                save_records(uid, result['records'], result['scope'], result['profile'])
            return self.send(INTELLIGENT.refresh(uid))
        if path == "/api/recommendations":
            return self.send(TRAINING.recommend(uid, data))
        if path in ("/api/training/start", "/api/training/dismiss", "/api/training/skip", "/api/training/resume", "/api/training/feedback"):
            return self.send(TRAINING.act(uid, path.rsplit("/", 1)[1], data))
        if path == "/api/training/refresh":
            settings = user_settings(uid)
            if not settings.get("handle"):
                raise ValueError("请先在数据与账号中同步 CF 账号")
            if get_sync_job(uid).get("status") != "running" and time.time() - settings.get("lastSync", 0) >= 30:
                result = ADAPTERS["codeforces"].sync(settings["handle"])
                save_records(uid, result["records"], result["scope"], result["profile"])
            return self.send(TRAINING.state(uid,data.get('mode')))
        if path == "/api/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            with SESSION_LOCK:
                SESSIONS.pop(cookie["vplab_session"].value, None)
            REVIEWS.config(uid, {"clear": True})
            BRIDGE.cancel_user(uid)
            return self.send({"ok": True}, extra={"Set-Cookie": "vplab_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if path == "/api/rooms" or path.startswith("/api/rooms/"):
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            return self.send(ROOMS.handle("POST", path, uid, data, query=q))
        if path == "/api/ai/config":
            return self.send(REVIEWS.config(uid, data))
        if path in ("/api/problem/statement", '/api/browser/create'):
            platform = data.get("platform", "codeforces")
            record = next((r for r in user_records(uid) if r["platform"] == platform and r["problemKey"] == data.get("problemKey")), None)
            if record is None:
                raise ReviewError("题目不存在或不属于当前训练记录。", 404)
            if path == '/api/browser/create':
                return self.send({'job': BRIDGE.create(uid, {k: record[k] for k in ('platform', 'problemKey', 'contestId', 'index')})})
            cached = STATEMENTS.cached_for(uid, record)
            if cached and cached.get('textOrigin') in ('codeforces_browser', 'cf_archive'):
                return self.send({'statement': cached | {'cached': True}})
            return self.send({"statement": STATEMENTS.get(record, data.get("refresh") is True)})
        if path == "/api/analyses/preview":
            _, preview = REVIEWS.preview(uid, data)
            return self.send({"preview": preview})
        if path == "/api/analyses/create":
            analysis, reused = REVIEWS.create_analysis(uid, data)
            return self.send({"analysis": analysis, "reused": reused}, 200 if reused else 202)
        if path == "/api/analyses/cancel":
            return self.send({"analysis": REVIEWS.cancel(uid, data.get("id", 0))})
        if path == "/api/experiences/from-analysis":
            return self.send(REVIEWS.collect(uid, data))
        if path in ("/api/experiences/create", "/api/experiences/update", "/api/experiences/confirm", "/api/experiences/delete", "/api/experiences/restore"):
            return self.send(REVIEWS.save_card(uid, path.rsplit("/", 1)[1], data))
        if path == "/api/sync/source/cancel":
            with JOB_LOCK:
                if JOBS.get(uid, {}).get("status") == "running":
                    JOBS[uid]["cancelRequested"] = True
                    JOBS[uid]["message"] = "正在中止，当前网络请求结束后停止"
            return self.send({"job": get_sync_job(uid) or None})
        if path == "/api/sync/source":
            handle = str(data.get("handle", "")).strip()
            api_key = normalize_api_credential(data.get("apiKey", ""), "API Key")
            api_secret = normalize_api_credential(data.get("apiSecret", ""), "API Secret")
            mode = data.get("mode", "all")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", handle):
                raise ValueError("CF handle 格式错误")
            if not api_key or not api_secret or max(len(api_key), len(api_secret)) > 256:
                raise ValueError("请填写 API Key 和 Secret")
            if mode not in ("all", "recent"):
                raise ValueError("同步范围无效")
            old = user_settings(uid).get("handle", "")
            if old and old.casefold() != handle.casefold():
                raise ValueError("此账号已绑定其他 handle；请使用绑定账号自己的 API 凭证")
            with JOB_LOCK:
                if JOBS.get(uid, {}).get("status") == "running":
                    raise ValueError("已有源码同步任务正在运行")
                JOBS[uid] = {"id": secrets.token_hex(8), "status": "running", "mode": mode,
                             "pages": 0, "received": 0, "added": 0, "sources": 0, "missing": 0,
                             "message": "正在连接 Codeforces…", "startedAt": int(time.time()), "cancelRequested": False}
            threading.Thread(target=run_source_sync, args=(uid, handle, api_key, api_secret, mode), daemon=True).start()
            return self.send({"job": get_sync_job(uid)}, 202)
        if path in ("/api/import", "/api/import-sample"):
            if path.endswith("sample"):
                if not SAMPLE.exists():
                    raise ValueError("未发现本地示例，请手动选择 JSONL 文件")
                rows = parse_import(SAMPLE.read_text(encoding="utf-8-sig"))
            else:
                rows = parse_import(data.get("text", ""))
            scope = "导入文件，仅含 AC" if all(r["verdict"] == "OK" for r in rows) else "导入文件，完整性未知"
            return self.send(save_records(uid, rows, scope))
        if path == "/api/sync":
            handle = str(data.get("handle", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", handle):
                raise ValueError("CF handle 格式错误")
            old = user_settings(uid).get("handle", "")
            if old and old.casefold() != handle.casefold():
                raise ValueError("当前账号已绑定其他 handle，请另建本地账号")
            result = ADAPTERS["codeforces"].sync(handle)
            return self.send(save_records(uid, result["records"], result["scope"], result["profile"]))
        if path == "/api/settings":
            reference = data.get("referenceRating")
            if reference is not None:
                reference = int(reference)
                if not 0 <= reference <= 4000:
                    raise ValueError("参考 Rating 应在 0～4000 之间")
            focus = data.get("focusTags", [])
            if not isinstance(focus, list) or len(focus) > 8 or not all(isinstance(t, str) and len(t) <= 50 for t in focus):
                raise ValueError("最多选择 8 个训练标签")
            with DB_LOCK, connect() as c:
                settings = json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])
                if reference is None:
                    settings.pop("referenceRating", None)
                else:
                    settings["referenceRating"] = reference
                settings["focusTags"] = focus
                c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(settings), uid))
            return self.send({"ok": True})
        if path == "/api/predict":
            reference = data.get("referenceRating")
            target = data.get("problemRating")
            return self.send(predict(summarize(user_records(uid)), int(reference) if reference is not None else None,
                                     int(target) if target is not None else None, str(data.get("problemKey", "")).strip().upper(), data.get("platform", "codeforces")))
        if path == "/api/tasks/create":
            title = str(data.get("title", "")).strip()[:160]
            if not title:
                raise ValueError("请输入题目名称")
            rating = data.get("rating")
            if rating is not None:
                rating = int(rating)
                if not 800 <= rating <= 4000:
                    raise ValueError("题目难度应在 800～4000 之间")
            tags = data.get("tags", [])
            if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(t, str) and len(t) <= 50 for t in tags):
                raise ValueError("标签格式错误")
            url = str(data.get("url", ""))[:500]
            if url and not url.startswith("https://codeforces.com/"):
                raise ValueError("第一版任务链接仅支持 https://codeforces.com/")
            key = str(data.get("problemKey", ""))[:60]
            with connect() as c:
                if key and c.execute("SELECT 1 FROM tasks WHERE user_id=? AND problem_key=? AND done=0", (uid, key)).fetchone():
                    raise ValueError("这道题已经在待完成列表中")
                c.execute("INSERT INTO tasks(user_id,title,problem_key,rating,tags,url,created_at) VALUES(?,?,?,?,?,?,?)",
                          (uid, title, key, rating, json.dumps(tags), url, int(time.time())))
            return self.send({"ok": True})
        if path == "/api/tasks/update":
            tid = int(data["id"])
            mode = str(data.get("completionMode", ""))
            if mode not in ("", "independent", "hint", "editorial"):
                raise ValueError("完成方式无效")
            with connect() as c:
                task = c.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (tid, uid)).fetchone()
                if not task:
                    return self.send({"error": "任务不存在"}, 404)
                done = int(bool(data.get("done", task["done"])))
                if done and not (mode or task["completion_mode"]):
                    raise ValueError("请选择独立完成、参考提示或阅读题解")
                c.execute("UPDATE tasks SET done=?,pinned=?,note=?,completion_mode=? WHERE id=? AND user_id=?",
                          (done, int(bool(data.get("pinned", task["pinned"]))), str(data.get("note", task["note"]))[:2000],
                           (mode or task["completion_mode"]) if done else "", tid, uid))
            return self.send({"ok": True})
        self.send({"error": "不存在的操作"}, 404)


class LocalServer(ThreadingHTTPServer):
    # Windows permits two ordinary listeners to bind the same port unless exclusive.
    allow_reuse_address = False
    request_queue_size = 64

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def running_vplab(port):
    """Recognize our compatible local server; never reuse an arbitrary busy port."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            health = json.loads(response.read(4096))
        return isinstance(health, dict) and health.get("app") == "vplab" and health.get("apiVersion") == API_VERSION
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--duo", action="store_true", help="open the 1-3 player room lobby")
    args = parser.parse_args()
    init_db()
    try:
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        if running_vplab(args.port):
            url = f"http://127.0.0.1:{args.port}" + ("/duo" if args.duo else "")
            print(f"VP Lab is already running: {url}", flush=True)
            if args.browser:
                webbrowser.open(url)
            return
        print(f"Cannot start VP Lab on port {args.port}: {exc}", flush=True)
        print("Close the previous VP Lab server window and run StartDemo.cmd again.", flush=True)
        print("Or run: python server.py --port 8766 --browser", flush=True)
        raise SystemExit(1)
    args.port = server.server_port
    REVIEWS.recover()
    SMART.recover()
    room_sync = RoomAutoSync(ROOMS)
    room_sync.start()
    print(f"VP Lab: http://127.0.0.1:{args.port}", flush=True)
    if args.browser:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}" + ("/duo" if args.duo else ""))).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        room_sync.stop()
        server.server_close()


if __name__ == "__main__":
    main()
