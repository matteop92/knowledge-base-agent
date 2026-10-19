from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

WEB = Path("app/web")


class ShellSourceTests(unittest.TestCase):
    def test_untrusted_text_is_never_inserted_as_html(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(unsafe, script)
        self.assertIn('url.startsWith("obsidian://")', script)

    def test_capture_and_worker_scripts_never_insert_html(self) -> None:
        for name in ("capture.js", "sw.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
                with self.subTest(name=name, unsafe=unsafe):
                    self.assertNotIn(unsafe, script)

    def test_the_app_is_a_share_target_for_screenshots_text_and_markdown(self) -> None:
        import json
        share = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))["share_target"]
        self.assertEqual((share["action"], share["method"], share["enctype"]), ("/share", "POST", "multipart/form-data"))
        self.assertIn("image/png", share["params"]["files"][0]["accept"])
        self.assertIn(".md", share["params"]["files"][0]["accept"])

    def test_page_has_no_inline_script(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertNotIn("onclick=", page)

    def test_page_has_no_inline_styles_and_scripts_set_none(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r"\sstyle=")
        for name in ("app.js", "capture.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn('setAttribute("style"', script)
                self.assertNotIn("cssText", script)

    def test_styles_use_only_local_fonts_and_no_remote_imports(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", css)
        self.assertNotIn("http", css)
        for url in re.findall(r"url\(([^)]+)\)", css):
            self.assertTrue(url.strip("'\" ").startswith("/static/fonts/"), url)
        self.assertIn("Newsreader", css)
        self.assertIn("Geist", css)

    def test_every_linked_static_file_is_in_the_offline_shell_list(self) -> None:
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        shell = re.search(r"const SHELL = \[(.*?)\];", worker, re.S).group(1)
        listed = set(re.findall(r'"([^"]+)"', shell))
        page = (WEB / "index.html").read_text(encoding="utf-8")
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        linked = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', page)) | set(re.findall(r"url\((/static/[^)]+)\)", css))
        self.assertTrue(linked)
        self.assertEqual(linked - listed, set())
        for path in linked:
            self.assertTrue((WEB / path.removeprefix("/static/")).is_file(), path)

    def test_navigation_is_two_tabs_with_names_and_one_back_handler(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'data-tab="ask"', page)), 2)
        self.assertEqual(len(re.findall(r'data-tab="add"', page)), 2)
        self.assertEqual(script.count('addEventListener("popstate"'), 1)
        self.assertNotIn("capture-close", page)
        self.assertNotIn('id="sidebar-add"', page)
        # Every icon-only button needs an accessible name.
        for match in re.finditer(r"<button([^>]*)>\s*<svg[^>]*>.*?</svg>\s*</button>", page, re.S):
            self.assertIn("aria-label", match.group(1))
        # The collapsed sidebar hides text labels, so its tab and action buttons keep their own names.
        for match in re.finditer(r"<button([^>]*class=\"(?:tab|side-action)[^>]*)>", page):
            self.assertIn("aria-label", match.group(1))


@unittest.skipUnless(TestClient, "needs fastapi")
class StaticRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.api.main import create_app
        from tests.api.test_api import FakeAuth, FakeEngine, settings

        def no_database():
            raise AssertionError("static routes must not touch the database")

        self.client = TestClient(create_app(settings(), connect=no_database, auth=FakeAuth(), engine=FakeEngine()))

    def test_shell_manifest_and_worker_are_served_with_csp(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        manifest = self.client.get("/manifest.webmanifest")
        self.assertIn("application/manifest+json", manifest.headers["content-type"])
        worker = self.client.get("/sw.js")
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/icons/icon-192.png").status_code, 200)

    def test_font_files_are_served_from_static(self) -> None:
        fonts = sorted((WEB / "fonts").glob("*.woff2"))
        self.assertTrue(fonts)
        for font in fonts:
            with self.subTest(font=font.name):
                self.assertEqual(self.client.get(f"/static/fonts/{font.name}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
