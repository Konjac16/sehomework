"""Catalog boundary and deterministic scoring for one to three independent contestants."""
import json
from pathlib import Path
import re
from zipfile import ZipFile

MAX_MEMBERS = 3

PENALIZED = {"WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED",
             "RUNTIME_ERROR", "IDLENESS_LIMIT_EXCEEDED"}
NO_PENALTY = {"COMPILATION_ERROR", "SKIPPED", "FAILED", "REJECTED", "CHALLENGED",
              "SECURITY_VIOLATED", "CRASHED", "INPUT_PREPARATION_CRASHED"}
TERMINAL = PENALIZED | NO_PENALTY | {"OK"}


class RoomError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise RoomError(f"{label}须为 {low} 至 {high} 的整数")
    return value


def read_catalog(path):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            doc = json.loads(archive.read("catalog.json"))
    else:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if doc.get("schemaVersion") != "1.0" or doc.get("mode") != "real_snapshot":
        raise ValueError("比赛目录必须是 1.0 版真实元数据快照")
    problems = {}
    contest_keys = {}
    for p in doc["problems"]:
        cid = integer(p["contestId"], 1, 99999, "普通比赛编号")
        index = p["index"]
        if not isinstance(index, str) or not re.fullmatch(r"[A-Z0-9]{1,6}", index):
            raise ValueError("题号不正确")
        key = f"cf:{cid}:{index}"
        if p["problemKey"] != key or key in problems:
            raise ValueError(f"重复或不一致的题目标识：{key}")
        contest_keys.setdefault(cid, set()).add(key)
        problems[key] = {"contestId": cid, "index": index, "title": str(p["name"]),
                         "url": f"https://codeforces.com/contest/{cid}/problem/{index}",
                         "rating": p.get("rating"), "tags": p.get("tags", [])}
    result = {}
    for c in doc["contests"]:
        if c.get("phase") != "FINISHED" or c.get("problemSetComplete") is not True:
            continue
        cid = integer(c["contestId"], 1, 99999, "普通比赛编号")
        keys = c["problemKeys"]
        if (cid in result or not keys or len(set(keys)) != len(keys)
                or set(keys) != contest_keys.get(cid)):
            raise ValueError(f"比赛 {cid} 的完整题集无效")
        result[cid] = {"contestId": cid, "name": str(c["name"]),
                       "startTimeSeconds": c.get("startTimeSeconds") or 0,
                       "durationSeconds": integer(c["durationSeconds"], 60, 86400, "比赛时长"),
                       "problems": [problems[k] for k in keys]}
    if not result:
        raise ValueError("没有完整且已结束的历史赛")
    return result


def check_replay(events, contest, slots):
    if not isinstance(events, list) or len(events) > 1000:
        raise RoomError("回放须为不超过 1000 条的事件数组")
    indices = {p["index"] for p in contest["problems"]}
    by_id = {}
    for raw in events:
        if not isinstance(raw, dict):
            raise RoomError("每条回放事件须为对象")
        eid = integer(raw.get("eventId"), 1, 2**53 - 1, "事件编号")
        e = {"eventId": eid, "slot": integer(raw.get("slot"), 1, MAX_MEMBERS, "席位"),
             "elapsedSeconds": integer(raw.get("elapsedSeconds"), 0, contest["durationSeconds"], "相对秒"),
             "index": raw.get("index"), "verdict": raw.get("verdict")}
        if e["slot"] not in slots:
            raise RoomError("事件引用了未入房的席位，请使用与当前成员相符的回放文件")
        if e["index"] not in indices or e["verdict"] not in TERMINAL:
            raise RoomError("事件必须引用本场题号和已明确的终态判定")
        if eid in by_id and by_id[eid] != e:
            raise RoomError("同一事件编号有冲突")
        by_id[eid] = e
    return list(by_id.values())


def sample_events(contest, slots):
    p = contest["problems"]
    if (contest["contestId"] == 1985 and slots == {1, 2} and contest["durationSeconds"] == 9000
            and {"A", "C", "D"}.issubset(problem["index"] for problem in p)):
        preset = Path(__file__).parent / "data" / "replays" / "1985-two-player.json"
        return check_replay(json.loads(preset.read_text(encoding="utf-8"))["events"], contest, slots)
    a, b = p[0]["index"], p[min(1, len(p)-1)]["index"]
    duration = contest["durationSeconds"]
    data = [(1, 300, a, "WRONG_ANSWER"), (1, 600, a, "COMPILATION_ERROR"),
            (1, 1200, a, "OK"), (1, 1350, b, "WRONG_ANSWER"), (2, 1800, a, "OK"),
            (3, 1800, b, "OK")]
    items = [dict(eventId=i, slot=slot, index=idx, elapsedSeconds=t, verdict=v)
             for i, (slot, t, idx, v) in enumerate(data, 1) if slot in slots and t < duration]
    items.append(dict(eventId=100, slot=2 if 2 in slots else 1, index=b, elapsedSeconds=duration, verdict="OK"))
    return items


def scoreboard(members, contest, events, cursor):
    """Window [0,duration); cursor inclusive. All IDs are normalized at ingress."""
    duration = contest["durationSeconds"]
    indices = [p["index"] for p in contest["problems"]]
    rows = {m["slot"]: {"slot": m["slot"], "handle": m["handle"], "solved": 0, "penalty": 0,
            "cells": {i: {"wrong": 0, "acSeconds": None, "pending": 0, "firstAcId": None}
                      for i in indices}} for m in members}
    for e in sorted(events, key=lambda x: (x["elapsedSeconds"], x["eventId"])):
        t, slot, idx, v = e["elapsedSeconds"], e["slot"], e["index"], e["verdict"]
        if not (0 <= t <= cursor and t < duration) or slot not in rows or idx not in indices:
            continue
        row = rows[slot]
        cell = row["cells"][idx]
        if v not in TERMINAL:
            cell["pending"] += 1
        elif cell["acSeconds"] is None:
            if v in PENALIZED:
                cell["wrong"] += 1
            elif v == "OK":
                cell.update(acSeconds=t, firstAcId=e["eventId"])
                row["solved"] += 1
                row["penalty"] += t // 60 + 20 * cell["wrong"]
    ordered = sorted(rows.values(), key=lambda x: (-x["solved"], x["penalty"], x["handle"].casefold()))
    previous, rank = None, 0
    for n, row in enumerate(ordered, 1):
        score = (row["solved"], row["penalty"])
        if score != previous:
            rank, previous = n, score
        row["rank"] = rank
    return ordered
