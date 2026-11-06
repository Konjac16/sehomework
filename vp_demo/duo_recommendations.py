"""Deterministic, spoiler-free rules for recommending at most three room contests.

These are product heuristics, not predicted solve probabilities. Hard eligibility
is applied before scoring. Each known member gets a score across the whole
contest; 60% of the room score comes from the least well served member and 40%
from the mean. A fully rated contest with no reasonably approachable problem for
one member is placed after contests that give every known member an entry point.
Missing problem ratings contribute no positive fit rather than fabricated values.
"""


def _reference(member):
    value = member.get("referenceRating")
    return value if type(value) is int and 0 <= value <= 4000 else None


def _difficulty(problem):
    value = problem.get("rating")
    return value if type(value) is int and 800 <= value <= 4000 else None


def recommend_contests(catalog, members, overlaps, max_minutes, allow_seen=False):
    """Return zero to three eligible, public room recommendation summaries.

    ``members`` contains only slot, handle and optional ``referenceRating``.
    ``overlaps`` is the room-wide known-contact count keyed by contest ID.
    Neither task names, tags, individual difficulties nor member ratings appear
    in the result. Unknown member levels are never replaced by an invented level.
    """
    if not members:
        return []
    levels = [value for member in members if (value := _reference(member)) is not None]
    unknown_members = len(members) - len(levels)
    ranked = []
    for cid, contest in catalog.items():
        if contest["durationSeconds"] > max_minutes * 60 or (overlaps.get(cid, 0) and not allow_seen):
            continue
        problems = contest["problems"]
        if not problems:
            continue
        difficulties = [value for problem in problems if (value := _difficulty(problem)) is not None]
        complete = len(difficulties) == len(problems)
        # Proximity peaks just above the member's reference level. Unknown task
        # ratings count in the denominator and cannot inflate apparent fit.
        fits = [sum(max(0, 1000 - abs(value - (level + 100))) for value in difficulties)
                / (1000 * len(problems)) for level in levels]
        score = (0.6 * min(fits) + 0.4 * sum(fits) / len(fits)) if fits else 0
        # Only a complete difficulty set can establish that every task is too
        # hard. Incomplete sets remain provisional instead of proving a barrier.
        blocked = sum(all(value > level + 400 for value in difficulties)
                      for level in levels) if complete else 0
        coverage = len(difficulties) / len(problems)
        provisional = bool(unknown_members or not complete)
        if not levels:
            label = "水平待补充"
        elif not difficulties:
            label = "难度资料不足"
        elif blocked:
            label = "偏挑战"
        elif provisional:
            label = "暂定推荐"
        elif min(fits) >= 0.4:
            label = "均衡适配"
        else:
            label = "可选训练"

        reasons = ["时长符合预算", "已允许重复练习" if overlaps.get(cid, 0) else "现有成员记录未发现接触"]
        if not levels:
            reasons.append("尚无有效成员参考水平，暂仅按硬条件筛选，不能判断水平适配")
        elif not difficulties:
            reasons.append("本场缺少题目难度，无法判断成员适配")
        elif not complete:
            reasons.append("仅有部分题目难度可供比较，无法完整判断整场适配")
        elif blocked:
            reasons.append("有已知水平成员可能缺少适合上手的题目，已降低优先级")
        else:
            reasons.append("每位已知水平成员都有可上手的题目")
            if min(fits) >= 0.4:
                reasons.append("整场适配较均衡，适配较弱的成员也得到兼顾")
            else:
                reasons.append("整场难度与部分已知成员的水平仍有距离，建议结合训练目标选择")
        if levels and unknown_members:
            reasons.append(f"{unknown_members} 位成员参考水平缺失，暂只评估已知成员")
        reasons.append("规则推荐，不代表通过概率")
        public = {"contestId": cid, "name": contest["name"],
                  "durationSeconds": contest["durationSeconds"], "problemCount": len(problems),
                  "fitLabel": label, "isProvisional": provisional,
                  "reason": "；".join(reasons) + "。"}
        # Unrated contests cannot outrank rated evidence when levels are known.
        # Other ties are reproducible across catalog insertion order and refresh.
        key = (bool(levels) and not difficulties, blocked, -score, -coverage,
               -contest.get("startTimeSeconds", 0), cid)
        ranked.append((key, public))
    ranked.sort(key=lambda item: item[0])
    return [item[1] for item in ranked[:3]]
