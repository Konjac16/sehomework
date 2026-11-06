"""Daily/focused practice: independent ratings, shared runs and completion records."""
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
MODES = ('daily', 'focused')


def profile_table(mode):
    if mode not in MODES:raise ValueError('训练模式无效')
    return 'training_profiles' if mode=='daily' else 'training_focused_profiles'


def recommendation_table(mode):
    profile_table(mode)
    return 'training_recommendations' if mode=='daily' else 'training_focused_recommendations'


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
        CREATE TABLE IF NOT EXISTS training_focused_profiles(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), preferences TEXT NOT NULL,
          reference INTEGER NOT NULL, cf_rating INTEGER, epoch INTEGER NOT NULL DEFAULT 0,
          assessment TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS training_mode_selection(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_catalog(
          platform TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS training_recommendations(
          user_id INTEGER PRIMARY KEY REFERENCES users(id), problem TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_focused_recommendations(
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
        # Keep all existing IDs, feedback and reference values. Legacy runs are daily.
        if 'mode' not in {r[1] for r in c.execute('PRAGMA table_info(training_runs)')}:
            c.execute("ALTER TABLE training_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'daily'")

    @staticmethod
    def _mode(c, uid, mode=None):
        if mode is None:
            row=c.execute('SELECT mode FROM training_mode_selection WHERE user_id=?',(uid,)).fetchone()
            mode=row[0] if row else 'daily'
        profile_table(mode)
        return mode

    def select_mode(self, uid, data):
        if set(data)!={'mode'}:raise ValueError('切换模式只接受 mode')
        mode=data['mode'];profile_table(mode)
        with self.lock,self.connect() as c:
            c.execute('INSERT OR REPLACE INTO training_mode_selection VALUES(?,?)',(uid,mode))
        return self.state(uid,mode)

    def _profile(self, c, uid, mode=None):
        mode=self._mode(c,uid,mode)
        settings = json.loads(c.execute("SELECT settings FROM users WHERE id=?", (uid,)).fetchone()[0])
        cf_rating = (settings.get("profile") or {}).get("rating")
        if type(cf_rating) is not int or cf_rating < 0:
            cf_rating = None
        for name in MODES:
            table=profile_table(name)
            row = c.execute(f'SELECT * FROM {table} WHERE user_id=?', (uid,)).fetchone()
            if row is None:
                prefs=DEFAULTS | ({'directions':[]} if name=='focused' else {})
                c.execute(f'INSERT INTO {table}(user_id,preferences,reference,cf_rating) VALUES(?,?,?,?)',
                          (uid,json.dumps(prefs),cf_rating if cf_rating is not None else 1800,cf_rating))
            elif cf_rating is not None and cf_rating != row['cf_rating']:
                c.execute(f"UPDATE {table} SET reference=?,cf_rating=?,epoch=epoch+1,assessment='{{}}' WHERE user_id=?",
                          (cf_rating,cf_rating,uid))
                c.execute(f'DELETE FROM {recommendation_table(name)} WHERE user_id=?',(uid,))
        return dict(c.execute(f'SELECT * FROM {profile_table(mode)} WHERE user_id=?',(uid,)).fetchone()) | {'mode':mode},settings

    def reconcile(self, uid, c=None, mode=None):
        if c is None:
            with self.lock, self.connect() as conn:
                return self.reconcile(uid, conn, mode)
        profile, settings = self._profile(c, uid, mode)
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
        blocked=set(accepted)
        blocked.update((r[0],r[1]) for r in c.execute("SELECT platform,problem_key FROM training_runs WHERE user_id=? AND status IN ('active','pending_feedback','completed')",(uid,)))
        blocked.update((r[0],r[1]) for r in c.execute('SELECT platform,problem_key FROM training_cooldowns WHERE user_id=? AND day=?',(uid,day_key(self.clock()))))
        for name in MODES:
            table=recommendation_table(name)
            rec=c.execute(f'SELECT problem FROM {table} WHERE user_id=?',(uid,)).fetchone()
            if rec:
                p=json.loads(rec[0])
                if (p['platform'],p['problemKey']) in blocked:
                    c.execute(f'DELETE FROM {table} WHERE user_id=?',(uid,))
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
        return {"id": row["id"], "mode":row['mode'], "problem": public_problem(json.loads(row["problem"])),
                "status": row["status"], "startedAt": row["started_at"], "referenceAtStart": row["reference"],
                "acId": row["ac_id"], "acAt": row["ac_at"], "feedback": row["feedback"],
                "feedbackAt": row["feedback_at"], "skipReason": row["skip_reason"]}

    def state(self, uid, mode=None):
        with self.lock, self.connect() as c:
            profile, settings, _ = self.reconcile(uid, c, mode)
            mode=profile['mode']
            rows = c.execute("SELECT * FROM training_runs WHERE user_id=? ORDER BY last_started_at DESC,id DESC", (uid,)).fetchall()
            runs = [self._public_run(r) for r in rows]
            rec = c.execute(f'SELECT problem FROM {recommendation_table(mode)} WHERE user_id=?', (uid,)).fetchone()
            catalog = c.execute("SELECT updated_at,payload FROM training_catalog WHERE platform='codeforces'").fetchone()
            window = c.execute("SELECT count(*) FROM training_runs WHERE user_id=? AND mode=? AND epoch=? AND status='completed' AND evaluated=0",
                               (uid,mode,profile["epoch"])).fetchone()[0]
            directions=sorted({tag for p in json.loads(catalog[1]) for tag in p.get('tags',[]) if not tag.startswith('*')}) if mode=='focused' and catalog else []
            return {"mode":mode, "preferences": json.loads(profile["preferences"]),
                    'directionOptions':directions,
                    'ratings':{name:c.execute(f'SELECT reference FROM {profile_table(name)} WHERE user_id=?',(uid,)).fetchone()[0] for name in MODES},
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
        with self.connect() as c:mode=self._mode(c,uid,data.get('mode'))
        if set(data)-set(DEFAULTS)-{'mode','directions'}:raise ValueError('训练设置包含未知字段')
        if 'directions' in data:
            dirs=data['directions']
            if mode!='focused' or not isinstance(dirs,list) or not 1<=len(dirs)<=3 or not all(isinstance(d,str) for d in dirs) or len(set(dirs))!=len(dirs):
                raise ValueError('专项训练请选择 1～3 个不同方向')
            available={t for p in self.catalog() for t in p.get('tags',[]) if not t.startswith('*')}
            if not set(dirs)<=available:raise ValueError('所选训练方向不在当前题库中')
        with self.lock, self.connect() as c:
            profile, _ = self._profile(c, uid, mode)
            prefs = json.loads(profile["preferences"])
            for key in list(DEFAULTS)+(['directions'] if mode=='focused' else []):
                if key in data:
                    prefs[key] = data[key]
            if prefs["difficultyMode"] not in ("default", "manual") or prefs["intensity"] not in OFFSETS:
                raise ValueError("难度设置无效")
            if type(prefs["includeAttempted"]) is not bool:
                raise ValueError("历史题目开关无效")
            if any(type(prefs[k]) is not int for k in ("minRating", "maxRating")) or not 0 <= prefs["minRating"] <= prefs["maxRating"] <= 5000:
                raise ValueError("难度上下限需为 0～5000 的整数，且下限不大于上限")
            c.execute(f'UPDATE {profile_table(mode)} SET preferences=? WHERE user_id=?', (json.dumps(prefs), uid))
            c.execute(f'DELETE FROM {recommendation_table(mode)} WHERE user_id=?', (uid,))
        return self.state(uid,mode)

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
        with self.connect() as c:mode=self._mode(c,uid,data.get('mode'))
        if data.get('platforms',['codeforces'])!=['codeforces']:raise ValueError('当前仅开放 Codeforces')
        # Return persisted selections without any network call.
        state = self.state(uid,mode)
        if state["recommendation"] or state["active"] or state["pending"]:
            return state
        catalog = self.catalog()
        with self.lock, self.connect() as c:
            profile, _, records = self.reconcile(uid, c, mode)
            if c.execute(f'SELECT 1 FROM {recommendation_table(mode)} WHERE user_id=?', (uid,)).fetchone() or c.execute(
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
                        if mode=='focused' and tag not in prefs['directions']:continue
                        groups[tag].append(p)
                if groups:
                    if mode=='focused':
                        chosen_tag=self.rng.choice(sorted(groups))
                    else:
                        recent = [json.loads(r["problem"]) for r in runs[:RECENT_COUNT]]
                        weights = tag_weights(groups, historical.values(), recent)
                        chosen_tag = self.rng.choices(list(groups), weights=list(weights.values()), k=1)[0]
                    problem = self.rng.choice(groups[chosen_tag])
                    c.execute(f'INSERT INTO {recommendation_table(mode)} VALUES(?,?)', (uid, json.dumps(problem, ensure_ascii=False)))
        return self.state(uid,mode)

    def _cooldown(self, c, uid, p, reason):
        c.execute("INSERT OR REPLACE INTO training_cooldowns VALUES(?,?,?,?,?)",
                  (uid, p["platform"], p["problemKey"], day_key(self.clock()), reason))

    def act(self, uid, action, data):
        now = int(self.clock())
        with self.lock, self.connect() as c:
            profile, settings, _ = self.reconcile(uid, c, data.get('mode'))
            view_mode=profile['mode']
            active = c.execute("SELECT id FROM training_runs WHERE user_id=? AND status='active'", (uid,)).fetchone()
            pending = c.execute("SELECT id FROM training_runs WHERE user_id=? AND status='pending_feedback'", (uid,)).fetchone()
            if action in ("start", "dismiss"):
                rec = c.execute(f'SELECT problem FROM {recommendation_table(view_mode)} WHERE user_id=?', (uid,)).fetchone()
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
                        c.execute("INSERT INTO training_runs(user_id,platform,problem_key,problem,status,started_at,last_started_at,reference,epoch,mode) VALUES(?,?,?,?,'active',?,?,?,?,?)",
                                  (uid, p["platform"], p["problemKey"], json.dumps(p), now, now, profile["reference"], profile["epoch"],view_mode))
                else:
                    reason = self._reason(data)
                    self._cooldown(c, uid, p, reason)
                c.execute(f'DELETE FROM {recommendation_table(view_mode)} WHERE user_id=?', (uid,))
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
                    for name in MODES:c.execute(f'DELETE FROM {recommendation_table(name)} WHERE user_id=?', (uid,))
                else:
                    completion = data.get("completionMode")
                    if row["status"] != "pending_feedback" or completion not in FEEDBACK:
                        raise ValueError("仅能为已核对 AC 的题填写独立完成、参考提示或参考题解")
                    c.execute("UPDATE training_runs SET status='completed',feedback=?,feedback_at=? WHERE id=?", (completion, now, row["id"]))
                    origin_profile,_=self._profile(c,uid,row['mode'])
                    self._assess(c, uid, origin_profile, now)
            else:
                raise ValueError("未知训练操作")
        return self.state(uid,view_mode)

    @staticmethod
    def _reason(data):
        reason = data.get("reason", "")
        if reason not in ("", "too_hard", "too_easy", "already_done", "not_now"):
            raise ValueError("换题原因无效")
        return reason

    @staticmethod
    def _assess(c, uid, profile, now):
        mode=profile['mode']
        rows = c.execute("SELECT * FROM training_runs WHERE user_id=? AND mode=? AND epoch=? AND status='completed' AND evaluated=0 ORDER BY feedback_at,id LIMIT 3",
                         (uid, mode, profile["epoch"])).fetchall()
        if len(rows) < 3:
            return
        up = sum(r["feedback"] == "independent" and json.loads(r["problem"])["rating"] >= r["reference"] for r in rows)
        down = sum(r["feedback"] == "editorial" and json.loads(r["problem"])["rating"] <= r["reference"] for r in rows)
        delta = 100 if up >= 2 else -100 if down >= 2 else 0
        new = max(0, min(5000, profile["reference"] + delta))
        assessment = {"at": now, "delta": new - profile["reference"], "reference": new}
        c.execute(f'UPDATE {profile_table(mode)} SET reference=?,assessment=? WHERE user_id=?', (new, json.dumps(assessment), uid))
        c.executemany("UPDATE training_runs SET evaluated=1 WHERE id=?", [(r["id"],) for r in rows])
        c.execute(f'DELETE FROM {recommendation_table(mode)} WHERE user_id=?', (uid,))
