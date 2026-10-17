from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from app.ingestion.vault_sync import (
    FailureLog, Scan, VaultFile, VaultSettings, VaultSyncError, parse_folders, plan_pass, run_pass, scan_vault,
)
from app.models.client import EmbeddingResult, ModelClientError, ModelUsage


class FakeEmbeddingClient:
    """Deterministic vectors; texts containing `fail_on` raise like a provider error."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls = 0
        self.billable_requests = []

    def embed(self, *, model, texts, dimensions=None, input_type=None):
        self.calls += 1
        if self.fail_on and any(self.fail_on in text for text in texts):
            raise ModelClientError("provider failed")
        usage = ModelUsage(input_tokens=sum(len(text.split()) for text in texts))
        self.billable_requests.append((model.model_id, usage))
        vectors = [[1.0] + [0.0] * (dimensions - 1) for _ in texts]
        return EmbeddingResult(vectors=vectors, model=model, usage=usage)


class VaultFixture:
    def __init__(self, base: Path, folders=("Knowledge",)) -> None:
        self.root = base / "vault"
        self.root.mkdir()
        self.settings = VaultSettings(source_root=self.root, root_alias="primary",
                                      included_folders=parse_folders(list(folders)),
                                      runtime_dir=base / "runs")

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class FolderAndScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = VaultFixture(Path(self.temp.name), folders=("Knowledge", "Learnings"))

    def test_folders_are_deduplicated_and_only_named_folders_are_included(self) -> None:
        self.assertEqual(parse_folders("Knowledge, knowledge ,Learnings"), ("Knowledge", "Learnings"))
        self.assertEqual(parse_folders("Knowledge"), ("Knowledge",))
        with self.assertRaises(VaultSyncError):
            parse_folders("Knowledge/Notes")

    def test_scan_reads_only_selected_markdown_and_skips_noise(self) -> None:
        self.vault.write("Knowledge/Notes/kept.md", "# Kept\n\nText.\n")
        self.vault.write("Inbox/new.md", "# New\n\nText.\n")
        self.vault.write("Career/private.md", "# Private\n\nNot selected.\n")
        self.vault.write("Knowledge/.obsidian/workspace.md", "x")
        self.vault.write("Knowledge/.trash/old.md", "x")
        self.vault.write("Knowledge/Notes/kept (Conflicted copy Laptop 202610161200).md", "x")
        self.vault.write("Knowledge/Notes/image.png", "x")
        self.vault.write("Knowledge/Notes/blank.md", "  \n")
        scan = scan_vault(self.vault.settings)
        paths = sorted(item.relative_path for item in scan.files.values())
        self.assertEqual(paths, ["Knowledge/Notes/blank.md", "Knowledge/Notes/kept.md"])
        self.assertTrue(next(item for item in scan.files.values() if item.relative_path.endswith("blank.md")).empty)
        self.assertEqual(scan.missing_folders, ["Learnings"])

    def test_missing_included_folder_trips_the_guard(self) -> None:
        self.vault.write("Knowledge/a.md", "# A\n\nText.\n")
        plan = plan_pass(self.vault.settings, scan_vault(self.vault.settings), {}, FailureLog(Path(self.temp.name) / "f.json"))
        self.assertIn("Learnings", plan.guard)

    def test_plan_retires_missing_files_and_holds_unselected_folders(self) -> None:
        settings = VaultSettings(self.vault.root, "primary", parse_folders("Knowledge"), Path(self.temp.name) / "runs")
        scan = Scan(files={"keep": VaultFile("Knowledge/a.md", Path("a"), "sha-a", 3, False)}, missing_folders=[], duplicates=[])
        active = {"keep": ("Knowledge/a.md", "sha-a"), "gone": ("Knowledge/b.md", "sha-b"),
                  "other": ("Career/c.md", "sha-c")}
        plan = plan_pass(settings, scan, active, FailureLog(Path(self.temp.name) / "f.json"))
        self.assertEqual(plan.unchanged, 1)
        self.assertEqual(plan.retire_missing, [("gone", "Knowledge/b.md")])
        self.assertEqual(plan.retire_unselected, [("other", "Career/c.md")])
        self.assertIn("no longer selected", plan.guard)

    def test_plan_guards_large_retirement(self) -> None:
        settings = VaultSettings(self.vault.root, "primary", parse_folders("Knowledge"), Path(self.temp.name) / "runs")
        active = {f"doc{i}": (f"Knowledge/{i}.md", "sha") for i in range(30)}
        plan = plan_pass(settings, Scan({}, [], []), active, FailureLog(Path(self.temp.name) / "f.json"))
        self.assertEqual(len(plan.retire_missing), 30)
        self.assertIn("would be retired", plan.guard)

    def test_dry_run_parses_and_estimates_without_a_database_or_model(self) -> None:
        for name in ("a", "b", "c"):
            self.vault.write(f"Knowledge/Notes/{name}.md", f"# {name}\n\nThe {name} orchard is ripe.\n")
        self.vault.write("Knowledge/Notes/broken.md", "---\ntitle: [unclosed\n---\n# Broken\n")
        report = run_pass(self.vault.settings)
        self.assertEqual(report["to_ingest"], 4)
        self.assertEqual(report["ready_to_embed"], 3)
        self.assertEqual(sum(report["parse_failures"].values()), 1)
        self.assertGreater(report["worst_case_usd"], 0)
        self.assertGreater(report["projected_db_bytes"], 0)
        self.assertFalse((self.vault.settings.state_dir / "failures.json").exists())

    def test_missing_vault_root_reads_and_retires_nothing(self) -> None:
        settings = VaultSettings(Path(self.temp.name) / "absent", "primary", parse_folders("Knowledge"),
                                 Path(self.temp.name) / "runs")
        report = run_pass(settings)
        self.assertIn("vault root is missing", report["guard"])

    def test_a_held_lock_stops_a_second_pass(self) -> None:
        self.vault.settings.state_dir.mkdir(parents=True)
        (self.vault.settings.state_dir / "pass.lock").write_text("1")
        self.vault.write("Knowledge/a.md", "# A\n\nText.\n")
        with self.assertRaises(VaultSyncError):
            run_pass(self.vault.settings)


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class LivePassTests(unittest.TestCase):
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
        for name in ("apple", "pear", "plum"):
            self.vault.write(f"Knowledge/Notes/{name}.md", f"# {name}\n\nThe {name} is ripe.\n")

    def live(self, client=None, **kwargs):
        client = client or FakeEmbeddingClient()
        report = run_pass(self.vault.settings, conn=self.conn, live=True, max_usd=1.0, client=client, **kwargs)
        return report, client

    def active_paths(self) -> list[str]:
        return [row[0] for row in self.conn.execute("""select d.relative_path from knowledge.active_runs a
            join knowledge.documents d using (document_id) order by 1""").fetchall()]

    def test_new_files_publish_and_an_unchanged_pass_makes_no_model_call(self) -> None:
        report, client = self.live()
        self.assertEqual((report["published"], report["selected_profile"] is not None), (3, True))
        self.assertEqual(len(self.active_paths()), 3)
        spend = self.conn.execute("select count(*), sum(estimated_usd) from knowledge.spend where source='indexing'").fetchone()
        self.assertEqual(spend[0], 3)
        self.assertGreater(spend[1], 0)
        second, client = self.live()
        self.assertEqual((second["unchanged"], second["to_ingest"], client.calls), (3, 0, 0))

    def test_editing_one_file_replaces_only_its_active_run(self) -> None:
        self.live()
        before = dict(self.conn.execute("select document_id, run_id from knowledge.active_runs").fetchall())
        self.vault.write("Knowledge/Notes/pear.md", "# pear\n\nThe pear is now soft.\n")
        report, client = self.live()
        after = dict(self.conn.execute("select document_id, run_id from knowledge.active_runs").fetchall())
        self.assertEqual((report["published"], client.calls), (1, 1))
        self.assertEqual(sum(before[key] != after[key] for key in before), 1)

    def test_deleting_or_emptying_a_file_retires_it(self) -> None:
        self.live()
        (self.vault.root / "Knowledge/Notes/plum.md").unlink()
        self.vault.write("Knowledge/Notes/pear.md", "\n")
        self.vault.write("Knowledge/Notes/new-empty.md", "")
        report, _ = self.live()
        # Two of three files is far over the 10% guard, so nothing goes without the flag.
        self.assertEqual((report["retired"], report["retire_missing"]), (0, 2))
        self.assertEqual(report["skipped_empty"], 1)
        report, _ = self.live(allow_mass_retire=True)
        self.assertEqual(report["retired"], 2)
        self.assertEqual(self.active_paths(), ["Knowledge/Notes/apple.md"])
        from app.storage.search import search_text
        self.assertEqual(search_text(self.conn, "plum"), [])

    def test_invalid_frontmatter_fails_once_until_the_file_changes(self) -> None:
        self.vault.write("Knowledge/Notes/broken.md", "---\ntitle: [unclosed\n---\n# Broken\n")
        report, _ = self.live()
        self.assertEqual(sum(report["parse_failures"].values()), 1)
        second, _ = self.live()
        self.assertEqual((second["skipped_failed"], second["to_ingest"]), (1, 0))
        self.vault.write("Knowledge/Notes/broken.md", "---\ntitle: Fixed\n---\n# Fixed\n\nNow fine.\n")
        third, _ = self.live()
        self.assertEqual(third["published"], 1)

    def test_a_failed_embedding_keeps_the_previous_run_and_others_publish(self) -> None:
        self.live()
        before = dict(self.conn.execute("select document_id, run_id from knowledge.active_runs").fetchall())
        self.vault.write("Knowledge/Notes/apple.md", "# apple\n\nThe apple is poison.\n")
        self.vault.write("Knowledge/Notes/pear.md", "# pear\n\nThe pear is crisp.\n")
        report, _ = self.live(FakeEmbeddingClient(fail_on="poison"))
        self.assertEqual((report["published"], report["ingest_failures"]), (1, {"ModelClientError": 1}))
        after = dict(self.conn.execute("select document_id, run_id from knowledge.active_runs").fetchall())
        self.assertEqual(sum(before[key] != after[key] for key in before), 1)
        self.assertEqual(len(after), 3)

    def test_spend_limit_stops_before_the_next_file(self) -> None:
        report = run_pass(self.vault.settings, conn=self.conn, live=True, max_usd=1e-9,
                          client=FakeEmbeddingClient())
        self.assertEqual((report["published"], report["stopped"]), (0, "pass spend limit reached"))

    def test_daily_cap_stops_the_pass(self) -> None:
        self.conn.execute("insert into knowledge.spend (source, estimated_usd) values ('answer', 2)")
        report, client = self.live()
        self.assertEqual((report["published"], report["stopped"], client.calls), (0, "daily spend cap reached", 0))

    def test_unselecting_a_folder_retires_nothing_without_the_flag(self) -> None:
        self.vault.write("Learnings/lesson.md", "# Lesson\n\nKeep going.\n")
        wider = VaultSettings(self.vault.root, "primary", parse_folders("Knowledge,Learnings"), self.vault.settings.runtime_dir)
        run_pass(wider, conn=self.conn, live=True, max_usd=1.0, client=FakeEmbeddingClient())
        report, _ = self.live()
        self.assertEqual((report["retired"], report["retire_unselected"]), (0, 1))
        self.assertIn("Learnings/lesson.md", self.active_paths())
        report, _ = self.live(allow_mass_retire=True)
        self.assertEqual(report["retired"], 1)
        self.assertNotIn("Learnings/lesson.md", self.active_paths())


if __name__ == "__main__":
    unittest.main()
