"""HTTP acceptance for reports, recommendation invalidation and real background sync.

All CF responses are synthetic. No credentials, personal databases or external
submissions are used; time advances explicitly for cutoff and outage checks.
"""
import itertools
import json
import threading
import unittest
from unittest.mock import patch

import server
import test_demo
import test_duo
from duo_sync import RoomAutoSync


class RoomFeatureHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request
    ids = itertools.count(10000)
    call = test_duo.DuoHttpTests.call
    create = test_duo.DuoHttpTests.create
    pair = test_duo.DuoHttpTests.pair
    select = test_duo.DuoHttpTests.select
    start_room = test_duo.DuoHttpTests.start_room

    @classmethod
    def setUpClass(cls):
        test_duo.DuoHttpTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        test_duo.DuoHttpTests.tearDownClass.__func__(cls)

    def setUp(self):
        test_duo.DuoHttpTests.setUp(self)
        # The scheduler intentionally scans every active room, so isolate each
        # acceptance scenario rather than leaving previous test rooms running.
        with server.connect() as c:
            c.execute("DELETE FROM vp_upsolves")
            c.execute("DELETE FROM vp_members")
            c.execute("DELETE FROM vp_rooms")
        server.ROOMS.sync_attempts.clear()
        for contest in server.ROOMS.catalog.values():
            for problem in contest["problems"]:
                problem["rating"] = 1400

    def cf_response(self, method, **params):
        self.assertEqual(method, "contest.status")
        handle = params["handle"]
        changes = [(1, 300, "WRONG_ANSWER"), (2, 1200, "OK")] if handle == "alpha" else [(3, 1800, "OK")]
        records = []
        for sid, seconds, verdict in changes:
            row = test_demo.record(sid, verdict)["submission"]
            row.update(contestId=1, creationTimeSeconds=self.start+seconds)
            row["problem"]["contestId"] = 1
            row["author"]["members"] = [{"handle": handle}]
            records.append(row)
        return records

    def test_auto_sync_refreshes_both_accounts_without_manual_sync(self):
        self.start_room(self.select(self.pair("training")))
        self.now += 1900
        with patch.object(server.ROOMS.adapter, "call", side_effect=self.cf_response) as call:
            self.assertTrue(server.ROOMS.auto_sync_once())
            self.assertTrue(server.ROOMS.auto_sync_once())
            self.assertFalse(server.ROOMS.auto_sync_once())
            self.assertEqual([x.kwargs["handle"] for x in call.call_args_list], ["alpha", "beta"])
        a = self.call(self.a, self.room_path)
        b = self.call(self.b, self.room_path)
        self.assertEqual(a["standings"], b["standings"])
        self.assertEqual([(x["handle"], x["solved"], x["penalty"]) for x in a["standings"]],
                         [("beta", 1, 30), ("alpha", 1, 40)])
        self.assertTrue(a["autoSync"]["enabled"])
        self.assertTrue(all(m["syncedAt"] == self.now for m in a["members"]))

    def test_outage_preserves_scores_and_success_time_and_recovers(self):
        self.start_room(self.select(self.pair("training")))
        self.now += 1900
        with patch.object(server.ROOMS.adapter, "call", side_effect=self.cf_response):
            server.ROOMS.auto_sync_once(); server.ROOMS.auto_sync_once()
        previous = self.call(self.a, self.room_path)
        self.now += 90
        self.assertFalse(any(m["syncDelayed"] for m in self.call(self.a, self.room_path)["members"]))
        self.now += 1
        with patch.object(server.ROOMS.adapter, "call", side_effect=ValueError("模拟断网")):
            self.assertTrue(server.ROOMS.auto_sync_once())
            self.assertTrue(server.ROOMS.auto_sync_once())
            self.assertFalse(server.ROOMS.auto_sync_once())
        a = self.call(self.a, self.room_path)
        b = self.call(self.b, self.room_path)
        self.assertEqual(a["standings"], previous["standings"])
        self.assertEqual(a["standings"], b["standings"])
        self.assertTrue(all(m["syncDelayed"] and m["syncError"] for m in a["members"]))
        self.assertEqual([m["syncedAt"] for m in a["members"]], [m["syncedAt"] for m in previous["members"]])
        self.now += 15
        with patch.object(server.ROOMS.adapter, "call", side_effect=self.cf_response):
            server.ROOMS.auto_sync_once(); server.ROOMS.auto_sync_once()
        self.assertTrue(all(not m["syncDelayed"] and not m["syncError"] for m in self.call(self.a, self.room_path)["members"]))

    def test_background_worker_keeps_http_responsive_and_rotates_after_failure(self):
        self.start_room(self.select(self.pair("training")))
        self.now += 1900
        entered, release, beta_done = threading.Event(), threading.Event(), threading.Event()
        def response(method, **params):
            if params["handle"] == "alpha":
                entered.set()
                if not release.wait(5):
                    raise ValueError("test timed out")
                raise ValueError("模拟首位成员失败")
            beta_done.set()
            return self.cf_response(method, **params)
        worker = RoomAutoSync(server.ROOMS)
        try:
            with patch.object(server.ROOMS.adapter, "call", side_effect=response):
                worker.start()
                self.assertTrue(entered.wait(3))
                # Network is blocked, but another authenticated session still reads the board.
                during = self.call(self.b, self.room_path)
                self.assertTrue(during["members"][0]["syncing"])
                self.assertEqual(during["phase"], "RUNNING")
                release.set()
                self.assertTrue(beta_done.wait(3))
                worker.stop()
        finally:
            release.set()
            if worker.thread.is_alive():
                worker.stop()
        result = self.call(self.a, self.room_path)
        self.assertTrue(result["members"][0]["syncError"])
        self.assertEqual(result["standings"][0]["handle"], "beta")
        self.assertEqual(result["standings"][0]["solved"], 1)

    def test_auto_sync_settles_and_stops_at_end_for_all_members(self):
        r = self.start_room(self.select(self.pair("training")))
        self.now = r["endAt"]
        with patch.object(server.ROOMS.adapter, "call", side_effect=self.cf_response) as call:
            self.assertTrue(server.ROOMS.auto_sync_once())
            self.assertEqual(self.call(self.a, self.room_path)["phase"], "SETTLING")
            self.assertTrue(server.ROOMS.auto_sync_once())
            result = self.call(self.b, self.room_path)
            self.assertEqual(result["phase"], "FINISHED")
            self.assertFalse(result["autoSync"]["enabled"])
            self.assertFalse(server.ROOMS.auto_sync_once())
            self.assertEqual(call.call_count, 2)
        self.assertIn("beta以 30 比 40", result["report"]["summary"])

    def test_report_is_member_only_final_and_consistent_for_both_accounts(self):
        r = self.start_room(self.select(self.pair()))
        self.call(self.a, self.room_path+"/report", status=409)
        self.call(self.c, self.room_path+"/report", status=404)
        self.call(self.a, self.room_path+"/replay", {"elapsedSeconds": r["selected"]["durationSeconds"]})
        report = self.call(self.a, self.room_path+"/report")
        self.assertEqual(report, self.call(self.b, self.room_path+"/report"))
        self.assertEqual(report, self.call(self.a, self.room_path)["report"])
        self.assertEqual([(p["handle"], p["solved"], p["penalty"], p["rank"]) for p in report["participants"]],
                         [("beta", 1, 30, 1), ("alpha", 1, 40, 2)])
        self.assertIn("两人各通过 1 题", report["summary"])
        self.assertIn("beta以 30 比 40", report["summary"])
        self.assertEqual(report["firstSolves"][0]["handles"], ["alpha"])
        self.call(self.c, self.room_path+"/report", status=404)
        self.call(self.client("outsider-report"), self.room_path+"/report", status=404)

    def test_recommendations_recompute_after_members_settings_and_history_change(self):
        for user, level in ((self.a, 1000), (self.b, 2000)):
            self.call(user, "/api/settings", {"referenceRating": level})
        r = self.create()
        self.assertEqual({x["contestId"] for x in r["recommendations"]}, {1, 4})
        self.assertFalse(any(x["isProvisional"] for x in r["recommendations"]))
        r = self.call(self.b, "/api/rooms/join", {"code": r["inviteCode"], "handle": "beta"})
        self.assertLessEqual(len(r["recommendations"]), 3)
        past = test_demo.record(90001)
        past["handle"] = "beta"
        past["submission"]["problem"]["contestId"] = 4
        self.call(self.b, "/api/import", {"text": json.dumps([past])})
        r = self.call(self.a, self.room_path)
        self.assertEqual([x["contestId"] for x in r["recommendations"]], [1])
        # Search affects browsing, not room-wide recommendation eligibility.
        searched = self.call(self.a, self.room_path+"?q=nonexistent")
        self.assertEqual(searched["recommendations"], r["recommendations"])
        r = self.call(self.a, self.room_path+"/preferences", {"version": r["version"], "maxMinutes": 60, "allowSeen": False})
        self.assertEqual(r["recommendations"], [])

    def test_reference_profile_requires_matching_handle_and_report_denies_anonymous(self):
        r = self.create()
        with server.connect() as c:
            uid = c.execute("SELECT uid FROM vp_members WHERE room_id=?", (r["id"],)).fetchone()[0]
            c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps({"profile": {"handle": "someone_else", "rating": 1800}}), uid))
        self.assertTrue(all(x["isProvisional"] for x in self.call(self.a, self.room_path)["recommendations"]))
        with server.connect() as c:
            c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps({"profile": {"handle": "ALPHA", "rating": 1800}}), uid))
        self.assertFalse(any(x["isProvisional"] for x in self.call(self.a, self.room_path)["recommendations"]))
        import urllib.request
        anonymous = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.call(anonymous, self.room_path+"/report", status=401)


if __name__ == "__main__":
    unittest.main()
