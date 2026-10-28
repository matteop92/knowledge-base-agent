"""Dates and numbers in a question, read by code: the router model reads neither well.

`read_period` finds a stated period (a year, a month with or without a year, this or last
week, month or year); `read_limit` finds a count like "three most recent". Both return None
when the question states nothing, so a router's yes for "limits by date" without a period
narrows nothing.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = [name.lower() for name in calendar.month_name[1:]]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10}
ADDED_WORDS = re.compile(r"\b(add(?:ed)?|upload(?:ed)?|sav(?:e|ed)|captur(?:e|ed)|import(?:ed)?)\b", re.I)


@dataclass(frozen=True)
class Period:
    start: str  # first day, YYYY-MM-DD
    end: str  # last day, inclusive
    field: str  # "date" (the note's own date) or "added" (when it entered the index)
    label: str  # for the scope line, e.g. "from September 2026"

    def record(self) -> dict[str, str]:
        """The period as stored in a receipt."""
        return {"start": self.start, "end": self.end, "field": self.field}

    def contains(self, day: str | None) -> bool:
        return bool(day) and self.start <= day[:10] <= self.end


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _month(question: str) -> int | None:
    for index, name in enumerate(MONTHS, 1):
        if name == "may":
            # "may" is also a verb, so it counts only when capitalised or next to a year.
            found = re.search(r"\bMay\b|\bmay\s+(?:19|20)\d{2}\b", question)
        else:
            found = re.search(rf"\b{name}\b", question, re.I)
        if found:
            return index
    return None


def read_period(question: str, today: date | None = None) -> Period | None:
    today = today or date.today()
    text = question.lower()
    field = "added" if ADDED_WORDS.search(question) else "date"
    year_match = re.search(r"\b((?:19|20)\d{2})\b", question)
    month = _month(question)
    if month:
        # A month named without a year is the most recent one, never a month still to come.
        year = int(year_match.group(1)) if year_match else (today.year if month <= today.month else today.year - 1)
        start, end = _month_range(year, month)
        return Period(start.isoformat(), end.isoformat(), field, f"from {calendar.month_name[month]} {year}")
    if year_match:
        year = int(year_match.group(1))
        return Period(f"{year}-01-01", f"{year}-12-31", field, f"from {year}")
    monday = today - timedelta(days=today.weekday())
    if re.search(r"\blast week\b", text):
        return Period((monday - timedelta(days=7)).isoformat(), (monday - timedelta(days=1)).isoformat(),
                      field, "from last week")
    if re.search(r"\bthis week\b", text):
        return Period(monday.isoformat(), today.isoformat(), field, "from this week")
    if re.search(r"\blast month\b", text):
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start, end = _month_range(year, month)
        return Period(start.isoformat(), end.isoformat(), field, "from last month")
    if re.search(r"\bthis month\b", text):
        start, _ = _month_range(today.year, today.month)
        return Period(start.isoformat(), today.isoformat(), field, "from this month")
    if re.search(r"\blast year\b", text):
        return Period(f"{today.year - 1}-01-01", f"{today.year - 1}-12-31", field, "from last year")
    if re.search(r"\bthis year\b", text):
        return Period(f"{today.year}-01-01", today.isoformat(), field, "from this year")
    return None


def read_limit(question: str) -> int | None:
    """A count in front of "most recent", "latest", "newest", "oldest" or "first", else None."""
    words = "|".join(NUMBER_WORDS)
    match = re.search(rf"\b(\d+|{words})\s+(?:most\s+recent(?:ly)?|latest|newest|oldest|earliest)\b", question, re.I)
    if not match:
        return None
    token = match.group(1).lower()
    value = NUMBER_WORDS.get(token) or int(token)
    return value if value > 0 else None
