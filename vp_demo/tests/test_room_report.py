"""Directed report checks use scoreboard events, including the requested WA/AC example."""
import copy
import unittest

from duo_report import build_report
from duo_rules import scoreboard


class RoomReportTests(unittest.TestCase):
    def setUp(self):
        self.contest = {
            "contestId": 42, "name": "房间训练赛", "durationSeconds": 7200,
            "problems": [{"index": "A"}, {"index": "B"}, {"index": "C"}],
        }
        self.members = [{"slot": 1, "handle": "甲"}, {"slot": 2, "handle": "乙"}]

    @staticmethod
    def event(eid, slot, seconds, index="A", verdict="OK"):
        return {"eventId": eid, "slot": slot, "elapsedSeconds": seconds, "index": index, "verdict": verdict}

    def report(self, events, members=None, mode="training"):
        board = scoreboard(members if members is not None else self.members, self.contest, events, 7200)
        report = build_report(self.contest, board, mode=mode, settled_at=1800007200)
        return report, board

    def test_requested_wa_then_ac_penalty_and_first_solve(self):
        report, board = self.report([
            self.event(1, 1, 300, verdict="WRONG_ANSWER"),
            self.event(2, 1, 1200), self.event(3, 2, 1800),
        ])
        self.assertEqual([(p["handle"], p["solved"], p["penalty"], p["rank"])
                          for p in report["participants"]], [("乙", 1, 30, 1), ("甲", 1, 40, 2)])
        participants = {p["handle"]: p for p in report["participants"]}
        self.assertEqual(participants["甲"]["firstAccepted"], {"index": "A", "indices": ["A"], "elapsedSeconds": 1200})
        self.assertEqual(participants["乙"]["firstAccepted"]["elapsedSeconds"], 1800)
        self.assertEqual(report["firstSolves"], [{"index": "A", "elapsedSeconds": 1200, "handles": ["甲"]}])
        self.assertIn("两人各通过 1 题", report["summary"])
        self.assertIn("乙以 30 比 40 的罚时领先甲", report["summary"])
        self.assertNotIn("20 比 30", report["summary"])

    def test_penalty_tie_is_shared_rank_and_zero_second_ac_is_kept(self):
        report, _ = self.report([self.event(1, 1, 0), self.event(2, 2, 0)])
        self.assertEqual([p["rank"] for p in report["participants"]], [1, 1])
        self.assertEqual(report["firstSolves"][0]["handles"], ["乙", "甲"])
        self.assertIn("并列第 1 名", report["summary"])
        self.assertNotIn("领先", report["summary"])
        self.assertTrue(all(p["firstAccepted"]["elapsedSeconds"] == 0 for p in report["participants"]))

    def test_three_members_keep_competition_ranks_and_shared_firsts(self):
        members = self.members + [{"slot": 3, "handle": "丙"}]
        report, _ = self.report([
            self.event(1, 1, 60), self.event(2, 2, 60), self.event(3, 3, 120),
        ], members=members)
        self.assertEqual([p["rank"] for p in report["participants"]], [1, 1, 3])
        self.assertEqual(report["participantCount"], 3)
        self.assertEqual(set(report["firstSolves"][0]["handles"]), {"甲", "乙"})

    def test_first_personal_solve_and_each_problem_first_solve_are_distinct(self):
        report, _ = self.report([
            self.event(1, 1, 600, "B"), self.event(2, 1, 600, "C"),
            self.event(3, 1, 1200, "A"), self.event(4, 2, 300, "A"),
        ])
        alpha = next(p for p in report["participants"] if p["handle"] == "甲")
        self.assertEqual(alpha["firstAccepted"], {"index": "B", "indices": ["B", "C"], "elapsedSeconds": 600})
        self.assertEqual([p["index"] for p in report["firstSolves"]], ["A", "B", "C"])
        self.assertEqual(report["firstSolves"][0]["handles"], ["乙"])

    def test_unpassed_problems_have_no_fabricated_first_ac(self):
        report, _ = self.report([
            self.event(1, 1, 300, verdict="WRONG_ANSWER"),
            self.event(2, 2, 7200),  # deadline is outside the scoring window
        ])
        self.assertEqual(report["firstSolves"], [])
        self.assertTrue(all(p["firstAccepted"] is None for p in report["participants"]))
        self.assertTrue(all(p["penalty"] == 0 for p in report["participants"]))
        self.assertIn("暂无通过题目", report["summary"])
        self.assertIn("所有成员并列第 1 名", report["summary"])

    def test_single_member_and_empty_board(self):
        report, _ = self.report([self.event(1, 1, 59)], members=self.members[:1])
        self.assertEqual((report["participantCount"], report["participants"][0]["penalty"]), (1, 0))
        self.assertIn("单人训练已完成", report["summary"])
        empty, _ = self.report([], members=[])
        self.assertEqual(empty["participants"], [])
        self.assertEqual(empty["firstSolves"], [])
        self.assertEqual(empty["summary"], "本场没有参赛成员。")

    def test_replay_is_labelled_and_inputs_are_never_mutated_or_referenced(self):
        board = scoreboard(self.members, self.contest, [self.event(1, 1, 600)], 7200)
        before = copy.deepcopy((self.contest, board))
        report = build_report(self.contest, board, mode="replay", settled_at=12345)
        self.assertEqual((self.contest, board), before)
        self.assertTrue(report["summary"].startswith("回放战报："))
        self.assertEqual(report["settledAt"], 12345)
        report["participants"][0]["handle"] = "changed"
        report["participants"][0]["firstAccepted"]["indices"].append("Z")
        self.assertEqual((self.contest, board), before)


if __name__ == "__main__":
    unittest.main()
