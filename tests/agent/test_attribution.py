from __future__ import annotations

import unittest

from app.agent.attribution import check_attribution, named_speakers, split_sentences
from app.agent.harness import answer_question
from app.models.client import GenerationResult, ModelSpec
from app.storage.records import FusedHit, SearchHit, SourcePassage
from tests.agent.test_actions import action_v3

MODEL = ModelSpec(provider="fake", model_id="fake", model_version="1")
SPEAKERS = ["Tim Ferriss", "Niels Hoven"]


def passage(speaker=None, speakers=None, doc="ep", text="Some words."):
    return SourcePassage(document_id=doc, run_id="run", source_sha256="a" * 64, root_alias="root",
                         relative_path=f"{doc}.md", chunk_id="c1", start_char=0, end_char=len(text), text=text,
                         speaker=speaker, run_speakers=speakers)


NIELS = passage("Niels Hoven", SPEAKERS)
TIM = passage("Tim Ferriss", SPEAKERS)


class AttributionTests(unittest.TestCase):
    def flags(self, answer, **cited):
        return check_attribution(answer, cited)

    def test_a_host_credited_with_a_guests_passage_is_flagged_even_when_no_host_passage_was_read(self):
        flags = self.flags("Tim notes that some platforms (he cites X) hire slowly [E1.2].", **{"E1.2": NIELS})
        self.assertEqual(len(flags), 1)
        self.assertEqual((flags[0].named, flags[0].cited_speakers), (["Tim Ferriss"], ["Niels Hoven"]))
        self.assertEqual(flags[0].citation_ids, ["E1.2"])

    def test_the_same_sentence_naming_the_real_speaker_passes(self):
        self.assertEqual(self.flags("Niels Hoven notes that some platforms hire slowly [E1.2].", **{"E1.2": NIELS}), [])

    def test_naming_the_host_as_the_show_while_crediting_the_guest_passes(self):
        self.assertEqual(self.flags("Niels Hoven, on Tim's show, notes that platforms hire slowly [E1.2].",
                                    **{"E1.2": NIELS}), [])

    def test_a_sentence_citing_a_passage_of_the_named_speaker_passes(self):
        self.assertEqual(self.flags("Tim says to ask for intros [E1.1][E1.2].", **{"E1.1": TIM, "E1.2": NIELS}), [])

    def test_a_sentence_naming_no_one_or_citing_only_articles_is_not_checked(self):
        self.assertEqual(self.flags("Platforms hire slowly [E1.2].", **{"E1.2": NIELS}), [])
        article = passage()
        self.assertEqual(self.flags("Tim Ferriss says hire slowly [E2.0].", **{"E2.0": article}), [])

    def test_a_first_name_matches_only_a_speaker_of_a_cited_document(self):
        self.assertEqual(named_speakers("Tim says so", SPEAKERS), ["Tim Ferriss"])
        self.assertEqual(named_speakers("Tim says so", ["Niels Hoven"]), [])
        self.assertEqual(self.flags("Tim says so [E2.0].", **{"E2.0": passage("Someone Else", ["Someone Else"])}), [])
        self.assertEqual(named_speakers("**Niels Hoven** and *Tim*", SPEAKERS), ["Tim Ferriss", "Niels Hoven"])
        self.assertEqual(named_speakers("Timothy says so", SPEAKERS), [])

    def test_a_first_name_must_be_written_as_a_name_and_an_unlabelled_passage_is_not_checked(self):
        will = ["Will Smith", "Ann Lee"]
        self.assertEqual(named_speakers("This will work", will), [])
        self.assertEqual(named_speakers("Will says so", will), ["Will Smith"])
        self.assertEqual(named_speakers("will smith says so", will), ["Will Smith"])
        unlabelled = passage(None, SPEAKERS)
        self.assertEqual(self.flags("Tim says so [E1.0].", **{"E1.0": unlabelled}), [])

    def test_citations_after_the_full_stop_stay_with_their_sentence(self):
        sentences = split_sentences("Tim notes the first point. [E1.2] Niels adds a second one. [E1.3]")
        self.assertEqual(sentences, ["Tim notes the first point [E1.2].", "Niels adds a second one [E1.3]."])
        self.assertEqual(len(self.flags("Tim notes the first point. [E1.2] Niels adds more. [E1.3]",
                                        **{"E1.2": NIELS, "E1.3": NIELS})), 1)

    def test_each_bullet_is_a_sentence(self):
        answer = "- Tim says a [E1.1].\n- Niels says b [E1.2]."
        self.assertEqual(self.flags(answer, **{"E1.1": NIELS, "E1.2": NIELS})[0].sentence, "Tim says a [E1.1].")


class FakeClient:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.prompts = []

    def generate_json(self, **kwargs):
        self.prompts.append(kwargs["system_prompt"])
        return GenerationResult(data=next(self.actions), model=MODEL)


class FakeRetriever:
    def search(self, query):
        hit = SearchHit(rank=1, score=0, document_id="ep", run_id="run", source_sha256="a" * 64, root_alias="root",
                        relative_path="ep.md", chunk_id="chunk", source_spans=[{"start_char": 0, "end_char": 13}],
                        raw_text="actual source")
        return [FusedHit(rank=1, rrf_score=1 / 61, channels=["text"], hit=hit)]

    def read(self, reference, span_index):
        return NIELS.model_copy(update={"chunk_id": "chunk", "text": "actual source", "end_char": 13})


def finish(answer):
    return [action_v3("read", reference_id="E1"),
            action_v3("finish", answer=answer, citation_ids=["E1.0"], completeness="complete", sufficient=True,
                      need_updates=[{"id": "N1", "description": "Explain", "status": "resolved",
                                     "evidence_ids": ["E1.0"], "rationale": None}])]


class HarnessCheckTests(unittest.TestCase):
    def test_a_flag_turns_a_complete_answer_into_a_partial_one_with_one_gap(self):
        result = answer_question("Explain", retriever=FakeRetriever(), client=FakeClient(finish("Tim says so [E1.0].")),
                                 model=MODEL)
        self.assertEqual(result.completeness, "partial")
        self.assertEqual(len(result.gaps), 1)
        self.assertIn("Tim Ferriss", result.gaps[0])
        self.assertIn("Niels Hoven", result.gaps[0])
        self.assertEqual(result.attribution[0]["named"], ["Tim Ferriss"])

    def test_a_correct_attribution_leaves_the_answer_complete(self):
        result = answer_question("Explain", retriever=FakeRetriever(),
                                 client=FakeClient(finish("Niels says so [E1.0].")), model=MODEL)
        self.assertEqual((result.completeness, result.gaps, result.attribution), ("complete", [], []))

    def test_prompt_versions_load_the_family_and_default_to_v13_and_v14(self):
        def run(**kwargs):
            client = FakeClient(finish("Niels says so [E1.0]."))
            result = answer_question("Explain", retriever=FakeRetriever(), client=client, model=MODEL, **kwargs)
            return client.prompts[0], result.prompt_version

        v13, version = run()
        self.assertEqual(version, "answer_agent_v13")
        v15, version = run(prompt_version="answer_agent_v15")
        self.assertEqual(version, "answer_agent_v15")
        self.assertIn("said by that speaker and by no one else", v15)
        self.assertNotIn("said by that speaker and by no one else", v13)
        # v15 is v13 with the speaker rule inserted and nothing else changed.
        head, tail = v13.split("attribute each accordingly.")
        self.assertTrue(v15.startswith(head + "attribute each accordingly."))
        self.assertTrue(v15.endswith(tail))

    def test_v16_adds_the_same_rule_text_to_v14_and_nothing_else(self):
        from pathlib import Path

        folder = Path(__file__).resolve().parents[2] / "prompts"
        v13, v14, v15, v16 = (folder.joinpath(f"answer_agent_v{n}.md").read_text(encoding="utf-8")
                              for n in (13, 14, 15, 16))
        head, tail = v14.split("attribute each accordingly.")
        self.assertTrue(v16.startswith(head + "attribute each accordingly.") and v16.endswith(tail))
        rule = v15[len(v13.split("attribute each accordingly.")[0]) + len("attribute each accordingly."):
                   len(v15) - len(v13.split("attribute each accordingly.")[1])]
        self.assertIn(rule, v16)
        self.assertEqual(v16.replace(rule, ""), v14)

    def test_an_unknown_prompt_version_is_refused(self):
        def run(**kwargs):
            return answer_question("Explain", retriever=FakeRetriever(),
                                   client=FakeClient(finish("Niels says so [E1.0].")), model=MODEL, **kwargs)

        with self.assertRaises(ValueError):
            run(prompt_version="answer_agent_v99")


if __name__ == "__main__":
    unittest.main()
