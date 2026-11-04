"""Persistent reviews and personal lessons; all operations are scoped to a session user."""
import hashlib
import json
import re
import threading
import time
from ai_provider import DeepSeekProvider, ProviderError, PROMPT_VERSION, SCHEMA_VERSION
from adapters import normalize_api_credential

CATEGORIES = ("错误复盘", "解题思路", "实现技巧", "调试方法", "复杂度判断")


class ReviewError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def init_tables(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
      fingerprint TEXT NOT NULL, status TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT, usage TEXT,
      model TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, completed_at INTEGER);
    CREATE INDEX IF NOT EXISTS analysis_owner ON analyses(user_id,fingerprint,status);
    CREATE TABLE IF NOT EXISTS experiences(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
      payload TEXT NOT NULL, analysis_id INTEGER REFERENCES analyses(id), candidate_key TEXT,
      version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(user_id,analysis_id,candidate_key));
    CREATE INDEX IF NOT EXISTS experience_owner ON experiences(user_id,deleted,updated_at);
    CREATE TABLE IF NOT EXISTS experience_revisions(id INTEGER PRIMARY KEY, experience_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL, created_at INTEGER NOT NULL);
    ''')


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_field(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ReviewError(f"{label}格式不正确或超过 {limit} 字符")
    return value.strip()


def text_list(value, label, count, length):
    if not isinstance(value, list) or len(value) > count:
        raise ReviewError(f"{label}最多 {count} 项")
    return list(dict.fromkeys(text_field(v, label, length, True) for v in value))


def validate_card(data, confirm=False):
    card = {k: text_field(data.get(k, ""), label, size, required) for k, label, size, required in (
        ("title", "标题", 100, True), ("scenario", "适用场景", 1000, confirm), ("rule", "经验规则", 2000, confirm),
        ("incident", "当时的问题", 2000, False), ("personalNote", "个人笔记", 2000, False))}
    category = data.get("category", "错误复盘")
    if category not in CATEGORIES:
        raise ReviewError("经验类型无效")
    card["category"] = category
    card["tags"] = text_list(data.get("tags", []), "标签", 8, 40)
    card["checklist"] = text_list(data.get("checklist", []), "检查步骤", 8, 300)
    if confirm and not card["checklist"]:
        raise ReviewError("确认前至少填写一项检查步骤")
    if data.get("practiceState", "practicing") not in ("practicing", "applied"):
        raise ReviewError("练习标记无效")
    if type(data.get("favorite", False)) is not bool:
        raise ReviewError("收藏状态无效")
    card.update(favorite=data.get("favorite", False), practiceState=data.get("practiceState", "practicing"))
    return card


def validate_output(raw, snapshot):
    """Never trust model-provided verification flags, IDs, URLs or line references."""
    if not isinstance(raw, dict):
        raise ProviderError("模型输出结构无效。")
    records = {r["externalId"]: r for r in snapshot["records"]}

    def evidence(items):
        if not isinstance(items, list) or len(items) > 8:
            raise ProviderError("模型引用格式不正确。")
        checked = []
        for item in items:
            if not isinstance(item, dict):
                raise ProviderError("模型引用格式不正确。")
            sid = str(item.get("submissionId", ""))
            start, end, quote = item.get("lineStart"), item.get("lineEnd"), item.get("quote")
            valid = sid in records and type(start) is int and type(end) is int and isinstance(quote, str) and 0 < len(quote) <= 5000
            if valid:
                lines = records[sid]["source"].split("\n")
                valid = 1 <= start <= end <= len(lines) and end-start < 80 and "\n".join(lines[start-1:end]).strip() == quote.strip()
            checked.append({"submissionId": sid[:80], "lineStart": start if type(start) is int else 0,
                            "lineEnd": end if type(end) is int else 0, "quote": quote[:5000] if isinstance(quote, str) else "",
                            "verified": bool(valid)})
        return checked

    summary = text_field(raw.get("summary", ""), "分析总结", 8000, True)
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) != len(snapshot["steps"]):
        raise ProviderError("模型未覆盖全部选定修改步骤，未接受不完整结果。")
    steps = []
    for item, expected in zip(raw_steps, snapshot["steps"]):
        if not isinstance(item, dict) or str(item.get("before")) != expected["before"] or str(item.get("after")) != expected["after"]:
            raise ProviderError("模型返回的提交顺序与输入不一致。")
        refs = evidence(item.get("evidence", []))
        steps.append(expected | {k: text_field(item.get(k, ""), "分析说明", 4000, True) for k in ("observation", "hypothesis", "test")} |
                     {"evidence": refs, "evidenceValid": bool(refs) and all(r["verified"] for r in refs), "testExecuted": False})
    candidates = raw.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 3:
        raise ProviderError("模型候选经验格式不正确。")
    clean = []
    for i, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ProviderError("模型候选经验格式不正确。")
        card = validate_card(candidate, confirm=True)
        card.update(favorite=False, practiceState="practicing")
        refs = evidence(candidate.get("evidence", []))
        valid = bool(refs) and all(r["verified"] for r in refs)
        clean.append(card | {"key": str(i), "evidence": refs, "evidenceValid": valid})
    return {"summary": summary, "steps": steps, "candidates": clean,
            "limitations": text_list(raw.get("limitations", []), "分析限制", 12, 1000)}


class ReviewService:
    def __init__(self, connect, db_lock, records, settings, statement=None):
        self.connect, self.db_lock, self.records, self.settings = connect, db_lock, records, settings
        self.statement = statement or (lambda uid, record: None)
        self.provider = DeepSeekProvider()
        self.lock = threading.RLock()
        self.keys = {}
        self.active = set()

    def recover(self):
        with self.connect() as c:
            c.execute("UPDATE analyses SET status='interrupted',error='服务重启，分析未完成，请手动重试' WHERE status IN ('queued','running')")

    def config(self, uid, data=None):
        with self.lock:
            if data is not None:
                if data.get("clear") is True:
                    self.keys.pop(uid, None)
                else:
                    self.keys[uid] = normalize_api_credential(data.get("apiKey", ""), "DeepSeek API Key")
            return {"configured": uid in self.keys, "provider": "DeepSeek", "model": self.provider.model,
                    "storage": "仅当前服务内存，退出登录或重启后需重新配置"}

    def get_card(self, uid, cid):
        with self.connect() as c:
            row = c.execute("SELECT * FROM experiences WHERE id=? AND user_id=?", (int(cid), uid)).fetchone()
        if not row:
            raise ReviewError("经验不存在", 404)
        return json.loads(row["payload"]) | {"id": row["id"], "version": row["version"], "deleted": bool(row["deleted"]),
                                             "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def list_cards(self, uid, query):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM experiences WHERE user_id=? ORDER BY updated_at DESC,id DESC", (uid,))]
        cards = [self.get_card(uid, cid) for cid in ids]
        stats = {"total": sum(not c["deleted"] for c in cards), "pending": sum(not c["deleted"] and c["reviewState"] == "pending" for c in cards),
                 "confirmed": sum(not c["deleted"] and c["reviewState"] == "confirmed" for c in cards)}
        q = str(query.get("q", "")).casefold()
        trash = query.get("trash") == "1"
        filtered = [c for c in cards if c["deleted"] == trash and
                    (not query.get("reviewState") or c["reviewState"] == query["reviewState"]) and
                    (not query.get("analysisScope") or c["analysisScope"] == query["analysisScope"]) and
                    (query.get("favorite") != "1" or c["favorite"]) and
                    (not query.get("category") or c["category"] == query["category"]) and
                    q in " ".join([c["title"], c["rule"], c["scenario"], c["personalNote"], *c["tags"]]).casefold()]
        page = min(max(1, int(query.get("page", 1))), max(1, (len(filtered)+19)//20))
        return {"rows": filtered[(page-1)*20:page*20], "total": len(filtered), "page": page, "pageSize": 20, "stats": stats}

    def manual_source(self, uid, data):
        key = data.get("problemKey", "")
        if not key:
            return []
        rows = [r for r in self.records(uid) if r["platform"] == "codeforces" and r["problemKey"] == key]
        if not rows:
            raise ReviewError("来源题目不存在", 404)
        r = rows[0]
        return [{"platform": r["platform"], "problemKey": key, "title": r["title"], "url": r["url"], "submissionIds": []}]

    def save_card(self, uid, action, data):
        with self.db_lock:
            old = self.get_card(uid, data["id"]) if action != "create" else None
            if old and data.get("expectedVersion") != old["version"]:
                raise ReviewError("这条经验已被修改，请重新打开后再保存。", 409)
            if old and old["deleted"] and action != "restore":
                raise ReviewError("经验已在回收站，请先恢复。", 409)
            if action in ("delete", "restore"):
                new = dict(old)
                deleted = action == "delete"
            else:
                state = "confirmed" if action == "confirm" else data.get("reviewState", old["reviewState"] if old else "pending")
                if state not in ("pending", "confirmed"):
                    raise ReviewError("确认状态无效")
                merged = (old or {}) | data
                new = validate_card(merged, confirm=state == "confirmed")
                provenance = {k: old[k] for k in ("origin", "analysisScope", "coverageState", "sources", "analysisId", "candidateKey", "evidence")} if old else {
                    "origin": "manual", "analysisScope": "manual", "coverageState": None, "sources": self.manual_source(uid, data),
                    "analysisId": None, "candidateKey": None, "evidence": []}
                new.update(provenance, reviewState=state, confirmedAt=int(time.time()) if state == "confirmed" else None)
                deleted = False
            now = int(time.time())
            for k in ("id", "version", "createdAt", "updatedAt", "deleted"):
                new.pop(k, None)
            with self.connect() as c:
                if old:
                    c.execute("INSERT INTO experience_revisions(experience_id,user_id,payload,version,created_at) VALUES(?,?,?,?,?)",
                              (old["id"], uid, dump(old), old["version"], now))
                    c.execute("UPDATE experiences SET payload=?,version=version+1,deleted=?,updated_at=? WHERE id=? AND user_id=?",
                              (dump(new), int(deleted), now, old["id"], uid))
                    cid = old["id"]
                else:
                    cid = c.execute("INSERT INTO experiences(user_id,payload,created_at,updated_at) VALUES(?,?,?,?)", (uid, dump(new), now, now)).lastrowid
            return {"card": self.get_card(uid, cid)}

    def preview(self, uid, data):
        scope = data.get("analysisScope", "pair")
        platform = data.get("platform", "codeforces")
        if scope not in ("pair", "first_ac") or platform != "codeforces":
            raise ReviewError("当前分析支持 CF 两次提交或首次 AC 过程。")
        all_rows = [r for r in self.records(uid) if r["platform"] == platform]
        if scope == "pair":
            ids = [str(data.get(k, "")) for k in ("beforeSubmissionId", "afterSubmissionId")]
            if ids[0] == ids[1]:
                raise ReviewError("请选择两次不同的提交。", 422)
            lookup = {r["externalId"]: r for r in all_rows}
            if any(sid not in lookup for sid in ids):
                raise ReviewError("所选提交不存在或尚未选择。", 404)
            rows = [lookup[sid] for sid in ids]
            if rows[0]["problemKey"] != rows[1]["problemKey"]:
                raise ReviewError("两次提交必须属于同一道题。")
            if (rows[0]["submittedAt"], int(rows[0]["externalId"])) >= (rows[1]["submittedAt"], int(rows[1]["externalId"])):
                raise ReviewError("修改前版本必须早于修改后版本。")
        else:
            rows = sorted([r for r in all_rows if r["problemKey"] == data.get("problemKey")], key=lambda r: (r["submittedAt"], int(r["externalId"])))
            if not rows:
                raise ReviewError("这道题没有可用提交。", 404)
            ac = next((i for i, r in enumerate(rows) if r["verdict"] == "OK"), None)
            if ac is not None:
                rows = rows[:ac+1]
        key = rows[0]["problemKey"]
        missing = [r["externalId"] for r in rows if not r["source"]]
        warnings, blockers = [], []
        coverage = "pair"
        if scope == "first_ac":
            history = self.settings(uid).get("historyCoverage", {})
            verified = history.get("platform") == platform and history.get("handle", "").casefold() == rows[0]["handle"].casefold() and rows[-1]["submittedAt"] <= history.get("completeThrough", 0)
            coverage = "no_ac" if rows[-1]["verdict"] != "OK" else "complete" if verified and not missing else "partial"
            if not verified:
                warnings.append("历史覆盖未确认：当前记录中最早的 AC 不一定是真正首次 AC。可先重新同步全部历史。")
            if coverage == "no_ac":
                warnings.append("尚未观察到 AC，本次仅为阶段复盘。")
            if missing:
                warnings.append("部分版本没有源码，只能按已有版本进行片段复盘；跨缺失版本的步骤不是相邻修改。")
        elif missing:
            blockers.append("选中的提交缺少源码，请先同步。")
        if any(r["verdict"] in ("PENDING", "TESTING") for r in rows):
            blockers.append("存在尚未完成判题的提交，请同步终态后分析。")
        available = [r for r in rows if r["source"]]
        if not available:
            blockers.append("没有可分析的源码。")
        position = {r["externalId"]: i for i, r in enumerate(rows)}
        steps = [{"before": a["externalId"], "after": b["externalId"], "adjacent": position[b["externalId"]] == position[a["externalId"]]+1}
                 for a, b in zip(available, available[1:])]
        if scope == "pair":
            between = [r for r in all_rows if r["problemKey"] == key and int(rows[0]["externalId"]) < int(r["externalId"]) < int(rows[1]["externalId"])]
            if steps:
                steps[0]["adjacent"] = not between
            if between:
                warnings.append(f"选定版本之间还有 {len(between)} 次提交，本次只分析所选两份代码。")
        statement = text_field(data.get("problemText", ""), "题面", 40000)
        statement_info = {"url": rows[0]["url"], "textOrigin": "user" if statement else "missing"}
        if not statement:
            cached = self.statement(uid, rows[0])
            if cached and cached.get("text"):
                statement = cached["text"]
                statement_info.update({k: cached[k] for k in ("url", "textOrigin", "fetchedAt") if k in cached})
                warnings.extend(cached.get("warnings", []))
                if cached.get("status") == "stale":
                    warnings.append("题面刷新失败，使用之前保存的题面版本。")
        question = text_field(data.get("question", ""), "分析问题", 2000)
        mode = data.get("mode", "hint" if rows[-1]["verdict"] != "OK" else "review")
        if mode not in ("hint", "review"):
            raise ReviewError("分析模式无效")
        if not statement:
            warnings.append("没有题面，本次仅基于代码分析，不能确认是否满足题意。")
        related = []
        selected = data.get("relatedExperienceIds", [])
        if not isinstance(selected, list) or len(selected) > 3:
            raise ReviewError("最多选择 3 条相关经验。")
        for cid in list(dict.fromkeys(selected)):
            card = self.get_card(uid, cid)
            if card["deleted"] or card["reviewState"] != "confirmed":
                raise ReviewError("只能引用已确认、未删除的个人经验。")
            related.append({k: card[k] for k in ("id", "version", "title", "scenario", "rule", "checklist")})
        snapshot = {"analysisScope": scope, "coverageState": coverage, "problem": {"platform": platform, "problemKey": key,
                    "title": rows[0]["title"], "text": statement, **statement_info},
                    "records": available, "timeline": [{k: r[k] for k in ("externalId", "submittedAt", "verdict", "participantType")} |
                    {"hasSource": bool(r["source"])} for r in rows], "missingSourceIds": missing, "steps": steps,
                    "warnings": warnings, "question": question, "mode": mode, "relatedExperiences": related,
                    "provider": self.provider.name, "model": self.provider.model, "promptVersion": PROMPT_VERSION, "schemaVersion": SCHEMA_VERSION}
        size = sum(len(r["source"]) for r in available)+len(statement)+len(question)+len(dump(related))
        if size > 100000 or len(steps) > 20:
            blockers.append("所选范围超过当前单次 100000 字符 / 20 次修改上限，请改选两次提交；不会静默截断全过程。")
        fingerprint = hashlib.sha256(dump(snapshot).encode()).hexdigest()
        with self.connect() as c:
            row = c.execute("SELECT id FROM analyses WHERE user_id=? AND fingerprint=? AND status IN ('succeeded','running') ORDER BY id DESC LIMIT 1", (uid, fingerprint)).fetchone()
        return snapshot, {"fingerprint": fingerprint, "analysisScope": scope, "coverageState": coverage, "problem": snapshot["problem"],
                          "timeline": snapshot["timeline"], "steps": steps, "warnings": warnings, "blockers": blockers,
                          "requiresPartialConsent": scope == "first_ac" and coverage != "complete", "inputCharacters": size,
                          "missingSourceIds": missing, "existingAnalysisId": row[0] if row else None}

    def analysis(self, uid, aid, snapshot=False):
        with self.connect() as c:
            row = c.execute("SELECT * FROM analyses WHERE id=? AND user_id=?", (int(aid), uid)).fetchone()
        if not row:
            raise ReviewError("分析不存在", 404)
        snap = json.loads(row["snapshot"])
        result = {"id": row["id"], "status": row["status"], "result": json.loads(row["result"]) if row["result"] else None,
                  "usage": json.loads(row["usage"]) if row["usage"] else None, "model": row["model"], "error": row["error"],
                  "createdAt": row["created_at"], "completedAt": row["completed_at"], "fingerprint": row["fingerprint"],
                  "context": {k: snap[k] for k in ("analysisScope", "coverageState", "problem", "timeline", "steps", "warnings", "missingSourceIds", "mode", "question", "relatedExperiences")}}
        if snapshot:
            result["snapshot"] = snap
        return result

    def create_analysis(self, uid, data):
        with self.lock, self.db_lock:
            snapshot, preview = self.preview(uid, data)
            if data.get("fingerprint") != preview["fingerprint"]:
                raise ReviewError("数据或分析范围已变化，请重新预览。", 409)
            if preview["blockers"]:
                raise ReviewError("；".join(preview["blockers"]), 422)
            if preview["requiresPartialConsent"] and data.get("allowPartial") is not True:
                raise ReviewError("历史不完整或尚未 AC，请明确选择片段 / 阶段复盘。", 422)
            if preview["existingAnalysisId"]:
                return self.analysis(uid, preview["existingAnalysisId"]), True
            key = self.keys.get(uid)
            if not key:
                raise ReviewError("请先配置 DeepSeek API Key。", 422)
            if uid in self.active or len(self.active) >= 2:
                raise ReviewError("已有分析正在运行，请等待完成后再试。", 429)
            with self.connect() as c:
                aid = c.execute("INSERT INTO analyses(user_id,fingerprint,status,snapshot,model,created_at) VALUES(?,?,'running',?,?,?)",
                                (uid, preview["fingerprint"], dump(snapshot), self.provider.model, int(time.time()))).lastrowid
            self.active.add(uid)
            try:
                threading.Thread(target=self.run_analysis, args=(uid, aid, snapshot, key), daemon=True).start()
            except Exception:
                self.active.discard(uid)
                with self.connect() as c:
                    c.execute("UPDATE analyses SET status='failed',error='无法启动分析任务' WHERE id=?", (aid,))
                raise ReviewError("无法启动分析任务。", 500)
            return self.analysis(uid, aid), False

    def run_analysis(self, uid, aid, snapshot, key):
        try:
            if self.analysis(uid, aid)["status"] != "running":
                return
            raw, usage, model = self.provider.analyze(snapshot, key)
            result = validate_output(raw, snapshot)
            with self.connect() as c:
                c.execute("UPDATE analyses SET status='succeeded',result=?,usage=?,model=?,completed_at=? WHERE id=? AND user_id=? AND status='running'",
                          (dump(result), dump(usage), model, int(time.time()), aid, uid))
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ReviewError, ProviderError)) else "分析失败，未保存不完整的结果，请手动重试。"
            if key:
                message = message.replace(key, "[已隐藏]")
            with self.connect() as c:
                c.execute("UPDATE analyses SET status='failed',error=?,completed_at=? WHERE id=? AND user_id=? AND status='running'", (message[:300], int(time.time()), aid, uid))
        finally:
            key = None
            with self.lock:
                self.active.discard(uid)

    def cancel(self, uid, aid):
        self.analysis(uid, aid)
        with self.connect() as c:
            c.execute("UPDATE analyses SET status='cancelled',error='已停止接收结果；已发出的模型请求可能仍计费',completed_at=? WHERE id=? AND user_id=? AND status='running'", (int(time.time()), aid, uid))
        return self.analysis(uid, aid)

    def collect(self, uid, data):
        with self.db_lock:
            analysis = self.analysis(uid, data["analysisId"], True)
            if analysis["status"] != "succeeded":
                raise ReviewError("分析尚未成功完成。", 409)
            key = str(data.get("candidateKey", ""))
            candidate = next((c for c in analysis["result"]["candidates"] if c["key"] == key), None)
            if not candidate or not candidate["evidenceValid"]:
                raise ReviewError("候选经验的源码引用未通过校验，不能直接入库。", 422)
            with self.connect() as c:
                prior = c.execute("SELECT id FROM experiences WHERE user_id=? AND analysis_id=? AND candidate_key=?", (uid, analysis["id"], key)).fetchone()
                if prior:
                    return {"card": self.get_card(uid, prior[0]), "existing": True}
                snap = analysis["snapshot"]
                card = validate_card(candidate, True) | {"reviewState": "pending", "confirmedAt": None, "origin": "ai",
                    "analysisScope": snap["analysisScope"], "coverageState": snap["coverageState"], "analysisId": analysis["id"],
                    "candidateKey": key, "evidence": candidate["evidence"], "sources": [{"platform": "codeforces", "problemKey": snap["problem"]["problemKey"],
                    "title": snap["problem"]["title"], "submissionIds": [r["externalId"] for r in snap["timeline"]]}]}
                now = int(time.time())
                cid = c.execute("INSERT INTO experiences(user_id,payload,analysis_id,candidate_key,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                (uid, dump(card), analysis["id"], key, now, now)).lastrowid
            return {"card": self.get_card(uid, cid), "existing": False}
