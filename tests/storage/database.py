"""Apply every migration once to a disposable test database."""

from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def apply_migrations(cur) -> None:
    """Create Supabase's API roles if missing and apply each migration in order once."""
    for role in ("anon", "authenticated"):
        cur.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') then create role {role} nologin; end if; end $$")
    cur.execute("create table if not exists public.test_applied_migrations (name text primary key)")
    cur.execute("select name from public.test_applied_migrations")
    applied = {row[0] for row in cur.fetchall()}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name not in applied:
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("insert into public.test_applied_migrations values (%s)", (path.name,))
