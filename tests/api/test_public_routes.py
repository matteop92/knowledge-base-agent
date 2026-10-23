"""Sign-in and request-shape checks that need no database."""

from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - fastapi is an optional server dependency
    TestClient = None

from app.api.auth import AuthError
from tests.api.test_api import EMAIL, FakeAuth, FakeEngine, settings


def no_database():
    raise AssertionError("this route must not touch the database")


@unittest.skipUnless(TestClient, "needs fastapi")
class PublicRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.api.main import create_app
        self.auth = FakeAuth()
        self.client = TestClient(create_app(settings(), connect=no_database, auth=self.auth, engine=FakeEngine()))

    def test_the_tag_routes_need_a_session_including_for_tags_with_a_slash(self):
        # 401 (not 404) shows the route matched: a tag such as "a/b" is a valid name and must reach the handler.
        for path in ("/api/tags/decision-making/approve", "/api/tags/a%2Fb/approve", "/api/tags/a%2Fb/block"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/tags").status_code, 401)
        self.assertEqual(self.client.get("/api/starters").status_code, 401)

    def test_codes_go_only_to_the_allowed_address(self):
        self.client.post("/auth/code", json={"email": "someone@example.com"})
        self.client.post("/auth/code", json={"email": "user@example.com"})
        self.assertEqual(self.auth.sent, [EMAIL])

    def test_a_wrong_address_and_a_wrong_code_get_the_same_reply(self):
        wrong_code = self.client.post("/auth/verify", json={"email": EMAIL, "code": "999999"})
        wrong_address = self.client.post("/auth/verify", json={"email": "x@example.com", "code": "111111"})
        self.assertEqual((wrong_code.status_code, wrong_code.json()), (wrong_address.status_code, wrong_address.json()))
        self.assertEqual(wrong_code.status_code, 401)
        # A valid code for another account is still refused by the user-id allowlist.
        self.assertEqual(self.client.post("/auth/verify", json={"email": EMAIL, "code": "222222"}).status_code, 403)

    def test_sign_in_is_rate_limited_per_address_even_with_spoofed_forwarding(self):
        codes = [self.client.post("/auth/code", json={"email": EMAIL},
                                  headers={"X-Forwarded-For": f"10.0.0.{index}, 203.0.113.9"}).status_code
                 for index in range(7)]
        self.assertIn(429, codes)

    def test_code_guessing_is_limited_across_all_addresses(self):
        codes = [self.client.post("/auth/verify", json={"email": EMAIL, "code": "999999"},
                                  headers={"X-Forwarded-For": f"203.0.113.{index}"}).status_code
                 for index in range(12)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10:], [429, 429])

    def test_a_sign_in_service_error_gets_the_same_reply(self):
        def broken(email):
            raise AuthError(502, "sign-in service unavailable")
        self.auth.send_code = broken
        self.assertEqual(self.client.post("/auth/code", json={"email": EMAIL}).status_code, 200)

    def test_invalid_requests_use_the_common_error_shape_without_echoing_input(self):
        response = self.client.post("/auth/verify", json={"email": EMAIL, "code": "abc<script>"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(list(response.json()), ["error"])
        self.assertIn("code", response.json()["error"])
        self.assertNotIn("<script>", response.text)

    def test_security_headers_are_sent(self):
        headers = self.client.post("/auth/code", json={"email": "nobody@example.com"}).headers
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
