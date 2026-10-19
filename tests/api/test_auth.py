from __future__ import annotations

import time
import unittest
from dataclasses import replace

import jwt

from app.api.auth import AuthError, RateLimiter, SupabaseAuth
from tests.api.test_api import USER, settings

SECRET = "test-secret-with-enough-length-for-hs256"


def token(**overrides) -> str:
    claims = {"sub": USER, "aud": "authenticated", "iss": "https://example.supabase.co/auth/v1",
              "exp": int(time.time()) + 300}
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


class TokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = SupabaseAuth(replace(settings(), supabase_jwt_secret=SECRET))

    def test_allowed_user_token_is_accepted(self) -> None:
        self.assertEqual(self.auth.user_id(token()), USER)

    def test_other_user_is_forbidden(self) -> None:
        with self.assertRaises(AuthError) as caught:
            self.auth.user_id(token(sub="22222222-2222-2222-2222-222222222222"))
        self.assertEqual(caught.exception.status, 403)

    def test_expired_wrong_audience_wrong_issuer_and_bad_signature_are_unauthorised(self) -> None:
        bad = [token(exp=int(time.time()) - 10), token(aud="anon"), token(iss="https://other.example/auth/v1"),
               jwt.encode({"sub": USER, "aud": "authenticated", "iss": "https://example.supabase.co/auth/v1",
                           "exp": int(time.time()) + 300}, "another-secret-of-enough-length-xx", algorithm="HS256")]
        for value in bad:
            with self.subTest(token=value[:20]), self.assertRaises(AuthError) as caught:
                self.auth.user_id(value)
            self.assertEqual(caught.exception.status, 401)


class SigningKeyTests(unittest.TestCase):
    """Projects on asymmetric keys: tokens are checked against the published signing key."""

    def setUp(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.auth = SupabaseAuth(settings())
        self.auth._jwks.get_signing_key_from_jwt = lambda _: type("Key", (), {"key": self.private_key.public_key()})

    def claims(self) -> dict:
        return {"sub": USER, "aud": "authenticated", "iss": "https://example.supabase.co/auth/v1",
                "exp": int(time.time()) + 300}

    def test_es256_token_from_the_signing_key_is_accepted(self) -> None:
        self.assertEqual(self.auth.user_id(jwt.encode(self.claims(), self.private_key, algorithm="ES256")), USER)

    def test_a_shared_secret_token_is_refused_when_signing_keys_are_in_use(self) -> None:
        with self.assertRaises(AuthError) as caught:
            self.auth.user_id(jwt.encode(self.claims(), SECRET, algorithm="HS256"))
        self.assertEqual(caught.exception.status, 401)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class SupabaseCallTests(unittest.TestCase):
    def auth(self, reply):
        self.requests = []

        def opener(request, timeout):
            import json
            self.requests.append((request.full_url, json.loads(request.data)))
            if isinstance(reply, Exception):
                raise reply
            return FakeResponse(reply)

        return SupabaseAuth(replace(settings(), supabase_jwt_secret=SECRET), opener=opener)

    def test_sending_a_code_never_creates_an_account(self) -> None:
        self.auth(b"{}").send_code("matt@example.com")
        url, body = self.requests[0]
        self.assertTrue(url.endswith("/auth/v1/otp"))
        self.assertIs(body["create_user"], False)

    def test_supabase_errors_map_to_sign_in_replies(self) -> None:
        from urllib import error
        cases = [(error.HTTPError("u", 400, "bad", {}, None), 401), (error.HTTPError("u", 500, "down", {}, None), 502),
                 (error.URLError("offline"), 502)]
        for exc, status in cases:
            with self.subTest(status=status), self.assertRaises(AuthError) as caught:
                self.auth(exc).verify_code("matt@example.com", "123456")
            self.assertEqual(caught.exception.status, status)

    def test_a_verify_reply_without_a_token_is_refused(self) -> None:
        with self.assertRaises(AuthError) as caught:
            self.auth(b'{"user": {}}').verify_code("matt@example.com", "123456")
        self.assertEqual(caught.exception.status, 401)

    def test_a_verified_token_returns_the_allowed_user(self) -> None:
        reply = ('{"access_token": "%s"}' % token()).encode()
        self.assertEqual(self.auth(reply).verify_code("matt@example.com", "123456"), USER)


class AnswerCostTests(unittest.TestCase):
    def test_default_answer_budget_fits_under_the_per_answer_limit(self) -> None:
        from app.api.answers import OpenRouterAnswerEngine
        engine = OpenRouterAnswerEngine(settings(), connect=lambda: None)
        worst = engine.worst_case_usd()
        self.assertGreater(worst, 0)
        self.assertLessEqual(worst, settings().answer.max_usd_per_answer)

    def test_unknown_or_missing_usage_is_flagged(self) -> None:
        from decimal import Decimal
        from app.agent.endpoints import AnswerRoutes, measured_usd
        from app.models.client import ModelUsage

        routes = AnswerRoutes({"pricing": {"prompt": "0.000000004"}}, {}, Decimal("0.00000025"), Decimal("0.000002"))
        client = type("Client", (), {})()
        client.billable_requests = [("gen", ModelUsage(input_tokens=1000, output_tokens=100)),
                                    ("embed", ModelUsage(input_tokens=500))]
        client.request_timings = [("gen", 1.0), ("embed", 0.2)]
        cost, known = measured_usd(client, routes, model="gen", embedding_model="embed")
        self.assertTrue(known)
        self.assertEqual(cost, Decimal("0.00025") + Decimal("0.0002") + Decimal("0.000002"))
        client.request_timings.append(("gen", 60.0))  # a request that timed out has no usage record
        self.assertFalse(measured_usd(client, routes, model="gen", embedding_model="embed")[1])

    def test_a_follow_up_rewrite_is_priced_at_its_own_rate(self) -> None:
        from decimal import Decimal
        from app.agent.endpoints import AnswerRoutes, measured_usd
        from app.models.client import ModelUsage

        routes = AnswerRoutes({"pricing": {"prompt": "0"}}, {}, Decimal("0.00000025"), Decimal("0.000002"))
        client = type("Client", (), {})()
        client.billable_requests = [("rewrite", ModelUsage(input_tokens=1000, output_tokens=20))]
        client.request_timings = [("rewrite", 0.5)]
        cost, known = measured_usd(client, routes, model="gen", embedding_model="embed",
                                   extra_prices={"rewrite": (Decimal("0.0000001"), Decimal("0.0000005"))})
        self.assertTrue(known)
        self.assertEqual(cost, Decimal("0.0001") + Decimal("0.00001"))


class RateLimiterTests(unittest.TestCase):
    def test_limit_per_address(self) -> None:
        limiter = RateLimiter(limit=2, window_seconds=60)
        limiter.check("a")
        limiter.check("a")
        limiter.check("b")
        with self.assertRaises(AuthError):
            limiter.check("a")


if __name__ == "__main__":
    unittest.main()
