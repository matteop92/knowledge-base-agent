"""Card facts from synthetic metadata: type, platform, source name, people and dates."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.ingestion.card_facts import card_facts


def facts(metadata=None, path="Knowledge/Notes/Item.md", added=None):
    return card_facts(metadata or {}, path, added)


class PlatformTests(unittest.TestCase):
    def test_platform_field_without_a_url(self) -> None:
        self.assertEqual(facts({"frontmatter": {"platform": "LinkedIn"}})["platform"], "linkedin")

    def test_platform_from_the_url_host(self) -> None:
        self.assertEqual(facts({"source_url": "https://www.youtube.com/watch?v=x"})["platform"], "youtube")
        self.assertEqual(facts({"source_url": "https://youtu.be/x"})["platform"], "youtube")
        self.assertEqual(facts({"source_url": "https://example.org/a"})["platform"], "website")

    def test_the_field_wins_over_the_url(self) -> None:
        found = facts({"frontmatter": {"platform": "X"}, "source_url": "https://www.linkedin.com/posts/a"})
        self.assertEqual(found["platform"], "x")

    def test_nothing_known_leaves_the_platform_out(self) -> None:
        found = facts({"frontmatter": {"platform": "Something odd"}})
        self.assertNotIn("platform", found)
        self.assertNotIn("source_name", found)

    def test_a_website_note_is_named_by_its_site(self) -> None:
        self.assertEqual(facts({"source_url": "https://www.example.org/a"})["source_name"], "example.org")


class PeopleTests(unittest.TestCase):
    def test_a_youtube_author_is_a_person_even_when_it_is_the_channel(self) -> None:
        for frontmatter in ({"author": "Channel Name", "source": "YouTube"}, {"author": "Channel Name", "platform": "Youtube"}):
            found = facts({"authors": ["Channel Name"], "frontmatter": frontmatter, "source_url": "https://youtu.be/x"})
            self.assertEqual((found["authors"], found["source_name"]), (["Channel Name"], "YouTube"))

    def test_an_explicit_channel_is_the_source_name_and_not_repeated_as_a_person(self) -> None:
        found = facts({"authors": ["Show", "Ben Hart"], "frontmatter": {"channel": "Show"}, "source_url": "https://youtu.be/x"})
        self.assertEqual((found["authors"], found["source_name"]), (["Ben Hart"], "Show"))

    def test_guest_from_frontmatter_and_author_kept(self) -> None:
        found = facts({"authors": ["Ben Hart"], "frontmatter": {"guest": "[[Ada Stone]]"}})
        self.assertEqual((found["authors"], found["guests"]), (["Ben Hart"], ["Ada Stone"]))


class TypeTests(unittest.TestCase):
    def test_social_post_is_a_post(self) -> None:
        self.assertEqual(facts({"generated": {"source_type": "social_post"}}, "Elsewhere/P.md")["type"], "post")

    def test_quote_and_folder_types(self) -> None:
        self.assertEqual(facts({"frontmatter": {"type": "quote"}})["type"], "quote")
        self.assertEqual(facts({}, "Knowledge/Books/Thing.md")["type"], "book")

    def test_everything_else_is_a_note(self) -> None:
        for value in ("meeting", "reference", "other", "mystery"):
            self.assertEqual(facts({"frontmatter": {"type": value}})["type"], "note", value)
        self.assertEqual(facts({}, "Elsewhere/Thing.md")["type"], "note")


class PodcastTests(unittest.TestCase):
    def test_a_transcript_with_a_show_gets_the_podcast_mark(self) -> None:
        found = facts({"frontmatter": {"type": "transcript", "show": "Show Alpha"}})
        self.assertEqual((found["platform"], found["source_name"]), ("podcast", "Show Alpha"))

    def test_a_flagged_note_without_a_show_is_just_a_podcast(self) -> None:
        found = facts({"frontmatter": {"type": "transcript", "platform": "podcast"}})
        self.assertEqual((found["platform"], found["source_name"]), ("podcast", "Podcast"))


class DateTests(unittest.TestCase):
    def test_published_beats_created(self) -> None:
        self.assertEqual(facts({"published": "2024-05-01", "created": "2025-01-01"})["card_date"], "2024-05-01")

    def test_a_value_that_is_not_a_day_is_skipped(self) -> None:
        self.assertEqual(facts({"published": "[[Oct 2024]]", "created": "2025-01-01"})["card_date"], "2025-01-01")

    def test_no_dates_falls_back_to_added(self) -> None:
        added = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
        self.assertEqual(facts({}, added=added)["card_date"], "2026-10-03")
        self.assertNotIn("card_date", facts({}))

    def test_book_year_needs_a_published_date(self) -> None:
        added = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertNotIn("book_year", facts({}, "Knowledge/Books/B.md", added))
        self.assertEqual(facts({"published": "2015-03-02"}, "Knowledge/Books/B.md")["book_year"], "2015")


if __name__ == "__main__":
    unittest.main()
