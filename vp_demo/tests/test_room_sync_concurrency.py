"""Directed synchronization races against an isolated SQLite room.

No HTTP test classes are imported and no live CF requests are made. Events
control the interleavings, so assertions do not depend on network/sleep timing.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from duo_rooms import DuoRooms
from duo_rules import RoomError
from duo_sync import RoomAutoSync


class RoomSyncConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "rooms.sqlite3"
        self.lock = threading.RLock()
        self.start = 1800000000
        self.now = self.start + 1900
        self.rid = "concurrencyroom"
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, settings TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE submissions(user_id INTEGER, platform TEXT, payload TEXT);
                CREATE TABLE tasks(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, problem_key TEXT,
                    rating INTEGER, tags TEXT, url TEXT, note TEXT, created_at INTEGER, done INTEGER DEFAULT 0);
                INSERT INTO users(id,username) VALUES(1,'alpha'),(2,'beta');
            """)
            DuoRooms.init_tables(c)
        self.rooms = DuoRooms(self.connect, self.lock, object(),
                              Path(__file__).resolve().parents[1] / "data/contests.json",
                              clock=lambda: self.now)
        self.contest = self.rooms.catalog[1]
        self.end = self.start + self.contest["durationSeconds"]
        with self.connect() as c:
            c.execute("INSERT INTO vp_rooms(id,code,owner,mode,phase,max_minutes,chosen,created_at,start_at,end_at) "
                      "VALUES(?,?,1,'training','RUNNING',120,?,?,?,?)",
                      (self.rid, "AAAAAAAAAA", json.dumps(self.contest), self.start, self.start, self.end))
            c.executemany("INSERT INTO vp_members(room_id,uid,slot,handle) VALUES(?,?,?,?)",
                          [(self.rid, 1, 1, "alpha"), (self.rid, 2, 2, "beta")])

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def view(self, uid=1):
        return self.rooms.handle("GET", "/api/rooms/" + self.rid, uid)

    def event(self, slot, seconds, verdict="OK"):
        return dict(eventId=slot, slot=slot, index="A", elapsedSeconds=seconds, verdict=verdict)

    def run_sync_thread(self, uid, name, results, errors):
        def run():
            try:
                results.append(self.rooms._sync(uid, self.rid))
            except Exception as exc:
                errors.append(exc)
        t = threading.Thread(target=run, name=name, daemon=True)
        t.start()
        return t

    def test_same_seat_in_flight_rejects_duplicate_without_second_fetch(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []

        def fetch(adapter, chosen, member, start, end):
            entered.set()
            if not release.wait(4):
                raise AssertionError("test release timed out")
            return [self.event(member["slot"], 1200)]

        with patch("duo_rooms.fetch_cf_events", side_effect=fetch) as fetch_mock:
            t = self.run_sync_thread(1, "initial-sync", results, errors)
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(RoomError) as caught:
                    self.rooms._sync(1, self.rid)
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(fetch_mock.call_count, 1)
                self.assertTrue(self.view()["members"][0]["syncing"])
            finally:
                release.set()
                t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertFalse(self.view()["members"][0]["syncing"])

    def test_old_completion_cannot_clear_replacement_sync_marker(self):
        # Pause just after the successful DB transaction releases its lock.
        # A new request now legitimately acquires this seat. The old request's
        # finally block must not clear the new request's ownership marker.
        released, continue_old = threading.Event(), threading.Event()
        entered_new, release_new = threading.Event(), threading.Event()
        results, errors = [], []
        base_lock = self.lock

        class CompletionGate:
            enters = 0

            def __enter__(gate):
                base_lock.acquire()
                if threading.current_thread().name == "completed-sync":
                    gate.enters += 1
                return gate

            def __exit__(gate, *exc):
                pause = threading.current_thread().name == "completed-sync" and gate.enters == 2
                base_lock.release()
                if pause:
                    released.set()
                    if not continue_old.wait(4):
                        raise AssertionError("old request release timed out")

        self.rooms.lock = CompletionGate()

        def fetch(adapter, chosen, member, start, end):
            if threading.current_thread().name == "replacement-sync":
                entered_new.set()
                if not release_new.wait(4):
                    raise AssertionError("replacement request release timed out")
            return [self.event(1, 1200)]

        with patch("duo_rooms.fetch_cf_events", side_effect=fetch):
            old = self.run_sync_thread(1, "completed-sync", results, errors)
            new = None
            try:
                self.assertTrue(released.wait(2))
                new = self.run_sync_thread(1, "replacement-sync", results, errors)
                self.assertTrue(entered_new.wait(2))
                continue_old.set()
                old.join(2)
                self.assertFalse(old.is_alive())
                self.assertIn((self.rid, 1), self.rooms.syncing)
                with self.assertRaises(RoomError) as caught:
                    self.rooms._sync(1, self.rid)
                self.assertEqual(caught.exception.status, 409)
            finally:
                continue_old.set()
                release_new.set()
                old.join(3)
                if new:
                    new.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(self.rooms.syncing, set())

    def test_other_member_in_flight_prevents_freezing_older_complete_board(self):
        self.now = self.end
        # Previous successes already cover the cutoff. A newer request can still
        # carry a changed verdict, so its old snapshot must not be frozen.
        with self.connect() as c:
            c.execute("UPDATE vp_members SET sync_through=?,synced_at=?", (self.end, self.end))
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []

        def fetch(adapter, chosen, member, start, end):
            if member["uid"] == 2:
                entered.set()
                if not release.wait(4):
                    raise AssertionError("second member release timed out")
            return [self.event(member["slot"], 600 if member["uid"] == 2 else 1200)]

        with patch("duo_rooms.fetch_cf_events", side_effect=fetch):
            t = self.run_sync_thread(2, "new-beta-verdict", results, errors)
            try:
                self.assertTrue(entered.wait(2))
                first = self.rooms._sync(1, self.rid)
                self.assertEqual(first["phase"], "SETTLING")
                self.assertIsNone(first["report"])
                with self.connect() as c:
                    self.assertIsNone(c.execute("SELECT final_board FROM vp_rooms").fetchone()[0])
                    self.assertEqual(c.execute("SELECT count(*) FROM vp_upsolves").fetchone()[0], 0)
            finally:
                release.set()
                t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        final = self.view()
        self.assertEqual(final["phase"], "FINISHED")
        self.assertEqual([(r["handle"], r["solved"], r["penalty"]) for r in final["standings"]],
                         [("beta", 1, 10), ("alpha", 1, 20)])
        self.assertEqual(results[0]["standings"], final["standings"])

    def test_pending_verdict_blocks_auto_settlement_until_later_refresh(self):
        self.now = self.end
        alpha_pending = True

        def fetch(adapter, chosen, member, start, end):
            return [self.event(member["slot"], 1200 if member["uid"] == 1 else 1800,
                               "TESTING" if member["uid"] == 1 and alpha_pending else "OK")]

        with patch("duo_rooms.fetch_cf_events", side_effect=fetch):
            self.assertTrue(self.rooms.auto_sync_once())
            self.assertTrue(self.rooms.auto_sync_once())
            waiting = self.view()
            self.assertEqual(waiting["phase"], "SETTLING")
            self.assertIsNone(waiting["report"])
            self.assertTrue(any(cell["pending"] for row in waiting["standings"] for cell in row["cells"].values()))
            with self.connect() as c:
                self.assertIsNone(c.execute("SELECT final_board FROM vp_rooms").fetchone()[0])
            alpha_pending = False
            self.now += 15
            self.assertTrue(self.rooms.auto_sync_once())
            settled = self.view()
            self.assertEqual(settled["phase"], "FINISHED")
            self.assertEqual([r["solved"] for r in settled["standings"]], [1, 1])
            self.assertFalse(self.rooms.auto_sync_once())


class RoomSyncWorkerLifecycleTests(unittest.TestCase):
    def test_unexpected_failure_is_retried_and_stop_interrupts_idle_wait(self):
        recovered = threading.Event()

        class FlakyRooms:
            calls = 0

            def auto_sync_once(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary database exception")
                recovered.set()
                return False

        rooms = FlakyRooms()
        worker = RoomAutoSync(rooms)
        worker.start()
        try:
            self.assertTrue(recovered.wait(3))
        finally:
            worker.stop()
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(rooms.calls, 2)

    def test_stop_during_refresh_finishes_current_seat_without_starting_next(self):
        entered, release = threading.Event(), threading.Event()

        class BlockedRooms:
            calls = 0

            def auto_sync_once(self):
                self.calls += 1
                entered.set()
                if not release.wait(4):
                    raise AssertionError("worker release timed out")
                return True

        rooms = BlockedRooms()
        worker = RoomAutoSync(rooms)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            worker.stop()
            self.assertTrue(worker.stopped.is_set())
        finally:
            release.set()
            worker.thread.join(3)
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(rooms.calls, 1)


if __name__ == "__main__":
    unittest.main()
