import unittest

from app.storage.search import source_author


class SourceAuthorTests(unittest.TestCase):
    def test_authors_come_first_and_guests_are_not_authors(self):
        metadata = {"authors": ["[[Ada Stone]]"], "guests": ["Ben Hart"]}
        self.assertEqual(source_author(metadata, "Knowledge/Transcripts/a.md"), "Ada Stone")

    def test_falls_back_to_other_named_people_then_to_nothing(self):
        self.assertEqual(source_author({"frontmatter": {"people": ["Ada Stone"]}}, "Knowledge/a.md"), "Ada Stone")
        self.assertEqual(source_author({}, "Knowledge/a.md"), "")


if __name__ == "__main__":
    unittest.main()
