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
        self.auth(b"{}").send_code("user@example.com")
        url, body = self.requests[0]
        self.assertTrue(url.endswith("/auth/v1/otp"))
        self.assertIs(body["create_user"], False)

    def test_supabase_errors_map_to_sign_in_replies(self) -> None:
        from urllib import error
        cases = [(error.HTTPError("u", 400, "bad", {}, None), 401), (error.HTTPError("u", 500, "down", {}, None), 502),
                 (error.URLError("offline"), 502)]
        for exc, status in cases:
            with self.subTest(status=status), self.assertRaises(AuthError) as caught:
                self.auth(exc).verify_code("user@example.com", "123456")
            self.assertEqual(caught.exception.status, status)

    def test_a_verify_reply_without_a_token_is_refused(self) -> None:
        with self.assertRaises(AuthError) as caught:
            self.auth(b'{"user": {}}').verify_code("user@example.com", "123456")
        self.assertEqual(caught.exception.status, 401)

    def test_a_verified_token_returns_the_allowed_user(self) -> None:
        reply = ('{"access_token": "%s"}' % token()).encode()
        self.assertEqual(self.auth(reply).verify_code("user@example.com", "123456"), USER)


class AnswerCostTests(unittest.TestCase):
    def test_default_answer_budget_fits_under_the_per_answer_limit(self) -> None:
        from app.api.answers import OpenRouterAnswerEngine
        engine = OpenRouterAnswerEngine(settings(), connect=lambda: None)
        worst = engine.worst_case_usd()
        self.assertGreater(worst, 0)
        self.assertLessEqual(worst, settings().answer.max_usd_per_answer)

    def test_the_web_answer_limit_is_2500_and_the_worst_case_counts_its_full_allowance(self) -> None:
        from decimal import Decimal
        from app.agent.records import AnswerLimits
        from app.api.answers import OpenRouterAnswerEngine

        engine = OpenRouterAnswerEngine(settings(), connect=lambda: None)
        self.assertEqual(engine.limits.max_output_tokens, 2500)
        raised = engine.worst_case_usd()
        engine.limits = AnswerLimits()
        answer = settings().answer
        # Every allowed request at four times the visible limit, at the generation price ceiling.
        extra = Decimal(answer.request_budget * (2500 - 900) * 4) * answer.max_generation_usd_per_million / 1_000_000
        self.assertEqual(raised - engine.worst_case_usd(), extra)

    def test_the_web_time_limits_leave_room_for_a_full_length_reply(self) -> None:
        from unittest import mock
        from app.api import answers

        created = {}

        class RecordingClient:
            request_timings: list = []
            usage_log: list = []

            def __init__(self, **kwargs) -> None:
                created.update(kwargs)

            def set_deadline(self, deadline: float) -> None:
                pass

        def no_database():
            raise RuntimeError("no database")

        engine = answers.OpenRouterAnswerEngine(settings(), connect=no_database)
        with mock.patch("app.models.openrouter.OpenRouterClient", RecordingClient),                 self.assertRaises(answers.AnswerFailed):
            engine.answer("Question")
        # About 110 output tokens a second: a 10,000-token reply takes about 90 s.
        self.assertEqual(created["timeout_seconds"], 120)
        self.assertEqual(engine.limits.max_seconds, 300)

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

    def test_worst_case_includes_a_rewrite_at_its_full_completion_allowance(self) -> None:
        from decimal import Decimal
        from unittest import mock
        from app.api import answers
        from app.models.routes import price_usd

        engine = answers.OpenRouterAnswerEngine(settings(), connect=lambda: None)
        with mock.patch.object(answers, "REWRITE_MAX_OUTPUT_TOKENS", 0), \
                mock.patch.object(answers, "REWRITE_INPUT_CHARS", 0):
            without = engine.worst_case_usd()
        rewrite = Decimal(str(price_usd(answers.REWRITE_MODEL, answers.REWRITE_INPUT_CHARS,
                                        answers.REWRITE_MAX_OUTPUT_TOKENS * 4)))
        self.assertEqual(engine.worst_case_usd() - without, rewrite)

    def test_a_failed_rewrite_route_check_searches_with_the_follow_up_and_bills_nothing(self) -> None:
        from app.api.answers import rewrite_followup
        from app.models.client import ModelClientError

        class RefusingClient:
            def require_zdr_route(self, *args, **kwargs):
                raise ModelClientError("endpoint is not zero-retention")

            def generate_json(self, **kwargs):
                raise AssertionError("no rewrite call expected")

        prices: dict = {}
        recent = [{"question": "Who talks about hiring?", "answer": "Ferrari."}]
        self.assertIsNone(rewrite_followup(RefusingClient(), "And the second?", recent, prices))
        self.assertEqual(prices, {})

    def test_a_checked_rewrite_route_records_its_prices_and_returns_the_query(self) -> None:
        from decimal import Decimal
        from app.api.answers import REWRITE_MODEL, rewrite_followup
        from app.models.client import GenerationResult

        class Client:
            def require_zdr_route(self, model_id, tag, structured=False):
                return {"tag": tag, "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}

            def generate_json(self, **kwargs):
                return GenerationResult(data={"query": "What does Derek Sivers say about hiring?"},
                                        model=kwargs["model"])

        prices: dict = {}
        recent = [{"question": "Who talks about hiring?", "answer": "Ferrari and Sivers."}]
        self.assertEqual(rewrite_followup(Client(), "And the second?", recent, prices),
                         "What does Derek Sivers say about hiring?")
        # The route check reports dollars per million as floats, so allow float rounding.
        self.assertAlmostEqual(prices[REWRITE_MODEL][0], Decimal("1E-7"), places=20)
        self.assertAlmostEqual(prices[REWRITE_MODEL][1], Decimal("5E-7"), places=20)

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


class PropertyRouteTests(unittest.TestCase):
    def test_speed_settings_reach_the_answer_limits(self) -> None:
        from app.api.answers import OpenRouterAnswerEngine

        base = settings()
        engine = OpenRouterAnswerEngine(replace(base, answer=replace(base.answer, host_reads=6, coverage_checks=0,
                                                                     max_model_calls=8)), connect=lambda: None)
        self.assertEqual((engine.limits.host_reads, engine.limits.coverage_checks, engine.limits.max_model_calls),
                         (6, 0, 8))

    def test_the_router_call_is_in_the_worst_case_only_when_it_is_on(self) -> None:
        from app.api.answers import OpenRouterAnswerEngine

        base = settings()
        off = OpenRouterAnswerEngine(replace(base, answer=replace(base.answer, property_router=False)), connect=lambda: None)
        on = OpenRouterAnswerEngine(replace(base, answer=replace(base.answer, property_router=True)), connect=lambda: None)
        self.assertGreater(on.worst_case_usd(), off.worst_case_usd())
        self.assertLessEqual(on.worst_case_usd(), base.answer.max_usd_per_answer)

    def test_a_failed_router_route_check_sends_the_question_to_the_loop(self) -> None:
        from unittest import mock
        from app.api import answers
        from app.models.client import ModelClientError

        class RefusingClient:
            def require_zdr_route(self, *args, **kwargs):
                raise ModelClientError("endpoint is not zero-retention")

            def generate_json(self, **kwargs):
                raise AssertionError("no router call expected")

        prices: dict = {}
        with mock.patch("app.agent.properties.load_sources", return_value=[]):
            self.assertIsNone(answers.route_properties(RefusingClient(), None, "How many?", None, prices))
        self.assertEqual(prices, {})

    def test_a_property_question_is_answered_from_the_matches(self) -> None:
        from unittest import mock
        from app.agent.properties import PropertyQuery
        from app.api import answers
        from tests.agent.test_properties import SOURCES

        class Client:
            def require_zdr_route(self, model_id, tag, structured=False):
                return {"tag": tag, "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}

        retriever = mock.MagicMock()
        retriever.read.side_effect = lambda hit, span: mock.MagicMock()
        prices: dict = {}
        with mock.patch("app.agent.properties.load_sources", return_value=SOURCES), \
                mock.patch("app.agent.properties.route_model", return_value=PropertyQuery(count=True, authors=["Ada Stone"])), \
                mock.patch("app.agent.properties.answer_properties", wraps=None) as answer:
            answer.return_value = "answered"
            self.assertEqual(answers.route_properties(Client(), None, "How many by Tim?", retriever, prices), "answered")
        self.assertEqual(answer.call_args.args[1].authors, ["Ada Stone"])
        self.assertEqual(answer.call_args.kwargs["model_calls"], 1)
        self.assertIn("openai/gpt-6-luna", prices)
        with mock.patch("app.agent.properties.load_sources", return_value=SOURCES), \
                mock.patch("app.agent.properties.route_model", return_value=None):
            self.assertIsNone(answers.route_properties(Client(), None, "What does Tim say?", retriever, {}))
