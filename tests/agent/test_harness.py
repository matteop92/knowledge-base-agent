from __future__ import annotations

import unittest

from app.agent.harness import answer_question as _answer_question
from app.agent.records import AnswerLimits
from app.models.client import GenerationResult, ModelSpec
from app.storage import StorageError
from app.storage.records import FusedHit, SearchHit, SourcePassage
from tests.agent.test_actions import action, action_v2, action_v3


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

    def read(self, reference, span_index):
        if span_index != 0:
            raise IndexError
        return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                             root_alias="root", relative_path="note.md", chunk_id="chunk",
                             start_char=0, end_char=13, text="actual source")


class HarnessTest(unittest.TestCase):
    def test_complete_finish_with_declared_gap_is_recorded_partial(self):
        resolved = [{"id": "N1", "description": "Explain", "status": "resolved",
                     "evidence_ids": ["E1.0"], "rationale": None}]
        result = answer_question("Explain", retriever=FakeRetriever(), client=FakeClient([
            action_v3("read", reference_id="E1"),
            action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True, gaps=["One detail is unavailable"],
                      need_updates=resolved)]), model=MODEL)
        self.assertEqual(result.termination, "completed")
        self.assertEqual(result.completeness, "partial")
        self.assertEqual(result.gaps, ["One detail is unavailable"])
        self.assertNotIn("rejected", result.actions[-1])

    def test_candidate_shows_title_and_people_without_path(self):
        class LabeledRetriever(FakeRetriever):
            def search(self, query):
                found = super().search(query)[0]
                return [found.model_copy(update={"hit": found.hit.model_copy(update={
                    "title": "Anything You Want", "people": ["Derek Sivers"]})})]

        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.first_input = kwargs["input_text"]
                return super().generate_json(**kwargs)

        import json
        client = CapturingClient([action_v3("finish", answer="No verified source.",
            completeness="unsupported", sufficient=False, gaps=["Need a read"])])
        answer_question("Explain", retriever=LabeledRetriever(), client=client, model=MODEL,
                        limits=AnswerLimits(max_model_calls=1))
        candidate = json.loads(client.first_input)["candidates"][0]
        self.assertEqual(candidate["title"], "Anything You Want")
        self.assertEqual(candidate["people"], ["Derek Sivers"])
        self.assertNotIn("path", candidate)

    def test_nonfinish_draft_answer_does_not_discard_valid_source_read(self):
        draft_read = action_v3("read", reference_id="E1", span_index=0,
                               answer="Unverified draft", citation_ids=["E1.0"],
                               completeness="partial", sufficient=False,
                               gaps=["More to inspect"])
        resolved = [{"id": "N1", "description": "Explain", "status": "resolved",
                     "evidence_ids": ["E1.0"], "rationale": None}]
        client = FakeClient([action_v3("search", query="source"), draft_read,
                             action_v3("finish", answer="Grounded [E1.0]",
                                       citation_ids=["E1.0"], completeness="complete",
                                       sufficient=True, need_updates=resolved)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL,
                                 limits=AnswerLimits(max_model_calls=3))
        self.assertEqual(result.termination, "completed")
        self.assertEqual(result.actions[1]["ignored_draft_fields"],
                         ["answer", "citation_ids", "completeness", "gaps", "sufficient"])
        self.assertEqual(result.citations["E1.0"].text, "actual source")

    def test_two_discovery_actions_require_an_original_read(self):
        class CapturingClient(FakeClient):
            def __init__(self, actions):
                super().__init__(actions)
                self.allowed = []

            def generate_json(self, **kwargs):
                self.allowed.append(kwargs["json_schema"]["properties"]["action"]["enum"])
                return super().generate_json(**kwargs)

        client = CapturingClient([
            action_v3("search", query="first"),
            action_v3("search", query="second"),
            action_v3("read", reference_id="E1", span_index=0),
            action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True,
                      need_updates=[{"id": "N1", "description": "Explain", "status": "resolved",
                                     "evidence_ids": ["E1.0"], "rationale": None}]),
        ])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(client.allowed[2], ["read"])
        self.assertEqual(result.termination, "completed")

    def test_new_run_rejects_legacy_action_version(self):
        client = FakeClient([action_v2('search', query='source'),
                             action_v3('finish', answer='No verified source.',
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
        client = FakeClient([action_v3("search", query="source"),
            action_v3("find", reference_id="E1", query="later detail"),
            action_v3("read", reference_id="E2", span_index=0),
            *[action_v3("finish", answer="Later fact [E2.0]", citation_ids=["E2.0"],
                        completeness="complete", sufficient=True, need_updates=needs)] * 2])
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
        client = CapturingClient([action_v3("search", query="source"),
            action_v3("read", reference_id="E1", span_index=0),
            action_v3("finish", answer="Too early [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True),
            action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True, need_updates=resolved)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(result.actions[2]["rejected"], "Material investigation needs remain open")
        self.assertIn("still-open material needs", client.last_input)

    def test_one_read_inspects_every_span_and_continuation_of_the_chunk(self):
        class ChunkRetriever(FakeRetriever):
            spans = [{"start_char": 0, "end_char": 5000}, {"start_char": 5010, "end_char": 5030}]

            def search(self, query):
                item = super().search(query)[0]
                return [item.model_copy(update={"hit": item.hit.model_copy(update={
                    "source_spans": self.spans, "raw_text": "x" * 5020})})]

            def read(self, reference, span_index, *, offset=0):
                span = self.spans[span_index]
                start = span["start_char"] + offset
                end = min(span["end_char"], start + 2400)
                self.offsets.append((span_index, offset))
                return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                    root_alias="root", relative_path="note.md", chunk_id="chunk",
                    start_char=start, end_char=end, text="x" * (end - start))

        retriever = ChunkRetriever()
        retriever.offsets = []
        resolved = [{"id": "N1", "description": "Later detail", "status": "resolved",
                     "evidence_ids": ["E1.0@2400"], "rationale": None}]
        client = FakeClient([action_v3("read", reference_id="E1"),
            action_v3("read", reference_id="E1.1"),
            action_v3("finish", answer="Later detail [E1.0@2400] and [E1.1]",
                      citation_ids=["E1.0@2400", "E1.1"],
                      completeness="complete", sufficient=True, need_updates=resolved)])
        result = answer_question("Later detail", retriever=retriever, client=client, model=MODEL)
        self.assertEqual(retriever.offsets, [(0, 0), (0, 2400), (0, 4800), (1, 0)])
        self.assertEqual(list(result.inspected), ["E1.0", "E1.0@2400", "E1.0@4800", "E1.1"])
        self.assertEqual(result.actions[1]["rejected"], "Already inspected chunk")
        self.assertEqual(result.inspected["E1.0"].next_offset, 2400)
        self.assertEqual(result.citations["E1.0@2400"].start_char, 2400)
        self.assertEqual(result.host_search["query"], "Later detail")

    def test_candidates_keep_best_rank_without_facts(self):
        class Ranked(FakeRetriever):
            def search(self, query):
                sample = super().search(query)[0]
                order = range(20) if query == "Explain" else [19, *range(20, 39)]
                return [sample.model_copy(update={"rank": rank, "hit": sample.hit.model_copy(update={
                    "chunk_id": str(index), "raw_text": f"chunk {index} text"})})
                    for rank, index in enumerate(order, 1)]

        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.last_input = kwargs["input_text"]
                return super().generate_json(**kwargs)

        import json
        client = CapturingClient([action_v3("search", query="narrow"),
            action_v3("finish", answer="Not yet verified.", completeness="unsupported",
                      sufficient=False, gaps=["Need original reads"],
                      need_updates=[{"id": "N1", "description": "Explain", "status": "excluded",
                                     "evidence_ids": [], "rationale": "test"}]),
            action_v3("finish", answer="Not yet verified.", completeness="unsupported",
                      sufficient=False, gaps=["Need original reads"])])
        answer_question("Explain", retriever=Ranked(), client=client, model=MODEL,
                        limits=AnswerLimits(max_model_calls=3))
        shown = json.loads(client.last_input)["candidates"]
        # Top hits of both searches stay visible; the first search's leaders are not pushed out.
        self.assertEqual([item["id"] for item in shown[:3]], ["E1", "E20", "E2"])
        self.assertEqual(len(shown), 20)
        self.assertNotIn("facts", shown[0])

    def test_stray_fields_are_ignored_and_invalid_feedback_names_the_rule(self):
        class CapturingClient(FakeClient):
            def __init__(self, actions):
                super().__init__(actions)
                self.inputs = []

            def generate_json(self, **kwargs):
                self.inputs.append(kwargs["input_text"])
                return super().generate_json(**kwargs)

        resolved = [{"id": "N1", "description": "Explain", "status": "resolved",
                     "evidence_ids": ["E1.0"], "rationale": None}]
        client = CapturingClient([action_v3("read", reference_id="E1.0", query="stray"),
            action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=False, need_updates=resolved),
            action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                      completeness="complete", sufficient=True, need_updates=resolved)])
        result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL)
        self.assertEqual(result.actions[0]["ignored_fields"], ["query"])
        self.assertEqual(result.actions[0]["inspected_ids"], ["E1.0"])
        self.assertEqual(result.actions[1]["action"], "invalid")
        self.assertIn("complete answer requires a positive sufficiency decision", client.inputs[2])
        self.assertEqual(result.termination, "completed")

    def test_finish_is_checked_against_unread_chunks_of_sources_already_used(self):
        class TwoChunks(FakeRetriever):
            def search(self, query):
                first = super().search(query)[0]
                second = first.model_copy(update={"rank": 2, "hit": first.hit.model_copy(update={
                    "chunk_id": "second", "source_spans": [{"start_char": 20, "end_char": 31}],
                    "raw_text": "more source"})})
                other = first.model_copy(update={"rank": 3, "hit": first.hit.model_copy(update={
                    "document_id": "other-doc", "chunk_id": "other"})})
                return [first, second, other]

            def read(self, reference, span_index):
                if reference.hit.chunk_id == "second":
                    return SourcePassage(document_id="doc", run_id="run", source_sha256="a" * 64,
                                         root_alias="root", relative_path="note.md", chunk_id="second",
                                         start_char=20, end_char=31, text="more source")
                return super().read(reference, span_index)

        class CapturingClient(FakeClient):
            def __init__(self, actions):
                super().__init__(actions)
                self.inputs = []

            def generate_json(self, **kwargs):
                self.inputs.append(kwargs["input_text"])
                return super().generate_json(**kwargs)

        resolved = [{"id": "N1", "description": "Explain", "status": "resolved",
                     "evidence_ids": ["E1.0"], "rationale": None}]
        early = action_v3("finish", answer="Grounded [E1.0]", citation_ids=["E1.0"],
                          completeness="complete", sufficient=True, need_updates=resolved)
        client = CapturingClient([action_v3("read", reference_id="E1"), early, early])
        result = answer_question("Explain", retriever=TwoChunks(), client=client, model=MODEL)
        self.assertEqual(result.actions[1]["rejected"], "Unread chunks remain in sources already used")
        self.assertIn("E2", client.inputs[2])
        self.assertNotIn("'E3'", client.inputs[2].split('"feedback"')[1].split('"remaining"')[0])
        # A second finish without new reading is accepted.
        self.assertEqual(result.termination, "completed")
        self.assertEqual(result.model_calls, 3)

        # With the check off, the first finish is accepted.
        client = CapturingClient([action_v3("read", reference_id="E1"), early])
        result = answer_question("Explain", retriever=TwoChunks(), client=client, model=MODEL,
                                 limits=AnswerLimits(coverage_checks=0))
        self.assertNotIn("rejected", result.actions[-1])
        self.assertEqual(result.model_calls, 2)

        # Host reads: the best chunks are read before the first turn, count as reads, and can be cited.
        import json
        both = [{**resolved[0], "evidence_ids": ["E1.0", "E2.0"]}]
        client = CapturingClient([action_v3("finish", answer="Grounded [E1.0] and [E2.0]",
                                            citation_ids=["E1.0", "E2.0"], completeness="complete",
                                            sufficient=True, need_updates=both)])
        result = answer_question("Explain", retriever=TwoChunks(), client=client, model=MODEL,
                                 limits=AnswerLimits(host_reads=2, coverage_checks=0, max_reads=8))
        first = json.loads(client.inputs[0])
        self.assertEqual([item["chunk"] for item in first["inspected"]], ["E1", "E2"])
        self.assertEqual([item["id"] for item in first["candidates"]], ["E3"])
        self.assertEqual(first["remaining"]["reads"], 6)
        self.assertEqual(result.actions[0], {"action": "host_read", "inspected_ids": ["E1.0", "E2.0"]})
        self.assertEqual((result.termination, result.model_calls, set(result.citations)),
                         ("completed", 1, {"E1.0", "E2.0"}))

        # A host read that fails its source checks is skipped; the chunk stays a candidate.
        class BadFirst(TwoChunks):
            def read(self, reference, span_index):
                if reference.hit.chunk_id == "chunk":
                    raise StorageError("passage crosses an excluded source gap")
                return super().read(reference, span_index)

        only_second = [{**resolved[0], "evidence_ids": ["E2.0"]}]
        client = CapturingClient([action_v3("finish", answer="Grounded [E2.0]", citation_ids=["E2.0"],
                                            completeness="complete", sufficient=True, need_updates=only_second)])
        result = answer_question("Explain", retriever=BadFirst(), client=client, model=MODEL,
                                 limits=AnswerLimits(host_reads=2, coverage_checks=0, max_reads=8))
        first = json.loads(client.inputs[0])
        self.assertEqual([item["chunk"] for item in first["inspected"]], ["E2"])
        self.assertIn("E1", [item["id"] for item in first["candidates"]])
        self.assertEqual(result.termination, "completed")

    def test_repeated_query_is_rejected_and_low_ranked_sources_stay_visible(self):
        class TwoSources(FakeRetriever):
            def search(self, query):
                sample = super().search(query)[0]
                self.calls += 1
                return [sample.model_copy(update={"rank": rank, "hit": sample.hit.model_copy(update={
                    "chunk_id": str(rank), "relative_path": "big.md" if rank < 25 else "small.md",
                    "document_id": "big" if rank < 25 else "small"})}) for rank in range(1, 27)]

        class CapturingClient(FakeClient):
            def generate_json(self, **kwargs):
                self.last_input = kwargs["input_text"]
                return super().generate_json(**kwargs)

        import json
        retriever = TwoSources()
        retriever.calls = 0
        done = action_v3("finish", answer="Not verified.", completeness="unsupported",
                         sufficient=False, gaps=["Need original reads"])
        client = CapturingClient([action_v3("search", query="Small  Source"),
                                  action_v3("search", query="small source"), done, done])
        result = answer_question("Explain", retriever=retriever, client=client, model=MODEL,
                                 limits=AnswerLimits(max_model_calls=4))
        self.assertEqual(retriever.calls, 2)  # host search plus one model search
        self.assertEqual(result.actions[1]["rejected"], "Repeated query")
        shown = json.loads(client.last_input)["candidates"]
        self.assertEqual([item["title"] for item in shown].count("small.md"), 2)
        self.assertTrue(all("path" not in item for item in shown))
        self.assertEqual(len(shown), 22)

    def test_unknown_card_action_does_not_call_retriever(self):
        class TrackingRetriever(FakeRetriever):
            calls = 0
            def search(self, query):
                self.calls += 1
                return super().search(query)
        retriever = TrackingRetriever()
        client = FakeClient([action_v3("search_cards", query="lead"),
            action_v3("finish", answer="Not verified.", completeness="unsupported",
                      sufficient=False, gaps=["No inspected evidence"])])
        result = _answer_question("Explain", retriever=retriever, client=client, model=MODEL,
                                  limits=AnswerLimits(max_model_calls=2))
        self.assertEqual(retriever.calls, 1)
        self.assertEqual(result.actions[0]["action"], "invalid")

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

    def test_first_malformed_json_gets_one_format_correction(self):
        from app.models.client import ModelClientError

        class RepairClient(FakeClient):
            calls = 0
            correction_input = None

            def generate_json(self, **kwargs):
                self.calls += 1
                self.last_input = kwargs['input_text']
                if self.calls == 1:
                    raise ModelClientError('OpenRouter returned malformed structured output (finish=stop)')
                if self.calls == 2:
                    self.correction_input = kwargs['input_text']
                return super().generate_json(**kwargs)

        client = RepairClient([action('read', reference_id='E1', span_index=0),
            action('finish', answer='Verified [E1.0]', citation_ids=['E1.0'],
                   completeness='complete', sufficient=True)])
        result = answer_question('Explain', retriever=FakeRetriever(), client=client,
                                 model=MODEL, limits=AnswerLimits(max_model_calls=3))
        self.assertEqual(result.termination, 'completed')
        self.assertEqual(result.model_calls, 3)
        self.assertEqual(result.actions[0], {'action': 'invalid', 'reason': 'malformed_output'})
        self.assertIn('no prose or Markdown fences', client.correction_input)

    def test_a_cut_off_reply_gets_one_retry_asking_for_a_shorter_one(self):
        from app.models.client import ModelClientError

        class CutOnce(FakeClient):
            calls = 0
            retry_input = None

            def generate_json(self, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise ModelClientError('OpenRouter structured output was incomplete (finish=length)')
                if self.calls == 3:
                    self.retry_input = kwargs['input_text']
                return super().generate_json(**kwargs)

        client = CutOnce([action('read', reference_id='E1', span_index=0),
            action('finish', answer='Verified [E1.0]', citation_ids=['E1.0'],
                   completeness='complete', sufficient=True)])
        result = answer_question('Explain', retriever=FakeRetriever(), client=client,
                                 model=MODEL, limits=AnswerLimits(max_model_calls=4))
        self.assertEqual(result.termination, 'completed')
        self.assertEqual(result.model_calls, 3)
        self.assertEqual(result.actions[1], {'action': 'invalid', 'reason': 'completion_budget'})
        self.assertIn('cut off because it was too long', client.retry_input)
        self.assertEqual(result.citations['E1.0'].text, 'actual source')

    def test_a_second_cut_off_reply_stops_after_one_retry(self):
        from app.models.client import ModelClientError

        class AlwaysCut(FakeClient):
            def generate_json(self, **kwargs):
                raise ModelClientError('OpenRouter structured output was incomplete (finish=length)')

        result = answer_question('Explain', retriever=FakeRetriever(), client=AlwaysCut([]),
                                 model=MODEL, limits=AnswerLimits(max_model_calls=5))
        self.assertEqual(result.termination, 'model_failed')
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.gaps, ['Model stopped: completion_budget.'])
        self.assertEqual([a['action'] for a in result.actions], ['invalid', 'model_failed'])

    def test_a_cut_off_reply_is_not_retried_without_a_model_call_left(self):
        from app.models.client import ModelClientError

        class AlwaysCut(FakeClient):
            def generate_json(self, **kwargs):
                raise ModelClientError('OpenRouter structured output was incomplete (finish=length)')

        result = answer_question('Explain', retriever=FakeRetriever(), client=AlwaysCut([]),
                                 model=MODEL, limits=AnswerLimits(max_model_calls=1))
        self.assertEqual(result.termination, 'model_failed')
        self.assertEqual(result.model_calls, 1)
        self.assertEqual([a['action'] for a in result.actions], ['model_failed'])

    def test_a_timed_out_request_stops_without_the_cut_off_retry(self):
        from app.models.client import ModelClientError

        class TimesOut(FakeClient):
            def generate_json(self, **kwargs):
                raise ModelClientError('OpenRouter request failed: TimeoutError')

        result = answer_question('Explain', retriever=FakeRetriever(), client=TimesOut([]),
                                 model=MODEL, limits=AnswerLimits(max_model_calls=5))
        self.assertEqual(result.termination, 'model_failed')
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.gaps, ['Model stopped: model_or_route_failure.'])
        self.assertEqual([a['action'] for a in result.actions], ['model_failed'])

    def test_second_malformed_json_stops_after_one_correction(self):
        from app.models.client import ModelClientError

        class AlwaysMalformed(FakeClient):
            def generate_json(self, **kwargs):
                raise ModelClientError('OpenRouter returned malformed structured output (finish=stop)')

        result = answer_question('Explain', retriever=FakeRetriever(), client=AlwaysMalformed([]),
                                 model=MODEL, limits=AnswerLimits(max_model_calls=3))
        self.assertEqual(result.termination, 'model_failed')
        self.assertEqual(result.model_calls, 2)
        self.assertEqual([a['action'] for a in result.actions], ['invalid', 'model_failed'])

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
            def search(self, query):
                if query == "another angle":
                    raise StorageError("private passage must not enter error")
                return super().search(query)
        client = FakeClient([action("search", query="source"),
                             action("read", reference_id="E1", span_index=0),
                             action("search", query="another angle")])
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


class SpeakerInputTests(unittest.TestCase):
    def inspected_input(self, speaker):
        import json

        class SpeakerRetriever(FakeRetriever):
            def read(self, reference, span_index):
                return super().read(reference, span_index).model_copy(update={"speaker": speaker})

        class CapturingClient(FakeClient):
            def __init__(self, actions):
                super().__init__(actions)
                self.inputs = []

            def generate_json(self, **kwargs):
                self.inputs.append(json.loads(kwargs["input_text"]))
                return super().generate_json(**kwargs)

        client = CapturingClient([action_v3("read", reference_id="E1"),
                                  action_v3("finish", answer="No verified source.", completeness="unsupported",
                                            sufficient=False, gaps=["none"])])
        answer_question("Explain", retriever=SpeakerRetriever(), client=client, model=MODEL,
                        limits=AnswerLimits(max_model_calls=2))
        return client.inputs[-1]["inspected"][0]["passages"][0]

    def test_a_spoken_passage_is_a_labelled_object(self):
        self.assertEqual(self.inspected_input("Niels Hoven"),
                         {"id": "E1.0", "speaker": "Niels Hoven", "text": "actual source"})

    def test_a_passage_with_no_speaker_keeps_the_id_and_text_pair(self):
        self.assertEqual(self.inspected_input(None), ["E1.0", "actual source"])
