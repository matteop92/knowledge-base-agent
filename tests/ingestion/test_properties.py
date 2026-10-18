from __future__ import annotations

import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import psycopg
import yaml

from app.ingestion.preview import preview_markdown
from app.ingestion.contracts import ParsedDocument
from app.ingestion.properties import (
    PropertiesError, TagList, classify_properties, draft_tag_list, existing_properties, missing_fields,
)
from app.ingestion.vault_sync import VaultSettings, parse_folders, propose_tags, run_pass
from app.models.client import GenerationResult, ModelUsage
from tests.ingestion.test_vault_sync import FakeEmbeddingClient, VaultFixture

TAGS = TagList(tags=("leadership", "marketing", "ai"), approved=True)


def reply(**overrides):
    data = {"source_type": "note", "tags": ["leadership", "ai"], "proposed_tag": None,
            "people": ["Derek Sivers"], "date": "2024-05-01", "description": "A note about leading teams.",
            "language": "en"}
    data.update(overrides)
    return data


class FakeClassifierClient(FakeEmbeddingClient):
    def __init__(self, data=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = data or reply()
        self.generations = 0

    def generate_json(self, *, model, system_prompt, input_text, schema_name, json_schema, max_output_tokens,
                      reasoning_effort=None):
        self.generations += 1
        usage = ModelUsage(input_tokens=len(input_text.split()), output_tokens=50)
        self.billable_requests.append((model.model_id, usage))
        return GenerationResult(data=self.data, model=model, usage=usage)


def parse(text: str, relative: str = "Notes/note.md") -> ParsedDocument:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "vault"
        (root / relative).parent.mkdir(parents=True)
        (root / relative).write_text(text, encoding="utf-8")
        result = preview_markdown(root, relative, Path(folder) / "runs")
        return ParsedDocument.model_validate_json(
            (Path(folder) / "runs" / result["run_id"] / "parsed.json").read_text(encoding="utf-8"))


class ClassifierTests(unittest.TestCase):
    def test_file_without_frontmatter_gets_every_property(self) -> None:
        result = classify_properties(parse("# Leading\n\nHow to lead a team.\n"), TAGS, FakeClassifierClient())
        self.assertEqual(result.properties, {"source_type": "note", "tags": ["leadership", "ai"],
                                             "people": ["Derek Sivers"], "date": "2024-05-01",
                                             "description": "A note about leading teams.", "language": "en"})

    def test_existing_frontmatter_is_kept_and_only_gaps_are_filled(self) -> None:
        parsed = parse("---\ntags: [ops]\nauthor: Jane Doe\n---\n# Leading\n\nText.\n")
        self.assertEqual(existing_properties(parsed)["people"], ["Jane Doe"])
        result = classify_properties(parsed, TAGS, FakeClassifierClient())
        self.assertNotIn("tags", result.properties)
        self.assertNotIn("people", result.properties)
        self.assertEqual(result.properties["source_type"], "note")

    def test_a_complete_file_makes_no_model_call(self) -> None:
        parsed = parse("---\ntype: note\ntags: [ops]\nauthor: Jane Doe\ndate: 2024-01-02\n"
                       "description: Ops notes.\nlanguage: en\n---\n# Ops\n\nText.\n")
        client = FakeClassifierClient()
        self.assertEqual(missing_fields(parsed), [])
        self.assertFalse(classify_properties(parsed, TAGS, client).called_model)
        self.assertEqual(client.generations, 0)

    def test_unlisted_tags_are_dropped_and_one_is_kept_as_a_proposal(self) -> None:
        client = FakeClassifierClient(reply(tags=["leadership", "gardening", "cooking"], proposed_tag=None))
        result = classify_properties(parse("# Garden\n\nText.\n"), TAGS, client)
        self.assertEqual((result.properties["tags"], result.proposed_tag), (["leadership"], "gardening"))

    def test_an_invalid_reply_is_an_error(self) -> None:
        with self.assertRaises(PropertiesError):
            classify_properties(parse("# X\n\nText.\n"), TAGS, FakeClassifierClient({"tags": []}))
        with self.assertRaises(PropertiesError):
            classify_properties(parse("# X\n\nText.\n"), TAGS, FakeClassifierClient(reply(source_type="poem")))

    def test_bad_dates_are_dropped(self) -> None:
        result = classify_properties(parse("# X\n\nText.\n"), TAGS, FakeClassifierClient(reply(date="1mo")))
        self.assertNotIn("date", result.properties)

    def test_draft_tag_list_is_unapproved_and_ranks_existing_tags(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tags.yaml"
            draft_tag_list(Counter({"Leadership": 3}), Counter({"ai": 2, "Bad Tag!": 9}), path)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertFalse(data["approved"])
            self.assertEqual([item["name"] for item in data["tags"]], ["leadership", "ai"])
            self.assertIsNone(TagList.load(Path(folder) / "absent.yaml"))
            self.assertFalse(TagList.load(path).approved)

    def test_a_note_gets_at_most_three_list_tags(self) -> None:
        tags = TagList(tags=("leadership", "marketing", "ai", "career"), approved=True)
        client = FakeClassifierClient(reply(tags=["leadership", "marketing", "ai", "career"]))
        self.assertEqual(classify_properties(parse("# X\n\nText.\n"), tags, client).properties["tags"],
                         ["leadership", "marketing", "ai"])

    def test_tags_without_a_rule_are_still_offered(self) -> None:
        from app.ingestion.properties import model_input
        tags = TagList(tags=("emotions", "ai"), approved=True, definitions=(("emotions", "Feelings."),))
        text = model_input(parse("# X\n\nText.\n"), tags)
        self.assertIn("- emotions: Feelings.", text)
        self.assertIn("- ai\n", text)

    def test_tag_definitions_reach_the_model(self) -> None:
        from app.ingestion.properties import model_input
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tags.yaml"
            path.write_text("approved: true\ntags:\n- name: Emotions\n  when: Feelings and how to work with them.\n- ai\n",
                            encoding="utf-8")
            tags = TagList.load(path)
            self.assertEqual((tags.tags, tags.approved), (("emotions", "ai"), True))
            self.assertIn("- emotions: Feelings and how to work with them.", model_input(parse("# X\n\nText.\n"), tags))

    def test_propose_tags_writes_a_draft_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            vault = VaultFixture(Path(folder))
            vault.write("Knowledge/a.md", "---\ntags: [leadership]\n---\n# A\n\nText.\n")
            vault.write("Knowledge/b.md", "# B\n\nText.\n")
            output = Path(folder) / "tags.yaml"
            client = FakeClassifierClient(reply(tags=["hiring"]))
            report = propose_tags(vault.settings, 2, max_usd=1.0, client=client, output=output)
            self.assertEqual((report["sampled_files"], client.generations, client.calls), (2, 2, 0))
            names = [item["name"] for item in yaml.safe_load(output.read_text(encoding="utf-8"))["tags"]]
            self.assertEqual(set(names), {"leadership", "hiring"})
            self.assertFalse(vault.settings.runtime_dir.exists())

    def test_dry_run_includes_classifier_cost(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            vault = VaultFixture(Path(folder))
            vault.write("Knowledge/a.md", "# A\n\nText.\n")
            report = run_pass(vault.settings, tag_list=TAGS)
            self.assertGreater(report["properties_worst_case_usd"], 0)
            self.assertAlmostEqual(report["worst_case_usd"],
                                   report["embedding_worst_case_usd"] + report["properties_worst_case_usd"])


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class LiveClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.storage.database import apply_migrations
        cls.conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        apply_migrations(cls.conn.cursor())

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self) -> None:
        self.conn.execute("reset role")
        self.conn.execute("update knowledge.corpus_state set profile_id=null where singleton=true")
        self.conn.execute("truncate knowledge.documents, knowledge.spend cascade")
        self.conn.execute("delete from knowledge.profiles")
        self.conn.execute("set role knowledge_runtime")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = VaultFixture(Path(self.temp.name))
        self.vault.write("Knowledge/Notes/lead.md", "# Leading\n\nThe orchard team needs a lead.\n")

    def live(self, client, tag_list=TAGS):
        return run_pass(self.vault.settings, conn=self.conn, live=True, max_usd=1.0, client=client, tag_list=tag_list)

    def stored(self):
        return self.conn.execute("select status, properties, proposed_tag from knowledge.document_properties").fetchall()

    def test_new_file_gets_stored_properties_and_unchanged_file_is_not_reclassified(self) -> None:
        client = FakeClassifierClient(reply(tags=["leadership", "gardening"]))
        report = self.live(client)
        self.assertEqual((report["published"], report["properties_ready"], client.generations), (1, 1, 1))
        status, properties, proposed = self.stored()[0]
        self.assertEqual((status, properties["tags"], proposed), ("ready", ["leadership"], "gardening"))
        again = FakeClassifierClient()
        self.live(again)
        self.assertEqual(again.generations, 0)

    def test_classifier_failure_still_indexes_the_file(self) -> None:
        report = self.live(FakeClassifierClient({"broken": True}))
        self.assertEqual((report["published"], report["properties_failed"]), (1, 1))
        self.assertEqual(self.stored()[0][0], "failed")

    def test_files_loaded_before_approval_are_backfilled(self) -> None:
        self.live(FakeClassifierClient(), tag_list=TagList(tags=("ai",), approved=False))
        self.assertEqual(self.stored(), [])
        client = FakeClassifierClient()
        report = self.live(client)
        self.assertEqual((report["to_ingest"], report["properties_backfill"], client.generations), (0, 1, 1))
        self.assertEqual(self.stored()[0][0], "ready")

    def test_stored_people_make_the_file_a_named_source(self) -> None:
        from app.storage.retrieval import named_sources
        self.live(FakeClassifierClient(reply(people=["Luca Ferrari"])))
        self.assertEqual(len(named_sources(self.conn, "What did Luca Ferrari say about leading?")), 1)
        from app.storage.search import search_text
        hit = search_text(self.conn, "orchard")[0]
        self.assertEqual(hit.people, ["Luca Ferrari"])


if __name__ == "__main__":
    unittest.main()
