from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
