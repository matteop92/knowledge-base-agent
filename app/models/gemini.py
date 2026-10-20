"""Gemini API client for YouTube transcripts: only a public video link and the prompt are sent.

Uses the free tier of the Gemini API (a Google AI Studio key in GEMINI_API_KEY). On the free tier Google may
use requests to improve its products, so nothing from the vault is ever sent here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import error, request

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiError(RuntimeError):
    """A failed Gemini request; the message is safe to show and never contains the key."""

    def __init__(self, message: str, *, quota: bool = False) -> None:
        super().__init__(message)
        self.quota = quota


@dataclass
class Transcription:
    text: str
    finish_reason: str | None
    usage: dict


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 600) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("KNOWLEDGE_YOUTUBE_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def transcribe(self, video_url: str, prompt: str, *, max_output_tokens: int = 65536) -> Transcription:
        if not self.api_key:
            raise GeminiError("YouTube capture is not set up: GEMINI_API_KEY is missing")
        payload = {
            "contents": [{"parts": [{"file_data": {"file_uri": video_url}}, {"text": prompt}]}],
            # Low resolution keeps video frames cheap; the audio carries the transcript.
            "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW", "maxOutputTokens": max_output_tokens},
        }
        http_request = request.Request(f"{BASE_URL}/models/{self.model}:generateContent",
                                       data=json.dumps(payload).encode("utf-8"), method="POST",
                                       headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                data = json.load(response)
        except error.HTTPError as exc:
            if exc.code == 429:
                raise GeminiError("Gemini's free daily limit was reached; try again later", quota=True) from None
            if exc.code in (400, 403, 404):
                raise GeminiError("Gemini could not open this video (it may be private, age-restricted or too long)") from None
            if exc.code >= 500:
                raise GeminiError("YouTube transcripts are busy right now; try again in a few minutes") from None
            raise GeminiError(f"Gemini returned HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            raise GeminiError("Gemini could not be reached; try again") from None
        try:
            candidate = data["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise GeminiError("Gemini returned no transcript for this video") from None
        return Transcription(text=text, finish_reason=candidate.get("finishReason"), usage=data.get("usageMetadata") or {})
