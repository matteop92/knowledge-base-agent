"""After an answer: does a sentence credit words to someone who did not say them?

A transcript passage carries its speaker. A sentence that names a speaker of a cited transcript
("Tim notes that...") while every passage it cites was said by someone else is flagged. The check
only flags: it never rewrites the answer. Pronouns are out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.storage.records import SourcePassage

CITATION = re.compile(r"\[(E\d+\.\d+(?:@\d+)?)\]")
# Citation markers written after a sentence's full stop belong to that sentence.
TRAILING_MARKERS = re.compile(r"([.!?])((?:\s*\[E\d+\.\d+(?:@\d+)?\])+)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MARKUP = re.compile(r"[*_`#>]")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
MIN_FIRST_NAME = 3


@dataclass(frozen=True)
class Flag:
    sentence: str
    named: list[str]
    cited_speakers: list[str]
    citation_ids: list[str]

    def record(self) -> dict:
        return {"sentence": self.sentence, "named": self.named, "cited_speakers": self.cited_speakers,
                "citation_ids": self.citation_ids}

    def gap(self) -> str:
        said = ", ".join(self.cited_speakers) or "someone else"
        return (f"A sentence credits words to {', '.join(self.named)}, but the passage it cites was spoken by "
                f"{said}: “{self.sentence[:160]}”")


def split_sentences(answer: str) -> list[str]:
    """Sentences of an answer, each with its own citation markers; lists and paragraphs split first."""
    sentences = []
    for line in answer.splitlines():
        line = TRAILING_MARKERS.sub(lambda match: match.group(2) + match.group(1), BULLET.sub("", line).strip())
        sentences.extend(part.strip() for part in SENTENCE_END.split(line) if part.strip())
    return sentences


def _contains(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def named_speakers(sentence: str, speakers: list[str]) -> list[str]:
    """The speakers a sentence names, by full name or by first name (a first name of at least three letters)."""
    text = MARKUP.sub("", CITATION.sub("", sentence))
    found = []
    for speaker in speakers:
        first = speaker.split()[0] if speaker.split() else speaker
        # A first name must be written as the name ("Will", not the word "will").
        by_first = len(first) >= MIN_FIRST_NAME and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", text) is not None
        if _contains(text, speaker) or by_first:
            found.append(speaker)
    return found


def check_attribution(answer: str, citations: dict[str, SourcePassage]) -> list[Flag]:
    """Sentences naming a speaker of a cited transcript when no named speaker spoke a passage they cite."""
    flags = []
    for sentence in split_sentences(answer):
        ids = list(dict.fromkeys(CITATION.findall(sentence)))
        cited = [citations[ref] for ref in ids if ref in citations]
        speakers = list(dict.fromkeys(name for passage in cited for name in passage.run_speakers or []))
        if not speakers:
            continue  # nothing cited is a transcript
        named = named_speakers(sentence, speakers)
        if not named:
            continue
        who_spoke = list(dict.fromkeys(passage.speaker for passage in cited if passage.speaker))
        if not who_spoke:
            continue  # no cited passage says who spoke it, so there is nothing to compare
        if not set(named) & set(who_spoke):
            flags.append(Flag(sentence=sentence, named=named, cited_speakers=who_spoke, citation_ids=ids))
    return flags
