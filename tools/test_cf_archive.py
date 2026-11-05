"""Regression tests for failures actually observed during the public CF archive run."""
import gzip
import hashlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from build_cf_archive import ingest
from cf_archive_catalog import SCHEMA, add_text, attach_text
from fetch_cf_standings import fetch, read_receipts
from query_cf_archive import export_results, vp_catalog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vp_demo"))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO contests VALUES(10,'Fixture','FINISHED','ICPC',100,7200)")
        self.addCleanup(self.db.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "standings").mkdir()

    def scoreboard(self, **kwargs):
        roster = [dict(contestId=10, index=i, name=n, type="PROGRAMMING", tags=[])
                  for i,n in [("A", "First task"), ("01", "Second task")]]
        party = dict(participantType="CONTESTANT", members=[{"handle": "alice"}, {"handle": "bob"}])
        row = dict(rank=1, party=party, points=1, penalty=4, problemResults=[
            dict(points=1, rejectedAttemptCount=0, bestSubmissionTimeSeconds=245, type="FINAL"),
            dict(points=0, rejectedAttemptCount=2, type="FINAL")])
        obj = dict(contest=dict(id=10, type="ICPC", frozen=False), problems=roster, rows=[row])
        obj.update(kwargs)
        path = self.root / "standings/10.json.gz"
        packed = gzip.compress(json.dumps(dict(status="OK", result=obj)).encode())
        path.write_bytes(packed)
        return dict(contestId=10, status="ok", file="standings/10.json.gz", problems=2, rows=1,
                    checkedAt="2026-11-05T08:00:00Z", url="https://codeforces.com/api/contest.standings?contestId=10",
                    compressedBytes=len(packed), sha256=hashlib.sha256(packed).hexdigest())

    def test_actual_order_and_team_scores_survive_export(self):
        # Global catalog has only A. The public contest roster additionally has numeric index 01.
        self.db.execute("INSERT INTO problems VALUES(10,'A','First task','PROGRAMMING',NULL,NULL,'[]',7)")
        entry = self.scoreboard()
        ingest(self.db, self.root, entry)
        target = self.root / "team.jsonl.gz"
        self.assertEqual(export_results(self.db, self.root, 10, target)["exportedRows"], 2)
        with gzip.open(target, "rt", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual([(r["index"], r["problemPoints"], r["partyMembers"]) for r in rows],
                         [("A",1,["alice","bob"]), ("01",0,["alice","bob"])])
        self.assertIsNone(rows[1]["bestSubmissionTimeSeconds"])
        self.assertIsNone(self.db.execute("SELECT solved_count FROM contest_problem_details WHERE idx='01'").fetchone()[0])
        catalog = vp_catalog(self.db, [10])
        self.assertEqual(catalog["contests"][0]["problemKeys"], ["cf:10:A", "cf:10:01"])
        self.assertEqual([p["index"] for p in catalog["problems"]], ["A", "01"])
        ingest(self.db, self.root, entry)
        self.assertEqual(self.db.execute("SELECT count(*) FROM contest_problems").fetchone()[0], 2)

    def test_title_mismatch_quarantines_a_published_alias(self):
        add_text(self.db, "openr1", "10/A", ["10/B"], "First task", ["Body",None,None,None,None],
                 [None,None], [{"input":"1\n2","output":"3"}])
        attach_text(self.db, 10, "A", " First Task ")
        attach_text(self.db, 10, "B", "Different challenge")
        direct, alias = list(self.db.execute("SELECT body_id,sample_id,match_rule FROM problem_text ORDER BY idx"))
        self.assertIsNotNone(direct["body_id"])
        self.assertIsNotNone(direct["sample_id"])
        self.assertEqual(tuple(alias), (None, None, "title_requires_review"))

    def test_corrupted_scoreboard_does_not_enter_catalog(self):
        entry = self.scoreboard()
        (self.root / entry["file"]).write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "Damaged"):
            ingest(self.db, self.root, entry)
        self.assertEqual(self.db.execute("SELECT count(*) FROM standings_files").fetchone()[0], 0)
        entry = self.scoreboard(rows=[{"problemResults":[]}])
        with self.assertRaisesRegex(ValueError, "dimensions"):
            ingest(self.db, self.root, entry)

    def test_local_statement_and_empty_source_coverage(self):
        from problem_statement import StatementService
        from os import environ
        add_text(self.db, "openr1", "10/A", [], "First task", ["Body", "Read n.", "Print n.", None, None],
                 ["1 second","256 MB"], [{"input":"7","output":"7"}])
        add_text(self.db, "openr1", "10/01", [], "Only a link", ["View the statement as a PDF.",None,None,None,None],
                 [None,None], [{"input":"","output":""}])
        attach_text(self.db, 10, "A", "First task")
        attach_text(self.db, 10, "01", "Only a link")
        self.db.commit()
        target = self.root / "catalog.sqlite3"
        output = sqlite3.connect(target)
        self.db.backup(output)
        output.close()
        record = {"platform":"codeforces", "contestId":10, "index":"A"}
        service = StatementService(None, {})
        with patch.dict(environ, {"VPLAB_CF_ARCHIVE":str(target)}), \
                patch("problem_statement.urllib.request.urlopen", side_effect=AssertionError("unexpected network")):
            local = service.get(record)
            self.assertEqual((local["status"],local["textOrigin"]), ("ready","cf_archive"))
            self.assertIn("Examples\n\nInput\n\n7\n\nOutput\n\n7", local["text"])
            self.assertEqual(service.get(dict(record, index="01"))["status"], "unavailable")
        missing = self.db.execute("SELECT body_id,sample_id FROM problem_text WHERE idx='01'").fetchone()
        self.assertEqual(tuple(missing), (None,None))

    def test_truncated_http_body_retries(self):
        class Reply(io.BytesIO):
            headers = {}
        uri = "https://codeforces.com/api/contest.standings?contestId=10"
        with patch("fetch_cf_standings.acquire_slot") as slot, patch("fetch_cf_standings.time.sleep"), \
                patch("fetch_cf_standings.urlopen", side_effect=[IncompleteRead(b"partial"), Reply(b"{}")]):
            self.assertEqual(fetch(uri, self.root), b"{}")
            self.assertEqual(slot.call_count, 2)

    def test_resume_preserves_a_torn_receipt_for_audit(self):
        ledger = self.root / 'standings-index.jsonl'
        full = b'{"contestId":10,"status":"ok"}\n'
        tail = b'{"contestId":11,"sta'
        ledger.write_bytes(full + tail)
        self.assertEqual(list(read_receipts(ledger)), [10])
        self.assertEqual(ledger.read_bytes(), full + tail)
        self.assertEqual(list(read_receipts(ledger, repair_tail=True)), [10])
        self.assertEqual(ledger.read_bytes(), full)
        self.assertEqual(ledger.with_suffix('.interrupted-tail').read_bytes(), tail)


if __name__ == "__main__":
    unittest.main()
