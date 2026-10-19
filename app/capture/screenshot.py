"""Screenshots (tweets, LinkedIn posts, book pages, articles) become text notes.

A vision model on a pinned zero-retention route copies out the visible text and, when shown, the author,
platform and posting date. Only the extracted text is saved; the image is never stored.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from app.capture.notes import CaptureError, Draft, MAX_TEXT_BYTES, _clean_tags, _clean_title, _first_line_title
from app.models.client import ModelClient, ModelSpec
from app.models.routes import price_usd

PROMPT_VERSION = "screenshot_extract_v1"
PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
DEFAULT_MODEL = "openai/gpt-6-luna"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Base64 grows data by a third; the prefix adds a few bytes.
MAX_DATA_URL_CHARS = MAX_IMAGE_BYTES * 4 // 3 + 64
MAX_OUTPUT_TOKENS = 3000
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text", "title", "author", "platform", "posted"],
          "properties": {"text": {"type": "string"}, "title": {"type": ["string", "null"]},
                         "author": {"type": ["string", "null"]}, "platform": {"type": ["string", "null"]},
                         "posted": {"type": ["string", "null"]}}}


def model_spec() -> ModelSpec:
    return ModelSpec(provider="openrouter", model_id=os.getenv("KNOWLEDGE_VISION_MODEL", DEFAULT_MODEL),
                     model_version="current")


def input_bytes(image: str) -> int:
    return len(PROMPT.encode("utf-8")) + len(image) + 200


def worst_case_usd(image: str, model: ModelSpec | None = None) -> float:
    """Every byte of the request counted as a token, plus the full completion allowance."""
    from app.models.openrouter import completion_limit

    model = model or model_spec()
    _, output_tokens = completion_limit(model.model_id, PROMPT_VERSION, MAX_OUTPUT_TOKENS, None)
    return price_usd(model.model_id, input_bytes(image), output_tokens)


def _optional(value: object, limit: int) -> str | None:
    text = " ".join(value.split())[:limit] if isinstance(value, str) else ""
    return text or None


def extract_screenshot(image: str, client: ModelClient, model: ModelSpec | None = None, *,
                       title: str | None = None, tags: list[str] | None = None) -> Draft:
    if len(image) > MAX_DATA_URL_CHARS:
        raise CaptureError("the image is too large")
    model = model or model_spec()
    result = client.generate_json(model=model, system_prompt=PROMPT, input_text="Screenshot to transcribe.",
                                  schema_name=PROMPT_VERSION, json_schema=SCHEMA, max_output_tokens=MAX_OUTPUT_TOKENS,
                                  images=[image])
    data = result.data if isinstance(result.data, dict) else {}
    text = data.get("text").strip() if isinstance(data.get("text"), str) else ""
    if not text:
        raise CaptureError("no readable text was found in the screenshot")
    text = text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
    own: dict[str, str] = {}
    author = _optional(data.get("author"), 200)
    if author:
        own["author"] = author
    platform = _optional(data.get("platform"), 40)
    if platform:
        own["platform"] = platform
    posted = _optional(data.get("posted"), 10)
    if posted:
        try:
            own["published"] = date.fromisoformat(posted).isoformat()
        except ValueError:
            pass
    return Draft(kind="screenshot", body=text,
                 title=_clean_title(title) or _clean_title(_optional(data.get("title"), 200)) or _first_line_title(text),
                 own=own, tags=_clean_tags(tags))
