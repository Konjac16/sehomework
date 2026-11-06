"""Fixed-template, room-private reports built from an already settled scoreboard.

The service owns authorization and settlement.  This module never fetches CF,
replays events, or changes a frozen score; ranks and penalties are copied from
the final board so the report and leaderboard cannot disagree.
"""


def _summary(participants, mode):
    prefix = "回放战报：" if mode == "replay" else ""
    if not participants:
        return prefix + "本场没有参赛成员。"
    results = "；".join(
        f"{p['handle']}通过 {p['solved']} 题，罚时 {p['penalty']} 分钟"
        for p in participants
    ) + "。"
    if not any(p["solved"] for p in participants):
        return prefix + results + "本场暂无通过题目，所有成员并列第 1 名。"
    leaders = [p for p in participants if p["rank"] == min(x["rank"] for x in participants)]
    names = "、".join(p["handle"] for p in leaders)
    if len(leaders) > 1:
        conclusion = f"{names}并列第 {leaders[0]['rank']} 名。"
    elif len(participants) == 1:
        conclusion = "单人训练已完成。"
    else:
        leader = leaders[0]
        followers = sorted(
            (p for p in participants if p is not leader),
            key=lambda p: (p["rank"], p["handle"].casefold()),
        )
        runner_up = followers[0]
        if leader["solved"] == runner_up["solved"]:
            count = "两人" if len(participants) == 2 else leader["handle"] + "与" + runner_up["handle"]
            conclusion = (
                f"{count}各通过 {leader['solved']} 题，{leader['handle']}以 "
                f"{leader['penalty']} 比 {runner_up['penalty']} 的罚时领先{runner_up['handle']}。"
            )
        else:
            conclusion = f"{leader['handle']}以通过 {leader['solved']} 题位列第 1 名。"
    return prefix + results + conclusion


def build_report(contest, board, *, mode, settled_at):
    """Return a fresh JSON-ready report using only the immutable final board.

    ``firstAccepted`` is each member's first solved problem; ``firstSolves`` is
    each problem's first room solve.  These are deliberately different facts.
    Exact-second ties retain every index or handle, in catalog/board order.
    """
    indices = [p["index"] for p in contest["problems"]]
    participants = []
    for row in board:
        accepted = [
            (index, row["cells"][index]["acSeconds"])
            for index in indices
            if index in row["cells"] and row["cells"][index]["acSeconds"] is not None
        ]
        first = None
        if accepted:
            seconds = min(t for _, t in accepted)
            first_indices = [index for index, t in accepted if t == seconds]
            first = {"index": first_indices[0], "indices": first_indices, "elapsedSeconds": seconds}
        participants.append({
            "slot": row["slot"], "handle": row["handle"], "rank": row["rank"],
            "solved": row["solved"], "penalty": row["penalty"], "firstAccepted": first,
        })

    first_solves = []
    for index in indices:
        accepted = [
            (row["handle"], row["cells"][index]["acSeconds"])
            for row in board
            if index in row["cells"] and row["cells"][index]["acSeconds"] is not None
        ]
        if not accepted:
            continue
        seconds = min(t for _, t in accepted)
        first_solves.append({
            "index": index, "elapsedSeconds": seconds,
            "handles": [handle for handle, t in accepted if t == seconds],
        })

    return {
        "contestId": contest["contestId"], "contestName": contest["name"],
        "durationSeconds": contest["durationSeconds"], "mode": mode, "settledAt": settled_at,
        "participantCount": len(participants), "participants": participants,
        "summary": _summary(participants, mode), "firstSolves": first_solves,
    }
