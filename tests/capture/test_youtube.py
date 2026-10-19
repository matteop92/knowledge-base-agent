from __future__ import annotations

import io
import json
import unittest
from datetime import date
from unittest.mock import patch
from urllib.error import HTTPError

from app.capture.notes import CaptureError, draft_from_markdown, note_properties, parse_draft, render
from app.capture.youtube import clean_transcript, transcribe_youtube, video_url
from app.models.gemini import GeminiClient, GeminiError, Transcription

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
DETAILS = lambda url: {"title": "Me at the zoo", "channel": "jawed"}  # noqa: E731


class FakeGemini:
    def __init__(self, text="**Jawed:** All right, so here we are.", finish="STOP", error=None):
        self.text, self.finish, self.error, self.calls = text, finish, error, []

    def transcribe(self, url, prompt, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        return Transcription(self.text, self.finish, {})


class LinkTests(unittest.TestCase):
    def test_youtube_links_become_one_canonical_url(self):
        for link in ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=10", "youtu.be/jNQXAC9IVRw",
                     "https://m.youtube.com/watch?v=jNQXAC9IVRw", "https://www.youtube.com/shorts/jNQXAC9IVRw"):
            with self.subTest(link=link):
                self.assertEqual(video_url(link), URL)

    def test_other_links_are_refused(self):
        for link in ("https://example.com/watch?v=jNQXAC9IVRw", "https://youtube.com/watch?v=short",
                     "javascript:alert(1)", "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw"):
            with self.subTest(link=link), self.assertRaises(CaptureError):
                video_url(link)


class TranscribeTests(unittest.TestCase):
    def test_a_video_becomes_a_transcript_draft_with_youtubes_title_and_channel(self):
        gemini = FakeGemini(text="Here is the transcript:\n\n**Jawed:** All right, so here we are.")
        draft = transcribe_youtube("youtu.be/jNQXAC9IVRw", gemini, details=DETAILS)
        self.assertEqual(gemini.calls, [URL])
        self.assertEqual((draft.kind, draft.title, draft.url), ("youtube", "Me at the zoo", URL))
        self.assertEqual(draft.own["author"], "jawed")
        self.assertTrue(draft.body.startswith("**Jawed:**"))

    def test_the_saved_note_routes_as_a_bold_speaker_transcript(self):
        draft = transcribe_youtube(URL, FakeGemini(text="**Jawed:** One.\n\n**Friend:** Two.\n\n**Jawed:** Three."),
                                   details=DETAILS)
        rendered = render(draft, note_properties(draft, {}, date(2026, 10, 19)))
        parsed = parse_draft(draft_from_markdown(rendered), date(2026, 10, 19))
        self.assertEqual(parsed.metadata.authors, ["jawed"])
        self.assertEqual(parsed.metadata.source_url, URL)
        self.assertIn("bold speaker labels", parsed.route_reason)

    def test_a_cut_off_transcript_says_so(self):
        draft = transcribe_youtube(URL, FakeGemini(finish="MAX_TOKENS"), details=DETAILS)
        self.assertIn("Transcript cut off", draft.body)

    def test_a_transcript_without_speaker_labels_is_refused(self):
        with self.assertRaises(CaptureError):
            clean_transcript("Just some words.")

    def test_a_private_video_fails_before_any_model_call(self):
        gemini = FakeGemini()

        def private(url):
            raise CaptureError("this video is private or unavailable")
        with self.assertRaises(CaptureError):
            transcribe_youtube(URL, gemini, details=private)
        self.assertEqual(gemini.calls, [])

    def test_a_quota_error_reads_plainly(self):
        gemini = FakeGemini(error=GeminiError("Gemini's free daily limit was reached; try again later", quota=True))
        with self.assertRaisesRegex(CaptureError, "daily limit"):
            transcribe_youtube(URL, gemini, details=DETAILS)


class GeminiClientTests(unittest.TestCase):
    def test_only_the_link_and_prompt_are_sent(self):
        reply = {"candidates": [{"content": {"parts": [{"text": "**A:** hi"}]}, "finishReason": "STOP"}]}
        with patch("app.models.gemini.request.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            result = GeminiClient(api_key="test-key", model="m").transcribe(URL, "Transcribe.")
        sent = json.loads(send.call_args[0][0].data)
        self.assertEqual(sent["contents"][0]["parts"], [{"file_data": {"file_uri": URL}}, {"text": "Transcribe."}])
        self.assertEqual((result.text, result.finish_reason), ("**A:** hi", "STOP"))

    def test_rate_limits_and_missing_keys_are_readable(self):
        with patch("app.models.gemini.request.urlopen", side_effect=HTTPError(URL, 429, "busy", {}, None)):
            with self.assertRaises(GeminiError) as caught:
                GeminiClient(api_key="test-key").transcribe(URL, "x")
        self.assertTrue(caught.exception.quota)
        with self.assertRaisesRegex(GeminiError, "missing"):
            GeminiClient(api_key="").transcribe(URL, "x")


if __name__ == "__main__":
    unittest.main()
