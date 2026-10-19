from __future__ import annotations

import unittest
from datetime import date

from app.capture.notes import CaptureError, note_properties, parse_draft, render, draft_from_markdown
from app.capture.screenshot import MAX_DATA_URL_CHARS, extract_screenshot, worst_case_usd
from app.models.client import GenerationResult, ModelSpec

MODEL = ModelSpec(provider="openrouter", model_id="openai/gpt-6-luna", model_version="current")
IMAGE = "data:image/png;base64,iVBORw0KGgo="


class FakeVision:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return GenerationResult(data=self.reply, model=kwargs["model"])


class ScreenshotTests(unittest.TestCase):
    def test_a_tweet_becomes_a_draft_with_its_author_platform_and_text(self):
        vision = FakeVision({"text": "Hire slowly, fire fast.\n\nThat's it.", "title": "Hiring advice",
                             "author": "Ann Lee", "platform": "X", "posted": "2026-10-01"})
        draft = extract_screenshot(IMAGE, vision, MODEL, tags=["hiring"])
        self.assertEqual(vision.calls[0]["images"], [IMAGE])
        self.assertEqual((draft.kind, draft.title, draft.body), ("screenshot", "Hiring advice",
                                                                "Hire slowly, fire fast.\n\nThat's it."))
        self.assertEqual(draft.own, {"author": "Ann Lee", "platform": "X", "published": "2026-10-01"})
        self.assertEqual(draft.tags, ["hiring"])

    def test_a_missing_author_stays_empty_rather_than_guessed(self):
        vision = FakeVision({"text": "Some text.", "title": None, "author": None, "platform": None, "posted": "Oct 1"})
        draft = extract_screenshot(IMAGE, vision, MODEL)
        self.assertEqual(draft.own, {})
        self.assertEqual(draft.title, "Some text.")

    def test_a_screenshot_without_text_is_refused(self):
        with self.assertRaises(CaptureError):
            extract_screenshot(IMAGE, FakeVision({"text": "  ", "title": None, "author": None, "platform": None,
                                                  "posted": None}), MODEL)

    def test_an_oversized_image_is_refused_before_any_call(self):
        vision = FakeVision({})
        with self.assertRaises(CaptureError):
            extract_screenshot("data:image/png;base64," + "A" * MAX_DATA_URL_CHARS, vision, MODEL)
        self.assertEqual(vision.calls, [])

    def test_the_author_reaches_the_parsed_note(self):
        vision = FakeVision({"text": "Hire slowly.", "title": "Hiring", "author": "Ann Lee", "platform": "LinkedIn",
                             "posted": "2026-10-01"})
        draft = extract_screenshot(IMAGE, vision, MODEL)
        rendered = render(draft, note_properties(draft, {"people": ["Someone Else"]}, date(2026, 10, 19)))
        parsed = parse_draft(draft_from_markdown(rendered), date(2026, 10, 19))
        self.assertEqual(parsed.metadata.authors, ["Ann Lee"])
        self.assertEqual(parsed.metadata.published.isoformat(), "2026-10-01")

    def test_the_worst_case_grows_with_the_image(self):
        self.assertGreater(worst_case_usd(IMAGE + "A" * 100_000, MODEL), worst_case_usd(IMAGE, MODEL))


if __name__ == "__main__":
    unittest.main()
