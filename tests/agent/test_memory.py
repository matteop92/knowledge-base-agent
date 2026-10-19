from __future__ import annotations

import json
import unittest

from app.agent.harness import answer_question
from app.agent.memory import (MAX_CARRIED_PASSAGES, PriorTurn, build_memory, prior_turn_from_receipt,
                              standalone_query)
from app.agent.records import AnswerLimits
from app.models.client import GenerationResult, ModelClientError, ModelSpec
from app.storage.records import FusedHit, SearchHit, SourcePassage
from tests.agent.test_actions import action_v3

MODEL = ModelSpec(provider="fake", model_id="fake", model_version="1")
REWRITER = ModelSpec(provider="fake", model_id="rewriter", model_version="1")


def passage(doc="doc-1", run="run-1", chunk="c1", text="Hire slowly.", start=0) -> SourcePassage:
    return SourcePassage(document_id=doc, run_id=run, source_sha256="a" * 64, root_alias="primary",
                         relative_path=f"Knowledge/{doc}.md", chunk_id=chunk, start_char=start,
                         end_char=start + len(text), text=text)


def turn(question="Who talks about hiring?", answer="Ferrari [E1.0] and Sivers [E2.0].", passages=None,
         titles=None) -> PriorTurn:
    passages = passages if passages is not None else {
        "E1.0": passage("ferrari", "run-f", "cf", "Hire for attitude."),
        "E2.0": passage("sivers", "run-s", "cs", "Hire only when it hurts.")}
    return PriorTurn(question=question, answer=answer, passages=passages,
                     titles=titles or {"ferrari": "Ferrari interview", "sivers": "Derek Sivers notes"})


class RecordingClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls: list[dict] = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return GenerationResult(data=reply, model=kwargs["model"])


class StandaloneQueryTests(unittest.TestCase):
    def test_follow_up_is_rewritten_with_recent_turns(self):
        memory = build_memory([turn()], active={("ferrari", "run-f"), ("sivers", "run-s")})
        client = RecordingClient([{"query": "What does Derek Sivers say about hiring?"}])
        query = standalone_query("What about the second one?", memory.turns, client=client, model=REWRITER)
        self.assertEqual(query, "What does Derek Sivers say about hiring?")
        sent = json.loads(client.calls[0]["input_text"])
        self.assertEqual(sent["follow_up"], "What about the second one?")
        self.assertEqual(sent["conversation"][0]["question"], "Who talks about hiring?")
        # Old citation markers mean nothing in a new turn, so they are stripped from carried answers.
        self.assertNotIn("[E", sent["conversation"][0]["answer"])
        self.assertEqual(client.calls[0]["model"], REWRITER)

    def test_first_question_passes_through_without_a_call(self):
        client = RecordingClient([])
        self.assertEqual(standalone_query("Who talks about hiring?", [], client=client, model=REWRITER),
                         "Who talks about hiring?")
        self.assertEqual(client.calls, [])

    def test_failed_or_empty_rewrite_falls_back_to_the_follow_up(self):
        memory = build_memory([turn()], active=set())
        for reply in (ModelClientError("boom"), {"query": "   "}):
            with self.subTest(reply=reply):
                client = RecordingClient([reply])
                self.assertEqual(standalone_query("And the second?", memory.turns, client=client, model=REWRITER),
                                 "And the second?")


class BuildMemoryTests(unittest.TestCase):
    def test_passages_are_renumbered_per_chunk_and_keep_their_span_suffix(self):
        passages = {"E3.0": passage(chunk="c3", text="One."), "E3.1@40": passage(chunk="c3", text="Two.", start=40),
                    "E7.0": passage(chunk="c7", text="Three.")}
        memory = build_memory([turn(passages=passages, titles={"doc-1": "Note"})], active={("doc-1", "run-1")})
        self.assertEqual(list(memory.carried), ["E1.0", "E1.1@40", "E2.0"])
        self.assertEqual(memory.carried["E1.1@40"].text, "Two.")
        self.assertEqual(memory.chunk_titles, {"E1": "Note", "E2": "Note"})
        self.assertEqual(memory.chunk_count, 2)

    def test_republished_document_is_dropped_and_named_as_changed(self):
        memory = build_memory([turn()], active={("ferrari", "run-f"), ("sivers", "run-s2")})
        self.assertEqual([item.document_id for item in memory.carried.values()], ["ferrari"])
        self.assertEqual(memory.changed, ["Derek Sivers notes"])

    def test_ledger_over_the_cap_keeps_the_newest_passages(self):
        old = turn(question="Old", passages={f"E{i}.0": passage(chunk=f"old{i}", text="old")
                                             for i in range(1, MAX_CARRIED_PASSAGES + 1)}, titles={"doc-1": "Note"})
        new = turn(question="New", passages={"E1.0": passage(chunk="new", text="new")}, titles={"doc-1": "Note"})
        memory = build_memory([old, new], active={("doc-1", "run-1")})
        self.assertEqual(len(memory.carried), MAX_CARRIED_PASSAGES)
        self.assertEqual(memory.carried["E1.0"].chunk_id, "new")
        self.assertEqual(memory.dropped_over_cap, 1)
        self.assertEqual(memory.record()["dropped_over_cap"], 1)

    def test_the_same_passage_read_in_two_turns_is_carried_once(self):
        same = {"E1.0": passage()}
        memory = build_memory([turn(passages=same), turn(passages=same)], active={("doc-1", "run-1")})
        self.assertEqual(len(memory.carried), 1)

    def test_only_recent_turns_are_resent_as_text(self):
        turns = [turn(question=f"Q{i}") for i in range(6)]
        memory = build_memory(turns, active=set())
        self.assertEqual([item["question"] for item in memory.turns], ["Q3", "Q4", "Q5"])


class ReceiptTests(unittest.TestCase):
    def test_receipt_passages_become_a_prior_turn(self):
        receipt = {"answer": "Ripe [E1.0].", "inspected": {"E1.0": {
            "document_id": "doc-1", "title": "Pear tree", "relative_path": "Knowledge/doc-1.md", "run_id": "run-1",
            "chunk_id": "c1", "source_sha256": "a" * 64, "root_alias": "primary", "start_char": 0, "end_char": 5,
            "text": "Ripe."}}}
        prior = prior_turn_from_receipt("Is it ripe?", receipt)
        self.assertEqual(prior.passages["E1.0"].run_id, "run-1")
        self.assertEqual(prior.titles, {"doc-1": "Pear tree"})

    def test_receipt_without_revision_ids_carries_text_only(self):
        receipt = {"answer": "Ripe.", "inspected": {"E1.0": {"document_id": "doc-1", "relative_path": "x.md",
                                                              "start_char": 0, "end_char": 5, "text": "Ripe."}}}
        prior = prior_turn_from_receipt("Is it ripe?", receipt)
        self.assertEqual((prior.answer, prior.passages), ("Ripe.", {}))


class EmptyRetriever:
    def __init__(self):
        self.queries: list[str] = []

    def search(self, query):
        self.queries.append(query)
        return []

    def read(self, reference, span_index):
        raise AssertionError("no read expected")


class CapturingClient:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.inputs: list[dict] = []
        self.prompts: list[str] = []

    def generate_json(self, **kwargs):
        self.inputs.append(json.loads(kwargs["input_text"]))
        self.prompts.append(kwargs["system_prompt"])
        return GenerationResult(data=next(self.actions), model=MODEL)


class FollowUpHarnessTests(unittest.TestCase):
    def test_follow_up_cites_a_carried_passage_without_reading_again(self):
        memory = build_memory([turn()], active={("ferrari", "run-f"), ("sivers", "run-s")})
        retriever = EmptyRetriever()
        resolved = [{"id": "N1", "description": "second", "status": "resolved", "evidence_ids": ["E2.0"],
                     "rationale": None}]
        client = CapturingClient([action_v3("finish", answer="Sivers hires only when it hurts [E2.0].",
                                            citation_ids=["E2.0"], completeness="complete", sufficient=True,
                                            need_updates=resolved)])
        result = answer_question("What about the second one?", retriever=retriever, client=client, model=MODEL,
                                 memory=memory, search_query="What does Derek Sivers say about hiring?")
        self.assertEqual(result.termination, "completed")
        self.assertEqual(result.citations["E2.0"].document_id, "sivers")
        self.assertEqual(retriever.queries, ["What does Derek Sivers say about hiring?"])
        self.assertEqual(result.memory["search_query"], "What does Derek Sivers say about hiring?")
        sent = client.inputs[0]
        self.assertEqual(sent["question"], "What about the second one?")
        self.assertEqual(sent["conversation"][0]["question"], "Who talks about hiring?")
        self.assertEqual([item["chunk"] for item in sent["earlier_evidence"]], ["E1", "E2"])
        self.assertIn("earlier turns", client.prompts[0])

    def test_dropped_passage_cannot_be_cited_and_the_change_is_shown(self):
        memory = build_memory([turn()], active={("ferrari", "run-f")})
        client = CapturingClient([
            action_v3("finish", answer="Sivers [E2.0].", citation_ids=["E2.0"], completeness="complete",
                      sufficient=True),
            action_v3("finish", answer="No verified source.", completeness="unsupported", sufficient=False,
                      gaps=["The Sivers note changed"])])
        result = answer_question("And Sivers?", retriever=EmptyRetriever(), client=client, model=MODEL,
                                 memory=memory, limits=AnswerLimits(max_model_calls=2))
        self.assertEqual(client.inputs[0]["changed_sources"], ["Derek Sivers notes"])
        self.assertIn("rejected", result.actions[0])
        self.assertNotIn("E2.0", result.inspected)
        self.assertNotIn("E2.0", result.citations)
        self.assertEqual(result.completeness, "unsupported")

    def test_new_candidates_are_numbered_after_carried_chunks(self):
        class OneHit(EmptyRetriever):
            def search(self, query):
                hit = SearchHit(rank=1, score=0, document_id="new", run_id="run-n", source_sha256="b" * 64,
                                root_alias="primary", relative_path="Knowledge/new.md", chunk_id="cn",
                                source_spans=[{"start_char": 0, "end_char": 3}], raw_text="new")
                return [FusedHit(rank=1, rrf_score=1 / 61, channels=["text"], hit=hit)]

        memory = build_memory([turn()], active={("ferrari", "run-f"), ("sivers", "run-s")})
        client = CapturingClient([action_v3("finish", answer="No verified source.", completeness="unsupported",
                                            sufficient=False, gaps=["none"])])
        answer_question("More?", retriever=OneHit(), client=client, model=MODEL, memory=memory,
                        limits=AnswerLimits(max_model_calls=1))
        self.assertEqual([item["id"] for item in client.inputs[0]["candidates"]], ["E3"])

    def test_without_memory_the_v13_prompt_and_input_are_unchanged(self):
        client = CapturingClient([action_v3("finish", answer="No verified source.", completeness="unsupported",
                                            sufficient=False, gaps=["none"])])
        result = answer_question("Explain", retriever=EmptyRetriever(), client=client, model=MODEL,
                                 limits=AnswerLimits(max_model_calls=1))
        from app.agent.harness import PROMPT
        self.assertEqual(client.prompts[0], PROMPT)
        self.assertNotIn("conversation", client.inputs[0])
        self.assertNotIn("earlier_evidence", client.inputs[0])
        self.assertIsNone(result.memory)


if __name__ == "__main__":
    unittest.main()
