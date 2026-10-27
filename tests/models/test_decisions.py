import io
import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError

from app.agent.endpoints import measured_usd
from app.models.client import ModelClientError
from app.models.decisions import Choice, decide
from app.models.openrouter import OpenRouterClient
from app.models.routes import DECISION_CALL_WORST_USD, DECISION_MODEL, PRIVACY

QUESTIONS = {"route": {"type": "choice", "instructions": "Which?", "criteria": {"plain": "a", "catalog": "b"}},
             "by_date": {"type": "noul", "instructions": "Dated?"}}
ZDR = {"data": [{"model_id": DECISION_MODEL, "tag": "typesafe", "supported_parameters": [],
                 "pricing": {"prompt": "0.000000042", "completion": "0"}}]}


def answer(**usage):
    return {"answers": {"route": {"type": "choice", "choice": "catalog", "probabilities": {"plain": 0.02, "catalog": 0.98}},
                        "by_date": {"type": "noul", "noul": 0.15}}, "usage": usage}


def reply(data):
    return io.BytesIO(json.dumps(data).encode())


def client(requests=2):
    return OpenRouterClient(allow_live=True, request_budget=requests, estimated_input_token_budget=100_000,
                            conservative_input_budget=True, provider=dict(PRIVACY), api_key="k")


def fake_urlopen(post_reply, sent):
    def urlopen(http_request, timeout=None):
        if http_request.full_url.endswith("/endpoints/zdr"):
            return reply(ZDR)
        sent.append(json.loads(http_request.data))
        if isinstance(post_reply, Exception):
            raise post_reply
        return reply(post_reply)
    return urlopen


class DecisionTests(unittest.TestCase):
    def run_decide(self, post_reply, c=None):
        sent, c = [], c or client()
        with patch("app.models.openrouter.request.urlopen", fake_urlopen(post_reply, sent)):
            return decide(c, {"question": "How many books do I have?"}, QUESTIONS), sent, c

    def test_choice_and_noul_parse_into_typed_answers_with_cost(self):
        result, _, c = self.run_decide(answer(cost=0.000013944))
        self.assertEqual(result["route"], Choice("catalog", {"plain": 0.02, "catalog": 0.98}))
        self.assertEqual(result["by_date"], 0.15)
        self.assertEqual(c.decision_costs, [0.000013944])

    def test_every_request_denies_collection_and_requires_zdr(self):
        _, sent, _ = self.run_decide(answer(cost=0.00001))
        # Pinned to the endpoint whose route was verified, with no fallback to another provider.
        self.assertEqual(sent[0]["provider"], {"data_collection": "deny", "zdr": True, "only": ["typesafe"],
                                               "allow_fallbacks": False})
        self.assertEqual(sent[0]["model"], DECISION_MODEL)

    def test_a_missing_zero_retention_route_raises_before_any_question_is_sent(self):
        sent = []

        def urlopen(http_request, timeout=None):
            if http_request.full_url.endswith("/endpoints/zdr"):
                return reply({"data": []})
            sent.append(1)
            return reply(answer())

        with patch("app.models.openrouter.request.urlopen", urlopen), self.assertRaises(ModelClientError):
            decide(client(), {"question": "q"}, QUESTIONS)
        self.assertEqual(sent, [])

    def test_failures_return_none(self):
        http = HTTPError("u", 500, "err", {}, None)
        broken = answer()
        del broken["answers"]["by_date"]
        for name, post in (("http", http), ("timeout", TimeoutError()), ("missing", broken),
                           ("error", {"error": {"code": 500}}), ("bad choice", {"answers": {
                               **answer()["answers"], "route": {"choice": "other", "probabilities": {}}}})):
            with self.subTest(name):
                result, _, _ = self.run_decide(post)
                self.assertIsNone(result)

    def test_reported_cost_is_added_as_dollars_and_a_missing_one_is_booked_at_the_worst_case(self):
        _, _, reported = self.run_decide(answer(cost=0.00005))
        _, _, missing = self.run_decide(answer())
        _, _, failed = self.run_decide(TimeoutError())
        self.assertEqual(missing.decision_costs, [DECISION_CALL_WORST_USD])
        self.assertEqual(failed.decision_costs, [DECISION_CALL_WORST_USD])

        class Routes:
            prompt_price = completion_price = embedding_price = Decimal(0)

        measured, known = measured_usd(reported, Routes(), model="m", embedding_model="e")
        self.assertEqual((measured, known), (Decimal("0.00005"), True))

    def test_request_budget_of_one_allows_the_call_and_two_calls_are_refused(self):
        result, _, c = self.run_decide(answer(cost=0.00001), client(requests=1))
        self.assertIsNotNone(result)
        with patch("app.models.openrouter.request.urlopen", fake_urlopen(answer(cost=0.1), [])):
            self.assertIsNone(decide(c, {"question": "q"}, QUESTIONS))


if __name__ == "__main__":
    unittest.main()
