"""Profile-scoped hybrid discovery and exact stored-source reads."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import re

import psycopg
from pydantic import ValidationError

from app.ingestion.embeddings import EmbeddingSpec
from app.models.client import EmbeddingResult, ModelClientError
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.records import FusedHit, SearchHit, SourcePassage, spec_id as embedding_spec_id
from app.storage.search import LABEL_METADATA, _check_k, _hits, _profile, search_text, search_vector, source_label

RRF_K = 60
MAX_CHANNEL_K = 20
MAX_PASSAGE_CHARS = 2400
COMMON_WORDS = frozenset("a an and are as at be by for from how in is it of on or the to what when where who why with you your".split())


def _words(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold(), re.UNICODE)


def _label_score(query: str, title: str, people: list[str], tags: list[str]) -> int:
    words = _words(query)
    labels = [title, *people, *tags]
    best = 0
    for label in labels:
        tokens = _words(label)
        for length in range(min(len(words), len(tokens)), 1, -1):
            for start in range(len(words) - length + 1):
                phrase = words[start:start + length]
                if any(word not in COMMON_WORDS for word in phrase) and any(
                    tokens[index:index + length] == phrase
                    for index in range(len(tokens) - length + 1)
                ):
                    best = max(best, length)
        if label in people and tokens and tokens[-1] in words and tokens[-1] not in COMMON_WORDS:
            best = max(best, 1)
        if label in tags and len(tokens) == 1 and tokens[0] in words and tokens[0] not in COMMON_WORDS:
            best = max(best, 1)
    return best


def named_sources(conn: psycopg.Connection, query: str) -> list[str]:
    """Select at most three published files whose stored label appears in the query."""
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute(f"""select r.document_id,d.relative_path,{LABEL_METADATA}
            from knowledge.active_runs ar
            join knowledge.runs r on r.run_id=ar.run_id and r.document_id=ar.document_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.corpus_state state on state.profile_id=ar.profile_id
            where ar.profile_id=%s order by r.document_id""", (profile[0],))
        rows = cur.fetchall()
    matches = []
    for document_id, relative_path, metadata in rows:
        score = _label_score(query, *source_label(metadata, relative_path))
        if score:
            matches.append((score, document_id))
    return [document_id for _, document_id in sorted(matches, key=lambda item: (-item[0], item[1]))[:3]]


def fuse_hits(vector: list[SearchHit], lexical: list[SearchHit], *, k: int = 20) -> list[FusedHit]:
    _check_k(k)
    if k > MAX_CHANNEL_K:
        raise StorageError("fused K exceeds 20")
    scores: dict[tuple[str, str], float] = defaultdict(float)
    hits: dict[tuple[str, str], SearchHit] = {}
    channels: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, items in (("vector", vector[:MAX_CHANNEL_K]), ("text", lexical[:MAX_CHANNEL_K])):
        seen: set[tuple[str, str]] = set()
        for rank, hit in enumerate(items, 1):
            key = (hit.run_id, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            scores[key] += 1 / (RRF_K + rank)
            hits.setdefault(key, hit)
            channels[key].append(name)
    ordered = sorted(scores, key=lambda key: (-scores[key], key[1], key[0]))
    result: list[FusedHit] = []
    seen_spans: set[tuple[str, tuple[tuple[int, int], ...]]] = set()
    for key in ordered:
        hit = hits[key]
        span_key = (hit.run_id, tuple((span["start_char"], span["end_char"])
                                      for span in hit.source_spans))
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        result.append(FusedHit(rank=len(result) + 1, rrf_score=scores[key],
                               channels=channels[key], hit=hit))
        if len(result) == k:
            break
    return result


def selected_embedding_spec(conn: psycopg.Connection) -> tuple[str, EmbeddingSpec]:
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("select payload from knowledge.embedding_specs where spec_id=%s", (profile[3],))
        row = cur.fetchone()
    if row is None:
        raise StorageError("selected embedding specification is missing")
    try:
        spec = EmbeddingSpec.model_validate_json(json.dumps(row[0]))
    except (ValidationError, TypeError, ValueError):
        raise StorageError("selected embedding specification is invalid") from None
    if embedding_spec_id(spec) != profile[3]:
        raise StorageError("selected embedding specification identity is invalid")
    if (spec.dimensions, spec.distance_metric, spec.chunking_version) != (
        profile[5], profile[6], profile[2]
    ):
        raise StorageError("selected embedding specification disagrees with profile")
    return profile[3], spec


def price_ok(pricing: object, max_usd_per_million: Decimal) -> None:
    if not max_usd_per_million.is_finite() or max_usd_per_million < 0:
        raise ModelClientError("query embedding price ceiling is invalid")
    if not isinstance(pricing, dict) or pricing.get("prompt") is None:
        raise ModelClientError("query embedding endpoint has no current input price")
    try:
        per_token = Decimal(str(pricing["prompt"]))
    except InvalidOperation:
        raise ModelClientError("query embedding endpoint has invalid price") from None
    if not per_token.is_finite() or per_token < 0 or per_token * 1_000_000 > max_usd_per_million:
        raise ModelClientError("query embedding endpoint exceeds price ceiling")


def embed_queries(conn: psycopg.Connection, client: OpenRouterClient, queries: list[str], *,
                  provider_tag: str, max_embedding_usd_per_million: Decimal
                  ) -> tuple[str, EmbeddingSpec, EmbeddingResult]:
    """One route-gated batch, shared by the answer tools and retrieval evaluation."""
    if not queries or any(not isinstance(query, str) or not query.strip() for query in queries):
        raise StorageError("query embedding needs nonempty strings")
    spec_id, spec = selected_embedding_spec(conn)
    route = client.require_zdr_route(spec.model, provider_tag)
    price_ok(route.get("pricing"), max_embedding_usd_per_million)
    provider = client._model_providers.get(spec.model, client._provider)
    if (not isinstance(provider, dict) or provider.get("only") != [provider_tag]
            or provider.get("allow_fallbacks") is not False
            or provider.get("data_collection") != "deny" or provider.get("zdr") is not True):
        raise ModelClientError("query embedding provider is not pinned and private")
    embedded = client.embed(model=spec.model_spec(), texts=queries, dimensions=spec.dimensions,
                            input_type=spec.query_input_type)
    if len(embedded.vectors) != len(queries):
        raise ModelClientError("query embedding returned the wrong vector count")
    return spec_id, spec, embedded


def hybrid_search(conn: psycopg.Connection, client: OpenRouterClient, query: str, *,
                  provider_tag: str, max_embedding_usd_per_million: Decimal,
                  k: int = 20, document_ids: list[str] | None = None) -> list[FusedHit]:
    """Fused hits over the whole vault, or over only `document_ids` (a narrowed question's scope)."""
    _check_k(k)
    if k > MAX_CHANNEL_K:
        raise StorageError("hybrid K must be at most 20")
    spec_id, spec, embedded = embed_queries(
        conn, client, [query], provider_tag=provider_tag,
        max_embedding_usd_per_million=max_embedding_usd_per_million)
    vector = search_vector(conn, embedded.vectors[0], spec_id=spec_id,
                           metric=spec.distance_metric, k=MAX_CHANNEL_K, document_ids=document_ids)
    lexical = search_text(conn, query, k=MAX_CHANNEL_K, document_ids=document_ids)
    global_hits = fuse_hits(vector, lexical, k=k)
    sources = named_sources(conn, query)
    if document_ids is not None:
        sources = [document_id for document_id in sources if document_id in document_ids]
    if not sources:
        return global_hits
    reserved: list[FusedHit] = []
    for document_id in sources:
        scoped_vector = search_vector(conn, embedded.vectors[0], spec_id=spec_id,
                                      metric=spec.distance_metric, k=MAX_CHANNEL_K,
                                      document_id=document_id)
        scoped_text = search_text(conn, query, k=MAX_CHANNEL_K, document_id=document_id)
        reserved.extend(fuse_hits(scoped_vector, scoped_text, k=5))
    ordered = [*reserved, *global_hits]
    unique: list[FusedHit] = []
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        key = (item.hit.run_id, item.hit.chunk_id)
        if key not in seen:
            seen.add(key)
            unique.append(item.model_copy(update={"rank": len(unique) + 1}))
        if len(unique) == k:
            break
    return unique


def find_in_document(conn: psycopg.Connection, reference: FusedHit,
                     query: str, *, k: int = 10) -> list[FusedHit]:
    """Find published original chunks in the exact document revision of a lead."""
    _check_k(k)
    if k > 20:
        raise StorageError("document find K exceeds 20")
    if not isinstance(query, str) or not query.strip():
        raise StorageError("document find needs a query")
    lead = reference.hit
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("""select 1 from knowledge.active_runs ar
            join knowledge.runs r on r.run_id=ar.run_id
            cross join knowledge.corpus_state state
            where ar.profile_id=%s and state.profile_id=ar.profile_id
              and ar.document_id=%s and ar.run_id=%s and r.source_sha256=%s""",
                    (profile[0], lead.document_id, lead.run_id, lead.source_sha256))
        if cur.fetchone() is None:
            raise StorageError("document reference is not in the active published revision")
        sql = f"""select ts_rank(to_tsvector('simple'::regconfig,c.raw_text),
                    websearch_to_tsquery('simple'::regconfig,%s)) as score,
                r.document_id,c.run_id,r.source_sha256,d.root_alias,d.relative_path,
                c.chunk_id,c.source_spans,c.raw_text,{LABEL_METADATA}
            from knowledge.chunks c
            join knowledge.runs r on r.run_id=c.run_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.active_runs ar on ar.run_id=c.run_id and ar.document_id=r.document_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            cross join knowledge.corpus_state state
            where state.profile_id=p.profile_id and p.profile_id=%s
              and r.document_id=%s and r.run_id=%s and r.source_sha256=%s
              and c.variant=p.variant and c.chunking_version=p.chunking_version
              and to_tsvector('simple'::regconfig,c.raw_text) @@
                  websearch_to_tsquery('simple'::regconfig,%s)
            order by score desc,c.chunk_id,c.run_id limit %s"""
        args = (query, profile[0], lead.document_id, lead.run_id, lead.source_sha256, query, k)
        cur.execute(sql, args)
        rows = cur.fetchall()
        if not rows:
            terms = list(dict.fromkeys(re.findall(r"\w+", query, re.UNICODE)))[:64]
            if len(terms) > 1:
                fallback = " OR ".join(f'"{term}"' for term in terms)
                cur.execute(sql, (fallback, profile[0], lead.document_id, lead.run_id,
                                  lead.source_sha256, fallback, k))
                rows = cur.fetchall()
    return [FusedHit(rank=hit.rank, rrf_score=hit.score, channels=["document_find"], hit=hit)
            for hit in _hits(rows)]


def read_passage(conn: psycopg.Connection, *, document_id: str, run_id: str,
                 source_sha256: str, chunk_id: str, start_char: int,
                 end_char: int) -> SourcePassage:
    """One passage of a published chunk; reads the chunk with `read_chunk` and cuts it with `passage_from_chunk`."""
    if not 0 <= start_char < end_char or end_char - start_char > MAX_PASSAGE_CHARS:
        raise StorageError("passage span is invalid or too large")
    chunk = read_chunk(conn, document_id=document_id, run_id=run_id, source_sha256=source_sha256,
                       chunk_id=chunk_id)
    return passage_from_chunk(chunk, start_char, end_char)


# A parsed speaker-turn block `b` as the {start_char, end_char, speaker} object `speaker_at` reads.
TURN_JSON = """jsonb_build_object('start_char', (b->'span'->>'start_char')::int,
                    'end_char', (b->'span'->>'end_char')::int, 'speaker', b->>'speaker')"""
TURN_START = "(b->'span'->>'start_char')::int"


def read_chunk(conn: psycopg.Connection, *, document_id: str, run_id: str, source_sha256: str,
               chunk_id: str) -> dict:
    """The text of every source span of one published chunk, in one query.

    Holds what `passage_from_chunk` checks against: the source length, the chunk's stored spans
    and the non-whitespace excluded gaps that touch them.
    """
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("""select length(r.source_text),d.root_alias,d.relative_path,c.source_spans,
            (select coalesce(jsonb_agg(substr(r.source_text,(span->>'start_char')::int+1,
                    (span->>'end_char')::int-(span->>'start_char')::int) order by position), '[]'::jsonb)
                from jsonb_array_elements(c.source_spans) with ordinality as item(span, position)),
            (select coalesce(jsonb_agg(gap), '[]'::jsonb)
                from jsonb_array_elements(coalesce(r.parsed->'excluded_spans', '[]'::jsonb)) gap
                where gap->>'reason' is distinct from 'whitespace' and exists (
                    select 1 from jsonb_array_elements(c.source_spans) span
                    where (gap->'span'->>'start_char')::int < (span->>'end_char')::int
                      and (span->>'start_char')::int < (gap->'span'->>'end_char')::int)),
            -- Speaker turns touching the chunk, so a passage takes the speaker of the turn it starts in.
            (select coalesce(jsonb_agg(""" + TURN_JSON + """ order by """ + TURN_START + """), '[]'::jsonb)
                from jsonb_array_elements(coalesce(r.parsed->'blocks', '[]'::jsonb)) b
                where b->>'kind'='speaker_turn' and exists (
                    select 1 from jsonb_array_elements(c.source_spans) span
                    where (b->'span'->>'start_char')::int < (span->>'end_char')::int
                      and (span->>'start_char')::int < (b->'span'->>'end_char')::int)),
            (select coalesce(jsonb_agg(speaker order by first_at), '[]'::jsonb) from (
                select b->>'speaker' as speaker, min((b->'span'->>'start_char')::int) as first_at
                from jsonb_array_elements(coalesce(r.parsed->'blocks', '[]'::jsonb)) b
                where b->>'kind'='speaker_turn' group by 1) speakers)
            from knowledge.runs r join knowledge.documents d on d.document_id=r.document_id
            join knowledge.active_runs ar on ar.run_id=r.run_id and ar.document_id=r.document_id
            join knowledge.chunks c on c.run_id=r.run_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            cross join knowledge.corpus_state state
            where r.document_id=%s and r.run_id=%s and r.source_sha256=%s
              and c.chunk_id=%s and ar.profile_id=%s and state.profile_id=p.profile_id
              and c.variant=p.variant and c.chunking_version=p.chunking_version""",
                    (document_id, run_id, source_sha256, chunk_id, profile[0]))
        row = cur.fetchone()
    if row is None:
        raise StorageError("source passage is not in the active published run")
    source_length, root_alias, relative_path, spans, texts, gaps, turns, speakers = row
    return {"document_id": document_id, "run_id": run_id, "source_sha256": source_sha256, "chunk_id": chunk_id,
            "source_length": source_length, "root_alias": root_alias, "relative_path": relative_path,
            "spans": spans, "texts": texts, "gaps": gaps, "turns": turns, "speakers": speakers}


def chunk_is_active(conn: psycopg.Connection, *, document_id: str, run_id: str, source_sha256: str,
                    chunk_id: str) -> bool:
    """Whether a chunk still belongs to the active published run, without fetching any text."""
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("""select 1 from knowledge.runs r
            join knowledge.active_runs ar on ar.run_id=r.run_id and ar.document_id=r.document_id
            join knowledge.chunks c on c.run_id=r.run_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            cross join knowledge.corpus_state state
            where r.document_id=%s and r.run_id=%s and r.source_sha256=%s
              and c.chunk_id=%s and ar.profile_id=%s and state.profile_id=p.profile_id
              and c.variant=p.variant and c.chunking_version=p.chunking_version""",
                    (document_id, run_id, source_sha256, chunk_id, profile[0]))
        return cur.fetchone() is not None


def passage_from_chunk(chunk: dict, start_char: int, end_char: int) -> SourcePassage:
    """One passage out of a `read_chunk` result: inside one stored span and crossing no excluded gap."""
    if not 0 <= start_char < end_char or end_char - start_char > MAX_PASSAGE_CHARS:
        raise StorageError("passage span is invalid or too large")
    if end_char > chunk["source_length"]:
        raise StorageError("passage is outside the selected chunk source spans")
    index = next((index for index, span in enumerate(chunk["spans"])
                  if span["start_char"] <= start_char < end_char <= span["end_char"]), None)
    if index is None:
        raise StorageError("passage is outside the selected chunk source spans")
    if any(item["span"]["start_char"] < end_char and start_char < item["span"]["end_char"]
           for item in chunk["gaps"]):
        raise StorageError("passage crosses an excluded source gap")
    span_start = chunk["spans"][index]["start_char"]
    text = chunk["texts"][index][start_char - span_start:end_char - span_start]
    return SourcePassage(document_id=chunk["document_id"], run_id=chunk["run_id"],
                         source_sha256=chunk["source_sha256"], root_alias=chunk["root_alias"],
                         relative_path=chunk["relative_path"], chunk_id=chunk["chunk_id"],
                         start_char=start_char, end_char=end_char, text=text,
                         speaker=speaker_at(chunk, start_char),
                         passage_speakers=speakers_between(chunk, start_char, end_char) or None,
                         run_speakers=chunk.get("speakers") or None)


def speakers_between(chunk: dict, start_char: int, end_char: int) -> list[str]:
    """Everyone whose turn overlaps [start_char, end_char), in order, from a `read_chunk` result."""
    return list(dict.fromkeys(turn["speaker"] for turn in chunk.get("turns") or []
                              if turn["start_char"] < end_char and start_char < turn["end_char"]))


def speaker_at(chunk: dict, position: int) -> str | None:
    """The speaker of the turn that contains `position`, from a `read_chunk` result; None outside a transcript.

    A continuation passage, or a chunk that starts partway through a turn, has no label in its own
    text, so the speaker comes from the parsed blocks.
    """
    return next((turn["speaker"] for turn in chunk.get("turns") or []
                 if turn["start_char"] <= position < turn["end_char"]), None)


def run_turns(conn: psycopg.Connection, run_id: str) -> list[dict] | None:
    """Every speaker turn of a stored run, as `speaker_at` reads them; None when the run is no longer stored.

    Receipts saved before passages carried a speaker use this to find who spoke a cited passage.
    """
    row = conn.execute("""select coalesce(jsonb_agg(""" + TURN_JSON + """ order by """ + TURN_START + """)
            filter (where b->>'kind'='speaker_turn'), '[]'::jsonb)
        from knowledge.runs r left join lateral jsonb_array_elements(coalesce(r.parsed->'blocks', '[]'::jsonb)) b on true
        where r.run_id=%s group by r.run_id""", (run_id,)).fetchone()
    return row[0] if row else None


class DatabaseRetriever:
    """Connection-scoped adapter; caller owns a read-only repeatable-read transaction."""

    def __init__(self, conn: psycopg.Connection, client: OpenRouterClient, *,
                 provider_tag: str, max_embedding_usd_per_million: Decimal):
        self.conn = conn
        self.client = client
        self.provider_tag = provider_tag
        self.max_embedding_usd_per_million = max_embedding_usd_per_million
        # A narrowed question searches only these documents; None is the whole vault.
        self.scope: list[str] | None = None
        self.widened = False  # the scope matched nothing, so the whole vault was searched instead
        self._searched = False
        # One query per chunk: every passage of a chunk comes from its first read. The caller's
        # repeatable-read transaction keeps the cached text identical to a fresh read.
        self._chunks: dict[tuple[str, str, str, str], dict] = {}

    def narrow(self, document_ids: list[str]) -> None:
        """Search only these documents. An empty scope widens at once."""
        self.scope = list(document_ids)
        if not self.scope:
            self.scope, self.widened = None, True

    def search(self, query: str) -> list[FusedHit]:
        def run() -> list[FusedHit]:
            return hybrid_search(self.conn, self.client, query, provider_tag=self.provider_tag,
                                 max_embedding_usd_per_million=self.max_embedding_usd_per_million,
                                 document_ids=self.scope)

        first, self._searched = not self._searched, True
        found = run()
        if first and self.scope is not None and not found:
            self.scope, self.widened = None, True
            found = run()
        return found

    def find(self, reference: FusedHit, query: str) -> list[FusedHit]:
        # A find stays inside its lead's document, so carried evidence from outside the scope can still be searched.
        return find_in_document(self.conn, reference, query)

    def read(self, reference: FusedHit, span_index: int, *, offset: int = 0) -> SourcePassage:
        hit = reference.hit
        span = hit.source_spans[span_index]
        fields = (hit.document_id, hit.run_id, hit.source_sha256, hit.chunk_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < span["end_char"] - span["start_char"]:
            raise ValueError("read offset is outside the selected span")
        start = span["start_char"] + offset
        end = min(span["end_char"], start + MAX_PASSAGE_CHARS)
        keys = dict(zip(("document_id", "run_id", "source_sha256", "chunk_id"), fields))
        if fields not in self._chunks:
            self._chunks[fields] = read_chunk(self.conn, **keys)
        elif not chunk_is_active(self.conn, **keys):
            # The text is cached, but a revision published since must not be read across.
            raise StorageError("source passage is not in the active published run")
        passage = passage_from_chunk(self._chunks[fields], start, end)
        next_offset = offset + len(passage.text)
        return passage.model_copy(update={"span_end_char": span["end_char"],
                                  "next_offset": next_offset if end < span["end_char"] else None,
                                  "truncated": end < span["end_char"]})
