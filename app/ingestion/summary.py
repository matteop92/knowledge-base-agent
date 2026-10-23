"""Bounded, resumable summaries linked to structural source chunks."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field

from app.ingestion.contracts import Chunk, ChunkSet, ParsedDocument, SourceSpan, StrictRecord
from app.ingestion.enrichment import PROMPT_ROOT
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelClient, ModelClientError, ModelSpec, ModelUsage


class Summary(StrictRecord):
    document_id: str
    run_id: str
    source_sha256: str
    level: str
    summary: str = Field(min_length=1)
    chunk_ids: list[str]
    source_spans: list[SourceSpan]
    child_artifact_ids: list[str] = Field(default_factory=list)
    model: ModelSpec
    prompt_version: str
    usage: ModelUsage


class SummaryProgress(StrictRecord):
    completed_sections: int
    total_sections: int
    document_summary: Summary | None = None


def _validate_evidence_method_grounding(summary: str, source_input: str) -> None:
    """Reject evidence-method claims absent from the generation input."""
    output = summary.casefold()
    source = source_input.casefold()
    for phrase in ("randomized", "randomised", "controlled trial", "independently verified", "audited"):
        if phrase in output and phrase not in source:
            raise ValueError("summary adds an unsupported evidence-method claim")


def read_prompt(name: str) -> str:
    """Read a prompt by version name, falling back to prompts/archive/ for retired trial versions."""
    for folder in (PROMPT_ROOT, PROMPT_ROOT / "archive"):
        path = folder / f"{name}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no prompt named {name}")


def _generate(
    document: ParsedDocument, chunks: list[Chunk], level: str, input_text: str,
    children: list[str], client: ModelClient, model: ModelSpec,
    manifest: RunManifest, run_dir: Path,
    prompt_family: str = "v1",
) -> tuple[Summary, str]:
    prompt_version = ("section_summary_v1" if level == "section" else "document_summary_v1") if prompt_family == "v1" else f"{level}_summary_{prompt_family}"
    prompt = read_prompt(prompt_version)
    version = {
        "level": level, "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "child_artifact_ids": children, "prompt_version": prompt_version,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "input_recipe_version": f"hierarchical-summary-{prompt_family}",
        "output_contract_version": "summary-string-v3" if prompt_family in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17") else "summary-string-v1",
    }
    record = manifest.begin("summary", version, model)
    if record.status == "ready" and record.path and (run_dir / record.path).is_file():
        existing = Summary.model_validate_json((run_dir / record.path).read_text(encoding="utf-8"))
        if (
            (existing.document_id, existing.run_id, existing.source_sha256)
            != (document.document_id, document.run_id, document.source_sha256)
            or existing.chunk_ids != [chunk.chunk_id for chunk in chunks]
            or existing.child_artifact_ids != children
            or existing.model != model
        ):
            raise ValueError("stored summary provenance is invalid")
        _validate_evidence_method_grounding(existing.summary, input_text)
        return existing, record.artifact_id
    manifest.save(run_dir)
    try:
        input_limit = {"v1": 14000, "v2": 24000, "v3": 60000, "v4": 60000, "v5": 60000, "v6": 60000, "v7": 60000, "v8": 60000, "v9": 60000, "v10": 60000, "v11": 60000, "v12": 60000, "v13": 60000, "v14": 60000, "v15": 60000, "v16": 60000, "v17": 60000}[prompt_family]
        if len(input_text) > input_limit:
            raise ValueError("summary input exceeds its character budget")
        result = client.generate_json(
            model=model, system_prompt=prompt, input_text=input_text,
            schema_name="summary", json_schema={
                "type": "object", "properties": {"summary": {"type": "string"}},
                "required": ["summary"], "additionalProperties": False,
            }, max_output_tokens=(1200 if prompt_family == "v6" else
                                  900 if prompt_family in ("v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17") else
                                  700 if prompt_family in ("v2", "v3", "v4", "v5") else 400),
        )
        body = result.data.get("summary")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("model returned an empty summary")
        _validate_evidence_method_grounding(body, input_text)
        artifact = Summary(
            document_id=document.document_id, run_id=document.run_id,
            source_sha256=document.source_sha256, level=level,
            summary=body.strip(), chunk_ids=[chunk.chunk_id for chunk in chunks],
            source_spans=[span for chunk in chunks for span in chunk.source_spans],
            child_artifact_ids=children, model=model,
            prompt_version=prompt_version, usage=result.usage,
        )
        path = write_artifact_json(run_dir, "summary", record.artifact_id, artifact.model_dump(mode="json"))
        manifest.succeed(record.artifact_id, path, result.usage)
        manifest.save(run_dir)
        return artifact, record.artifact_id
    except (ModelClientError, ValueError, OSError, TypeError) as exc:
        manifest.fail(record.artifact_id, type(exc).__name__)
        manifest.save(run_dir)
        raise


def summarize_document(
    document: ParsedDocument, chunks: ChunkSet, client: ModelClient,
    model: ModelSpec, manifest: RunManifest, run_dir: Path,
    *, max_sections: int | None = None, prompt_family: str = "v1",
) -> SummaryProgress:
    """Summarize up to max_sections missing chunks, then assemble when complete."""
    chunks.validate_against(document)
    if chunks.variant != "structural_raw" or (
        manifest.document_id, manifest.run_id, manifest.source_sha256
    ) != (document.document_id, document.run_id, document.source_sha256):
        raise ValueError("summary inputs do not match the structural source run")
    manifest.require_client_selection(client)
    if max_sections is not None and max_sections < 0:
        raise ValueError("max_sections cannot be negative")
    if prompt_family not in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"):
        raise ValueError("unsupported summary prompt family")
    section_family = "v8" if prompt_family in ("v9", "v10", "v11", "v12", "v13") else "v14" if prompt_family == "v15" else prompt_family
    source_header = f"Title: {document.metadata.title}"
    if prompt_family in ("v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"):
        source_header += f"\nSource kind: {document.source_kind}"
        if document.metadata.authors:
            source_header += f"\nSource author metadata: {', '.join(document.metadata.authors)}"
    if len(chunks.chunks) == 1:
        if max_sections == 0:
            return SummaryProgress(completed_sections=0, total_sections=1)
        chunk = chunks.chunks[0]
        summary, _ = _generate(
            document, [chunk], "document",
            f"{source_header}\n\n{chunk.raw_text}",
            [], client, model, manifest, run_dir, prompt_family,
        )
        return SummaryProgress(
            completed_sections=1, total_sections=1, document_summary=summary,
        )
    sections: list[tuple[Summary, str]] = []
    new_count = 0
    section_prompt = read_prompt(f"section_summary_{section_family}")
    section_prompt_hash = hashlib.sha256(section_prompt.encode()).hexdigest()
    for chunk in chunks.chunks:
        prior_ready = any(
            record.kind == "summary" and record.status == "ready"
            and record.version_inputs.get("level") == "section"
            and record.version_inputs.get("chunk_ids") == [chunk.chunk_id]
            and record.version_inputs.get("prompt_sha256") == section_prompt_hash
            and record.model == model and record.path is not None
            and (run_dir / record.path).is_file()
            for record in manifest.artifacts.values()
        )
        if max_sections is not None and new_count >= max_sections and not prior_ready:
            break
        if len(chunk.raw_text) > 13500:
            raise ValueError("structural chunk exceeds summary input budget")
        section_input = (
            f"{source_header}\n"
            f"Heading: {' > '.join(chunk.heading_path or [])}\n"
            f"Source chunk: {chunk.chunk_id}\n\n{chunk.raw_text}"
        )
        sections.append(_generate(document, [chunk], "section", section_input, [], client, model, manifest, run_dir, section_family))
        if not prior_ready:
            new_count += 1
    if len(sections) != len(chunks.chunks):
        return SummaryProgress(completed_sections=len(sections), total_sections=len(chunks.chunks))
    if prompt_family in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"):
        section_input = (source_header + "\n\n" if prompt_family in ("v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17") else "") + "\n\n".join(
            f"Section {index + 1}: {summary.summary}"
            for index, (summary, _) in enumerate(sections)
        )
        final, _ = _generate(
            document, chunks.chunks, "document", section_input,
            [artifact_id for _, artifact_id in sections],
            client, model, manifest, run_dir, prompt_family,
        )
        return SummaryProgress(
            completed_sections=len(sections), total_sections=len(sections),
            document_summary=final,
        )
    groups = sections
    while len(groups) > 1:
        next_groups: list[tuple[Summary, str]] = []
        for start in range(0, len(groups), 4):
            batch = groups[start:start + 4]
            batch_ids = {chunk_id for summary, _ in batch for chunk_id in summary.chunk_ids}
            batch_chunks = [chunk for chunk in chunks.chunks if chunk.chunk_id in batch_ids]
            group_input = "\n\n".join(
                f"Section {start + offset + 1}: {section.summary}"
                for offset, (section, _) in enumerate(batch)
            )
            next_groups.append(_generate(
                document, batch_chunks, "group", group_input,
                [artifact_id for _, artifact_id in batch], client, model, manifest, run_dir, prompt_family,
            ))
        if len(next_groups) == len(groups):
            break
        groups = next_groups
    final_input = groups[0][0].summary
    final, _ = _generate(
        document, chunks.chunks, "document", final_input,
        [artifact_id for _, artifact_id in groups], client, model, manifest, run_dir, prompt_family,
    )
    return SummaryProgress(
        completed_sections=len(sections), total_sections=len(sections),
        document_summary=final,
    )
