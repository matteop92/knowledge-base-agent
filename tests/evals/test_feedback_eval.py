import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import yaml

from evals.feedback_checks import check_attribution, check_result_set, check_route, validate_check
from evals.feedback_eval import compare, import_routes, load_set, promote, run_evals

TIM_DOC = "Knowledge/Transcripts/tim-and-niels.md"


def cited(speaker, ref_doc=TIM_DOC, **extra):
    return {"document_id": "doc-1", "relative_path": ref_doc, "run_id": "run-1", "start_char": 100,
            "speaker": speaker, **extra}


def receipt(answer, **citations):
    return {"answer": answer, "citations": citations}


ATTRIBUTION = {"kind": "attribution", "document": TIM_DOC, "person": "Tim Ferriss"}


class RouteCheckTests(unittest.TestCase):
    def test_right_route_and_properties_pass_and_a_plain_question_sent_to_narrowed_fails(self):
        narrowed = {"route": "narrowed", "narrowing": {"types": ["highlights"]}}
        check = {"kind": "route", "route": "narrowed", "types": ["highlights", "book"], "period": False}
        self.assertTrue(check_route(check, narrowed).passed)
        wrong = check_route({"kind": "route", "route": "plain"}, narrowed)
        self.assertFalse(wrong.passed)
        self.assertIn("routed narrowed, expected plain", wrong.detail)

    def test_narrowing_properties_are_compared(self):
        check = {"kind": "route", "route": "narrowed", "types": ["note"], "latest": True}
        self.assertFalse(check_route(check, {"route": "narrowed", "narrowing": {"types": ["note"]}}).passed)
        self.assertFalse(check_route(check, {"route": "narrowed", "narrowing": {"types": ["article"], "latest": 1}}).passed)
        self.assertTrue(check_route(check, {"route": "narrowed", "narrowing": {"types": ["note"], "latest": 1}}).passed)
        self.assertFalse(check_route(check, None).passed)


class ResultSetCheckTests(unittest.TestCase):
    def test_equal_sets_pass_and_one_missing_or_extra_fails(self):
        check = {"kind": "result_set", "ids": ["a", "b"]}
        self.assertTrue(check_result_set(check, {"matched": ["b", "a"]}).passed)
        self.assertIn("missing ['b']", check_result_set(check, {"matched": ["a"]}).detail)
        self.assertIn("extra ['c']", check_result_set(check, {"matched": ["a", "b", "c"]}).detail)
        self.assertFalse(check_result_set(check, None).passed)
        self.assertTrue(check_result_set({"kind": "result_set", "ids": []}, {"matched": []}).passed)

    def test_an_ordered_check_fails_when_the_same_ids_come_back_in_another_order(self):
        check = {"kind": "result_set", "ids": ["a", "b"], "ordered": True}
        self.assertTrue(check_result_set(check, {"matched": ["a", "b"]}).passed)
        result = check_result_set(check, {"matched": ["b", "a"]})
        self.assertFalse(result.passed)
        self.assertIn("another order", result.detail)


class AttributionCheckTests(unittest.TestCase):
    def test_a_sentence_naming_the_host_while_citing_a_guest_fails_and_citing_the_host_passes(self):
        failing = check_attribution(ATTRIBUTION, receipt("Tim notes that platforms hire slowly [E1.2].",
                                                         **{"E1.2": cited("Niels Hoven")}))
        self.assertFalse(failing.passed)
        self.assertIn("Niels Hoven", failing.detail)
        self.assertTrue(check_attribution(ATTRIBUTION, receipt("Tim notes that platforms hire slowly [E1.2].",
                                                               **{"E1.2": cited("Tim Ferriss")})).passed)

    def test_a_sentence_naming_nobody_passes_and_naming_the_guest_passes(self):
        guest = {"E1.2": cited("Niels Hoven")}
        self.assertTrue(check_attribution(ATTRIBUTION, receipt("He cites X as a platform [E1.2].", **guest)).passed)
        self.assertTrue(check_attribution(ATTRIBUTION, receipt("Build a two-minute readiness test [E1.2].", **guest)).passed)
        self.assertFalse(check_attribution(ATTRIBUTION, receipt("Tim says to build a readiness test [E1.2].", **guest)).passed)
        self.assertTrue(check_attribution(ATTRIBUTION, receipt(
            "Niels Hoven, on Tim's show, notes that platforms hire slowly [E1.2].", **guest)).passed)

    def test_a_pass_that_examined_nothing_is_marked_not_exercised(self):
        host_only = check_attribution(ATTRIBUTION, receipt("Tim notes that platforms hire slowly [E1.2].",
                                                          **{"E1.2": cited("Tim Ferriss")}))
        self.assertEqual((host_only.passed, host_only.exercised), (True, False))
        guest = check_attribution(ATTRIBUTION, receipt("He cites X as a platform [E1.2].", **{"E1.2": cited("Niels Hoven")}))
        self.assertEqual((guest.passed, guest.exercised), (True, True))

    def test_passages_of_other_documents_are_ignored(self):
        other = {"E2.0": cited("Niels Hoven", ref_doc="Knowledge/Articles/other.md", document_id="doc-2")}
        self.assertTrue(check_attribution(ATTRIBUTION, receipt("Tim says so [E2.0].", **other)).passed)

    def test_a_receipt_from_before_speakers_is_looked_up_from_the_stored_run(self):
        old = cited(None)
        old.pop("speaker")
        calls = []

        def lookup(run_id, start_char):
            calls.append((run_id, start_char))
            return "Niels Hoven"

        result = check_attribution(ATTRIBUTION, receipt("Tim says so [E1.0].", **{"E1.0": old}), lookup)
        self.assertFalse(result.passed)
        self.assertEqual(calls, [("run-1", 100)])

    def test_a_receipt_whose_run_is_gone_fails_with_a_message_naming_the_run(self):
        old = cited(None)
        old.pop("speaker")

        def lookup(run_id, start_char):
            raise LookupError(f"run {run_id} is no longer stored")

        result = check_attribution(ATTRIBUTION, receipt("Tim says so [E1.0].", **{"E1.0": old}), lookup)
        self.assertEqual((result.passed, result.detail), (False, "run run-1 is no longer stored"))


class SetTests(unittest.TestCase):
    def setUp(self):
        self.old = Path.cwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        Path(".local").mkdir()

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def write_export(self):
        export = Path(".local/export.json")
        export.write_text(json.dumps({"turns": [{"turn_id": "t" * 8 + "-1", "question": "Q one?", "note": "bad"}]}),
                          encoding="utf-8")
        return export

    def test_promote_writes_unapproved_drafts_and_never_overwrites(self):
        export = self.write_export()
        target = promote(export, Path(".local/set.yaml"), ["tttttttt-1"])
        entries = yaml.safe_load(target.read_text(encoding="utf-8"))["evals"]
        self.assertEqual((entries[0]["question"], entries[0]["approved"]), ("Q one?", False))
        with self.assertRaises(ValueError):
            promote(export, Path(".local/set.yaml"), ["tttttttt-1"])
        with self.assertRaises(ValueError):
            promote(export, Path(".local/other.yaml"), ["unknown-turn"])
        self.assertFalse(Path(".local/other.yaml").exists())

    def test_a_set_outside_local_is_refused(self):
        outside = Path(self.tmp.name) / "set.yaml"
        outside.write_text("evals: []", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_set(outside)
        with self.assertRaises(ValueError):
            promote(self.write_export(), outside.with_name("new.yaml"), ["tttttttt-1"])

    def test_an_approved_entry_needs_a_valid_check_but_a_draft_does_not(self):
        Path(".local/set.yaml").write_text(yaml.safe_dump({"evals": [
            {"id": "a", "question": "q", "check": {"kind": "TODO"}, "approved": False}]}), encoding="utf-8")
        self.assertEqual(len(load_set(Path(".local/set.yaml"))), 1)
        Path(".local/bad.yaml").write_text(yaml.safe_dump({"evals": [
            {"id": "a", "question": "q", "check": {"kind": "TODO"}, "approved": True}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_set(Path(".local/bad.yaml"))
        with self.assertRaises(ValueError):
            validate_check({"kind": "result_set"})
        self.assertEqual(validate_check({"kind": "result_set", "ids": []})["ids"], [])  # "no match" is a valid answer

    def test_import_routes_makes_an_approved_route_set(self):
        questions = Path(".local/q.json")
        questions.write_text(json.dumps([
            {"q": "How many books?", "route": "catalog"},
            {"q": "In my highlights, X?", "route": "narrowed", "type": ["highlights"], "date": True}]), encoding="utf-8")
        entries = load_set(import_routes(questions, Path(".local/routes.yaml")))
        self.assertEqual(entries[0]["check"], {"kind": "route", "route": "catalog"})
        self.assertEqual(entries[1]["check"], {"kind": "route", "route": "narrowed", "types": ["highlights"],
                                               "period": True})
        self.assertTrue(all(entry["approved"] for entry in entries))


class RunTests(unittest.TestCase):
    ENTRIES = [
        {"id": "route-1", "question": "How many?", "approved": True, "check": {"kind": "route", "route": "catalog"}},
        {"id": "latest", "question": "Latest?", "approved": True, "check": {"kind": "result_set", "ids": ["d1"]}},
        {"id": "draft", "question": "Skipped", "approved": False, "check": {"kind": "TODO"}},
    ]

    def run_evals(self, answers, max_usd="1", **kwargs):
        def answer(question):
            return {"routing": {"matched": next(answers)}}, 0.1

        return run_evals(self.ENTRIES, answer=answer, route=lambda question: ({"route": "catalog"}, 0.01), repeats=3,
                         max_usd=Decimal(max_usd), reserve_usd=Decimal("0.1"), **kwargs)

    def test_an_unapproved_entry_is_skipped_and_counted(self):
        summary = self.run_evals(iter([["d1"]] * 3))
        self.assertEqual(summary["skipped_unapproved"], 1)
        self.assertNotIn("draft", summary["evals"])

    def test_a_seed_eval_passes_only_when_all_runs_pass(self):
        summary = self.run_evals(iter([["d1"], ["d1"], ["d1", "d2"]]))
        self.assertEqual((summary["evals"]["latest"]["passes"], summary["evals"]["latest"]["all_runs_pass"]), (2, False))
        self.assertEqual(summary["evals"]["route-1"]["all_runs_pass"], True)
        self.assertTrue(self.run_evals(iter([["d1"]] * 3))["evals"]["latest"]["all_runs_pass"])

    def test_the_ceiling_stops_the_run_before_the_next_call_and_marks_it_incomplete(self):
        summary = self.run_evals(iter([["d1"]] * 3), max_usd="0.25")
        self.assertFalse(summary["complete"])
        self.assertLessEqual(Decimal(summary["spent_usd"]), Decimal("0.25"))
        self.assertFalse(summary["evals"]["latest"]["all_runs_pass"])

    def test_a_failing_call_is_a_failed_run(self):
        def broken(question):
            raise RuntimeError("boom")

        summary = run_evals(self.ENTRIES[:1], answer=broken, route=broken, repeats=1, max_usd=Decimal("1"),
                            reserve_usd=Decimal("0.1"))
        self.assertEqual(summary["evals"]["route-1"]["passes"], 0)
        self.assertIn("RuntimeError", summary["evals"]["route-1"]["details"][0])

    def test_a_failure_is_charged_what_the_call_cost_and_a_failed_check_keeps_the_billed_answer(self):
        class Billed(Exception):
            cost_usd = 0.4

        def failed_answer(question):
            raise Billed()

        entry = [{"id": "a", "question": "Q", "approved": True,
                  "check": {"kind": "attribution", "document": "ep", "person": "Tim Ferriss"}}]
        summary = run_evals(entry, answer=failed_answer, route=failed_answer, repeats=1, max_usd=Decimal("5"),
                            reserve_usd=Decimal("0.1"))
        self.assertEqual(Decimal(summary["spent_usd"]), Decimal("0.4"))

        def billed_then_broken_check(question):
            return {"answer": "x [E1.0]", "citations": {"E1.0": object()}}, 0.3  # object() breaks the check after billing

        summary = run_evals(entry, answer=billed_then_broken_check, route=failed_answer, repeats=1, max_usd=Decimal("5"),
                            reserve_usd=Decimal("0.1"))
        self.assertEqual((summary["evals"]["a"]["passes"], Decimal(summary["spent_usd"])), (0, Decimal("0.3")))

    def test_compare_lists_every_eval_once_with_both_pass_rates(self):
        first = self.run_evals(iter([["d1"]] * 3))
        second = self.run_evals(iter([["d1"], ["x"], ["x"]]))
        second["evals"]["extra"] = {"passes": 1, "runs": 3}
        lines = compare(first, second, ("v13", "v15"))
        self.assertEqual([line.split()[0] for line in lines], ["eval", "route-1", "latest", "extra"])
        self.assertIn("3/3", lines[2])
        self.assertIn("1/3", lines[2])
        self.assertTrue(lines[3].rstrip().endswith("1/3"))


if __name__ == "__main__":
    unittest.main()
