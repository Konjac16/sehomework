import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import server
from training_service import TrainingService, tag_counts, tag_weights, day_key


def problem(key="1900A", rating=1800, tags=None):
    return {"platform": "codeforces", "externalId": key, "problemKey": key, "title": "Test <title>",
            "rating": rating, "tags": tags if tags is not None else ["dp", "math"],
            "url": "https://codeforces.com/contest/1900/problem/" + key[-1]}


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_db = patch.object(server, "DB", Path(self.tmp.name) / "test.sqlite3")
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        server.init_db()
        with server.connect() as c:
            for i in (1, 2):
                c.execute("INSERT INTO users(id,username,salt,password_hash,settings) VALUES(?,?, 'salt','hash',?)",
                          (i, f"tester{i}", json.dumps({"handle": f"tester{i}"})))
        self.now = 1800000000
        self.adapter = Mock()
        self.adapter.problem_catalog.return_value = [problem()]
        self.s = TrainingService(server.connect, server.DB_LOCK, {"codeforces": self.adapter}, lambda: self.now, random.Random(1))
        self.service_patch = patch.object(server, "TRAINING", self.s)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    def catalog(self, problems):
        with server.connect() as c:
            c.execute("INSERT OR REPLACE INTO training_catalog VALUES('codeforces',?,?)", (json.dumps(problems), self.now))

    def records(self, p, verdict="OK", timestamp=None, uid=1, profile=None):
        timestamp = self.now if timestamp is None else timestamp
        record = {**p, "externalId": str(timestamp), "handle": f"tester{uid}", "source": "", "submittedAt": timestamp, "verdict": verdict}
        server.save_records(uid, [record], "test", profile)

    def start(self, p=None):
        p = p or problem()
        self.catalog([p])
        self.s.recommend(1)
        return self.s.act(1, "start", {"problemKey": p["problemKey"]})["active"]

    def finish(self, p, mode="independent"):
        run = self.start(p)
        self.now += 5
        self.records(p)
        return self.s.act(1, "feedback", {"id": run["id"], "completionMode": mode})

    def test_defaults_and_manual_preferences(self):
        s = self.s.state(1)
        self.assertEqual((s["referenceRating"], s["range"]), (1800, [1600, 2000]))
        for intensity, expected in (("easy", [1200, 1600]), ("challenge", [2000, 2400])):
            self.assertEqual(self.s.preferences(1, {"intensity": intensity})["range"], expected)
        self.assertEqual(self.s.preferences(1, {"difficultyMode": "manual", "minRating": 1300, "maxRating": 1750})["range"], [1300, 1750])
        for bad in ({"minRating": 2000, "maxRating": 1000}, {"includeAttempted": "false"}, {"intensity": "wrong"}, {"minRating": True}):
            with self.assertRaises(ValueError):
                self.s.preferences(1, bad)

    def test_weight_formula_fractional_and_recent(self):
        p = problem(tags=["dp", "math", "math"])
        self.assertEqual(tag_counts([p]), {"dp": .5, "math": .5})
        weights = tag_weights(["dp", "math", "graphs"], [p, problem(tags=["dp"])], [problem(tags=["dp"])])
        self.assertAlmostEqual(weights["dp"], .7)
        self.assertAlmostEqual(weights["math"], 1 + .5 * (1 - .5 / 1.5))
        self.assertEqual(weights["graphs"], 1.5)
        self.assertEqual(tag_weights(["dp"], [], []), {"dp": 1})

    def test_selection_persisted_no_tags_and_no_runs_until_start(self):
        a = self.s.recommend(1)
        self.assertIsNotNone(a["recommendation"])
        self.assertEqual(a["history"], [])
        self.assertNotIn('"tags"', json.dumps(a))
        self.assertEqual(self.s.recommend(1)["recommendation"], a["recommendation"])
        self.adapter.problem_catalog.assert_called_once()
        self.assertIsNone(self.s.state(2)["recommendation"])

    def test_dismiss_cooldown_shanghai_day_and_no_level_change(self):
        self.s.recommend(1)
        self.s.act(1, "dismiss", {"problemKey": "1900A"})
        self.assertIsNone(self.s.recommend(1)["recommendation"])
        self.assertEqual(self.s.state(1)["history"], [])
        self.now += 86400
        self.assertIsNotNone(self.s.recommend(1)["recommendation"])
        self.assertEqual(self.s.state(1)["referenceRating"], 1800)
        self.assertEqual(day_key(16 * 3600), "1970-01-02")

    def test_attempted_toggle_solved_exclusion_and_no_range_expansion(self):
        p = problem()
        self.records(p, "WRONG_ANSWER", self.now - 10)
        self.assertIsNone(self.s.recommend(1)["recommendation"])
        self.s.preferences(1, {"includeAttempted": True})
        self.assertIsNotNone(self.s.recommend(1)["recommendation"])
        self.records(p)
        self.assertIsNone(self.s.recommend(1)["recommendation"])
        self.catalog([problem("1900B", 2100)])
        self.assertIsNone(self.s.recommend(1)["recommendation"])

    def test_skip_resume_and_later_ac_require_feedback(self):
        p = problem()
        run = self.start(p)
        with self.assertRaises(ValueError):
            self.s.act(1, "feedback", {"id": run["id"], "completionMode": "independent"})
        self.s.act(1, "skip", {"id": run["id"]})
        self.assertEqual(self.s.state(1)["history"][0]["status"], "skipped")
        self.now += 86400
        self.assertIsNone(self.s.recommend(1)["recommendation"])
        self.s.preferences(1, {"includeAttempted": True})
        self.assertIsNotNone(self.s.recommend(1)["recommendation"])
        self.s.act(1, "resume", {"id": run["id"]})
        self.assertEqual(len(self.s.state(1)["history"]), 1)
        self.s.act(1, "skip", {"id": run["id"]})
        self.records(p)
        s = self.s.state(1)
        self.assertEqual(s["pending"][0]["acId"], str(self.now))
        with self.assertRaises(ValueError):
            self.s.act(1, "skip", {"id": run["id"]})
        with self.assertRaises(ValueError):
            self.s.act(1, "feedback", {"id": run["id"], "completionMode": ""})
        self.assertIsNone(self.s.recommend(1)["recommendation"])
        s = self.s.act(1, "feedback", {"id": run["id"], "completionMode": "hint"})
        self.assertEqual(s["history"][0]["status"], "completed")
        with self.assertRaises(ValueError):
            self.s.act(1, "feedback", {"id": run["id"], "completionMode": "hint"})

    def test_other_pending_does_not_interrupt_active(self):
        a = self.start()
        self.s.act(1, "skip", {"id": a["id"]})
        b = self.start(problem("1900B"))
        self.records(problem())
        s = self.s.state(1)
        self.assertEqual(s["active"]["id"], b["id"])
        self.assertEqual(s["pending"][0]["id"], a["id"])

    def test_only_post_start_ac_and_user_isolation(self):
        a = self.start()
        for action in ("skip", "resume", "feedback"):
            with self.assertRaises(ValueError):
                self.s.act(2, action, {"id": a["id"], "completionMode": "independent"})
        self.records(problem(), "OK", self.now - 1)
        self.assertIsNotNone(self.s.state(1)["active"])
        self.now += 10
        self.records(problem(), "WRONG_ANSWER")
        self.assertIsNotNone(self.s.state(1)["active"])
        self.now += 10
        self.records(problem())
        self.assertIsNone(self.s.state(1)["active"])
        self.assertEqual(len(self.s.state(1)["pending"]), 1)

    def test_dynamic_level_three_feedback_and_official_reset(self):
        self.finish(problem("1900A", 1800))
        self.finish(problem("1900B", 1900))
        s = self.finish(problem("1900C", 1700), "hint")
        self.assertEqual((s["referenceRating"], s["feedbackWindow"]), (1900, 0))
        self.assertEqual(s["range"], [1700, 2100])
        server.save_records(1, [], "test", {"handle": "tester1", "rating": 1500})
        self.assertEqual(self.s.state(1)["referenceRating"], 1500)
        self.finish(problem("1900D", 1500))
        self.finish(problem("1900E", 1500))
        self.finish(problem("1900F", 1500))
        self.assertEqual(self.s.state(1)["referenceRating"], 1600)
        server.save_records(1, [], "test", {"handle": "tester1", "rating": 1500})
        self.assertEqual(self.s.state(1)["referenceRating"], 1600)
        self.s.preferences(1, {"difficultyMode": "manual", "minRating": 1400, "maxRating": 1800})
        server.save_records(1, [], "test", {"handle": "tester1", "rating": 1700})
        self.assertEqual(self.s.state(1)["referenceRating"], 1700)
        self.assertEqual(self.s.state(1)["range"], [1400, 1800])

    def test_editorial_at_high_difficulty_does_not_lower_reference(self):
        self.s.preferences(1, {"difficultyMode": "manual", "minRating": 1600, "maxRating": 2400})
        for key in ("1900A", "1900B", "1900C"):
            self.finish(problem(key, 2200), "editorial")
        self.assertEqual(self.s.state(1)["referenceRating"], 1800)
        for key in ("1900D", "1900E", "1900F"):
            self.finish(problem(key, 1700), "editorial")
        self.assertEqual(self.s.state(1)["referenceRating"], 1700)

    def test_old_epoch_cannot_change_new_official_rating(self):
        self.finish(problem("1900A"))
        run = self.start(problem("1900B"))
        server.save_records(1, [], "test", {"handle": "tester1", "rating": 2000})
        self.now += 5
        self.records(problem("1900B"))
        self.s.act(1, "feedback", {"id": run["id"], "completionMode": "independent"})
        self.assertEqual(self.s.state(1)["feedbackWindow"], 0)

    def test_cache_failure_and_stale_cache_fallback(self):
        self.adapter.problem_catalog.side_effect = ValueError("offline")
        with self.assertRaises(ValueError):
            self.s.recommend(1)
        self.catalog([problem()])
        self.now += 86401
        self.assertIsNotNone(self.s.recommend(1)["recommendation"])

    def test_concurrent_start_creates_only_one_run(self):
        self.s.recommend(1)
        outcomes = []
        def start():
            try:
                self.s.act(1, "start", {"problemKey": "1900A"})
                outcomes.append("ok")
            except ValueError:
                outcomes.append("rejected")
        threads = [threading.Thread(target=start) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(outcomes), ["ok", "rejected"])
        self.assertEqual(len(self.s.state(1)["history"]), 1)


if __name__ == '__main__':
    unittest.main()
