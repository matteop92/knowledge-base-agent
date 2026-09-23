"""Local operator commands for staged runs and independent candidate queries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

from app.storage import StorageError
from app.storage.loader import connect_runtime, load_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile
from app.storage.search import search_text, search_vector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate the private knowledge index")
    actions = parser.add_subparsers(dest="action", required=True)
    load = actions.add_parser("load")
    load.add_argument("run_dir", type=Path)
    profile = actions.add_parser("create-profile")
    profile.add_argument("profile_id")
    profile.add_argument("variant", choices=("fixed_raw", "structural_raw"))
    profile.add_argument("chunking_version")
    profile.add_argument("spec_id")
    profile.add_argument("--require-kind", action="append", default=[])
    publication = actions.add_parser("publish")
    publication.add_argument("profile_id")
    publication.add_argument("run_id")
    publication.add_argument("--require-artifact-id", action="append", default=[])
    selected = actions.add_parser("select-profile")
    selected.add_argument("profile_id")
    lexical = actions.add_parser("search-text")
    lexical.add_argument("query")
    lexical.add_argument("--k", type=int, default=10)
    lexical.add_argument("--profile-id")
    lexical.add_argument("--historical-run-id")
    semantic = actions.add_parser("search-vector")
    semantic.add_argument("vector_file", type=Path, help="local JSON file containing a float array")
    semantic.add_argument("spec_id")
    semantic.add_argument("metric", choices=("cosine", "dot", "euclidean"))
    semantic.add_argument("--k", type=int, default=10)
    semantic.add_argument("--profile-id")
    semantic.add_argument("--historical-run-id")
    actions.add_parser("status")
    args = parser.parse_args(argv)
    try:
        with connect_runtime() as conn:
            if args.action == "load":
                result = {"staged_run_id": load_run(conn, args.run_dir)}
            elif args.action == "create-profile":
                value = SearchProfile(profile_id=args.profile_id, variant=args.variant,
                                      chunking_version=args.chunking_version,
                                      embedding_spec_id=args.spec_id,
                                      required_artifact_kinds=args.require_kind)
                create_profile(conn, value)
                result = {"profile_id": value.profile_id}
            elif args.action == "publish":
                value = PublicationSelection(run_id=args.run_id, profile_id=args.profile_id,
                                             required_artifact_ids=args.require_artifact_id)
                result = {"document_id": publish_run(conn, value), "active_run_id": args.run_id}
            elif args.action == "select-profile":
                select_profile(conn, args.profile_id)
                result = {"selected_profile_id": args.profile_id}
            elif args.action == "search-text":
                hits = search_text(conn, args.query, k=args.k, profile_id=args.profile_id,
                                   historical_run_id=args.historical_run_id)
                result = [{key: value for key, value in hit.model_dump().items() if key != "raw_text"}
                          for hit in hits]
            elif args.action == "search-vector":
                vector = json.loads(args.vector_file.read_text(encoding="utf-8"))
                hits = search_vector(conn, vector, spec_id=args.spec_id, metric=args.metric,
                                     k=args.k, profile_id=args.profile_id,
                                     historical_run_id=args.historical_run_id)
                result = [{key: value for key, value in hit.model_dump().items() if key != "raw_text"}
                          for hit in hits]
            else:
                with conn.cursor() as cur:
                    cur.execute("select profile_id from knowledge.corpus_state where singleton=true")
                    active = cur.fetchone()[0]
                    cur.execute("select profile_id,document_id,run_id from knowledge.active_runs order by profile_id,document_id")
                    result = {"selected_profile_id": active,
                              "active_runs": [dict(zip(("profile_id", "document_id", "run_id"), row))
                                              for row in cur.fetchall()]}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (StorageError, psycopg.Error, OSError, ValueError) as exc:
        print(f"storage command failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
