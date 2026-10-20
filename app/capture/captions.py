"""YouTube's own auto-generated captions become a bold-speaker transcript, without any video model.

Auto captions mark each change of speaker with ">>". A text model on a pinned zero-retention route names the
speaker of each numbered turn; it never rewrites the words, which come from YouTube unchanged. Only the public
captions, title, channel and description are sent. When a video has no English auto captions, YouTube refuses the request
(it may block cloud servers) or no model can name the turns, the caller falls back to Gemini.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib import request

from app.models.client import ModelSpec
from app.models.routes import price_usd

log = logging.getLogger("knowledge.capture.captions")

PROMPT_VERSION = "youtube_speakers_v1"
PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
# DeepSeek agreed with Gemini's speakers on 66 of 70 turns in a 46-minute test, Luna on 63; both cost under a cent.
DEFAULT_MODELS = ("deepseek/deepseek-v4.1-flash", "openai/gpt-6-luna")
LANGUAGES = ("en", "en-US", "en-GB")
TURN_PREVIEW_CHARS = 1500  # the start of a turn is enough to tell who speaks
CHUNK_CHARS = 60_000
NAME_TOKENS = 20  # per turn: the number, the name and JSON punctuation
MAX_MISSING = 0.1
REQUEST_TIMEOUT = 30
# Naming stops after this, so the Gemini fallback (10 minutes at most) still fits the capture job's time limit.
CAPTION_BUDGET_SECONDS = 240
MONOLOGUE_CHARS = 3 * 1500
DESCRIPTION_CHARS = 2000
DESCRIPTION = re.compile(r'"shortDescription":"((?:[^"\\]|\\.)*)"')
NOISE = re.compile(r"\[(?:music|applause|laughter|__)\]\s*", re.IGNORECASE)
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["people", "names"],
          "properties": {"people": {"type": "array", "items": {"type": "string"}},
                         "names": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False, "required": ["turn", "name"],
                             "properties": {"turn": {"type": "integer"}, "name": {"type": "string"}}}}}}


class CaptionsUnavailable(Exception):
    """No usable auto captions; the message says why, for the server log."""


def speaker_models() -> list[str]:
    configured = os.getenv("KNOWLEDGE_SPEAKER_MODELS")
    return [item.strip() for item in configured.split(",") if item.strip()] if configured else list(DEFAULT_MODELS)


def fetch_caption_text(video_id: str) -> str:
    """English auto captions as one string, through youtube-transcript-api."""
    import requests
    from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

    class TimeoutSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", REQUEST_TIMEOUT)
            return super().request(*args, **kwargs)

    try:
        transcripts = YouTubeTranscriptApi(http_client=TimeoutSession()).list(video_id)
        # Auto captions carry the ">>" speaker marks; uploaded captions usually do not.
        fetched = transcripts.find_generated_transcript(list(LANGUAGES)).fetch()
    except YouTubeTranscriptApiException as exc:
        raise CaptionsUnavailable(type(exc).__name__) from None
    except requests.RequestException as exc:
        raise CaptionsUnavailable(type(exc).__name__) from None
    return " ".join(snippet.text.replace("\n", " ") for snippet in fetched)


def fetch_description(video_id: str) -> str:
    """The video's description from its public page, for spelling names; empty when it cannot be read."""
    try:
        page = request.urlopen(request.Request(f"https://www.youtube.com/watch?v={video_id}",
                                               headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}),
                               timeout=REQUEST_TIMEOUT).read(3_000_000).decode("utf-8", "ignore")
        match = DESCRIPTION.search(page)
        return json.loads(f'"{match.group(1)}"')[:DESCRIPTION_CHARS] if match else ""
    except Exception:  # noqa: BLE001 - the description only helps spell names
        return ""


def caption_turns(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", NOISE.sub("", text))
    return [turn.strip() for turn in text.split(">>") if turn.strip()]


def chunks(turns: list[str]) -> list[list[str]]:
    groups: list[list[str]] = [[]]
    size = 0
    for turn in turns:
        length = min(len(turn), TURN_PREVIEW_CHARS) + 8
        if groups[-1] and size + length > CHUNK_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(turn)
        size += length
    return groups


def request_text(title: str, channel: str, turns: list[str], people: list[str], description: str = "") -> str:
    about = f"Description:\n{description}\n" if description else ""
    known = f"Names already found: {', '.join(people)}\n" if people else ""
    lines = (f"{number}. {turn[:TURN_PREVIEW_CHARS]}{' ...' if len(turn) > TURN_PREVIEW_CHARS else ''}"
             for number, turn in enumerate(turns, 1))
    return f"Title: {title}\nChannel: {channel}\n{about}{known}\nTurns:\n" + "\n".join(lines)


def output_tokens(turns: list[str]) -> int:
    return NAME_TOKENS * len(turns) + 200


def request_bytes(title: str, channel: str, group: list[str]) -> int:
    # Earlier chunks' names are repeated in later requests; 2,000 bytes bounds that list. A description of
    # DESCRIPTION_CHARS characters is at most four bytes each.
    return (len(PROMPT.encode("utf-8")) + len(request_text(title, channel, group, []).encode("utf-8"))
            + 2000 + 4 * DESCRIPTION_CHARS)


def input_bytes(title: str, channel: str, turns: list[str]) -> int:
    return sum(request_bytes(title, channel, group) for group in chunks(turns))


def worst_case_usd(model_id: str, title: str, channel: str, turns: list[str]) -> float:
    """Every byte of every request counted as a token, plus each request's full completion allowance."""
    from app.models.openrouter import completion_limit

    total = 0.0
    for group in chunks(turns):
        _, completion = completion_limit(model_id, PROMPT_VERSION, output_tokens(group), None)
        total += price_usd(model_id, request_bytes(title, channel, group), completion)
    return total


def clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = re.sub(r"[*:\n]", " ", value)
    name = re.sub(r"\s+", " ", name).strip()[:80]
    return name or None


def name_turns(turns: list[str], title: str, channel: str, client, model: ModelSpec, description: str = "") -> list[str]:
    """One speaker name per turn; raises ValueError when the model leaves too many turns unnamed."""
    names: list[str] = []
    people: list[str] = []
    for group in chunks(turns):
        result = client.generate_json(model=model, system_prompt=PROMPT,
                                      input_text=request_text(title, channel, group, people, description),
                                      schema_name=PROMPT_VERSION, json_schema=SCHEMA,
                                      max_output_tokens=output_tokens(group))
        data = result.data if isinstance(result.data, dict) else {}
        by_turn = {entry.get("turn"): clean_name(entry.get("name"))
                   for entry in data.get("names") or [] if isinstance(entry, dict)}
        labels = [by_turn.get(number) for number in range(1, len(group) + 1)]
        if sum(label is None for label in labels) > MAX_MISSING * len(group):
            raise ValueError("too many turns came back without a speaker")
        for label in labels:
            # Turns alternate, so a turn left unnamed is more likely the speaker from two turns back.
            names.append(label or (names[-2] if len(names) > 1 else "Unknown speaker"))
        people = list(dict.fromkeys(people + [clean_name(person) for person in data.get("people") or []
                                              if clean_name(person)] + names))[:20]
    return names


@dataclass
class CaptionTranscriber:
    """Captions plus speaker names, trying each model in turn. cost_usd is what the model calls cost.

    approve(worst_case_usd) raises when a call would pass the daily cap; source says which way the transcript went.
    """

    approve: Callable[[float], None] = lambda usd: None
    fetch: Callable[[str], str] | None = None  # fetch_caption_text
    describe: Callable[[str], str] | None = None  # fetch_description
    models: list[str] = field(default_factory=speaker_models)
    cost_usd: float = 0.0
    source: str = "not tried"
    backup: str | None = None

    def transcript(self, video_id: str, title: str, channel: str) -> str | None:
        """A bold-speaker transcript, or None when the caller should fall back to Gemini."""
        try:
            turns = caption_turns((self.fetch or fetch_caption_text)(video_id))
        except Exception as exc:  # noqa: BLE001 - any caption failure (blocked, missing, unparsable) means Gemini
            self.source = f"gemini (captions: {exc if isinstance(exc, CaptionsUnavailable) else type(exc).__name__})"
            return None
        if not turns:
            self.source = "gemini (captions: empty)"
            return None
        if len(turns) == 1 and len(turns[0]) > MONOLOGUE_CHARS:
            # A long video with no speaker marks may still have several speakers, and Gemini can tell the voices
            # apart, so it goes first; the captions under one name are kept in case Gemini fails.
            body = self._named(video_id, title, channel, turns)
            self.backup = body or f"**{clean_name(channel) or 'Speaker 1'}:** {turns[0]}"
            self.source = "gemini (captions: no speaker marks)"
            return None
        return self._named(video_id, title, channel, turns)

    def fallback(self) -> str | None:
        """The captions under one speaker name when Gemini failed on a video without speaker marks."""
        if self.backup:
            self.source = "captions, one speaker (gemini failed)"
        return self.backup

    def _named(self, video_id: str, title: str, channel: str, turns: list[str]) -> str | None:
        from app.ingestion.vault_sync import measured_cost, openrouter_client, usage_mark
        from app.models.routes import PRICE_CEILINGS
        from app.storage.spend import SpendCapError

        description = (self.describe or fetch_description)(video_id)
        deadline = time.monotonic() + CAPTION_BUDGET_SECONDS
        for model_id in self.models:
            if model_id not in PRICE_CEILINGS or time.monotonic() >= deadline:
                continue
            worst = worst_case_usd(model_id, title, channel, turns)
            try:
                # Money a failed earlier model already cost in this job is not in the ledger yet.
                self.approve(worst + self.cost_usd)
            except SpendCapError:
                self.source = "gemini (captions: daily cap)"
                return None
            model = ModelSpec(provider="openrouter", model_id=model_id, model_version="current")
            client = mark = None
            names = None
            try:
                client = openrouter_client([model_id], requests=len(chunks(turns)),
                                           input_bytes=input_bytes(title, channel, turns), structured={model_id})
                client.set_deadline(deadline)
                mark = usage_mark(client)
                names = name_turns(turns, title, channel, client, model, description)
            except Exception as exc:  # noqa: BLE001 - any failure moves on to the next model, then Gemini
                log.warning("youtube speaker naming failed model=%s error=%s", model_id, type(exc).__name__)
            finally:
                if client is not None:
                    self.cost_usd += measured_cost(client, mark, worst)
            if names is not None:
                self.source = f"captions ({model_id})"
                return "\n\n".join(f"**{name}:** {turn}" for name, turn in zip(names, turns))
        self.source = "gemini (captions: speakers not named)"
        return None
