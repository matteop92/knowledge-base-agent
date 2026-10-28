import unittest

from app.agent.records import AnswerResult
from app.api.answers import build_receipt
from app.storage.records import SourcePassage


def passage(document_id, path):
    return SourcePassage(document_id=document_id, run_id="run", source_sha256="a" * 64, root_alias="root",
                         relative_path=path, chunk_id="c1", start_char=0, end_char=4, text="text")


class ReceiptTests(unittest.TestCase):
    def test_citations_carry_title_and_author_with_path_and_blank_as_fallbacks(self):
        cited = {"E1.0": passage("d1", "Knowledge/a.md"), "E2.0": passage("d2", "Knowledge/b.md")}
        result = AnswerResult(answer="A [E1.0] B [E2.0]", completeness="complete", citations=cited, gaps=[],
                              actions=[], model_calls=1, inspected=dict(cited), needs={}, elapsed_seconds=0.1)
        receipt = build_receipt(result, {"d1": ("Note A", "Ada Stone")}, "Vault")
        self.assertEqual((receipt["citations"]["E1.0"]["title"], receipt["citations"]["E1.0"]["author"]),
                         ("Note A", "Ada Stone"))
        self.assertEqual((receipt["citations"]["E2.0"]["title"], receipt["citations"]["E2.0"]["author"]),
                         ("Knowledge/b.md", ""))


    def test_receipt_stores_each_passage_speaker_and_each_cited_documents_speakers(self):
        spoken = passage("d1", "Knowledge/a.md").model_copy(update={"speaker": "Niels Hoven",
                                                                       "run_speakers": ["Tim Ferriss", "Niels Hoven"]})
        cited = {"E1.0": spoken, "E2.0": passage("d2", "Knowledge/b.md")}
        result = AnswerResult(answer="A [E1.0] B [E2.0]", completeness="complete", citations=cited, gaps=[],
                              actions=[], model_calls=1, inspected=dict(cited), needs={}, elapsed_seconds=0.1,
                              routing={"route": "plain"})
        receipt = build_receipt(result, {}, "Vault")
        self.assertEqual(receipt["citations"]["E1.0"]["speaker"], "Niels Hoven")
        self.assertIsNone(receipt["citations"]["E2.0"]["speaker"])
        self.assertEqual(receipt["speakers"], {"d1": ["Tim Ferriss", "Niels Hoven"]})
        self.assertEqual(receipt["routing"], {"route": "plain"})


if __name__ == "__main__":
    unittest.main()
