"""Directed acceptance checks for small-group, spoiler-free contest ranking."""
from collections import Counter
from copy import deepcopy
import json
import unittest

from duo_recommendations import recommend_contests


def contest(cid, ratings, *, minutes=120, started=0):
    return {"contestId": cid, "name": f"Training round {cid}",
            "durationSeconds": minutes * 60, "startTimeSeconds": started,
            "problems": [{"index": chr(65 + i), "rating": rating,
                          "title": "private problem-title sentinel", "tags": ["secret tag sentinel"]}
                         for i, rating in enumerate(ratings)]}


def member(slot, level):
    return {"slot": slot, "handle": f"member{slot}", "referenceRating": level}


class RoomRecommendationTests(unittest.TestCase):
    def recommend(self, catalog, members=None, overlaps=None, minutes=120, allow_seen=False):
        return recommend_contests(catalog, members or [member(1, 1400)],
                                 overlaps or Counter(), minutes, allow_seen)

    def test_same_average_fit_does_not_hide_a_barrier_for_the_weaker_member(self):
        # For levels 1000 and 2000, 1400 tasks have proximity scores .7/.3;
        # 2000 tasks have .1/.9: both mean .5, but only the former gives the
        # weaker member a task within level+400. Catalog order favors neither.
        catalog = {2: contest(2, [2000, 2000, 2000]), 1: contest(1, [1400, 1400, 1400])}
        result = self.recommend(catalog, [member(1, 1000), member(2, 2000)])
        self.assertEqual([row["contestId"] for row in result], [1, 2])
        self.assertEqual(result[1]["fitLabel"], "偏挑战")
        self.assertIn("降低优先级", result[1]["reason"])

    def test_worst_member_still_matters_when_both_contests_have_an_entry_point(self):
        # Both have an accessible task for the weaker member. Averages remain
        # equal, so a simple room mean cannot distinguish these two contests.
        catalog = {2: contest(2, [1400, 2000]), 1: contest(1, [1400, 1800])}
        result = self.recommend(catalog, [member(1, 1000), member(2, 2000)])
        self.assertEqual([row["contestId"] for row in result], [1, 2])

    def test_hard_filters_apply_before_fit_and_at_most_three_are_returned(self):
        catalog = {cid: contest(cid, [1500], minutes=180 if cid == 1 else 120)
                   for cid in range(1, 8)}
        result = self.recommend(catalog, overlaps=Counter({2: 1, 3: 2}))
        self.assertEqual([row["contestId"] for row in result], [4, 5, 6])

    def test_only_one_eligible_contest_returns_one_and_does_not_pad(self):
        catalog = {1: contest(1, [1500]), 2: contest(2, [1500], minutes=121),
                   3: contest(3, [1500])}
        self.assertEqual([row["contestId"] for row in self.recommend(catalog, overlaps=Counter({3: 1}))], [1])

    def test_no_eligible_contest_returns_empty(self):
        self.assertEqual(self.recommend({1: contest(1, [1500])}, minutes=119), [])
        self.assertEqual(self.recommend({1: contest(1, [1500])}, overlaps=Counter({1: 1})), [])

    def test_allow_seen_is_explicit_and_does_not_relax_time(self):
        catalog = {1: contest(1, [1500]), 2: contest(2, [1500], minutes=121)}
        result = self.recommend(catalog, overlaps=Counter({1: 1}), allow_seen=True)
        self.assertEqual([row["contestId"] for row in result], [1])
        self.assertIn("已允许重复练习", result[0]["reason"])

    def test_unknown_level_is_not_fabricated(self):
        result = self.recommend({1: contest(1, [1500])}, [member(1, None), member(2, 1400)])
        self.assertTrue(result[0]["isProvisional"])
        self.assertIn("1 位成员参考水平缺失", result[0]["reason"])
        self.assertIn("每位已知水平成员", result[0]["reason"])
        self.assertNotIn("每位成员都", result[0]["reason"])
        result = self.recommend({1: contest(1, [1500])}, [member(1, None)])
        self.assertEqual(result[0]["fitLabel"], "水平待补充")
        self.assertIn("不能判断水平适配", result[0]["reason"])
        self.assertNotIn("可上手", result[0]["reason"])

    def test_reasons_describe_whole_contest_fit_without_claiming_learning_outcomes(self):
        members = [member(1, 1200), member(2, 1800)]
        balanced = self.recommend({1: contest(1, [1300, 1500, 1700])}, members)[0]
        self.assertIn("每位已知水平成员都有可上手的题目", balanced["reason"])
        self.assertIn("整场适配较均衡", balanced["reason"])
        self.assertIn("适配较弱的成员也得到兼顾", balanced["reason"])
        distant = self.recommend({1: contest(1, [800, 800])}, [member(1, 2500)])[0]
        self.assertIn("水平仍有距离", distant["reason"])
        self.assertNotIn("较均衡", distant["reason"])

    def test_invalid_levels_are_treated_as_unknown_instead_of_zero(self):
        for value in [True, "1400", -1, 5000]:
            with self.subTest(level=value):
                result = self.recommend({1: contest(1, [1500])}, [member(1, value)])
                self.assertEqual(result[0]["fitLabel"], "水平待补充")

    def test_missing_problem_difficulties_are_conservative(self):
        catalog = {1: contest(1, [1500]), 2: contest(2, [1500, None, None]),
                   3: contest(3, [None, None])}
        result = self.recommend(catalog)
        self.assertEqual([row["contestId"] for row in result], [1, 2, 3])
        self.assertFalse(result[0]["isProvisional"])
        self.assertTrue(result[1]["isProvisional"])
        self.assertIn("无法完整判断整场适配", result[1]["reason"])
        self.assertNotIn("每位已知水平成员都有", result[1]["reason"])
        self.assertNotIn("较均衡", result[1]["reason"])
        self.assertEqual(result[2]["fitLabel"], "难度资料不足")
        self.assertIn("无法判断成员适配", result[2]["reason"])

    def test_one_to_three_members_and_no_members(self):
        catalog = {1: contest(1, [1400, 1500, 1600])}
        for count in [1, 2, 3]:
            with self.subTest(count=count):
                self.assertEqual(len(self.recommend(catalog, [member(n, 1400) for n in range(1, count + 1)])), 1)
        self.assertEqual(recommend_contests(catalog, [], Counter(), 120), [])

    def test_ties_are_stable_and_independent_of_catalog_order(self):
        catalog = {cid: contest(cid, [1500]) for cid in [5, 3, 1, 4, 2]}
        reverse = dict(reversed(list(catalog.items())))
        expected = [1, 2, 3]
        self.assertEqual([row["contestId"] for row in self.recommend(catalog)], expected)
        self.assertEqual(self.recommend(catalog), self.recommend(reverse))

    def test_public_result_is_spoiler_free_and_inputs_are_unchanged(self):
        catalog = {1: contest(1, [1500])}
        members = [member(1, 1400), member(2, None)]
        before = deepcopy((catalog, members))
        output = self.recommend(catalog, members)
        text = json.dumps(output)
        self.assertNotIn("private problem-title sentinel", text)
        self.assertNotIn("secret tag sentinel", text)
        self.assertNotIn('"rating"', text)
        self.assertNotIn('"referenceRating"', text)
        self.assertNotIn('"problems"', text)
        self.assertEqual((catalog, members), before)


if __name__ == "__main__":
    unittest.main()
