import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.enrichment import (
    extract_evidence_cards,
    generate_chunk_context,
)
from app.ingestion.router import route_source
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import capture_source
from app.models.client import GenerationResult, ModelClientError, ModelSpec, ModelUsage
from app.models.openrouter import OpenRouterClient


class FakeClient:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return GenerationResult(
            data=self.data,
            model=kwargs["model"],
            usage=ModelUsage(input_tokens=40, output_tokens=12, total_tokens=52),
        )

    def embed(self, **kwargs):
        raise AssertionError("unexpected embedding call")


class EnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"
        self.model = ModelSpec(
            provider="openrouter", model_id="example/open-model", model_version="v1"
        )

    def setup_source(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        capture = capture_source(self.root, relative, self.runtime)
        document = route_source(capture)
        chunks = structural_raw_chunks(document)
        return capture, document, chunks, RunManifest.for_document(document)

    def test_context_uses_bounded_source_and_is_versioned(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "## Thoughts\n\nA source passage.\n"
        )
        client = FakeClient({"context": "This note records a short thought."})
        first = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, max_input_tokens=200,
        )
        second = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, max_input_tokens=200,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("A source passage.", client.calls[0]["input_text"])
        self.assertLess(len(client.calls[0]["input_text"]), 500)
        self.assertEqual(first.source_spans, chunks.chunks[0].source_spans)
        self.assertEqual(next(iter(manifest.artifacts.values())).status, "ready")
        self.assertEqual(RunManifest.load(capture.run_dir), manifest)

    def test_context_version_includes_effective_neighbor_excerpt(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "A source passage.\n"
        )
        client = FakeClient({"context": "A short context."})
        first = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, neighbor_text="first neighbor",
        )
        second = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, neighbor_text="second neighbor",
        )
        again = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, neighbor_text="second neighbor",
        )
        self.assertEqual(second, again)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(manifest.artifacts), 2)
        self.assertNotEqual(
            list(manifest.artifacts.values())[0].version_inputs["neighbor_sha256"],
            list(manifest.artifacts.values())[1].version_inputs["neighbor_sha256"],
        )
        self.assertEqual(first.source_spans, second.source_spans)

    def test_context_prompt_revision_creates_a_separate_artifact(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "## Thoughts\n\nA source passage.\n"
        )
        client = FakeClient({"context": "This passage records a thought."})
        old = generate_chunk_context(document, chunks, chunks.chunks[0], client,
                                     self.model, manifest, capture.run_dir)
        revised = generate_chunk_context(document, chunks, chunks.chunks[0], client,
                                         self.model, manifest, capture.run_dir,
                                         prompt_version="chunk_context_v2")
        self.assertEqual(old.prompt_version, "chunk_context_v1")
        self.assertEqual(revised.prompt_version, "chunk_context_v2")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(manifest.artifacts), 2)
        self.assertIn("at most 60 words", client.calls[1]["system_prompt"])

    def test_live_client_requires_matching_manifest_before_model_request(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "A source passage.\n"
        )
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=2,
            estimated_input_token_budget=200,
        )
        with patch.object(client, "generate_json", return_value=GenerationResult(
            data={"context": "A context."}, model=self.model,
        )) as generate:
            with self.assertRaisesRegex(ModelClientError, "do not match"):
                generate_chunk_context(
                    document, chunks, chunks.chunks[0], client, self.model,
                    manifest, capture.run_dir,
                )
            with self.assertRaisesRegex(ModelClientError, "do not match"):
                extract_evidence_cards(
                    document, chunks, chunks.chunks[0], client, self.model,
                    manifest, capture.run_dir,
                )
            generate.assert_not_called()
            wrong_budget = RunManifest.for_document(
                document, live_call_selected=True, request_budget=1,
                estimated_input_token_budget=200,
            )
            with self.assertRaisesRegex(ModelClientError, "do not match"):
                generate_chunk_context(
                    document, chunks, chunks.chunks[0], client, self.model,
                    wrong_budget, capture.run_dir,
                )
            matching = RunManifest.for_document(
                document, live_call_selected=True, request_budget=2,
                estimated_input_token_budget=200,
            )
            generate_chunk_context(
                document, chunks, chunks.chunks[0], client, self.model,
                matching, capture.run_dir,
            )
            generate.assert_called_once()
            saved = RunManifest.load(capture.run_dir)
            self.assertTrue(saved.live_call_selected)
            self.assertEqual(saved.request_budget, 2)
            self.assertEqual(saved.estimated_input_token_budget, 200)

    def test_exact_card_and_nullable_fields(self) -> None:
        text = "**Host:** We called ten customers first.\n"
        capture, document, chunks, manifest = self.setup_source("Transcripts/episode.md", text)
        quote = "called ten customers"
        start = document.source_text.index(quote)
        client = FakeClient({"cards": [{
            "card_type": "tactic", "statement": "Call customers early.",
            "situation": None, "action": None, "result": None,
            "principle": "Seek firsthand feedback.",
            "evidence_start_char": start,
            "evidence_end_char": start + len(quote), "evidence_text": quote,
        }]})
        artifact = extract_evidence_cards(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir,
        )
        card = artifact.cards[0]
        self.assertIsNone(card.situation)
        self.assertIsNone(card.action)
        self.assertIsNone(card.result)
        self.assertTrue(card.principle_is_derived)
        self.assertEqual(card.attribution, "Host")
        card.validate_against(document, chunks.chunks[0])

    def test_quote_location_v2_requires_one_exact_source_occurrence(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "A real sentence supports this tactic.\n"
        )
        card = {"card_type": "tactic", "statement": "Use the stated tactic.",
                "situation": None, "action": None, "result": None,
                "principle": None, "evidence_text": "real sentence supports"}
        artifact = extract_evidence_cards(
            document, chunks, chunks.chunks[0], FakeClient({"cards": [card]}),
            self.model, manifest, capture.run_dir,
            prompt_version="evidence_cards_v2",
        )
        self.assertEqual(artifact.extraction_version, "quote-locate-v2")
        self.assertEqual(artifact.cards[0].evidence_span.start_char,
                         document.source_text.index(card["evidence_text"]))
        artifact.cards[0].validate_against(document, chunks.chunks[0])

        capture2, document2, chunks2, manifest2 = self.setup_source(
            "Notes/repeated.md", "Same phrase. Same phrase.\n"
        )
        with self.assertRaisesRegex(ValueError, "exactly once"):
            extract_evidence_cards(
                document2, chunks2, chunks2.chunks[0],
                FakeClient({"cards": [{**card, "evidence_text": "Same phrase"}]}),
                self.model, manifest2, capture2.run_dir,
                prompt_version="evidence_cards_v2",
            )

    def test_block_location_v3_uses_exact_source_span(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/block.md", "A real sentence supports this tactic.\n"
        )
        block = next(block for block in document.blocks
                     if block.index in chunks.chunks[0].block_indices)
        card = {"card_type": "tactic", "statement": "Use the stated tactic.",
                "situation": None, "action": None, "result": None,
                "principle": None, "evidence_block_index": block.index}
        artifact = extract_evidence_cards(
            document, chunks, chunks.chunks[0], FakeClient({"cards": [card]}),
            self.model, manifest, capture.run_dir,
            prompt_version="evidence_cards_v3",
        )
        self.assertEqual(artifact.extraction_version, "block-locate-v3")
        artifact.cards[0].validate_against(document, chunks.chunks[0])
        self.assertEqual(artifact.cards[0].evidence_text,
                         document.source_text[block.span.start_char:block.span.end_char])

        bad_capture, bad_document, bad_chunks, bad_manifest = self.setup_source(
            "Notes/bad-block.md", "A real sentence supports this tactic.\n"
        )
        with self.assertRaisesRegex(ValueError, "invalid source block"):
            extract_evidence_cards(
                bad_document, bad_chunks, bad_chunks.chunks[0],
                FakeClient({"cards": [{**card, "evidence_block_index": 9999}]}),
                self.model, bad_manifest, bad_capture.run_dir,
                prompt_version="evidence_cards_v3",
            )

    def test_v4_rejects_a_short_source_fragment(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/fragments.md",
            "Belly crash.\n\nA complete source block explains that a practical choice requires direct observation and a deliberate response.\n",
        )
        short = next(block for block in document.blocks
                     if block.raw_text.strip() == "Belly crash.")
        card = {"card_type": "tactic", "statement": "Observe before acting.",
                "situation": None, "action": None, "result": None,
                "principle": None, "evidence_block_index": short.index}
        with self.assertRaisesRegex(ValueError, "source fragment"):
            extract_evidence_cards(
                document, chunks, chunks.chunks[0], FakeClient({"cards": [card]}),
                self.model, manifest, capture.run_dir,
                prompt_version="evidence_cards_v4",
            )

    def test_v5_through_v8_group_adjacent_turns_from_one_speaker(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Transcripts/grouped.md",
            "**Host:** A useful observation begins here, and it concerns a decision that needs more context.\n\n"
            "**Host:** The decision should be made after checking the relevant customer evidence.\n\n"
            "**Guest:** I agree with the approach, but my example is a separate observation.\n",
        )
        card = {"card_type": "tactic", "statement": "Check customer evidence before deciding.",
                "situation": None, "action": None, "result": None,
                "principle": None, "evidence_group_index": 0}
        for prompt_version, output_bound in (("evidence_cards_v5", 700),
                                             ("evidence_cards_v6", 1400),
                                             ("evidence_cards_v7", 1400),
                                             ("evidence_cards_v8", 1400)):
            with self.subTest(prompt_version=prompt_version):
                client = FakeClient({"cards": [card]})
                artifact = extract_evidence_cards(
                    document, chunks, chunks.chunks[0], client,
                    self.model, manifest, capture.run_dir,
                    prompt_version=prompt_version,
                )
                self.assertEqual(client.calls[0]["max_output_tokens"], output_bound)
                if prompt_version in ("evidence_cards_v7", "evidence_cards_v8"):
                    self.assertIn("Valid evidence_group_index values (zero-based):",
                                  client.calls[0]["input_text"])
                self.assertIn("A useful observation", artifact.cards[0].evidence_text)
                self.assertIn("checking the relevant customer evidence", artifact.cards[0].evidence_text)
                self.assertNotIn("Guest", artifact.cards[0].evidence_text)
                self.assertEqual(artifact.cards[0].attribution, "Host")
                artifact.cards[0].validate_against(document, chunks.chunks[0])

    def test_v8_preserves_supported_fields_and_separate_facts(self) -> None:
        text = ("**Host:** When signups fell, I interviewed five customers and changed the form. "
                "Signups recovered the next week. Separately, I asked our support team to "
                "try a shorter reply template; no outcome was reported for that advice.\n")
        capture, document, chunks, manifest = self.setup_source("Transcripts/fields.md", text)
        client = FakeClient({"cards": [
            {"card_type": "example", "statement": "The speaker revised a form after signups fell.",
             "situation": "Signups fell", "action": "The speaker interviewed five customers and changed the form",
             "result": "The speaker reports signups recovered the next week",
             "principle": None, "evidence_group_index": 0},
            {"card_type": "tactic", "statement": "The speaker advised a shorter support reply template.",
             "situation": "Support reply template", "action": "The speaker advised trying a shorter template",
             "result": None, "principle": None, "evidence_group_index": 0},
        ]})
        artifact = extract_evidence_cards(document, chunks, chunks.chunks[0], client,
                                          self.model, manifest, capture.run_dir,
                                          prompt_version="evidence_cards_v8")
        self.assertEqual(len(artifact.cards), 2)
        self.assertEqual(artifact.cards[0].evidence_span, artifact.cards[1].evidence_span)
        self.assertNotEqual(artifact.cards[0].card_id, artifact.cards[1].card_id)
        self.assertIsNone(artifact.cards[1].result)
        self.assertIn("Observed or reported outcome", client.calls[0]["json_schema"]
                      ["properties"]["cards"]["items"]["properties"]["result"]["description"])

    def test_personal_annotation_is_not_attributed_to_book_author(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Book Highlights/book.md",
            Path("sources/fixtures/sample_highlights.md").read_text(encoding="utf-8"),
        )
        quote = "I want to revisit this."
        start = document.source_text.index(quote)
        target = next(
            chunk for chunk in chunks.chunks
            if any(span.start_char <= start < span.end_char for span in chunk.source_spans)
        )
        client = FakeClient({"cards": [{
            "card_type": "lesson", "statement": "Revisit the passage.",
            "evidence_start_char": start,
            "evidence_end_char": start + len(quote), "evidence_text": quote,
        }]})
        artifact = extract_evidence_cards(
            document, chunks, target, client, self.model, manifest, capture.run_dir,
        )
        self.assertEqual(artifact.cards[0].content_role, "personal_note")
        self.assertEqual(artifact.cards[0].attribution, "personal_note")

    def test_new_group_recipe_keeps_adjacent_highlights_separate(self) -> None:
        from app.ingestion.enrichment import _card_groups

        text = ("---\ntitle: A small book\nauthor: Example Author\nsource: Kindle\n---\n\n"
                "# A small book\n\n## A useful idea\n\n"
                "**Highlight · p. 12 · aqua **\n\n> The first independent passage describes a concrete action and its outcome in enough detail to be useful.\n\n"
                "**Highlight · p. 13 · aqua **\n\n> The second independent passage describes another episode and must not inherit the first outcome, attribution, or action when a new card is produced.\n")
        capture, document, chunks, manifest = self.setup_source("Book Highlights/adjacent.md", text)
        old = _card_groups(document, chunks.chunks[0])
        revised = _card_groups(document, chunks.chunks[0], split_highlights=True)
        self.assertEqual(len(old), 1)
        self.assertEqual(len(revised), 2)

    def test_invented_evidence_and_model_failure_leave_no_ready_artifact(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "A real sentence.\n"
        )
        invented = FakeClient({"cards": [{
            "card_type": "claim", "statement": "Unsupported.",
            "evidence_start_char": 0, "evidence_end_char": 6,
            "evidence_text": "made up",
        }]})
        with self.assertRaisesRegex(ValueError, "exact source text"):
            extract_evidence_cards(
                document, chunks, chunks.chunks[0], invented, self.model,
                manifest, capture.run_dir,
            )
        self.assertTrue(all(record.status == "failed" for record in manifest.artifacts.values()))
        self.assertEqual(list((capture.run_dir / "artifacts").rglob("*.json")) if (capture.run_dir / "artifacts").exists() else [], [])
        failing = FakeClient(error=ModelClientError("timeout"))
        with self.assertRaises(ModelClientError):
            generate_chunk_context(
                document, chunks, chunks.chunks[0], failing, self.model,
                manifest, capture.run_dir,
            )
        self.assertTrue(all(record.status == "failed" for record in manifest.artifacts.values()))


if __name__ == "__main__":
    unittest.main()
