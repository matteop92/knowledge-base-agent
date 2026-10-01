from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.ingestion.embeddings import EmbeddingSpec
from app.models.client import EmbeddingResult, ModelClientError
from app.storage import StorageError
from app.storage.records import FusedHit, SearchHit, spec_id
from app.storage.retrieval import DatabaseRetriever, find_in_document, fuse_hits, hybrid_search, read_passage, search_cards, selected_embedding_spec


def hit(chunk: str, start: int, *, rank: int = 1, score: float = 0) -> SearchHit:
    return SearchHit(rank=rank, score=score, document_id="doc", run_id="run",
                     source_sha256="a" * 64, root_alias="root", relative_path="note.md",
                     chunk_id=chunk, source_spans=[{"start_char": start, "end_char": start + 20}],
                     raw_text="source text")


class RetrievalTest(unittest.TestCase):
    def test_card_read_cannot_cross_excluded_source_gap(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ("x" * 30, "root", "note.md",
            [{"start_char": 0, "end_char": 8}, {"start_char": 10, "end_char": 20}], True,
            [{"reason": "unclassified", "span": {"start_char": 8, "end_char": 10}}])
        with patch("app.storage.retrieval._profile", return_value=("profile",)), self.assertRaises(StorageError):
            read_passage(conn, document_id="doc", run_id="run", source_sha256="a" * 64,
                         chunk_id="chunk", start_char=0, end_char=20)

    def test_find_rejects_stale_revision_before_search(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        lead = FusedHit(rank=1, rrf_score=0.1, channels=["text"], hit=hit("lead", 0))
        with patch("app.storage.retrieval._profile", return_value=("profile",)), self.assertRaises(StorageError):
            find_in_document(conn, lead, "detail")
        self.assertEqual(cur.execute.call_count, 1)

    def test_find_filters_exact_published_document_before_limit(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (1,)
        cur.fetchall.return_value = [(0.7, "doc", "run", "a" * 64, "root", "note.md",
                                       "later", [{"start_char": 20, "end_char": 32}], "later source")]
        lead = FusedHit(rank=1, rrf_score=0.1, channels=["text"], hit=hit("lead", 0))
        with patch("app.storage.retrieval._profile", return_value=("profile",)):
            found = find_in_document(conn, lead, "later", k=3)
        self.assertEqual(found[0].hit.chunk_id, "later")
        sql, args = cur.execute.call_args.args
        self.assertIn("r.document_id=%s and r.run_id=%s and r.source_sha256=%s", sql)
        self.assertIn("state.profile_id=p.profile_id", sql)
        self.assertIn("c.variant=p.variant", sql)
        self.assertEqual(args[1:5], ("profile", "doc", "run", "a" * 64))
        self.assertEqual(args[-1], 3)

    def test_continuation_stays_within_selected_span(self):
        sample = FusedHit(rank=1, rrf_score=1 / 61, channels=["text"],
                         hit=hit("long", 100).model_copy(update={"source_spans": [
                             {"start_char": 100, "end_char": 5100}]}))
        retriever = DatabaseRetriever(MagicMock(), MagicMock(), provider_tag="pinned",
                                      max_embedding_usd_per_million=Decimal("1"))
        with patch("app.storage.retrieval.read_passage") as read:
            retriever.read(sample, 0, offset=2400)
            self.assertEqual(read.call_args.kwargs["start_char"], 2500)
            self.assertEqual(read.call_args.kwargs["end_char"], 4900)
            for offset in (-1, 5000, 5001):
                with self.assertRaises((IndexError, ValueError)):
                    retriever.read(sample, 0, offset=offset)
            self.assertEqual(read.call_count, 1)

    def test_card_search_retries_zero_hit_query_with_bounded_or_terms(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [(0.3, "doc", "run", "a" * 64, "root", "note.md",
                                         "card", "chunk", "Relevant statement",
                                         {"start_char": 1, "end_char": 10},
                                         "situation", "action", "result", "principle")]]
        with patch("app.storage.retrieval._profile", return_value=("profile",)):
            found = search_cards(conn, "long unusual principle query", k=3)
        self.assertEqual(found[0].query_mode, "or_fallback")
        self.assertEqual(found[0].situation, "situation")
        self.assertEqual(cur.execute.call_count, 2)
        first_sql, first_args = cur.execute.call_args_list[0].args
        second_sql, second_args = cur.execute.call_args_list[1].args
        self.assertEqual(first_sql, second_sql)
        self.assertEqual(first_args[0], "long unusual principle query")
        self.assertEqual(second_args[0], '"long" OR "unusual" OR "principle" OR "query"')
        self.assertEqual(second_args[1], "profile")
        self.assertEqual(second_args[-1], 3)

    def test_rrf_uses_rank_not_raw_score(self):
        fused = fuse_hits([hit("A", 0, score=999), hit("B", 20, score=-10)],
                          [hit("B", 20, score=0.001), hit("C", 40, score=9999)])
        self.assertEqual([item.hit.chunk_id for item in fused], ["B", "A", "C"])
        self.assertEqual(fused[0].channels, ["vector", "text"])

    def test_empty_channel_and_duplicate_source_span(self):
        fused = fuse_hits([hit("A", 0), hit("overlap", 0)], [])
        self.assertEqual([item.hit.chunk_id for item in fused], ["A"])

    def test_cap(self):
        with self.assertRaises(StorageError):
            fuse_hits([], [], k=21)

    def test_spec_payload_must_match_its_identity(self):
        spec = EmbeddingSpec(provider="fixture", model="original", model_version="1",
                             dimensions=3, distance_metric="cosine",
                             input_recipe_version="raw-v1", chunking_version="v1")
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            spec.model_copy(update={"model": "different"}).model_dump(mode="json"),)
        with patch("app.storage.retrieval._profile", return_value=(
            "profile", "fixed_raw", "v1", spec_id(spec), "simple", 3, "cosine"
        )), self.assertRaises(StorageError):
            selected_embedding_spec(conn)

    def test_privacy_and_price_fail_before_embedding(self):
        spec = EmbeddingSpec(provider="fixture", model="synthetic", model_version="1",
                             dimensions=3, distance_metric="cosine",
                             input_recipe_version="raw-v1", chunking_version="v1",
                             query_input_type="search_query")
        private = {"only": ["pinned"], "allow_fallbacks": False,
                   "data_collection": "deny", "zdr": True}
        for price, provider in (("NaN", private), ("-1", private),
                                ("0.001", private), (None, private),
                                ("0", {**private, "allow_fallbacks": True})):
            with self.subTest(price=price, provider=provider):
                client = MagicMock()
                client._provider = provider
                client._model_providers = {}
                client.require_zdr_route.return_value = {"pricing": {"prompt": price}}
                with patch("app.storage.retrieval.selected_embedding_spec",
                           return_value=(spec_id(spec), spec)), self.assertRaises(ModelClientError):
                    hybrid_search(MagicMock(), client, "private question", provider_tag="pinned",
                                  max_embedding_usd_per_million=Decimal("0.01"))
                client.embed.assert_not_called()

    def test_query_uses_spec_and_effective_model_route(self):
        spec = EmbeddingSpec(provider="fixture", model="synthetic", model_version="1",
                             dimensions=3, distance_metric="cosine",
                             input_recipe_version="raw-v1", chunking_version="v1",
                             query_input_type="search_query")
        client = MagicMock()
        client._provider = {"only": ["pinned"], "allow_fallbacks": False,
                            "data_collection": "deny", "zdr": True}
        client._model_providers = {spec.model: {**client._provider, "allow_fallbacks": True}}
        client.require_zdr_route.return_value = {"pricing": {"prompt": "0"}}
        with patch("app.storage.retrieval.selected_embedding_spec",
                   return_value=(spec_id(spec), spec)), self.assertRaises(ModelClientError):
            hybrid_search(MagicMock(), client, "private question", provider_tag="pinned",
                          max_embedding_usd_per_million=Decimal("0.01"))
        client.embed.assert_not_called()
        client._model_providers = {}
        client.embed.return_value = EmbeddingResult(model=spec.model_spec(), vectors=[[1.0, 0.0, 0.0]])
        with patch("app.storage.retrieval.selected_embedding_spec", return_value=(spec_id(spec), spec)), \
             patch("app.storage.retrieval.search_vector", return_value=[]) as vector_search, \
             patch("app.storage.retrieval.search_text", return_value=[]):
            self.assertEqual(hybrid_search(MagicMock(), client, "private question", provider_tag="pinned",
                                          max_embedding_usd_per_million=Decimal("0.01")), [])
        client.embed.assert_called_once_with(model=spec.model_spec(), texts=["private question"],
                                             dimensions=3, input_type="search_query")
        self.assertEqual(vector_search.call_args.kwargs["spec_id"], spec_id(spec))
