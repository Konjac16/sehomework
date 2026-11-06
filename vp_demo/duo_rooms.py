"""Small-group VP integration. Authentication, SQLite and the CF client come from VP Lab."""
import json
import re
import secrets
import time
from collections import Counter
from zipfile import ZipFile
from duo_rules import MAX_MEMBERS, RoomError, check_replay, integer, read_catalog, sample_events, scoreboard
from duo_recommendations import recommend_contests
from duo_report import build_report

SYNC_INTERVAL_SECONDS = 15
SYNC_STALE_SECONDS = 90

DDL = """
CREATE TABLE IF NOT EXISTS vp_rooms (
 id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, owner INTEGER NOT NULL REFERENCES users(id),
 mode TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'WAITING', version INTEGER NOT NULL DEFAULT 1,
 max_minutes INTEGER NOT NULL, allow_seen INTEGER NOT NULL DEFAULT 0, chosen TEXT,
 created_at INTEGER NOT NULL, start_at INTEGER, end_at INTEGER, cursor INTEGER NOT NULL DEFAULT 0,
 replay TEXT NOT NULL DEFAULT '[]', final_board TEXT, settled_at INTEGER);
CREATE TABLE IF NOT EXISTS vp_members (
 room_id TEXT NOT NULL REFERENCES vp_rooms(id), uid INTEGER NOT NULL REFERENCES users(id),
 slot INTEGER NOT NULL, handle TEXT NOT NULL COLLATE NOCASE, ready INTEGER NOT NULL DEFAULT 0,
 synced_at INTEGER, sync_through INTEGER, sync_error TEXT NOT NULL DEFAULT '', events TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(room_id,uid), UNIQUE(room_id,slot), UNIQUE(room_id,handle));
CREATE INDEX IF NOT EXISTS vp_members_uid ON vp_members(uid);
CREATE TABLE IF NOT EXISTS vp_upsolves (
 room_id TEXT NOT NULL REFERENCES vp_rooms(id), uid INTEGER NOT NULL REFERENCES users(id),
 idx TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id), demo_done INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(room_id,uid,idx));
"""


def dump(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def fetch_cf_events(adapter, chosen, member, start, end):
    """One bounded, fully paginated contest+handle scan. Never certify a truncated refresh."""
    events = {}
    cid, handle = chosen["contestId"], member["handle"]
    indices = {p["index"] for p in chosen["problems"]}
    for offset in range(1, 1001, 100):
        page = adapter.call("contest.status", contestId=cid, handle=handle, count=100, **{"from": offset})
        if not isinstance(page, list):
            raise ValueError("CF 提交页无效")
        for raw in page:
            author = raw.get("author") or {}
            people = author.get("members", [])
            if (author.get("participantType") not in {"VIRTUAL", "PRACTICE"} or len(people) != 1
                    or str(people[0].get("handle", "")).casefold() != handle.casefold()):
                continue
            row = adapter.normalize(raw)
            if (row["contestId"] == cid and row["index"] in indices and start <= row["submittedAt"] < end):
                eid = int(row["externalId"])
                events[eid] = dict(eventId=eid, slot=member["slot"], index=row["index"],
                                   elapsedSeconds=row["submittedAt"]-start, verdict=row["verdict"])
        if len(page) < 100:
            return list(events.values())
    raise ValueError("本场该账号的提交分页超过上限，未确认完整；保留上次榜单")


class DuoRooms:
    def __init__(self, connect, lock, adapter, catalog_path, clock=time.time):
        self.connect, self.lock, self.adapter, self.clock = connect, lock, adapter, clock
        self.catalog = read_catalog(catalog_path)
        self.reading_pack = catalog_path if catalog_path.suffix.lower() == ".zip" else None
        self.indices = {cid: {p["index"] for p in contest["problems"]} for cid, contest in self.catalog.items()}
        self.syncing = set()
        self.sync_attempts = {}

    @staticmethod
    def init_tables(c):
        c.executescript(DDL)

    @staticmethod
    def _record(c, rid, uid):
        row = c.execute("SELECT r.* FROM vp_rooms r JOIN vp_members m ON m.room_id=r.id "
                        "WHERE r.id=? AND m.uid=?", (rid, uid)).fetchone()
        if not row:
            raise RoomError("房间不存在或你不是成员", 404)
        return dict(row)

    @staticmethod
    def _members(c, rid):
        return [dict(x) for x in c.execute("SELECT * FROM vp_members WHERE room_id=? ORDER BY slot", (rid,))]

    @staticmethod
    def _settings(c, uid):
        return json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])

    def _handle(self, c, uid, handle, mode):
        bound = self._settings(c, uid).get("handle", "")
        handle = str(handle or "").strip() or bound
        if mode == "replay" and not handle:
            return c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()[0]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", handle):
            raise RoomError("CF 用户名须为 1 至 40 位字母、数字、下划线、点或短横线")
        if bound and handle.casefold() != bound.casefold():
            raise RoomError("请使用此账号已绑定的训练 handle")
        return handle

    @staticmethod
    def _known(c, member, *, ac_before=None):
        sql = """SELECT DISTINCT CAST(json_extract(payload,'$.contestId') AS INTEGER),json_extract(payload,'$.index')
                 FROM submissions WHERE user_id=? AND platform='codeforces'
                 AND json_extract(payload,'$.handle')=? COLLATE NOCASE"""
        args = [member["uid"], member["handle"]]
        if ac_before is not None:
            sql += " AND json_extract(payload,'$.verdict')='OK' AND json_extract(payload,'$.submittedAt')<?"
            args.append(ac_before)
        return {tuple(r) for r in c.execute(sql, args)}

    def _overlaps(self, c, members):
        return Counter(cid for m in members for cid, idx in self._known(c, m)
                       if cid in self.indices and idx in self.indices[cid])

    def _eligible(self, c, r, ms, cid):
        return (cid in self.catalog and self.catalog[cid]["durationSeconds"] <= 60*r["max_minutes"]
                and (r["allow_seen"] or not self._overlaps(c, ms)[cid]))

    def _recommendations(self, c, r, members):
        profiles = []
        for member in members:
            settings = self._settings(c, member["uid"])
            reference = settings.get("referenceRating")
            if type(reference) is not int or not 0 <= reference <= 4000:
                profile = settings.get("profile") or {}
                same_handle = str(profile.get("handle") or settings.get("handle", "")).casefold() == member["handle"].casefold()
                reference = profile.get("rating") if same_handle else None
            if type(reference) is not int or not 0 <= reference <= 4000:
                reference = None
            profiles.append(dict(slot=member["slot"], handle=member["handle"], referenceRating=reference))
        rows = recommend_contests(self.catalog, profiles, self._overlaps(c, members),
                                  r["max_minutes"], bool(r["allow_seen"]))
        note = "先按时长和已知接触记录筛选，再兼顾全员适配与较弱成员；规则推荐不代表通过概率。"
        if any(p["referenceRating"] is None for p in profiles):
            note += " 部分成员缺少参考水平，适配判断有限；可在个人工作台设置参考 Rating 或同步 CF 资料。"
        return rows, note

    def auto_sync_once(self):
        """Refresh one due seat, fairly across rooms. No network call holds the DB lock."""
        now = int(self.clock())
        with self.lock, self.connect() as c:
            rows = c.execute("SELECT m.room_id,m.uid,m.slot,r.start_at FROM vp_members m "
                             "JOIN vp_rooms r ON r.id=m.room_id WHERE r.mode='training' "
                             "AND r.phase IN ('RUNNING','SETTLING')").fetchall()
            active = {(r["room_id"], r["uid"]) for r in rows}
            self.sync_attempts = {k: v for k, v in self.sync_attempts.items() if k in active}
            due = []
            for row in rows:
                marker = (row["room_id"], row["uid"])
                attempted = self.sync_attempts.get(marker)
                if marker not in self.syncing and (attempted is None or now-attempted >= SYNC_INTERVAL_SECONDS):
                    due.append((attempted if attempted is not None else -1, row["start_at"],
                                row["slot"], row["room_id"], row["uid"]))
            if not due:
                return False
            _, _, _, rid, uid = min(due)
        try:
            self._sync(uid, rid)
        except RoomError:
            # Failures are recorded per member by _sync; the next seat still gets a turn.
            pass
        return True

    @staticmethod
    def _invited_room(c, code):
        code = str(code or "").strip().upper()
        row = c.execute("SELECT * FROM vp_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            raise RoomError("找不到这个房间，请核对十位房间码", 404)
        return row

    @staticmethod
    def _join_issue(row, members):
        if row["phase"] != "WAITING":
            return "这个房间已关闭入场，请队友创建新房间"
        if len(members) >= MAX_MEMBERS:
            return f"房间已满，最多 {MAX_MEMBERS} 人"
        return ""

    def _options(self, c, r, members, query):
        term = str(query.get("q", "")).strip()
        page_arg = str(query.get("page", "1"))
        if len(term) > 100 or not re.fullmatch(r"[1-9][0-9]{0,5}", page_arg):
            raise RoomError("搜索词最多 100 字；页码须为正整数")
        numeric = term.removeprefix("#")
        find_id = numeric if numeric.isascii() and numeric.isdecimal() else None
        needle = term.casefold()
        only = query.get("eligible") == "1"
        overlaps = self._overlaps(c, members)
        eligible_total = 0
        matches = []
        for cid, contest in self.catalog.items():
            overlap = overlaps[cid]
            over_time = contest["durationSeconds"] > r["max_minutes"]*60
            seen = bool(overlap and not r["allow_seen"])
            eligible = not (over_time or seen)
            eligible_total += eligible
            if only and not eligible:
                continue
            if (find_id is not None and str(cid) != find_id) or (find_id is None and needle not in contest["name"].casefold()):
                continue
            matches.append((not eligible, overlap, -contest["startTimeSeconds"], cid, over_time, seen))
        matches.sort()
        size = 8
        pages = (len(matches)+size-1)//size
        page = min(int(page_arg), max(1, pages))
        rows = []
        for blocked, overlap, _, cid, over_time, seen in matches[(page-1)*size:page*size]:
            contest = self.catalog[cid]
            reasons = (["超过可用时长"] if over_time else []) + (["至少一人在已导入记录中接触过本场"] if seen else [])
            rows.append(dict(contestId=cid, name=contest["name"], durationSeconds=contest["durationSeconds"],
                             problemCount=len(self.indices[cid]), eligible=not blocked, knownOverlap=overlap,
                             extendToMinutes=(contest["durationSeconds"] + 59)//60 if over_time and not seen else None,
                             reason="；".join(reasons) or ("已允许重复练习" if overlap else "时长合适，现有记录未发现重合")))
        return rows, dict(total=len(self.catalog), eligible=eligible_total, matched=len(matches), page=page,
                          pages=pages, pageSize=size, q=term, onlyEligible=only)

    def _statement(self, c, uid, rid, idx):
        r = self._record(c, rid, uid)
        if r["phase"] not in ("RUNNING", "SETTLING", "FINISHED"):
            raise RoomError("开赛后才能查看本场题面", 409)
        chosen = json.loads(r["chosen"])
        if not self.reading_pack or not any(p["index"] == idx for p in chosen["problems"]):
            raise RoomError("未收录这道房间题目，可打开 CF 原题", 404)
        try:
            with ZipFile(self.reading_pack) as pack:
                p = json.loads(pack.read(f"statements/{chosen['contestId']}.json"))[idx]
        except KeyError:
            raise RoomError("本地快照未收录此题，请打开 CF 原题", 404) from None
        return dict(contestId=chosen["contestId"], index=idx, **p)

    def _finish(self, c, r, members, contest, board, now):
        # Each transition and all task writes are in the same transaction. A GET cannot double-settle.
        solved = {row["slot"]: {idx for idx, cell in row["cells"].items() if cell["acSeconds"] is not None} for row in board}
        for m in members:
            prior = self._known(c, m, ac_before=r["start_at"])
            for p in contest["problems"]:
                idx = p["index"]
                if idx in solved[m["slot"]] or (contest["contestId"], idx) in prior:
                    continue
                task_id = None
                if r["mode"] == "training":
                    key = f"{contest['contestId']}{idx}"
                    old = c.execute("SELECT id FROM tasks WHERE user_id=? AND problem_key=? ORDER BY done,id LIMIT 1",
                                    (m["uid"], key)).fetchone()
                    task_id = old[0] if old else c.execute(
                        "INSERT INTO tasks(user_id,title,problem_key,rating,tags,url,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (m["uid"], p["title"], key, p["rating"], dump(p["tags"]), p["url"],
                         f"{len(members)} 人 VP · {contest['name']}", now)).lastrowid
                c.execute("INSERT INTO vp_upsolves(room_id,uid,idx,task_id) VALUES(?,?,?,?)",
                          (r["id"], m["uid"], idx, task_id))
        c.execute("UPDATE vp_rooms SET phase='FINISHED',final_board=?,settled_at=? WHERE id=?", (dump(board), now, r["id"]))
        r.update(phase="FINISHED", final_board=dump(board), settled_at=now)

    def _view(self, c, uid, rid, query=None):
        r = self._record(c, rid, uid)
        ms = self._members(c, rid)
        now = int(self.clock())
        chosen = json.loads(r["chosen"]) if r["chosen"] else None
        out = {"id": rid, "inviteCode": r["code"], "mode": r["mode"], "phase": r["phase"], "version": r["version"],
               "maxMembers": MAX_MEMBERS, "isOwner": r["owner"] == uid, "serverNow": now,
               "startAt": r["start_at"], "endAt": r["end_at"],
               "maxMinutes": r["max_minutes"], "allowSeen": bool(r["allow_seen"]), "selected": None,
               "members": [{"slot": m["slot"], "handle": m["handle"], "isMe": m["uid"] == uid,
                            "ready": bool(m["ready"]), "syncedAt": m["synced_at"], "syncError": m["sync_error"],
                            "syncing": (rid, m["uid"]) in self.syncing,
                            "syncDelayed": (r["mode"] == "training" and r["phase"] in ("RUNNING", "SETTLING")
                                            and now-(m["synced_at"] or r["start_at"]) > SYNC_STALE_SECONDS),
                            "through": m["sync_through"]} for m in ms],
               "autoSync": {"enabled": False, "intervalSeconds": SYNC_INTERVAL_SECONDS, "staleAfterSeconds": SYNC_STALE_SECONDS},
               "candidates": [], "recommendations": [], "recommendationNote": "", "report": None,
               "problems": [], "standings": [], "upsolves": [], "elapsedSeconds": 0}
        if chosen:
            out["selected"] = {k: chosen[k] for k in ("contestId", "name", "durationSeconds")}
        if r["phase"] == "WAITING":
            out["candidates"], out["candidatePage"] = self._options(c, r, ms, query or {})
            out["recommendations"], out["recommendationNote"] = self._recommendations(c, r, ms)
            return out
        if r["phase"] == "CANCELLED":
            return out
        duration = chosen["durationSeconds"]
        elapsed = r["cursor"] if r["mode"] == "replay" else min(duration, max(0, now-r["start_at"]))
        out["elapsedSeconds"] = elapsed
        out["problems"] = [{k: p[k] for k in ("index", "title", "url")} for p in chosen["problems"]]
        out["offlineReading"] = self.reading_pack is not None and chosen["contestId"] in self.catalog
        if r["final_board"]:
            board = json.loads(r["final_board"])
        else:
            events = json.loads(r["replay"]) if r["mode"] == "replay" else [e for m in ms for e in json.loads(m["events"])]
            board = scoreboard(ms, chosen, events, elapsed)
            if elapsed == duration:
                complete = r["mode"] == "replay" or (all((m["sync_through"] or 0) >= r["end_at"] for m in ms)
                            and not any((rid, m["uid"]) in self.syncing for m in ms))
                pending = any(cell["pending"] for row in board for cell in row["cells"].values())
                if complete and not pending:
                    self._finish(c, r, ms, chosen, board, now)
                elif r["phase"] != "SETTLING":
                    c.execute("UPDATE vp_rooms SET phase='SETTLING' WHERE id=?", (rid,))
                    r["phase"] = "SETTLING"
        out.update(phase=r["phase"], standings=board, settledAt=r["settled_at"])
        out["autoSync"]["enabled"] = r["mode"] == "training" and r["phase"] in ("RUNNING", "SETTLING")
        if r["phase"] == "FINISHED":
            out["report"] = build_report(chosen, board, mode=r["mode"], settled_at=r["settled_at"])
            for member in out["members"]:
                member["syncDelayed"] = False
            rows = c.execute("SELECT v.idx,v.task_id,COALESCE(t.done,v.demo_done) AS done FROM vp_upsolves v "
                             "LEFT JOIN tasks t ON t.id=v.task_id WHERE v.room_id=? AND v.uid=? ORDER BY v.idx", (rid, uid))
            by_idx = {p["index"]: p for p in chosen["problems"]}
            out["upsolves"] = [dict(index=x["idx"], title=by_idx[x["idx"]]["title"], url=by_idx[x["idx"]]["url"],
                                     taskId=x["task_id"], done=bool(x["done"])) for x in rows]
        return out

    def _sync(self, uid, rid):
        marker = (rid, uid)
        with self.lock, self.connect() as c:
            r = self._record(c, rid, uid)
            if r["mode"] != "training" or r["phase"] not in ("RUNNING","SETTLING"):
                raise RoomError("仅进行中或待结算的真实训练可同步", 409)
            if marker in self.syncing:
                raise RoomError("此成员已有同步正在进行", 409)
            m = next(x for x in self._members(c, rid) if x["uid"] == uid)
            began = int(self.clock())
            self.syncing.add(marker)
            self.sync_attempts[marker] = began
        released = False
        try:
            try:
                events = fetch_cf_events(self.adapter, json.loads(r["chosen"]), m, r["start_at"], r["end_at"])
            except Exception as exc:
                msg = str(exc)[:180] if isinstance(exc, ValueError) else "CF 暂不可用，请稍后重试"
                with self.lock, self.connect() as c:
                    c.execute("UPDATE vp_members SET sync_error=? WHERE room_id=? AND uid=?", (msg, rid, uid))
                raise RoomError("未更新榜单，保留上次结果："+msg, 503) from None
            with self.lock, self.connect() as c:
                c.execute("UPDATE vp_members SET synced_at=?,sync_through=?,sync_error='',events=? WHERE room_id=? AND uid=?",
                          (int(self.clock()), began, dump(events), rid, uid))
                self.syncing.discard(marker)
                self.sync_attempts[marker] = int(self.clock())
                released = True
                return self._view(c, uid, rid)
        finally:
            if not released:
                with self.lock:
                    self.syncing.discard(marker)
                    self.sync_attempts[marker] = int(self.clock())

    def handle(self, method, path, uid, data=None, query=None):
        data = data or {}
        match = re.fullmatch(r"/api/rooms(?:/([A-Za-z0-9]+)(?:/([a-z-]+))?)?", path)
        if not match:
            raise RoomError("房间接口不存在", 404)
        rid, action = match.groups()
        if method == "GET" and action == "statement":
            with self.connect() as c:
                return self._statement(c, uid, rid, (query or {}).get("index", ""))
        if method == "POST" and action == "sync":
            return self._sync(uid, rid)
        if method == "GET" and action == "report":
            with self.lock, self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                view = self._view(c, uid, rid)
                if view["phase"] != "FINISHED":
                    raise RoomError("终场结算后才能查看战报", 409)
                return view["report"]
        with self.lock, self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if method == "GET" and rid == "invite" and not action:
                row = self._invited_room(c, (query or {}).get("code"))
                members = self._members(c, row["id"])
                member = any(m["uid"] == uid for m in members)
                return {"mode": row["mode"], "memberCount": len(members), "maxMembers": MAX_MEMBERS,
                        "memberRoomId": row["id"] if member else None,
                        "issue": "" if member else self._join_issue(row, members),
                        "boundHandle": self._settings(c, uid).get("handle", "")}
            if method == "GET" and not rid:
                rows = c.execute("SELECT r.* FROM vp_rooms r JOIN vp_members m ON m.room_id=r.id "
                                 "WHERE m.uid=? ORDER BY r.created_at DESC,r.rowid DESC LIMIT 40", (uid,))
                return {"handle": self._settings(c, uid).get("handle", ""), "maxMembers": MAX_MEMBERS,
                        "rooms": [{"id": r["id"], "inviteCode": r["code"], "mode":r["mode"], "phase":r["phase"],
                                   "name": json.loads(r["chosen"])["name"] if r["chosen"] else "尚未选场"} for r in rows]}
            if method == "GET" and rid and not action:
                return self._view(c, uid, rid, query)
            if method != "POST":
                raise RoomError("接口不存在", 404)
            if not rid:
                mode = data.get("mode", "training")
                if mode not in ("training","replay"):
                    raise RoomError("请选择真实训练或回放")
                minutes = integer(data.get("maxMinutes",120), 1, 1440, "时长预算")
                handle = self._handle(c, uid, data.get("handle"), mode)
                rid = secrets.token_hex(10)
                c.execute("INSERT INTO vp_rooms(id,code,owner,mode,max_minutes,created_at) VALUES(?,?,?,?,?,?)",
                          (rid,secrets.token_hex(5).upper(),uid,mode,minutes,int(self.clock())))
                c.execute("INSERT INTO vp_members(room_id,uid,slot,handle) VALUES(?,?,1,?)", (rid,uid,handle))
                return self._view(c, uid, rid)
            if rid == "join" and not action:
                row = self._invited_room(c, data.get("code"))
                ms = self._members(c, row["id"])
                if any(m["uid"] == uid for m in ms):
                    return self._view(c, uid, row["id"])
                issue = self._join_issue(row, ms)
                if issue:
                    raise RoomError(issue, 409)
                handle = self._handle(c, uid, data.get("handle"), row["mode"])
                if any(m["handle"].casefold() == handle.casefold() for m in ms):
                    raise RoomError("成员不能使用同一 handle", 409)
                slot = next(n for n in range(1, MAX_MEMBERS+1) if n not in {m["slot"] for m in ms})
                c.execute("INSERT INTO vp_members(room_id,uid,slot,handle) VALUES(?,?,?,?)", (row["id"],uid,slot,handle))
                # Both the suitability calculation and replay slots belong to the old roster.
                c.execute("UPDATE vp_rooms SET chosen=NULL,replay='[]',version=version+1 WHERE id=?", (row["id"],))
                c.execute("UPDATE vp_members SET ready=0 WHERE room_id=?", (row["id"],))
                return self._view(c, uid, row["id"])
            r = self._record(c, rid, uid)
            ms = self._members(c, rid)
            if action in ("select","preferences","start","replay","replay-file","cancel") and r["owner"] != uid:
                raise RoomError("仅房主可执行此操作", 403)
            if action in ("select","preferences","ready","start","replay-file","cancel"):
                if r["phase"] != "WAITING" or data.get("version") != r["version"]:
                    raise RoomError("房间或选场已变化，请按最新名单重新确认", 409)
                if action == "cancel":
                    c.execute("UPDATE vp_rooms SET phase='CANCELLED' WHERE id=?", (rid,))
                elif action == "preferences":
                    mins = integer(data.get("maxMinutes"),1,1440,"时长预算")
                    if type(data.get("allowSeen")) is not bool:
                        raise RoomError("请明确是否允许已接触的比赛")
                    c.execute("UPDATE vp_rooms SET max_minutes=?,allow_seen=?,chosen=NULL,replay='[]',version=version+1 WHERE id=?",
                              (mins,int(data["allowSeen"]),rid))
                elif action == "select":
                    cid = integer(data.get("contestId"),1,99999,"比赛编号")
                    minutes = r["max_minutes"]
                    if "extendToMinutes" in data:
                        minutes = integer(data["extendToMinutes"], 1, 1440, "时长预算")
                        chosen = self.catalog.get(cid)
                        if not chosen or minutes <= r["max_minutes"] or minutes != (chosen["durationSeconds"] + 59)//60:
                            raise RoomError("预算已变化，请按本场实际时长重新选择", 409)
                    if type(data.get("confirmOwner", False)) is not bool:
                        raise RoomError("请明确是否确认本场规则")
                    if not self._eligible(c, {**r, "max_minutes": minutes}, ms, cid):
                        raise RoomError("请选择符合当前成员与时长条件的比赛",409)
                    chosen = self.catalog[cid]
                    events = sample_events(chosen, {m["slot"] for m in ms}) if r["mode"] == "replay" else []
                    c.execute("UPDATE vp_rooms SET max_minutes=?,chosen=?,replay=?,version=version+1 WHERE id=?",
                              (minutes, dump(chosen), dump(events), rid))
                elif action == "replay-file":
                    if r["mode"]!="replay" or not r["chosen"]:
                        raise RoomError("仅可在回放房选场后载入事件",409)
                    ev = check_replay(data.get("events"), json.loads(r["chosen"]), {m["slot"] for m in ms})
                    c.execute("UPDATE vp_rooms SET replay=?,version=version+1 WHERE id=?", (dump(ev),rid))
                else:
                    if not r["chosen"]:
                        raise RoomError("请先按当前成员名单选场",409)
                    cid = json.loads(r["chosen"])["contestId"]
                    if not self._eligible(c, r, ms, cid):
                        raise RoomError("新的提交记录改变了适配情况，请重新选场或调整约束",409)
                    if action == "ready":
                        c.execute("UPDATE vp_members SET ready=1 WHERE room_id=? AND uid=?", (rid,uid))
                    elif not all(m["ready"] for m in ms):
                        raise RoomError("当前房间的每位成员都要确认本场规则",409)
                    else:
                        start = int(self.clock())
                        c.execute("UPDATE vp_rooms SET phase='RUNNING',start_at=?,end_at=? WHERE id=?",
                                  (start,start+json.loads(r["chosen"])["durationSeconds"],rid))
                if action in ("select","preferences","replay-file"):
                    c.execute("UPDATE vp_members SET ready=0 WHERE room_id=?", (rid,))
                    if action == "select" and data.get("confirmOwner") is True:
                        c.execute("UPDATE vp_members SET ready=1 WHERE room_id=? AND uid=?", (rid, uid))
            elif action == "replay":
                if r["mode"] != "replay" or r["phase"] != "RUNNING":
                    raise RoomError("只有进行中的回放房间能推进演示时间",409)
                cursor = integer(data.get("elapsedSeconds"),r["cursor"],json.loads(r["chosen"])["durationSeconds"],"回放进度")
                c.execute("UPDATE vp_rooms SET cursor=? WHERE id=?", (cursor,rid))
            elif action == "demo-task":
                if r["mode"] != "replay" or r["phase"] != "FINISHED" or type(data.get("done")) is not bool:
                    raise RoomError("只能修改已结算回放中的本人演示任务",409)
                if not c.execute("UPDATE vp_upsolves SET demo_done=? WHERE room_id=? AND uid=? AND idx=?",
                                 (int(data["done"]),rid,uid,str(data.get("index","")))).rowcount:
                    raise RoomError("该任务不在你的补题清单",404)
            else:
                raise RoomError("房间接口不存在",404)
            return self._view(c, uid, rid, query)
