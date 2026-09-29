from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.ingestion.embeddings import EmbeddingSpec
from app.models.client import EmbeddingResult, ModelClientError
from app.storage import StorageError
from app.storage.records import SearchHit, spec_id
from app.storage.retrieval import fuse_hits, hybrid_search, selected_embedding_spec


def hit(chunk: str, start: int, *, rank: int = 1, score: float = 0) -> SearchHit:
    return SearchHit(rank=rank, score=score, document_id="doc", run_id="run",
                     source_sha256="a" * 64, root_alias="root", relative_path="note.md",
                     chunk_id=chunk, source_spans=[{"start_char": start, "end_char": start + 20}],
                     raw_text="source text")


class RetrievalTest(unittest.TestCase):
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
