from __future__ import annotations

import unittest

from app.agent.actions import AgentAction


def action(kind: str, **updates):
    data = {"version": 1, "action": kind, "query": None, "reference_id": None,
            "span_index": None,
            "answer": None, "citation_ids": [], "completeness": None,
            "gaps": [], "sufficient": None}
    data.update(updates)
    return data


class ActionsTest(unittest.TestCase):
    def test_allowed_actions(self):
        self.assertEqual(AgentAction.model_validate(action("search", query="customers")).action, "search")
        self.assertEqual(AgentAction.model_validate(action("read", reference_id="E1", span_index=0)).action, "read")
        self.assertEqual(AgentAction.model_validate(action("finish", answer="Claim [E1.0]",
                            citation_ids=["E1.0"], completeness="complete", sufficient=True)).action, "finish")

    def test_bad_action_and_incomplete_finish(self):
        for value in (action("delete"), action("read"), action("finish", answer="Maybe",
                      completeness="partial", sufficient=False)):
            with self.assertRaises(ValueError):
                AgentAction.model_validate(value)
