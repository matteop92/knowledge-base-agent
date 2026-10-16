"""Independent FTS and exact-vector top-K candidate lists."""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

import psycopg

from app.storage import StorageError
from app.storage.records import SearchHit

MAX_K = 100
OPERATORS = {"cosine": "<=>", "dot": "<#>", "euclidean": "<->"}


def _check_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise StorageError("K must be an integer from 1 to 100")


def _profile(cur: psycopg.Cursor, profile_id: str | None, historical_run_id: str | None):
    if historical_run_id and not profile_id:
        raise StorageError("historical search needs an explicit profile")
    if not profile_id:
        cur.execute("select profile_id from knowledge.corpus_state where singleton=true")
        profile_id = cur.fetchone()[0]
    if not profile_id:
        raise StorageError("no search profile is selected")
    cur.execute("""select p.profile_id,p.variant,p.chunking_version,p.spec_id,p.text_config,s.dimensions,s.distance_metric
        from knowledge.profiles p join knowledge.embedding_specs s on s.spec_id=p.spec_id
        where p.profile_id=%s""", (profile_id,))
    profile = cur.fetchone()
    if profile is None:
        raise StorageError("unknown search profile")
    if historical_run_id is None:
        cur.execute("select profile_id from knowledge.corpus_state where singleton=true")
        if cur.fetchone()[0] != profile_id:
            raise StorageError("profile is not selected for ordinary search")
    return profile


def _scope(historical_run_id: str | None) -> str:
    if historical_run_id:
        return "c.run_id = %(history)s"
    return "ar.run_id = c.run_id and ar.profile_id = p.profile_id and state.profile_id = p.profile_id"


# Stored generated properties ride along with the run metadata; frontmatter still wins.
LABEL_METADATA = ("(r.parsed->'metadata') || jsonb_build_object('generated', coalesce(("
                  "select dp.properties from knowledge.document_properties dp where dp.status='ready' "
                  "and dp.document_id=r.document_id and dp.source_sha256=r.source_sha256), '{}'::jsonb))")


def _hits(rows) -> list[SearchHit]:
    result = []
    for index, row in enumerate(rows, 1):
        title, people, _ = source_label(row[9] if len(row) > 9 else {}, row[5])
        result.append(SearchHit(rank=index, score=float(row[0]), document_id=row[1], run_id=row[2],
                                source_sha256=row[3], root_alias=row[4], relative_path=row[5],
                                chunk_id=row[6], source_spans=row[7], raw_text=row[8],
                                title=title, people=people))
    return result


def source_label(metadata: object, relative_path: str) -> tuple[str, list[str], list[str]]:
    """Use published run metadata, including older runs with guest only in frontmatter."""
    data = metadata if isinstance(metadata, dict) else {}
    title = data.get("title") or PurePosixPath(relative_path).stem
    frontmatter = data.get("frontmatter") or {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}

    def names(value: object) -> list[str]:
        values = value if isinstance(value, list) else [value]
        return [item.removeprefix("[[").removesuffix("]]").strip()
                for item in values if isinstance(item, str) and item.strip()]

    people = list(dict.fromkeys(names(data.get("authors")) + names(data.get("guests"))
                                + names(frontmatter.get("guest"))))
    tags = names(data.get("tags"))
    generated = data.get("generated") if isinstance(data.get("generated"), dict) else {}
    # Generated properties fill only what the file does not set itself.
    return str(title), people or names(generated.get("people")), tags or names(generated.get("tags"))


def search_text(conn: psycopg.Connection, query: str, *, k: int = 10,
                profile_id: str | None = None, historical_run_id: str | None = None,
                document_id: str | None = None) -> list[SearchHit]:
    _check_k(k)
    if not isinstance(query, str):
        raise StorageError("text query must be a string")
    if not query.strip():
        return []
    with conn.cursor() as cur:
        profile = _profile(cur, profile_id, historical_run_id)
        cur.execute("select numnode(websearch_to_tsquery('simple'::regconfig,%s))", (query,))
        if cur.fetchone()[0] == 0:
            return []
        sql = f"""select ts_rank(to_tsvector('simple'::regconfig,c.raw_text),
                     websearch_to_tsquery('simple'::regconfig,%(query)s)) as score,
                r.document_id,c.run_id,r.source_sha256,d.root_alias,d.relative_path,
                c.chunk_id,c.source_spans,c.raw_text,{LABEL_METADATA}
            from knowledge.chunks c
            join knowledge.runs r on r.run_id=c.run_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.profiles p on p.profile_id=%(profile)s
            left join knowledge.active_runs ar on ar.document_id=r.document_id and ar.profile_id=p.profile_id
            cross join knowledge.corpus_state state
            where c.variant=p.variant and c.chunking_version=p.chunking_version
              and {_scope(historical_run_id)}
              and (%(document_id)s::text is null or r.document_id=%(document_id)s)
              and to_tsvector('simple'::regconfig,c.raw_text) @@ websearch_to_tsquery('simple'::regconfig,%(query)s)
            order by score desc,c.chunk_id,c.run_id limit %(k)s"""
        params = {"query": query, "profile": profile[0], "history": historical_run_id, "k": k,
                  "document_id": document_id}
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            # websearch_to_tsquery combines ordinary words with AND. A full
            # question often contains words spread across different chunks.
            terms = list(dict.fromkeys(re.findall(r"\w+", query, re.UNICODE)))[:64]
            if len(terms) > 1:
                params["query"] = " OR ".join(f'"{term}"' for term in terms)
                cur.execute(sql, params)
                rows = cur.fetchall()
        return _hits(rows)


def search_vector(conn: psycopg.Connection, vector: list[float], *, spec_id: str,
                  metric: str, k: int = 10, profile_id: str | None = None,
                  historical_run_id: str | None = None,
                  document_id: str | None = None) -> list[SearchHit]:
    _check_k(k)
    if metric not in OPERATORS:
        raise StorageError("unsupported vector metric")
    with conn.cursor() as cur:
        profile = _profile(cur, profile_id, historical_run_id)
        if spec_id != profile[3] or metric != profile[6]:
            raise StorageError("query vector spec or metric disagrees with profile")
        if not isinstance(vector, list) or len(vector) != profile[5] or any(
            isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
            for value in vector
        ):
            raise StorageError("query vector dimensions or values are invalid")
        if metric == "cosine" and not any(vector):
            raise StorageError("cosine query vector cannot be zero")
        value = "[" + ",".join(str(item) for item in vector) + "]"
        operator = OPERATORS[metric]
        sql = f"""select e.embedding OPERATOR(extensions.{operator}) %(vector)s::extensions.vector as distance,
                r.document_id,c.run_id,r.source_sha256,d.root_alias,d.relative_path,
                c.chunk_id,c.source_spans,c.raw_text,{LABEL_METADATA}
            from knowledge.embedding_vectors e
            join knowledge.chunks c on c.run_id=e.run_id and c.chunk_id=e.chunk_id
            join knowledge.runs r on r.run_id=c.run_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.profiles p on p.profile_id=%(profile)s and p.spec_id=e.spec_id
            left join knowledge.active_runs ar on ar.document_id=r.document_id and ar.profile_id=p.profile_id
            cross join knowledge.corpus_state state
            where c.variant=p.variant and c.chunking_version=p.chunking_version
              and {_scope(historical_run_id)}
              and (%(document_id)s::text is null or r.document_id=%(document_id)s)
            order by distance asc,c.chunk_id,c.run_id limit %(k)s"""
        cur.execute(sql, {"vector": value, "profile": profile[0], "history": historical_run_id,
                          "k": k, "document_id": document_id})
        return _hits(cur.fetchall())
