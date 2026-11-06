"""Synthetic CF fixture for browser acceptance; never run against a personal DB.

Only this test entry point exposes the token-protected control route. The normal
server has no such route. All Codeforces calls are replaced in this process.
"""
import hmac
import os
from pathlib import Path
import sys
import time

if not os.environ.get("VPLAB_DB") or not os.environ.get("VP_ROOM_TEST_TOKEN"):
    raise SystemExit("Set an isolated VPLAB_DB and VP_ROOM_TEST_TOKEN for this test fixture.")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

base = [int(time.time())]
origin = time.monotonic()
state = {"submitted": False, "failure": False}
server.ROOMS.clock = lambda: base[0] + int(time.monotonic()-origin)


def synthetic_cf(method, **params):
    if method != "contest.status":
        raise ValueError("This acceptance fixture only supports contest.status")
    if state["failure"]:
        raise ValueError("模拟网络中断，稍后自动重试")
    if not state["submitted"] or params.get("from", 1) != 1:
        return []
    cid, handle = params["contestId"], params["handle"]
    with server.connect() as c:
        row = c.execute("SELECT r.start_at,r.chosen FROM vp_rooms r JOIN vp_members m ON m.room_id=r.id "
                        "WHERE r.mode='training' AND r.phase IN ('RUNNING','SETTLING') "
                        "AND m.handle=? ORDER BY r.created_at DESC", (handle,)).fetchone()
    if not row:
        return []
    changes = [(1001, 300, "WRONG_ANSWER"), (1002, 1200, "OK")] if handle == "alpha" else [(2001, 1800, "OK")]
    return [{"id": sid, "contestId": cid, "creationTimeSeconds": row["start_at"]+seconds,
             "verdict": verdict, "problem": {"contestId": cid, "index": "A", "name": "Synthetic A"},
             "author": {"participantType": "PRACTICE", "members": [{"handle": handle}]}}
            for sid, seconds, verdict in changes]


class FixtureHandler(server.Handler):
    def post_route(self, path, data):
        if path != "/__acceptance/control":
            return super().post_route(path, data)
        if not hmac.compare_digest(self.headers.get("X-Test-Token", ""), os.environ["VP_ROOM_TEST_TOKEN"]):
            return self.send({"error": "test authorization required"}, 403)
        with server.DB_LOCK:
            for key in ("submitted", "failure"):
                if key in data:
                    state[key] = bool(data[key])
            base[0] += int(data.get("advance", 0))
            if data.get("settle"):
                with server.connect() as c:
                    end = c.execute("SELECT end_at FROM vp_rooms WHERE id=?", (data["settle"],)).fetchone()[0]
                base[0] = end-int(time.monotonic()-origin)
        return self.send({"ok": True})

    def send(self, data, status=200, content_type="application/json; charset=utf-8", extra=None):
        if content_type.startswith("text/html"):
            data = data.replace(b"<main>", "<main><div class=\"banner quiet\">功能验收预览 · 合成提交 / 独立测试数据库 · 不连接 Codeforces</div>".encode())
        return super().send(data, status, content_type, extra)


server.Handler = FixtureHandler
server.ROOMS.adapter.call = synthetic_cf

if __name__ == "__main__":
    server.main()
