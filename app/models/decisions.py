"""Fast structured decisions from TypeSafe's Jev model, through OpenRouter's `/systemone` endpoint.

One call asks every question about a piece of text in parallel. A `choice` question answers
with the chosen option and a probability per option; a `noul` (yes/no) question answers with
the probability of yes. Every call denies data collection and requires zero data retention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.models.client import ModelClientError
from app.models.routes import DECISION_CALL_WORST_USD, DECISION_MODEL, PRIVACY, TAGS, verify_routes

log = logging.getLogger("knowledge.models.decisions")


@dataclass(frozen=True)
class Choice:
    choice: str
    probabilities: dict[str, float]


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("probability out of range")
    return float(value)


def parse_answers(data: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Choice | float]:
    """Typed answers for every question asked; raises ValueError if one is missing or malformed."""
    answers = data["answers"]
    parsed: dict[str, Choice | float] = {}
    for name, question in questions.items():
        answer = answers[name]
        if question["type"] == "choice":
            probabilities = {key: _probability(value) for key, value in answer["probabilities"].items()}
            if answer["choice"] not in question["criteria"]:
                raise ValueError("choice is not one of the options")
            parsed[name] = Choice(answer["choice"], probabilities)
        else:
            parsed[name] = _probability(answer["noul"])
    return parsed


def decide(client, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Choice | float] | None:
    """Typed answers to `questions` about `state`, or None when the call fails.

    The zero-retention route is verified first and raises if it is not currently eligible, so
    nothing is sent over a route that lost its privacy terms.
    """
    verify_routes(client, [DECISION_MODEL])
    payload = {"model": DECISION_MODEL, "state": state, "questions": questions, "provider": {**PRIVACY, "only": [TAGS[DECISION_MODEL]], "allow_fallbacks": False}}
    try:
        data = client.decide(payload, worst_case_usd=DECISION_CALL_WORST_USD)
        return parse_answers(data, questions)
    except ModelClientError as exc:
        log.warning("decision call failed error=%s", exc)
    except (KeyError, TypeError, ValueError, AttributeError):
        log.warning("decision call returned an unusable answer")
    return None
