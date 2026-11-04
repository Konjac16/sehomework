"""Transparent demo statistics and an explicitly uncalibrated probability heuristic."""
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone


def summarize(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[(row["platform"], row["problemKey"])].append(row)
    problems = []
    for (_, _), history in grouped.items():
        history.sort(key=lambda r: (r["submittedAt"], int(r["externalId"])), reverse=True)
        latest = history[0]
        ac = [r for r in history if r["verdict"] == "OK"]
        finals = [r for r in history if r["verdict"] not in ("PENDING", "TESTING")]
        problems.append({k: latest[k] for k in ("platform", "problemKey", "title", "rating", "tags", "url")}
                        | {"attempts": len(history), "solved": bool(ac), "hasFinal": bool(finals),
                           "firstAcceptedAt": min((r["submittedAt"] for r in ac), default=None),
                           "lastSubmittedAt": latest["submittedAt"],
                           "hasSource": any(r["source"] for r in history)})
    problems.sort(key=lambda p: p["lastSubmittedAt"], reverse=True)
    bands = defaultdict(lambda: {"attempted": 0, "solved": 0})
    tags = defaultdict(lambda: {"attempted": 0, "solved": 0, "rated": []})
    for p in problems:
        band = "未评级" if p["rating"] is None else f"{p['rating']//400*400}–{p['rating']//400*400+399}"
        bands[band]["attempted"] += 1
        bands[band]["solved"] += int(p["solved"])
        for tag in set(p["tags"]):
            tags[tag]["attempted"] += 1
            tags[tag]["solved"] += int(p["solved"])
            if p["solved"] and p["rating"] is not None:
                tags[tag]["rated"].append(p["rating"])
    months = Counter(datetime.fromtimestamp(r["submittedAt"], timezone.utc).strftime("%Y-%m") for r in records)
    only_ac = bool(records) and all(r["verdict"] == "OK" for r in records)
    return {"problems": problems, "bands": dict(sorted(bands.items())),
            "tags": [{"name": name, "attempted": d["attempted"], "solved": d["solved"],
                      "maxSolvedRating": max(d["rated"], default=None)}
                     for name, d in sorted(tags.items(), key=lambda kv: (-kv[1]["solved"], kv[0]))],
            "months": [{"month": k, "count": v} for k, v in sorted(months.items())[-12:]],
            "stats": {"submissions": len(records), "problems": len(problems),
                      "solved": sum(p["solved"] for p in problems),
                      "rated": sum(p["rating"] is not None for p in problems),
                      "sources": sum(bool(r["source"]) for r in records)},
            "onlyAccepted": only_ac}


def predict(summary, rating, target, problem_key="", platform="codeforces"):
    if platform != "codeforces":
        return {"status": "unsupported", "probability": None, "reason": "当前估算模型仅支持 CF 难度体系"}
    if any(p["platform"] == platform and p["problemKey"] == problem_key and p["solved"] for p in summary["problems"]) and problem_key:
        return {"status": "already_solved", "probability": None, "reason": "该题已通过，不适用首次解题预测"}
    if rating is None or target is None:
        return {"status": "insufficient_data", "probability": None, "reason": "请填写用户参考 Rating 和题目难度"}
    if not 0 <= rating <= 4000 or not 800 <= target <= 4000:
        raise ValueError("用户 Rating 范围为 0～4000，题目难度范围为 800～4000")
    # Explicitly chosen demo constants, NOT fitted parameters or the CF rating formula.
    # Assumed two-hour VP; changing duration is intentionally not supported yet.
    probability = 1 / (1 + math.exp(-(rating - target - 100) / 300))
    nearby = [p for p in summary["problems"] if p["rating"] is not None and abs(p["rating"] - target) <= 200]
    return {"status": "estimated", "probability": round(probability, 3),
            "modelVersion": "heuristic-v0.1", "calibrated": False, "durationMinutes": 120,
            "referenceRating": rating, "problemRating": target,
            "nearbyObserved": len(nearby), "nearbySolved": sum(p["solved"] for p in nearby),
            "evidence": "仅 AC 样本，不能估计真实通过率" if summary["onlyAccepted"] else "导入/同步记录不代表完整赛内样本",
            "reason": "规则：sigmoid((用户参考 Rating − 题目难度 − 100) / 300)。参数为演示设定，未经训练或校准；标签和源码不参与计算。"}


def prioritize(task, reference, focus):
    if task["done"]:
        return (9, "已完成", "你已标记完成")
    if task["pinned"]:
        return (0, "优先", "你手动置顶了这道题")
    if task["rating"] is None or reference is None:
        return (4, "待判断", "缺少题目难度或用户参考 Rating")
    gap = task["rating"] - reference
    match = bool(set(task["tags"]) & set(focus))
    if gap > 500:
        return (3, "暂缓", "高于参考 Rating 超过 500；可手动置顶")
    if gap > 200:
        return (2, "挑战", "高于参考 Rating 200～500，预计需要更多投入")
    return (1, "优先", "接近或低于参考水平" + ("，符合所选训练标签" if match else ""))
