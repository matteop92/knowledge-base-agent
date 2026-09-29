"""Profile-scoped hybrid discovery and exact stored-source reads."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json

import psycopg
from pydantic import ValidationError

from app.ingestion.embeddings import EmbeddingSpec
from app.models.client import EmbeddingResult, ModelClientError
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.records import CardHit, FusedHit, SearchHit, SourcePassage, spec_id as embedding_spec_id
from app.storage.search import _check_k, _profile, search_text, search_vector

RRF_K = 60
MAX_CHANNEL_K = 20
MAX_PASSAGE_CHARS = 2400


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


def _price_ok(pricing: object, max_usd_per_million: Decimal) -> None:
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
    _price_ok(route.get("pricing"), max_embedding_usd_per_million)
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
                  k: int = 20) -> list[FusedHit]:
    _check_k(k)
    if k > MAX_CHANNEL_K:
        raise StorageError("hybrid K must be at most 20")
    spec_id, spec, embedded = embed_queries(
        conn, client, [query], provider_tag=provider_tag,
        max_embedding_usd_per_million=max_embedding_usd_per_million)
    vector = search_vector(conn, embedded.vectors[0], spec_id=spec_id,
                           metric=spec.distance_metric, k=MAX_CHANNEL_K)
    lexical = search_text(conn, query, k=MAX_CHANNEL_K)
    return fuse_hits(vector, lexical, k=k)


def search_cards(conn: psycopg.Connection, query: str, *, k: int = 10) -> list[CardHit]:
    _check_k(k)
    if k > 20:
        raise StorageError("card K exceeds 20")
    if not query.strip():
        return []
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("""select ts_rank(to_tsvector('simple'::regconfig,
                     concat_ws(' ', x.payload->>'statement',x.payload->>'situation',
                     x.payload->>'action',x.payload->>'result',x.payload->>'principle')),
                     websearch_to_tsquery('simple'::regconfig,%s)) as score,
                r.document_id,x.run_id,r.source_sha256,d.root_alias,d.relative_path,
                x.card_id,x.chunk_id,x.payload->>'statement',x.payload->'evidence_span'
            from knowledge.cards x
            join knowledge.artifacts a on a.run_id=x.run_id and a.artifact_id=x.artifact_id
            join knowledge.runs r on r.run_id=x.run_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.active_runs ar on ar.run_id=x.run_id and ar.document_id=r.document_id
            join knowledge.chunks c on c.run_id=x.run_id and c.chunk_id=x.chunk_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            cross join knowledge.corpus_state state
            where state.profile_id=p.profile_id and p.profile_id=%s
              and c.variant=p.variant and c.chunking_version=p.chunking_version
              and a.status='ready' and a.kind='cards'
              and (ar.selection->'required_artifact_ids') ? x.artifact_id
              and to_tsvector('simple'::regconfig,
                  concat_ws(' ',x.payload->>'statement',x.payload->>'situation',
                  x.payload->>'action',x.payload->>'result',x.payload->>'principle'))
                  @@ websearch_to_tsquery('simple'::regconfig,%s)
            order by score desc,x.card_id,x.run_id limit %s""", (query, profile[0], query, k))
        rows = cur.fetchall()
    result: list[CardHit] = []
    seen: set[tuple[str, int, int]] = set()
    for row in rows:
        span = row[9]
        key = (row[2], span["start_char"], span["end_char"])
        if key in seen:
            continue
        seen.add(key)
        result.append(CardHit(rank=len(result) + 1, score=float(row[0]),
                              document_id=row[1], run_id=row[2], source_sha256=row[3],
                              root_alias=row[4], relative_path=row[5], card_id=row[6],
                              chunk_id=row[7], statement=row[8], evidence_span=span))
    return result


def read_passage(conn: psycopg.Connection, *, document_id: str, run_id: str,
                 source_sha256: str, chunk_id: str, start_char: int,
                 end_char: int) -> SourcePassage:
    if not 0 <= start_char < end_char or end_char - start_char > MAX_PASSAGE_CHARS:
        raise StorageError("passage span is invalid or too large")
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute("""select r.source_text,d.root_alias,d.relative_path,c.source_spans,
            exists(select 1 from knowledge.cards x
                join knowledge.artifacts a on a.run_id=x.run_id and a.artifact_id=x.artifact_id
                where x.run_id=r.run_id and x.chunk_id=c.chunk_id
                  and a.status='ready' and a.kind='cards'
                  and (ar.selection->'required_artifact_ids') ? x.artifact_id
                  and (x.payload->'evidence_span'->>'start_char')::integer <= %s
                  and (x.payload->'evidence_span'->>'end_char')::integer >= %s)
            from knowledge.runs r join knowledge.documents d on d.document_id=r.document_id
            join knowledge.active_runs ar on ar.run_id=r.run_id and ar.document_id=r.document_id
            join knowledge.chunks c on c.run_id=r.run_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            cross join knowledge.corpus_state state
            where r.document_id=%s and r.run_id=%s and r.source_sha256=%s
              and c.chunk_id=%s and ar.profile_id=%s and state.profile_id=p.profile_id
              and c.variant=p.variant and c.chunking_version=p.chunking_version""",
                    (start_char, end_char, document_id, run_id, source_sha256, chunk_id, profile[0]))
        row = cur.fetchone()
    if row is None:
        raise StorageError("source passage is not in the active published run")
    source_text, root_alias, relative_path, spans, within_card = row
    if end_char > len(source_text) or not (within_card or any(
        span["start_char"] <= start_char < end_char <= span["end_char"] for span in spans
    )):
        raise StorageError("passage is outside the selected chunk source spans")
    return SourcePassage(document_id=document_id, run_id=run_id,
                         source_sha256=source_sha256, root_alias=root_alias,
                         relative_path=relative_path, chunk_id=chunk_id,
                         start_char=start_char, end_char=end_char,
                         text=source_text[start_char:end_char])


class DatabaseRetriever:
    """Connection-scoped adapter; caller owns a read-only repeatable-read transaction."""

    def __init__(self, conn: psycopg.Connection, client: OpenRouterClient, *,
                 provider_tag: str, max_embedding_usd_per_million: Decimal):
        self.conn = conn
        self.client = client
        self.provider_tag = provider_tag
        self.max_embedding_usd_per_million = max_embedding_usd_per_million

    def search(self, query: str) -> list[FusedHit]:
        return hybrid_search(self.conn, self.client, query,
                             provider_tag=self.provider_tag,
                             max_embedding_usd_per_million=self.max_embedding_usd_per_million)

    def search_cards(self, query: str) -> list[CardHit]:
        return search_cards(self.conn, query)

    def read(self, reference: FusedHit | CardHit, span_index: int) -> SourcePassage:
        if isinstance(reference, FusedHit):
            hit = reference.hit
            span = hit.source_spans[span_index]
            fields = (hit.document_id, hit.run_id, hit.source_sha256, hit.chunk_id)
        else:
            if span_index != 0:
                raise IndexError("card has one evidence span")
            span = reference.evidence_span
            fields = (reference.document_id, reference.run_id, reference.source_sha256,
                      reference.chunk_id)
        start = span["start_char"]
        end = min(span["end_char"], start + MAX_PASSAGE_CHARS)
        return read_passage(self.conn, document_id=fields[0], run_id=fields[1],
                            source_sha256=fields[2], chunk_id=fields[3],
                            start_char=start, end_char=end)
