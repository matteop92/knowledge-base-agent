"""A YouTube link becomes a transcript note in Knowledge/Transcripts.

Title and channel come from YouTube's public oEmbed endpoint, not from the model. The transcript uses the
bold-speaker-label format the transcript parser routes. It comes from YouTube's own captions when they can be
used (app/capture/captions.py) and from Gemini otherwise; only the public link goes to Gemini.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, parse, request

from app.capture.notes import CaptureError, Draft, MAX_TEXT_BYTES, clean_tags, clean_title
from app.models.gemini import GeminiClient, GeminiError

PROMPT_VERSION = "youtube_transcript_v1"
PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
OEMBED = "https://www.youtube.com/oembed?format=json&url="
FIRST_LABEL = re.compile(r"\*\*[^*\n]{1,80}:\*\*")


def video_url(link: str) -> str:
    """The canonical watch URL for a YouTube link, or a CaptureError if it is not one."""
    link = link.strip()
    if not re.match(r"^https?://", link, re.IGNORECASE):
        link = "https://" + link
    parsed = parse.urlparse(link)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    video = None
    if host == "youtu.be":
        video = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            video = (parse.parse_qs(parsed.query).get("v") or [None])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None
    if parsed.scheme not in ("http", "https") or not video or not VIDEO_ID.match(video):
        raise CaptureError("this is not a YouTube video link")
    return f"https://www.youtube.com/watch?v={video}"


def video_details(url: str, timeout: float = 30) -> dict:
    """Title and channel from YouTube itself; private or missing videos fail here, before any model call."""
    try:
        with request.urlopen(OEMBED + parse.quote(url, safe=""), timeout=timeout) as response:
            data = json.load(response)
    except error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise CaptureError("this video is private or unavailable") from None
        raise CaptureError("YouTube could not be reached; try again") from None
    except (error.URLError, TimeoutError, OSError, ValueError):
        raise CaptureError("YouTube could not be reached; try again") from None
    return {"title": data.get("title"), "channel": data.get("author_name")}


def clean_transcript(text: str) -> str:
    """Drop anything before the first speaker label so the transcript parser routes it."""
    match = FIRST_LABEL.search(text)
    if match is None:
        raise CaptureError("the transcript came back without speaker labels; try again")
    return text[match.start():].strip()


def transcribe_youtube(link: str, client: GeminiClient, *, title: str | None = None, tags: list[str] | None = None,
                       details=video_details, captions=None) -> Draft:
    """captions(video_id, title, channel) returns a bold-speaker transcript from YouTube's own captions, or None
    to fall back to Gemini, which transcribes the whole video."""
    url = video_url(link)
    info = details(url)
    body = captions(url.rsplit("=", 1)[1], str(info.get("title") or ""), str(info.get("channel") or "")) if captions else None
    if body:
        body = clean_transcript(body)
    else:
        try:
            result = client.transcribe(url, PROMPT)
        except GeminiError as exc:
            raise CaptureError(str(exc)) from None
        body = clean_transcript(result.text)
        if result.finish_reason == "MAX_TOKENS":
            body += "\n\n*(Transcript cut off: the video is longer than one request can transcribe.)*"
    body = body.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
    own = {"source": "YouTube"}
    if info.get("channel"):
        own["author"] = str(info["channel"])[:200]
    return Draft(kind="youtube", body=body, title=clean_title(title) or clean_title(info.get("title")) or "YouTube video",
                 own=own, url=url, tags=clean_tags(tags))
