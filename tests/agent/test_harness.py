from __future__ import annotations

import unittest

from app.agent.harness import answer_question as _answer_question
from app.agent.records import AnswerLimits
from app.models.client import GenerationResult, ModelSpec
from app.storage import StorageError
from app.storage.records import FusedHit, SearchHit, SourcePassage
from tests.agent.test_actions import action, action_v2


MODEL = ModelSpec(provider="fake", model_id="fake", model_version="1")


def answer_question(*args, **kwargs):
    return _answer_question(*args, allow_legacy_actions=True, **kwargs)


class FakeClient:
    def __init__(self, actions):
        self.actions = iter(actions)

    def generate_json(self, **kwargs):
        return GenerationResult(data=next(self.actions), model=MODEL)


class FakeRetriever:
    def search(self, query):
        item = SearchHit(rank=1, score=0, document_id="doc", run_id="run",
                         source_sha256="a" * 64, root_alias="root", relative_path="note.md",
                         chunk_id="chunk", source_spans=[{"start_char": 0, "end_char": 13}],
                         raw_text="actual source")
        return [FusedHit(rank=1, rrf_score=1 / 61, channels=["text"], hit=item)]

    def search_cards(self, query):
        return []

    def read(self, reference, span_index):
        if span_index != 0:
            raise IndexError
        return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                             root_alias="root", relative_path="note.md", chunk_id="chunk",
                             start_char=0, end_char=13, text="actual source")


class HarnessTest(unittest.TestCase):
    def test_new_run_rejects_legacy_action_version(self):
        client = FakeClient([action('search', query='source'),
                             action_v2('finish', answer='No verified source.',
                                       completeness='unsupported', sufficient=False,
                                       gaps=['No source was inspected'])])
        result = _answer_question('Explain', retriever=FakeRetriever(), client=client,
                                  model=MODEL, limits=AnswerLimits(max_model_calls=2))
        self.assertEqual(result.actions[0], {'action': 'invalid', 'reason': 'ValueError'})
        self.assertFalse(result.inspected)

    def test_find_registers_same_document_lead_then_read_supports_answer(self):
        class FindingRetriever(FakeRetriever):
            def find(self, reference, query):
                self.find_query = query
                later = reference.hit.model_copy(update={"chunk_id": "later", "raw_text": "later source",
                    "source_spans": [{"start_char": 20, "end_char": 32}]})
                return [FusedHit(rank=1, rrf_score=0.5, channels=["document_find"], hit=later)]

            def read(self, reference, span_index, *, offset=0):
                if reference.hit.chunk_id == "later":
                    return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                        root_alias="root", relative_path="note.md", chunk_id="later",
                        start_char=20, end_char=32, text="later source")
                return super().read(reference, span_index)

        retriever = FindingRetriever()
        needs = [{"id": "N1", "description": "Explain the later source", "status": "resolved",
                  "evidence_ids": ["E2.0"], "rationale": None}]
        client = FakeClient([action_v2("search", query="source"),
            action_v2("find", reference_id="E1", query="later detail"),
            action_v2("read", reference_id="E2", span_index=0),
            action_v2("finish", answer="Later fact [E2.0]", citation_ids=["E2.0"],
                      completeness="complete", sufficient=True, need_updates=needs)])
        result = answer_question("Explain the later source", retriever=retriever, client=client, model=MODEL)
        self.assertEqual(retriever.find_query, "later detail")
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.citations["E2.0"].chunk_id, "later")

    def test_unresolved_need_gets_one_targeted_finish_correction(self):
        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.last_input = kwargs["input_text"]
                return super().generate_json(**kwargs)

        resolved = [{"id": "N1", "description": "Explain", "status": "resolved",
                     "evidence_ids": ["E1.0"], "rationale": None}]
        client = CapturingClient([action_v2("search", query="source"),
            action_v2("read", reference_id="E1", span_index=0),
            action_v2("finish", answer="Too early [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True),
            action_v2("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True, need_updates=resolved)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.actions[2]["rejected"], "Material investigation needs remain open")
        self.assertIn("still-open material needs", client.last_input)

    def test_continuation_has_distinct_citation_and_exact_offset(self):
        class LongRetriever(FakeRetriever):
            def search(self, query):
                item = super().search(query)[0]
                return [item.model_copy(update={"hit": item.hit.model_copy(update={
                    "source_spans": [{"start_char": 0, "end_char": 5000}], "raw_text": "x" * 5000})})]

            def read(self, reference, span_index, *, offset=0):
                self.offsets.append(offset)
                return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                    root_alias="root", relative_path="note.md", chunk_id="chunk",
                    start_char=offset, end_char=offset + 2400, text="x" * 2400)

        retriever = LongRetriever()
        retriever.offsets = []
        resolved = [{"id": "N1", "description": "Later detail", "status": "resolved",
                     "evidence_ids": ["E1.0@2400"], "rationale": None}]
        client = FakeClient([action_v2("search", query="detail"),
            action_v2("read", reference_id="E1", span_index=0),
            action_v2("read", reference_id="E1", span_index=0, offset=100),
            action_v2("read", reference_id="E1", span_index=0, offset=2400),
            action_v2("finish", answer="Later detail [E1.0@2400]", citation_ids=["E1.0@2400"],
                      completeness="complete", sufficient=True, need_updates=resolved)])
        result = answer_question("Later detail", retriever=retriever, client=client, model=MODEL)
        self.assertEqual(retriever.offsets, [0, 2400])
        self.assertEqual(result.actions[2]['rejected'], 'Already inspected offset')
        self.assertEqual(result.inspected["E1.0"].next_offset, 2400)
        self.assertEqual(result.citations["E1.0@2400"].start_char, 2400)

    def test_later_malformed_json_can_be_repaired_without_losing_reads(self):
        from app.models.client import ModelClientError
        class RepairClient(FakeClient):
            calls = 0
            def generate_json(self, **kwargs):
                self.calls += 1
                self.last_input = kwargs['input_text']
                if self.calls == 3:
                    raise ModelClientError('OpenRouter returned malformed structured output (finish=stop)')
                return super().generate_json(**kwargs)
        client = RepairClient([action('search', query='source'),
            action('read', reference_id='E1', span_index=0),
            action('finish', answer='Verified [E1.0]', citation_ids=['E1.0'],
                   completeness='complete', sufficient=True)])
        result = answer_question('Explain', retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.termination, 'completed')
        self.assertEqual(result.actions[2], {'action':'invalid','reason':'malformed_output'})
        self.assertIn('no prose or Markdown fences', client.last_input)
        self.assertEqual(result.citations['E1.0'].text, 'actual source')

    def test_input_allowance_reserves_finish_and_keeps_inspected_originals(self):
        class BudgetClient(FakeClient):
            calls = 0
            @property
            def remaining_input_bytes(self):
                return 1000000 if self.calls < 2 else 10000
            def generate_json(self, **kwargs):
                self.last_input = kwargs['input_text']
                self.last_schema = kwargs['json_schema']
                self.calls += 1
                return super().generate_json(**kwargs)
        client = BudgetClient([action('search', query='source'),
            action('read', reference_id='E1', span_index=0),
            action('finish', answer='Supported finding [E1.0]', citation_ids=['E1.0'],
                   completeness='partial', sufficient=False, gaps=['Further detail unavailable within allowance'])])
        result = answer_question('Explain', retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(client.last_schema['properties']['action']['enum'], ['finish'])
        self.assertIn('actual source', client.last_input)
        self.assertIn('"candidates":[]', client.last_input)
        self.assertTrue(result.actions[-1]['budget_finalization'])
        self.assertEqual(result.termination, 'completed')

    def test_many_candidate_previews_fit_context_without_duplicate_raw_snippets(self):
        class ManyCandidates(FakeRetriever):
            def search(self, query):
                sample = super().search(query)[0]
                return [sample.model_copy(update={"hit": sample.hit.model_copy(update={
                    "chunk_id": str(index), "raw_text": "x" * 2400,
                    "source_spans": [{"start_char": offset, "end_char": offset + 200}
                                     for offset in range(0, 2400, 200)]})}) for index in range(20)]

        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.last_input = kwargs["input_text"]
                return super().generate_json(**kwargs)

        client = CapturingClient([action("search", query="source"),
            action("finish", answer="Not yet verified.", completeness="unsupported",
                   sufficient=False, gaps=["Need original reads"] )])
        answer_question("Explain", retriever=ManyCandidates(), client=client, model=MODEL)
        self.assertLess(len(client.last_input.encode("utf-8")), 26000)
        self.assertNotIn("snippet", client.last_input)

    def test_malformed_action_counts_one_model_call(self):
        client = FakeClient([action("delete"), action("finish", answer="No source support.",
                            completeness="unsupported", sufficient=False, gaps=["No evidence found"] )])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client,
                                 model=MODEL, limits=AnswerLimits(max_model_calls=2))
        self.assertEqual(result.answer, "No source support.")
        self.assertEqual(result.model_calls, 2)

    def test_source_read_cannot_change_candidate_revision(self):
        class WrongRevision(FakeRetriever):
            def read(self, reference, span_index):
                return super().read(reference, span_index).model_copy(update={"run_id": "foreign-run"})

        client = FakeClient([action("search", query="source"), action("read", reference_id="E1", span_index=0),
                             action("finish", answer="Claim [E1.0]", citation_ids=["E1.0"],
                                    completeness="complete", sufficient=True)])
        result = answer_question("Explain", retriever=WrongRevision(), client=client,
                                 model=MODEL, limits=AnswerLimits(max_model_calls=3))
        self.assertFalse(result.citations)

    def test_rejected_finish_retains_answer_and_explains_inline_citation_repair(self):
        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.last_input = kwargs["input_text"]
                return super().generate_json(**kwargs)
        client = CapturingClient([action("search", query="source"),
            action("read", reference_id="E1", span_index=0),
            action("finish", answer="Claim without brackets", citation_ids=["E1.0"],
                   completeness="complete", sufficient=True),
            action("finish", answer="Claim [E1.0]", citation_ids=["E1.0"],
                   completeness="complete", sufficient=True)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.actions[2]["rejected_answer"], "Claim without brackets")
        self.assertIn("IDs found in answer brackets", client.last_input)
        self.assertEqual(result.termination, "completed")

    def test_search_read_answer(self):
        client = FakeClient([action("search", query="source"),
                             action("read", reference_id="E1", span_index=0),
                             action("finish", answer="Explanation [E1.0]", citation_ids=["E1.0"],
                                    completeness="complete", sufficient=True)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.citations["E1.0"].text, "actual source")

    def test_unread_citation_rejected_and_budget_ends(self):
        client = FakeClient([action("search", query="source"),
                             action("finish", answer="False [E1.0]", citation_ids=["E1.0"],
                                    completeness="complete", sufficient=True)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client,
                                 model=MODEL, limits=AnswerLimits(max_model_calls=2))
        self.assertEqual(result.completeness, "unsupported")
        self.assertFalse(result.citations)

    def test_ledgers_are_fresh_for_consecutive_answers(self):
        retriever = FakeRetriever()
        first = answer_question("One", retriever=retriever,
                                client=FakeClient([action("search", query="source"),
                                                   action("read", reference_id="E1", span_index=0),
                                                   action("finish", answer="One [E1.0]", citation_ids=["E1.0"],
                                                          completeness="complete", sufficient=True)]), model=MODEL)
        second = answer_question("Two", retriever=retriever,
                                 client=FakeClient([action("finish", answer="Two [E1.0]", citation_ids=["E1.0"],
                                                        completeness="complete", sufficient=True)]),
                                 model=MODEL, limits=AnswerLimits(max_model_calls=1))
        self.assertTrue(first.citations)
        self.assertFalse(second.citations)

    def test_failed_tool_keeps_prior_reads_and_records_stop_reason(self):
        class FailingRetriever(FakeRetriever):
            def search_cards(self, query):
                raise StorageError("private passage must not enter error")
        client = FakeClient([action("search", query="source"),
                             action("read", reference_id="E1", span_index=0),
                             action("search_cards", query="another angle")])
        result = answer_question("Explain", retriever=FailingRetriever(), client=client, model=MODEL)
        self.assertEqual(result.termination, "tool_failed")
        self.assertEqual(result.inspected["E1.0"].text, "actual source")
        self.assertNotIn("private passage", str(result.actions))

    def test_trace_records_ranks_and_sufficiency(self):
        client = FakeClient([action("search", query="source"), action("read", reference_id="E1", span_index=0),
                             action("finish", answer="Explanation [E1.0]", citation_ids=["E1.0"],
                                    completeness="complete", sufficient=True)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.actions[0]["results"][0]["rank"], 1)
        self.assertTrue(result.actions[-1]["sufficient"])
        self.assertEqual(result.inspected, result.citations)

    def test_candidate_span_previews_show_later_passages_without_marking_them_read(self):
        from app.agent.harness import span_hints
        from app.storage.records import SearchHit
        hit = SearchHit(rank=1, score=0, document_id="d", run_id="r", source_sha256="a"*64,
            root_alias="root", relative_path="note.md", chunk_id="c", raw_text="Overview.Detailed step.",
            source_spans=[{"start_char":0,"end_char":9}, {"start_char":12,"end_char":26}])
        hints = span_hints(hit)
        self.assertEqual(hints[1]["preview"], "Detailed step.")
        self.assertEqual(hints[1]["index"], 1)
