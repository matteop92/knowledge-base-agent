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


def action_v2(kind: str, **updates):
    data = action(kind)
    data.update({"version": 2, "offset": None, "need_updates": []})
    data.update(updates)
    return data


def action_v3(kind: str, **updates):
    return {**action_v2(kind), "version": 3, **updates}


class ActionsTest(unittest.TestCase):
    def test_v2_find_and_need_boundaries(self):
        self.assertEqual(AgentAction.model_validate(action_v2("find", query="later detail",
                                                       reference_id="E1")).action, "find")
        for value in (action_v2("find", query="term"),
                      action_v2("read", reference_id="E1", span_index=0, offset=-1),
                      action_v2("search", query="term", need_updates=[{
                          "id": "N2", "description": "later example", "status": "resolved",
                          "evidence_ids": [], "rationale": None}])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentAction.model_validate(value)
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

    def test_unused_fields_cannot_silently_change_action(self):
        for value in (action("read", reference_id="E1", span_index=0, query="other term"),
                      action("search", query="term", reference_id="E1"),
                      action("finish", answer="No support", completeness="unsupported",
                             sufficient=False, gaps=["No source"], query="term"),
                      action("read", reference_id="E1", span_index=0, answer="Claim")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentAction.model_validate(value)
