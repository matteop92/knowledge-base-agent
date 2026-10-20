from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.capture import CaptureCostError, YouTubeInput, YouTubeTranscriber
from app.capture.captions import (CHUNK_CHARS, CaptionsUnavailable, CaptionTranscriber, caption_turns, chunks,
                                  fetch_caption_text, fetch_description, request_text, worst_case_usd)
from app.capture.notes import CaptureError, Draft
from app.capture.youtube import transcribe_youtube
from app.models.gemini import Transcription
from app.storage.spend import SpendCapError

URL = "https://www.youtube.com/watch?v=dAIIaepNhQM"
DETAILS = lambda url: {"title": "Jev: 8 real use cases", "channel": "How I AI"}  # noqa: E731
CAPTIONS = "In some ways managers are at risk. [music] >> Whenever you have constrained inputs >> Right, exactly."


class FakeModel:
    """Answers with one name per turn; `names` maps a turn number to a name, `fail` raises instead."""

    def __init__(self, names=None, fail=False, people=("Claire Vo", "John Lindquist")):
        self.names, self.fail, self.people, self.inputs, self.deadline = names, fail, list(people), [], None

    def set_deadline(self, deadline):
        self.deadline = deadline

    def generate_json(self, *, input_text, **kwargs):
        self.inputs.append(input_text)
        if self.fail:
            raise RuntimeError("route refused")
        turns = [line for line in input_text.split("Turns:\n", 1)[1].splitlines() if line]
        names = self.names or {number: self.people[(number - 1) % 2] for number in range(1, len(turns) + 1)}
        return SimpleNamespace(data={"people": self.people,
                                     "names": [{"turn": number, "name": name} for number, name in names.items()]})


class Harness:
    """Patches the OpenRouter client factory so each model id gets its FakeModel, costing 0.001 per call."""

    def __init__(self, models: dict[str, FakeModel]):
        self.models, self.opened = models, []

    def __enter__(self):
        self.patches = [
            patch("app.ingestion.vault_sync.openrouter_client", side_effect=self.open),
            patch("app.ingestion.vault_sync.usage_mark", return_value=(0, 0)),
            patch("app.ingestion.vault_sync.measured_cost", side_effect=lambda client, mark, worst: 0.001),
        ]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()

    def open(self, models, **kwargs):
        self.opened.append(models[0])
        return self.models[models[0]]


def transcriber(**changes) -> CaptionTranscriber:
    values = {"fetch": lambda video: CAPTIONS, "describe": lambda video: "Claire Vo talks to John Lindquist.",
              "models": ["deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna"]}
    return CaptionTranscriber(**{**values, **changes})


class TurnTests(unittest.TestCase):
    def test_speaker_marks_split_turns_and_sound_tags_are_dropped(self):
        self.assertEqual(caption_turns(CAPTIONS), ["In some ways managers are at risk.",
                                                   "Whenever you have constrained inputs", "Right, exactly."])

    def test_long_videos_are_named_in_chunks_and_long_turns_are_shortened(self):
        turns = ["word " * 2000] * 100
        groups = chunks(turns)
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(group) for group in groups), 100)
        self.assertLess(len(request_text("t", "c", groups[0], [])), CHUNK_CHARS + 2000)
        self.assertIn(" ...", request_text("t", "c", turns[:1], []))

    def test_the_worst_case_covers_every_chunk(self):
        short, long = ["hello"] * 10, ["word " * 2000] * 100
        self.assertGreater(worst_case_usd("deepseek/deepseek-v4.1-flash", "t", "c", long),
                           worst_case_usd("deepseek/deepseek-v4.1-flash", "t", "c", short) * 5)


class CaptionTranscriberTests(unittest.TestCase):
    def test_captions_become_bold_speaker_turns_with_the_words_unchanged(self):
        approved = []
        deepseek = FakeModel()
        with Harness({"deepseek/deepseek-v4.1-flash": deepseek}):
            captions = transcriber(approve=approved.append)
            body = captions.transcript("dAIIaepNhQM", "Jev", "How I AI")
        self.assertEqual(body, "**Claire Vo:** In some ways managers are at risk.\n\n"
                               "**John Lindquist:** Whenever you have constrained inputs\n\n**Claire Vo:** Right, exactly.")
        self.assertEqual((captions.cost_usd, captions.source), (0.001, "captions (deepseek/deepseek-v4.1-flash)"))
        self.assertEqual(len(approved), 1)
        self.assertIn("Claire Vo talks to John Lindquist.", deepseek.inputs[0])

    def test_a_failed_model_hands_over_to_the_next_and_both_costs_count(self):
        harness = Harness({"deepseek/deepseek-v4.1-flash": FakeModel(fail=True), "openai/gpt-6-luna": FakeModel()})
        with harness:
            captions = transcriber()
            self.assertIsNotNone(captions.transcript("dAIIaepNhQM", "Jev", "How I AI"))
        self.assertEqual(harness.opened, ["deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna"])
        self.assertAlmostEqual(captions.cost_usd, 0.002)
        self.assertEqual(captions.source, "captions (openai/gpt-6-luna)")

    def test_no_captions_or_no_names_fall_back_to_gemini(self):
        def missing(video):
            raise CaptionsUnavailable("TranscriptsDisabled")
        with Harness({}) as harness:
            captions = transcriber(fetch=missing)
            self.assertIsNone(captions.transcript("dAIIaepNhQM", "Jev", "How I AI"))
        self.assertEqual((harness.opened, captions.source), ([], "gemini (captions: TranscriptsDisabled)"))
        failing = {"deepseek/deepseek-v4.1-flash": FakeModel(fail=True), "openai/gpt-6-luna": FakeModel(fail=True)}
        with Harness(failing):
            captions = transcriber()
            self.assertIsNone(captions.transcript("dAIIaepNhQM", "Jev", "How I AI"))
        self.assertEqual(captions.source, "gemini (captions: speakers not named)")
        self.assertAlmostEqual(captions.cost_usd, 0.002)

    def test_the_daily_cap_sends_the_video_to_gemini_before_any_model_call(self):
        def refuse(usd):
            raise SpendCapError("cap")
        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel()}) as harness:
            captions = transcriber(approve=refuse)
            self.assertIsNone(captions.transcript("dAIIaepNhQM", "Jev", "How I AI"))
        self.assertEqual((harness.opened, captions.cost_usd), ([], 0.0))

    def test_an_unnamed_turn_takes_the_speaker_from_two_turns_back_but_many_move_on(self):
        many_turns = " >> ".join(f"turn {number}" for number in range(1, 21))
        alternating = {number: ("Ann" if number % 2 else "Bo") for number in range(1, 21)}
        few_missing = FakeModel(names={number: name for number, name in alternating.items() if number not in (1, 5)})
        with Harness({"deepseek/deepseek-v4.1-flash": few_missing}):
            body = transcriber(fetch=lambda video: many_turns).transcript("x", "t", "c")
        self.assertIn("**Unknown speaker:** turn 1\n", body)
        self.assertIn("**Ann:** turn 5\n", body)
        mostly_missing = FakeModel(names={1: "Ann"})
        with Harness({"deepseek/deepseek-v4.1-flash": mostly_missing, "openai/gpt-6-luna": FakeModel(fail=True)}):
            self.assertIsNone(transcriber(fetch=lambda video: many_turns).transcript("x", "t", "c"))

    def test_any_caption_failure_or_a_long_unmarked_video_goes_to_gemini(self):
        from xml.etree.ElementTree import ParseError

        def broken(video):
            raise ParseError("no element found")
        for fetch, source in ((broken, "gemini (captions: ParseError)"),
                              (lambda video: "[music]", "gemini (captions: empty)")):
            with Harness({}) as harness:
                captions = transcriber(fetch=fetch)
                self.assertIsNone(captions.transcript("x", "t", "c"))
            self.assertEqual((harness.opened, captions.source), ([], source))
        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel()}):
            self.assertIsNotNone(transcriber(fetch=lambda video: "A short solo clip.").transcript("x", "t", "c"))

    def test_a_long_unmarked_video_tries_gemini_first_and_keeps_the_captions_as_a_backup(self):
        solo = "word " * 2000
        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel(names={1: "Tim Ferriss"})}):
            captions = transcriber(fetch=lambda video: solo)
            self.assertIsNone(captions.transcript("x", "t", "The Tim Ferriss Show"))
        self.assertEqual(captions.source, "gemini (captions: no speaker marks)")
        self.assertTrue(captions.fallback().startswith("**Tim Ferriss:** word word"))
        self.assertEqual(captions.source, "captions, one speaker (gemini failed)")
        failing = {"deepseek/deepseek-v4.1-flash": FakeModel(fail=True), "openai/gpt-6-luna": FakeModel(fail=True)}
        with Harness(failing):
            captions = transcriber(fetch=lambda video: solo)
            captions.transcript("x", "t", "The Tim Ferriss Show")
        self.assertTrue(captions.fallback().startswith("**The Tim Ferriss Show:** word"))
        self.assertIsNone(transcriber().fallback())

    def test_an_unpriced_model_is_skipped_and_the_next_cap_check_counts_earlier_spend(self):
        approved = []
        models = ["vendor/unknown", "deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna"]
        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel(fail=True), "openai/gpt-6-luna": FakeModel()}) as harness:
            captions = transcriber(models=models, approve=approved.append)
            self.assertIsNotNone(captions.transcript("x", "t", "c"))
        self.assertEqual(harness.opened, ["deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna"])
        luna_worst = worst_case_usd("openai/gpt-6-luna", "t", "c", caption_turns(CAPTIONS))
        self.assertAlmostEqual(approved[1], luna_worst + 0.001)

    def test_the_cap_can_stop_the_second_model_and_keeps_the_first_cost(self):
        calls = []

        def second_refused(usd):
            calls.append(usd)
            if len(calls) == 2:
                raise SpendCapError("cap")
        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel(fail=True), "openai/gpt-6-luna": FakeModel()}):
            captions = transcriber(approve=second_refused)
            self.assertIsNone(captions.transcript("x", "t", "c"))
        self.assertEqual((captions.cost_usd, captions.source), (0.001, "gemini (captions: daily cap)"))

    def test_long_videos_name_each_chunk_and_carry_the_names_forward(self):
        long_turns = " >> ".join(f"turn {number} " + "word " * 300 for number in range(1, 81))
        model = FakeModel()
        with Harness({"deepseek/deepseek-v4.1-flash": model}):
            body = transcriber(fetch=lambda video: long_turns).transcript("x", "t", "c")
        self.assertGreater(len(model.inputs), 1)
        self.assertNotIn("Names already found:", model.inputs[0])
        self.assertIn("Names already found: Claire Vo, John Lindquist", model.inputs[1])
        self.assertEqual(body.count("\n\n**"), 79)
        self.assertIsNotNone(model.deadline)

    def test_names_cannot_break_the_bold_label(self):
        model = FakeModel(names={1: "**Ann: Lee**", 2: "Ann *Lee*", 3: "Bo\nB"})
        with Harness({"deepseek/deepseek-v4.1-flash": model}):
            body = transcriber().transcript("x", "t", "c")
        self.assertTrue(body.startswith("**Ann Lee:** In some ways"))
        self.assertIn("**Ann Lee:** Whenever", body)
        self.assertIn("**Bo B:** Right", body)


class FetchTests(unittest.TestCase):
    def test_library_and_network_errors_become_captions_unavailable(self):
        import requests
        from youtube_transcript_api import TranscriptsDisabled

        for error in (TranscriptsDisabled("x"), requests.ConnectionError("down")):
            with patch("youtube_transcript_api.YouTubeTranscriptApi.list", side_effect=error):
                with self.assertRaises(CaptionsUnavailable):
                    fetch_caption_text("dAIIaepNhQM")

    def test_the_description_is_read_from_the_page_or_left_empty(self):
        import io
        page = rb'..."shortDescription":"Claire Vo talks to \"John\" Lindquist.\nMore",...'
        with patch("app.capture.captions.request.urlopen", return_value=io.BytesIO(page)):
            self.assertEqual(fetch_description("x"), 'Claire Vo talks to "John" Lindquist.\nMore')
        with patch("app.capture.captions.request.urlopen", return_value=io.BytesIO(b"<html></html>")):
            self.assertEqual(fetch_description("x"), "")
        from http.client import IncompleteRead
        with patch("app.capture.captions.request.urlopen", side_effect=IncompleteRead(b"")):
            self.assertEqual(fetch_description("x"), "")


class FakeGemini:
    def __init__(self):
        self.calls = []

    def transcribe(self, url, prompt, **kwargs):
        self.calls.append(url)
        return Transcription("**Jawed:** All right.", "STOP", {})


class TranscribeTests(unittest.TestCase):
    def test_captions_skip_gemini_and_none_falls_back(self):
        gemini = FakeGemini()
        draft = transcribe_youtube(URL, gemini, details=DETAILS, captions=lambda video, title, channel: "**Ann:** Hi.")
        self.assertEqual((draft.body, gemini.calls), ("**Ann:** Hi.", []))
        seen = []
        draft = transcribe_youtube(URL, gemini, details=DETAILS,
                                   captions=lambda video, title, channel: seen.append((video, title, channel)))
        self.assertEqual(seen, [("dAIIaepNhQM", "Jev: 8 real use cases", "How I AI")])
        self.assertEqual((draft.body, gemini.calls), ("**Jawed:** All right.", [URL]))


class BusyGemini:
    def __init__(self, text=None):
        self.text = text

    def transcribe(self, url, prompt, **kwargs):
        from app.models.gemini import GeminiError
        if self.text is None:
            raise GeminiError("YouTube transcripts are busy right now; try again in a few minutes")
        return Transcription(self.text, "STOP", {})


class BackupTests(unittest.TestCase):
    def test_the_backup_saves_the_add_when_gemini_is_busy_or_unlabelled(self):
        for gemini in (BusyGemini(), BusyGemini("no speaker labels here")):
            draft = transcribe_youtube(URL, gemini, details=DETAILS, captions=lambda video, title, channel: None,
                                       backup=lambda: "**Ann:** Hello.")
            self.assertEqual(draft.body, "**Ann:** Hello.")
        with self.assertRaisesRegex(CaptureError, "busy"):
            transcribe_youtube(URL, BusyGemini(), details=DETAILS, captions=lambda video, title, channel: None,
                               backup=lambda: None)


class YouTubeTranscriberTests(unittest.TestCase):
    def test_a_failure_after_a_model_call_still_carries_its_cost(self):
        def fail_plainly(url, client, **kwargs):
            kwargs["captions"]("dAIIaepNhQM", "t", "c")
            raise CaptureError("YouTube transcripts are busy right now; try again in a few minutes")

        def fail_oddly(url, client, **kwargs):
            kwargs["captions"]("dAIIaepNhQM", "t", "c")
            raise KeyError("x")

        def succeed(url, client, **kwargs):
            kwargs["captions"]("dAIIaepNhQM", "t", "c")
            return Draft(kind="youtube", body="**A:** b", title="t")

        with Harness({"deepseek/deepseek-v4.1-flash": FakeModel(), "openai/gpt-6-luna": FakeModel()}), \
                patch("app.capture.captions.fetch_caption_text", return_value=CAPTIONS), \
                patch("app.capture.captions.fetch_description", return_value=""):
            with patch("app.capture.youtube.transcribe_youtube", side_effect=fail_plainly):
                with self.assertRaises(CaptureError) as caught:
                    YouTubeTranscriber().extract(YouTubeInput(url=URL))
            self.assertEqual(caught.exception.cost_usd, 0.001)
            with patch("app.capture.youtube.transcribe_youtube", side_effect=fail_oddly):
                with self.assertRaises(CaptureCostError) as odd:
                    YouTubeTranscriber().extract(YouTubeInput(url=URL))
            self.assertEqual(odd.exception.cost_usd, 0.001)
            with patch("app.capture.youtube.transcribe_youtube", side_effect=succeed):
                self.assertEqual(YouTubeTranscriber().extract(YouTubeInput(url=URL))[1], 0.001)


if __name__ == "__main__":
    unittest.main()
