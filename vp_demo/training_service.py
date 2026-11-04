"""Persistent daily practice. OJ acquisition stays in adapters; tags never leave this service."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import random
import time

RECENT_COUNT = 10
LONG_BONUS = 0.5
RECENT_PENALTY = 0.3
CATALOG_TTL = 86400
OFFSETS = {"easy": (-600, -200), "normal": (-200, 200), "challenge": (200, 600)}
FEEDBACK = ("independent", "hint", "editorial")
DEFAULTS = {"difficultyMode": "default", "intensity": "normal", "minRating": 1600,
            "maxRating": 2000, "includeAttempted": False}


def day_key(now):
    return datetime.fromtimestamp(now, timezone(timedelta(hours=8))).date().isoformat()


def tag_counts(problems):
    counts = Counter()
    for problem in problems:
        tags = set(problem.get("tags") or [])
        for tag in tags:
            counts[tag] += 1 / len(tags)
    return counts


def tag_weights(tags, historical, recent):
    long_counts, recent_counts = tag_counts(historical), tag_counts(recent)
    maximum, total = max(long_counts.values(), default=0), sum(recent_counts.values())
    return {t: 1 + LONG_BONUS * (1 - long_counts[t] / maximum if maximum else 0)
            - RECENT_PENALTY * (recent_counts[t] / total if total else 0) for t in tags}


def public_problem(problem):
    return {k: problem[k] for k in ("platform", "externalId", "problemKey", "title", "rating", "url")}


class TrainingService:
    def __init__(self, connect, lock, adapters, clock=time.time, rng=None):
        self.connect, self.lock, self.adapters, self.clock = connect, lock, adapters, clock
        self.rng = rng or random.SystemRandom()

    @staticmethod
    def init_tables(c):
        c.executescript('''
        CREATE TABLE IF NOT EXISTS training_profiles(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), preferences TEXT NOT NULL,
          reference INTEGER NOT NULL, cf_rating INTEGER, epoch INTEGER NOT NULL DEFAULT 0,
          assessment TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS training_catalog(
          platform TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS training_recommendations(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), problem TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_runs(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          platform TEXT NOT NULL, problem_key TEXT NOT NULL, problem TEXT NOT NULL,
          status TEXT NOT NULL, started_at INTEGER NOT NULL, last_started_at INTEGER NOT NULL,
          reference INTEGER NOT NULL, epoch INTEGER NOT NULL, ac_id TEXT, ac_at INTEGER,
          feedback TEXT, feedback_at INTEGER, evaluated INTEGER NOT NULL DEFAULT 0,
          skip_reason TEXT NOT NULL DEFAULT '',
          UNIQUE(user_id,platform,problem_key));
        CREATE UNIQUE INDEX IF NOT EXISTS training_one_active ON training_runs(user_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS training_cooldowns(
          user_id INTEGER NOT NULL REFERENCES users(id), platform TEXT NOT NULL,
          problem_key TEXT NOT NULL, day TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(user_id,platform,problem_key));
        ''')

    def _profile(self, c, uid):
        settings = json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])
        cf_rating = (settings.get("profile") or {}).get("rating")
        if not isinstance(cf_rating, int):
            cf_rating = None
        row = c.execute("SELECT * FROM training_profiles WHERE user_id=?", (uid,)).fetchone()
        if row is None:
            c.execute("INSERT INTO training_profiles(user_id,preferences,reference,cf_rating) VALUES(?,?,?,?)",
                      (uid, json.dumps(DEFAULTS), cf_rating if cf_rating is not None else 1800, cf_rating))
        elif cf_rating is not None and cf_rating != row["cf_rating"]:
            c.execute("UPDATE training_profiles SET reference=?,cf_rating=?,epoch=epoch+1,assessment='{}' WHERE user_id=?",
                      (cf_rating, cf_rating, uid))
            c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
        return dict(c.execute("SELECT * FROM training_profiles WHERE user_id=?", (uid,)).fetchone()), settings

    def reconcile(self, uid, c=None):
        if c is None:
            with self.lock, self.connect() as conn:
                return self.reconcile(uid, conn)
        profile, settings = self._profile(c, uid)
        records = [json.loads(r[0]) for r in c.execute("SELECT payload FROM submissions WHERE user_id=?", (uid,))]
        accepted = defaultdict(list)
        for r in records:
            if r.get("verdict") == "OK":
                accepted[(r["platform"], r["problemKey"])].append(r)
        for row in c.execute("SELECT * FROM training_runs WHERE user_id=? AND status IN ('active','skipped')", (uid,)).fetchall():
            ac = [r for r in accepted.get((row["platform"], row["problem_key"]), []) if r["submittedAt"] >= row["started_at"]]
            if ac:
                first = min(ac, key=lambda r: (r["submittedAt"], int(r["externalId"])))
                c.execute("UPDATE training_runs SET status='pending_feedback',ac_id=?,ac_at=? WHERE id=?",
                          (first["externalId"], first["submittedAt"], row["id"]))
        # A recommendation may have been solved on CF before the user starts it.
        rec = c.execute("SELECT problem FROM training_recommendations WHERE user_id=?", (uid,)).fetchone()
        if rec:
            p = json.loads(rec[0])
            if (p["platform"], p["problemKey"]) in accepted:
                c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
        return profile, settings, records

    @staticmethod
    def bounds(profile):
        prefs = json.loads(profile["preferences"])
        if prefs["difficultyMode"] == "manual":
            return prefs["minRating"], prefs["maxRating"]
        lo, hi = OFFSETS[prefs["intensity"]]
        return profile["reference"] + lo, profile["reference"] + hi

    @staticmethod
    def _public_run(row):
        return {"id": row["id"], "problem": public_problem(json.loads(row["problem"])),
                "status": row["status"], "startedAt": row["started_at"], "referenceAtStart": row["reference"],
                "acId": row["ac_id"], "acAt": row["ac_at"], "feedback": row["feedback"],
                "feedbackAt": row["feedback_at"], "skipReason": row["skip_reason"]}

    def state(self, uid):
        with self.lock, self.connect() as c:
            profile, settings, _ = self.reconcile(uid, c)
            rows = c.execute("SELECT * FROM training_runs WHERE user_id=? ORDER BY last_started_at DESC,id DESC", (uid,)).fetchall()
            runs = [self._public_run(r) for r in rows]
            rec = c.execute("SELECT problem FROM training_recommendations WHERE user_id=?", (uid,)).fetchone()
            catalog = c.execute("SELECT updated_at FROM training_catalog WHERE platform='codeforces'").fetchone()
            window = c.execute("SELECT count(*) FROM training_runs WHERE user_id=? AND epoch=? AND status='completed' AND evaluated=0",
                               (uid, profile["epoch"])).fetchone()[0]
            return {"mode": "daily", "preferences": json.loads(profile["preferences"]),
                    "referenceRating": profile["reference"], "cfRating": profile["cf_rating"],
                    "range": list(self.bounds(profile)), "handle": settings.get("handle", ""),
                    "lastSync": settings.get("lastSync"), "historyScope": settings.get("lastScope", "尚未同步提交"),
                    "assessment": json.loads(profile["assessment"]), "feedbackWindow": window,
                    "catalogUpdatedAt": catalog[0] if catalog else None,
                    "recommendation": public_problem(json.loads(rec[0])) if rec else None,
                    "active": next((r for r in runs if r["status"] == "active"), None),
                    "pending": [r for r in runs if r["status"] == "pending_feedback"],
                    "history": runs}

    def preferences(self, uid, data):
        if data.get("mode", "daily") != "daily":
            raise ValueError("当前仅开放日常练习")
        with self.lock, self.connect() as c:
            profile, _ = self._profile(c, uid)
            prefs = json.loads(profile["preferences"])
            for key in DEFAULTS:
                if key in data:
                    prefs[key] = data[key]
            if prefs["difficultyMode"] not in ("default", "manual") or prefs["intensity"] not in OFFSETS:
                raise ValueError("难度设置无效")
            if type(prefs["includeAttempted"]) is not bool:
                raise ValueError("历史题目开关无效")
            if any(type(prefs[k]) is not int for k in ("minRating", "maxRating")) or not 0 <= prefs["minRating"] <= prefs["maxRating"] <= 5000:
                raise ValueError("难度上下限需为 0～5000 的整数，且下限不大于上限")
            c.execute("UPDATE training_profiles SET preferences=? WHERE user_id=?", (json.dumps(prefs), uid))
            c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
        return self.state(uid)

    def catalog(self):
        with self.connect() as c:
            row = c.execute("SELECT * FROM training_catalog WHERE platform='codeforces'").fetchone()
        if row and self.clock() - row["updated_at"] < CATALOG_TTL:
            return json.loads(row["payload"])
        try:
            problems = self.adapters["codeforces"].problem_catalog()
        except ValueError:
            if row:
                return json.loads(row["payload"])
            raise
        with self.lock, self.connect() as c:
            c.execute("INSERT OR REPLACE INTO training_catalog VALUES(?,?,?)",
                      ("codeforces", json.dumps(problems, ensure_ascii=False), int(self.clock())))
        return problems

    def recommend(self, uid, data=None):
        data = data or {}
        if data.get("mode", "daily") != "daily" or data.get("platforms", ["codeforces"]) != ["codeforces"]:
            raise ValueError("当前仅开放 Codeforces 日常练习")
        # Return persisted selections without any network call.
        state = self.state(uid)
        if state["recommendation"] or state["active"] or state["pending"]:
            return state
        catalog = self.catalog()
        with self.lock, self.connect() as c:
            profile, _, records = self.reconcile(uid, c)
            if c.execute("SELECT 1 FROM training_recommendations WHERE user_id=?", (uid,)).fetchone() or c.execute(
                    "SELECT 1 FROM training_runs WHERE user_id=? AND status IN ('active','pending_feedback')", (uid,)).fetchone():
                pass  # Another tab may have started training while the catalog was loading.
            else:
                prefs = json.loads(profile["preferences"])
                lo, hi = self.bounds(profile)
                seen = {(r["platform"], r["problemKey"]) for r in records}
                solved = {(r["platform"], r["problemKey"]) for r in records if r.get("verdict") == "OK"}
                runs = c.execute("SELECT * FROM training_runs WHERE user_id=? ORDER BY last_started_at DESC,id DESC", (uid,)).fetchall()
                seen.update((r["platform"], r["problem_key"]) for r in runs)
                cooldowns = {(r[0], r[1]) for r in c.execute(
                    "SELECT platform,problem_key FROM training_cooldowns WHERE user_id=? AND day=?", (uid, day_key(self.clock())))}
                historical = {(r["platform"], r["problemKey"]): r for r in records}
                historical.update({(r["platform"], r["problem_key"]): json.loads(r["problem"]) for r in runs})
                # Prefer current catalog tags when old imported submissions lack metadata.
                for p in catalog:
                    key = (p["platform"], p["problemKey"])
                    if key in historical:
                        historical[key] = p
                candidates = [p for p in catalog if isinstance(p.get("rating"), int) and lo <= p["rating"] <= hi
                    and (p["platform"], p["problemKey"]) not in solved | cooldowns
                    and (prefs["includeAttempted"] or (p["platform"], p["problemKey"]) not in seen)]
                groups = defaultdict(list)
                for p in candidates:
                    for tag in set(p.get("tags") or ["__untagged__"]):
                        groups[tag].append(p)
                if groups:
                    recent = [json.loads(r["problem"]) for r in runs[:RECENT_COUNT]]
                    weights = tag_weights(groups, historical.values(), recent)
                    chosen_tag = self.rng.choices(list(groups), weights=list(weights.values()), k=1)[0]
                    problem = self.rng.choice(groups[chosen_tag])
                    c.execute("INSERT INTO training_recommendations VALUES(?,?)", (uid, json.dumps(problem, ensure_ascii=False)))
        return self.state(uid)

    def _cooldown(self, c, uid, p, reason):
        c.execute("INSERT OR REPLACE INTO training_cooldowns VALUES(?,?,?,?,?)",
                  (uid, p["platform"], p["problemKey"], day_key(self.clock()), reason))

    def act(self, uid, action, data):
        now = int(self.clock())
        with self.lock, self.connect() as c:
            profile, settings, _ = self.reconcile(uid, c)
            active = c.execute("SELECT id FROM training_runs WHERE user_id=? AND status='active'", (uid,)).fetchone()
            pending = c.execute("SELECT id FROM training_runs WHERE user_id=? AND status='pending_feedback'", (uid,)).fetchone()
            if action in ("start", "dismiss"):
                rec = c.execute("SELECT problem FROM training_recommendations WHERE user_id=?", (uid,)).fetchone()
                if not rec:
                    raise ValueError("推荐已更新，请刷新页面")
                p = json.loads(rec[0])
                if data.get("problemKey") != p["problemKey"] or data.get("platform", "codeforces") != p["platform"]:
                    raise ValueError("推荐已更新，请刷新页面")
                if action == "start":
                    if not settings.get("handle"):
                        raise ValueError("请先在数据与账号中同步 CF 账号，以便核对 AC")
                    if active or pending:
                        raise ValueError("请先处理当前训练或待填写的完成反馈")
                    old = c.execute("SELECT id,status FROM training_runs WHERE user_id=? AND platform=? AND problem_key=?",
                                    (uid, p["platform"], p["problemKey"])).fetchone()
                    if old:
                        if old["status"] != "skipped":
                            raise ValueError("这道题已有训练记录，请刷新页面")
                        c.execute("UPDATE training_runs SET status='active',last_started_at=? WHERE id=?", (now, old["id"]))
                    else:
                        c.execute("INSERT INTO training_runs(user_id,platform,problem_key,problem,status,started_at,last_started_at,reference,epoch) VALUES(?,?,?,?,'active',?,?,?,?)",
                                  (uid, p["platform"], p["problemKey"], json.dumps(p), now, now, profile["reference"], profile["epoch"]))
                else:
                    reason = self._reason(data)
                    self._cooldown(c, uid, p, reason)
                c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
            elif action in ("skip", "resume", "feedback"):
                row = c.execute("SELECT * FROM training_runs WHERE id=? AND user_id=?", (int(data.get("id", 0)), uid)).fetchone()
                if not row:
                    raise ValueError("训练记录不存在")
                if action == "skip":
                    if row["status"] != "active":
                        raise ValueError("当前题已更新；AC 后需填写完成反馈，不能跳过")
                    reason = self._reason(data)
                    c.execute("UPDATE training_runs SET status='skipped',skip_reason=? WHERE id=?", (reason, row["id"]))
                    self._cooldown(c, uid, json.loads(row["problem"]), reason)
                elif action == "resume":
                    if active or pending or row["status"] != "skipped":
                        raise ValueError("请先处理当前训练或待填写的完成反馈")
                    c.execute("UPDATE training_runs SET status='active',last_started_at=? WHERE id=?", (now, row["id"]))
                    c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
                else:
                    mode = data.get("completionMode")
                    if row["status"] != "pending_feedback" or mode not in FEEDBACK:
                        raise ValueError("仅能为已核对 AC 的题填写独立完成、参考提示或参考题解")
                    c.execute("UPDATE training_runs SET status='completed',feedback=?,feedback_at=? WHERE id=?", (mode, now, row["id"]))
                    self._assess(c, uid, profile, now)
            else:
                raise ValueError("未知训练操作")
        return self.state(uid)

    @staticmethod
    def _reason(data):
        reason = data.get("reason", "")
        if reason not in ("", "too_hard", "too_easy", "already_done", "not_now"):
            raise ValueError("换题原因无效")
        return reason

    @staticmethod
    def _assess(c, uid, profile, now):
        rows = c.execute("SELECT * FROM training_runs WHERE user_id=? AND epoch=? AND status='completed' AND evaluated=0 ORDER BY feedback_at,id LIMIT 3",
                         (uid, profile["epoch"])).fetchall()
        if len(rows) < 3:
            return
        up = sum(r["feedback"] == "independent" and json.loads(r["problem"])["rating"] >= r["reference"] for r in rows)
        down = sum(r["feedback"] == "editorial" and json.loads(r["problem"])["rating"] <= r["reference"] for r in rows)
        delta = 100 if up >= 2 else -100 if down >= 2 else 0
        new = max(0, min(5000, profile["reference"] + delta))
        assessment = {"at": now, "delta": new - profile["reference"], "reference": new}
        c.execute("UPDATE training_profiles SET reference=?,assessment=? WHERE user_id=?", (new, json.dumps(assessment), uid))
        c.executemany("UPDATE training_runs SET evaluated=1 WHERE id=?", [(r["id"],) for r in rows])
        c.execute("DELETE FROM training_recommendations WHERE user_id=?", (uid,))
