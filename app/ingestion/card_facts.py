"""What a source card shows, derived from a source's stored metadata. Pure: no I/O, no model call.

The vault is the authority and nothing here is written back to it. Rules that are presentation
(which platform, which name sits next to it) live here so they can change without a re-index.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.agent.properties import _names, source_type

CARD_TYPES = ("book", "highlights", "post", "note", "transcript", "article", "quote")
PLATFORM_HOSTS = {"linkedin.com": "linkedin", "x.com": "x", "twitter.com": "x",
                  "youtube.com": "youtube", "youtu.be": "youtube"}
PLATFORM_WORDS = {"linkedin": "linkedin", "x": "x", "twitter": "x", "x.com": "x", "youtube": "youtube",
                  "website": "website", "web": "website", "podcast": "podcast"}
PERSON_TYPES = ("post", "note", "transcript")


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _host(url: object) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower() if isinstance(url, str) else ""
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _platform_from_host(host: str | None) -> str | None:
    if not host:
        return None
    for known, platform in PLATFORM_HOSTS.items():
        if host == known or host.endswith("." + known):
            return platform
    return "website"


def card_type(generated: dict, relative_path: str, frontmatter: dict) -> str:
    found = source_type(generated, relative_path, frontmatter)
    found = "post" if found == "social_post" else found
    return found if found in CARD_TYPES else "note"


def card_facts(metadata: dict, relative_path: str, added: datetime | str | None = None) -> dict[str, Any]:
    """Type, date, platform, source name, authors, guests and book year. Unknown facts are left out."""
    frontmatter = metadata.get("frontmatter") if isinstance(metadata.get("frontmatter"), dict) else {}
    generated = metadata.get("generated") if isinstance(metadata.get("generated"), dict) else {}
    kind = card_type(generated, relative_path, frontmatter)
    url = _text(metadata.get("source_url"))
    host = _host(url)

    field = PLATFORM_WORDS.get((_text(frontmatter.get("platform")) or "").lower())
    platform = field or _platform_from_host(host)
    show = _text(frontmatter.get("show")) or (_text(frontmatter.get("podcast")) if
                                                 isinstance(frontmatter.get("podcast"), str) else None)
    channel = _text(frontmatter.get("channel"))
    authors = _names(metadata.get("authors"))
    guests = list(dict.fromkeys(_names(metadata.get("guests")) + _names(frontmatter.get("guest"))))

    flagged = (_text(frontmatter.get("type")) or _text(frontmatter.get("source_type")) or "").lower() == "podcast"
    if platform == "youtube" and not channel and len(authors) == 1 and _text(frontmatter.get("author")):
        # The capture writes the channel into `author`; it is the publisher, not a speaker.
        channel, authors = authors[0], []
    if channel:
        authors = [name for name in authors if name.casefold() != channel.casefold()]
    if show:
        authors = [name for name in authors if name.casefold() != show.casefold()]

    facts: dict[str, Any] = {"type": kind, "authors": authors, "guests": guests}
    if kind in PERSON_TYPES and (show or guests or flagged or platform == "podcast"):
        platform = "podcast"
    elif platform == "podcast" and kind not in PERSON_TYPES:
        platform = None
    if platform:
        facts["platform"] = platform
        name = show or channel or (host if kind == "article" or platform == "website" else None)
        if name is None and platform == "podcast":
            name = "Podcast"
        name = name or {"linkedin": "LinkedIn", "x": "X", "youtube": "YouTube"}.get(platform, host)
        if name:
            facts["source_name"] = name
    elif kind == "article" and host:
        facts["source_name"] = host

    dated = next((str(value)[:10] for value in (metadata.get("published"), metadata.get("date"),
                                               metadata.get("created"), generated.get("date")) if value), None)
    own_added = added.date().isoformat() if isinstance(added, datetime) else _text(added)
    if dated or own_added:
        facts["card_date"] = dated or own_added
    published = str(metadata.get("published") or "")[:4]
    if kind == "book" and published.isdigit():
        facts["book_year"] = published
    return facts
