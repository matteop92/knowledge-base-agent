"""A chosen scope never widens; a routed scope still does."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from app.storage.retrieval import DatabaseRetriever


def retriever():
    return DatabaseRetriever(mock.MagicMock(), mock.MagicMock(), provider_tag="t",
                             max_embedding_usd_per_million=Decimal("1"))


class ScopeTests(unittest.TestCase):
    def test_an_empty_scoped_search_finds_nothing_and_never_reruns_unscoped(self) -> None:
        with mock.patch("app.storage.retrieval.hybrid_search", return_value=[]) as search:
            found = retriever_ = retriever()
            found.narrow(["doc-1"], strict=True)
            self.assertEqual(found.search("pear"), [])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs["document_ids"], ["doc-1"])
        self.assertFalse(retriever_.widened)

    def test_an_empty_strict_scope_stays_empty(self) -> None:
        item = retriever()
        item.narrow([], strict=True)
        self.assertEqual((item.scope, item.widened), ([], False))

    def test_a_routed_scope_still_widens(self) -> None:
        with mock.patch("app.storage.retrieval.hybrid_search", side_effect=[[], ["hit"]]) as search:
            item = retriever()
            item.narrow(["doc-1"])
            self.assertEqual(item.search("pear"), ["hit"])
        self.assertEqual(search.call_count, 2)
        self.assertTrue(item.widened)


if __name__ == "__main__":
    unittest.main()
