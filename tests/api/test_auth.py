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
