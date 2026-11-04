"""Input/evidence regression checks; these do not evaluate live model quality."""
import copy
import unittest

from ai_provider import build_review_input
from review_service import validate_output


def snapshot(verdicts, statement="", missing=()):
    records = [{"externalId": str(i), "verdict": verdict, "language": "GNU C++17",
                "participantType": "PRACTICE", "submittedAt": i,
                "source": "int main() {\r\n    return 0;\r\n}\r\n"}
               for i, verdict in enumerate(verdicts, 1)]
    return {"records": records, "analysisScope": "first_ac", "coverageState": "partial",
            "warnings": [], "mode": "review", "problem": {"text": statement},
            "question": "", "relatedExperiences": [], "missingSourceIds": list(missing),
            "timeline": records + [{"externalId": sid} for sid in missing],
            "steps": [{"before": a["externalId"], "after": b["externalId"]}
                      for a, b in zip(records, records[1:])]}


class ReviewInputTests(unittest.TestCase):
    def test_representative_shapes_and_explicit_facts(self):
        cases = [(["OK"], "single_snapshot", 0),
                 (["WRONG_ANSWER", "OK"], "pair", 1),
                 (["TIME_LIMIT_EXCEEDED", "OK"], "pair", 1),
                 (["TIME_LIMIT_EXCEEDED"] * 4 + ["WRONG_ANSWER"] * 4 + ["OK"], "journey", 8)]
        for verdicts, kind, transitions in cases:
            with self.subTest(kind=kind, verdicts=verdicts):
                original = snapshot(verdicts, "A provided statement.")
                before = copy.deepcopy(original)
                data = build_review_input(original)
                self.assertEqual(data["facts"]["analysisKind"], kind)
                self.assertEqual(data["facts"]["sourceVersionCount"], len(verdicts))
                self.assertEqual(data["facts"]["transitionCount"], transitions)
                self.assertTrue(data["facts"]["hasProblemStatement"])
                self.assertFalse(data["facts"]["testsExecuted"])
                self.assertEqual(data["steps"], original["steps"])
                self.assertEqual(data["versions"][0]["source"], "1: int main() {\n2:     return 0;\n3: }\n4: ")
                self.assertEqual(original, before)

    def test_missing_source_and_statement_are_separate_from_history_coverage(self):
        data = build_review_input(snapshot(["OK"], missing=["2"]))
        self.assertFalse(data["facts"]["hasProblemStatement"])
        self.assertEqual(data["facts"]["observedSubmissionCount"], 2)
        self.assertEqual(data["facts"]["sourceVersionCount"], 1)
        self.assertEqual(data["facts"]["missingSourceCount"], 1)
        self.assertEqual(data["coverage"], "partial")

    def test_evidence_normalizes_only_line_endings(self):
        original = snapshot(["WRONG_ANSWER", "OK"])
        before = copy.deepcopy(original)
        quote = "int main() {\n    return 0;"
        ref = {"submissionId": "2", "lineStart": 1, "lineEnd": 2, "quote": quote}
        raw = {"summary": "Example", "steps": [{"before": "1", "after": "2",
               "observation": "Example", "hypothesis": "Example", "test": "Example",
               "evidence": [ref]}], "candidates": [], "limitations": []}
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=repr(newline)):
                ref["quote"] = quote.replace("\n", newline)
                self.assertTrue(validate_output(raw, original)["steps"][0]["evidenceValid"])
        for changed in ({"quote": quote.replace("0", "1")},
                        {"quote": quote.replace("    return", "  return")},
                        {"lineStart": 2, "lineEnd": 3}, {"submissionId": "99"}):
            with self.subTest(changed=changed):
                invalid = copy.deepcopy(raw)
                invalid["steps"][0]["evidence"][0].update(changed)
                self.assertFalse(validate_output(invalid, original)["steps"][0]["evidenceValid"])
        self.assertEqual(original, before)
