"""One ledger for every model-calling path, checked against a daily cap."""

from __future__ import annotations

import os
from decimal import Decimal

import psycopg

DEFAULT_DAILY_CAP_USD = Decimal("2")
# The one-time bulk load runs under its own approved limit, not the daily cap.
OUTSIDE_DAILY_CAP = ("bulk_load",)


class SpendCapError(RuntimeError):
    """A call would push the day's model spend over its cap."""


def daily_cap_usd() -> Decimal:
    return Decimal(os.getenv("KNOWLEDGE_DAILY_CAP_USD", str(DEFAULT_DAILY_CAP_USD)))


def spent_today(conn: psycopg.Connection) -> Decimal:
    row = conn.execute("""select coalesce(sum(estimated_usd), 0) from knowledge.spend
        where day = (now() at time zone 'utc')::date and source <> all(%s)""",
                       (list(OUTSIDE_DAILY_CAP),)).fetchone()
    return Decimal(row[0])


def check_daily_cap(conn: psycopg.Connection, worst_case_usd: Decimal | float,
                    cap: Decimal | None = None) -> None:
    cap = daily_cap_usd() if cap is None else cap
    if spent_today(conn) + Decimal(str(worst_case_usd)) > cap:
        raise SpendCapError("daily model spend cap reached")


def record_spend(conn: psycopg.Connection, source: str, usd: Decimal | float,
                 job_id: str | None = None) -> None:
    conn.execute("insert into knowledge.spend (source, estimated_usd, job_id) values (%s, %s, %s)",
                 (source, Decimal(str(round(float(usd), 8))), job_id))
