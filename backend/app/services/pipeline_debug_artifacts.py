"""
Pipeline debug artifacts — save intermediate parse/chunk/relation results.

Controlled by ``DOCUMENT_PIPELINE_DEBUG_ARTIFACTS_ENABLED`` (default **false**).
When disabled this module is a no-op: no directories are created, no files are
written, and no large objects are serialized.

Usage in the main pipeline (thin calls only)::

    from app.services.pipeline_debug_artifacts import (
        init_debug_run,
        finalize_debug_run,
        save_debug_parsed_document,
        save_debug_canonical_document,
        save_debug_page_profile,
        save_debug_resolved_policies,
        save_debug_chunk_plan,
        save_debug_relation_graph,
        save_debug_mineru_raw,
    )

    debug_run = init_debug_run(
        document_id=doc_id,
        task_id=task_id,
        requested_parser=...,
        actual_parser=...,
    )
    try:
        ...
        save_debug_parsed_document("02_parsed_document", parsed_doc)
        save_debug_parsed_document("03_enhanced_document", enhanced_doc)
        ...
    finally:
        finalize_debug_run()
"""

from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Context variable for thread + asyncio safety ────────────────────────────
_current_run: contextvars.ContextVar[DebugRun | None] = contextvars.ContextVar(
    "pipeline_debug_run", default=None
)

# ── Sensitive field name patterns (case-insensitive substring match) ────────
_SENSITIVE_FIELD_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "secret",
    "password",
    "token",
    "authorization",
    "cookie",
    "access_key",
    "secret_key",
)


def _is_sensitive_field(key: str) -> bool:
    """Check whether *key* names a sensitive field that must be redacted."""
    lower = key.lower()
    return any(needle in lower for needle in _SENSITIVE_FIELD_SUBSTRINGS)


# ── Safe recursive serializer ───────────────────────────────────────────────

_SERIALIZE_MAX_DEPTH = 10
_REDACTED = "<redacted>"

# Large string stripping: strings that are mostly base64 or data URLs
# above this byte-length threshold are replaced with metadata only.
_BASE64_STRIP_THRESHOLD = 10_000  # 10 KB
_BASE64_LIKE_RE = re.compile(r"^[A-Za-z0-9+/=]{100,}$")
_DATA_URL_RE = re.compile(r"^data:[^;]*;base64,", re.I)


def _looks_like_base64_or_data_url(s: str) -> str | None:
    """Return a label if *s* is a base64 blob or data URL, else None."""
    if not s:
        return None
    if _DATA_URL_RE.match(s):
        return "data_url"
    # Check the first 500 chars for base64 character set
    if len(s) >= 100 and _BASE64_LIKE_RE.match(s[:500]):
        return "base64_blob"
    return None


def _strip_large_string(s: str) -> Any:
    """Replace a large base64/data-URL string with a metadata dict.

    Returns the dict for JSON output, or the original string if it is
    not a recognised large-format string.
    """
    label = _looks_like_base64_or_data_url(s)
    if label is None:
        # For very long non-base64 strings, still note length but keep content
        if len(s) > _BASE64_STRIP_THRESHOLD * 5:
            return {
                "__long_text_length__": len(s),
                "__sha256__": hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest(),
                "__text_prefix__": s[:200],
            }
        return s

    return {
        f"__{label}_length__": len(s),
        "__sha256__": hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest(),
        "__prefix__": s[:80],
    }


def safe_serialize(obj: Any, depth: int = 0, *, _visited: set[int] | None = None) -> Any:
    """Recursively serialise *obj* to a JSON-safe structure.

    - Dataclasses, Pydantic models, dicts, lists, tuples, sets, Enums,
      UUIDs, datetimes, dates, and Paths are handled.
    - ``bytes`` is recorded as ``{"__bytes_length__": <n>}`` (never base64).
    - Sensitive-looking keys are redacted.
    - Circular references are detected and replaced with a sentinel.
    - Max recursion depth is enforced; exceeded nesting is truncated.
    """
    if depth >= _SERIALIZE_MAX_DEPTH:
        return "<max recursion depth reached>"

    # ── Primitives ──────────────────────────────────────────────────────
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        if abs(obj) > 2**53:  # beyond JS safe integer
            return str(obj)
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, str):
        if len(obj) >= _BASE64_STRIP_THRESHOLD:
            stripped = _strip_large_string(obj)
            if not isinstance(stripped, str):
                return stripped
        return obj

    # ── Circular reference guard ────────────────────────────────────────
    if _visited is None:
        _visited = set()
    obj_id = id(obj)
    if obj_id in _visited:
        return "<circular reference>"
    _visited.add(obj_id)

    try:
        # ── Standard library types ──────────────────────────────────────
        if isinstance(obj, bytes):
            return {"__bytes_length__": len(obj)}

        if isinstance(obj, datetime):
            if obj.tzinfo is None:
                return obj.isoformat()
            return obj.isoformat()

        if isinstance(obj, date):
            return obj.isoformat()

        if isinstance(obj, Enum):
            return obj.value

        if isinstance(obj, uuid.UUID):
            return str(obj)

        if isinstance(obj, Path):
            return str(obj)

        if isinstance(obj, (set, frozenset, tuple)):
            return [safe_serialize(v, depth + 1, _visited=_visited) for v in obj]

        # ── dict-like ───────────────────────────────────────────────────
        if isinstance(obj, dict):
            result: dict[str, Any] = {}
            for k, v in obj.items():
                key_str = str(k)
                if _is_sensitive_field(key_str):
                    result[key_str] = _REDACTED
                else:
                    result[key_str] = safe_serialize(v, depth + 1, _visited=_visited)
            return result

        # ── Pydantic v2 models ──────────────────────────────────────────
        if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump", None)):
            try:
                raw = obj.model_dump()
            except Exception:
                raw = str(obj)
            if isinstance(raw, dict):
                return safe_serialize(raw, depth + 1, _visited=_visited)
            return raw

        # ── Dataclasses ─────────────────────────────────────────────────
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            result = {}
            for field in dataclasses.fields(obj):
                key = field.name
                if _is_sensitive_field(key):
                    result[key] = _REDACTED
                else:
                    result[key] = safe_serialize(
                        getattr(obj, field.name), depth + 1, _visited=_visited
                    )
            return result

        # ── Lists (after dataclass check so dataclasses aren't caught) ──
        if isinstance(obj, list):
            return [safe_serialize(v, depth + 1, _visited=_visited) for v in obj]

        # ── Generic objects: best-effort __dict__ ───────────────────────
        if hasattr(obj, "__dict__"):
            raw = vars(obj)
            if isinstance(raw, dict):
                return safe_serialize(raw, depth + 1, _visited=_visited)

        # ── Last resort ─────────────────────────────────────────────────
        return str(obj)

    finally:
        _visited.discard(obj_id)


def _write_json_atomic(path: Path, data: Any) -> None:
    """Write *data* as UTF-8 JSON to *path* atomically.

    Writes to a tempfile, flushes, then renames into place.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ── Debug run context ───────────────────────────────────────────────────────


class DebugRun:
    """Holds state for one document pipeline debug-artifact run."""

    def __init__(
        self,
        document_id: str,
        task_id: str | None = None,
        *,
        requested_parser: str = "",
        actual_parser: str = "",
        fallback_used: bool = False,
    ):
        self.document_id = document_id
        self.run_id = task_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S.%f"
        )
        self.run_dir: Path | None = None
        self.requested_parser = requested_parser
        self.actual_parser = actual_parser
        self.fallback_used = fallback_used
        self._manifest: dict[str, Any] | None = None
        self._mineru_source_dir: Path | None = None

    # ── Initialisation ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return True  # DebugRun is only created when the feature flag is on

    def _ensure_run_dir(self) -> Path:
        """Create (if needed) and return the run output directory."""
        if self.run_dir is not None:
            return self.run_dir
        base = Path(settings.local_cache_dir) / "pipeline_debug_artifacts"
        run_dir = base / str(self.document_id) / str(self.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = run_dir
        return run_dir

    def _stage_dir(self, name: str) -> Path:
        """Return ``run_dir / name``, creating the directory lazily."""
        d = self._ensure_run_dir() / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Manifest ────────────────────────────────────────────────────────────

    def _init_manifest(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id),
            "run_id": str(self.run_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_parser": self.requested_parser,
            "actual_parser": self.actual_parser,
            "fallback_used": self.fallback_used,
            "feature_flags": {
                "document_adaptive_chunk_policy_enabled": bool(
                    settings.document_adaptive_chunk_policy_enabled
                ),
                "document_page_visual_preservation_enabled": bool(
                    settings.document_page_visual_preservation_enabled
                ),
                "document_visual_ocr_quarantine_enabled": bool(
                    settings.document_visual_ocr_quarantine_enabled
                ),
                "document_field_content_admission_enabled": bool(
                    settings.document_field_content_admission_enabled
                ),
                "document_visual_section_parent_enabled": bool(
                    settings.document_visual_section_parent_enabled
                ),
                "document_relation_graph_enabled": bool(
                    settings.document_relation_graph_enabled
                ),
                "document_asset_group_enabled": bool(
                    settings.document_asset_group_enabled
                ),
                "document_relation_materialization_enabled": bool(
                    settings.document_relation_materialization_enabled
                ),
            },
            "completed_stages": [],
            "failed_debug_stages": {},
        }

    def _ensure_manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            self._manifest = self._init_manifest()
        return self._manifest

    def _write_manifest(self) -> None:
        manifest = self._ensure_manifest()
        _write_json_atomic(self._ensure_run_dir() / "00_manifest.json", manifest)

    def _mark_stage_completed(self, stage: str) -> None:
        m = self._ensure_manifest()
        if stage not in m["completed_stages"]:
            m["completed_stages"].append(stage)
        self._write_manifest()

    def _mark_stage_failed(self, stage: str, reason: str) -> None:
        m = self._ensure_manifest()
        m["failed_debug_stages"][stage] = reason
        self._write_manifest()
        logger.warning(
            "Debug artifact stage failed: document_id=%s run_id=%s stage=%s reason=%s",
            self.document_id,
            self.run_id,
            stage,
            reason,
        )

    # ── Checkpoint: MinerU raw ──────────────────────────────────────────────

    def save_mineru_raw(
        self,
        *,
        payload: dict[str, Any],
        content_list: list[dict] | None = None,
        image_map: dict[str, Any] | None = None,
        markdown: str | None = None,
        source_dir: Path | None = None,
        source_file_name: str = "",
    ) -> None:
        """Save MinerU raw response artifacts to ``01_mineru_raw/``."""
        stage = "01_mineru_raw"
        try:
            run_dir = self._stage_dir(stage)

            # Full raw payload
            _write_json_atomic(run_dir / "payload.json", safe_serialize(payload))

            # Content list
            if content_list is not None:
                _write_json_atomic(
                    run_dir / "content_list.json", safe_serialize(content_list)
                )

            # Markdown
            if markdown:
                _write_text_atomic(run_dir / "full.md", markdown)

            # Images — copy from source directory (best-effort)
            if source_dir is not None and source_dir.exists():
                images_dir = run_dir / "images"
                images_dir.mkdir(parents=True, exist_ok=True)
                copied = 0
                _copied_paths: set[Path] = set()
                if image_map:
                    for img_path_str in image_map.values():
                        if not isinstance(img_path_str, str) or not img_path_str.strip():
                            continue
                        src = _resolve_mineru_image_path(
                            img_path_str, source_dir, source_file_name
                        )
                        if src is not None and src.exists() and src not in _copied_paths:
                            _copied_paths.add(src)
                            try:
                                dst = images_dir / src.name
                                if not dst.exists():
                                    shutil.copy2(src, dst)
                                    copied += 1
                            except OSError:
                                pass
                # Also scan source dir for common image files
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    for src in source_dir.rglob(f"*{ext}"):
                        if src not in _copied_paths and src.is_file():
                            _copied_paths.add(src)
                            try:
                                dst = images_dir / src.name
                                if not dst.exists():
                                    shutil.copy2(src, dst)
                                    copied += 1
                            except OSError:
                                pass
                if copied:
                    logger.debug(
                        "Debug artifacts: copied %d images to %s", copied, images_dir
                    )
                elif not any(images_dir.iterdir()):
                    # No images found — note in manifest
                    (_write_json_atomic(
                        run_dir / "images.json",
                        {
                            "status": "no_images_found",
                            "source_dir": str(source_dir),
                            "image_map_keys": (
                                list(image_map.keys()) if image_map else []
                            ),
                        },
                    ))
            else:
                _write_json_atomic(
                    run_dir / "images.json",
                    {
                        "status": "no_source_dir",
                        "note": "Source directory not available; MinerU images not copied.",
                    },
                )

            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_mineru_raw failed")

    # ── Checkpoint: ParsedDocument ──────────────────────────────────────────

    def save_parsed_document(self, stage: str, doc: Any) -> None:
        """Save a ``ParsedDocument`` at a named checkpoint stage.

        *stage* should be one of ``"02_parsed_document"``,
        ``"03_enhanced_document"``, ``"04_reflowed_document"``.
        """
        try:
            serialized = safe_serialize(doc)
            target = self._stage_dir(stage) / f"{stage}.json"
            _write_json_atomic(target, serialized)
            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_parsed_document failed")

    # ── Checkpoint: CanonicalDocument ───────────────────────────────────────

    def save_canonical_document(self, canonical: Any) -> None:
        """Save ``CanonicalDocument`` to ``05_canonical/``."""
        stage = "05_canonical"
        try:
            d = self._stage_dir(stage)

            # canonical.md
            markdown = getattr(canonical, "markdown_content", "") or ""
            _write_text_atomic(d / "canonical.md", markdown)

            # source_nodes.json
            nodes = getattr(canonical, "provenance_nodes", []) or []
            _write_json_atomic(
                d / "source_nodes.json", safe_serialize(list(nodes))
            )

            # assets.json
            assets = getattr(canonical, "assets", {}) or {}
            _write_json_atomic(d / "assets.json", safe_serialize(assets))

            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_canonical_document failed")

    # ── Checkpoint: Page profile ────────────────────────────────────────────

    def save_page_profile(self, profile: Any) -> None:
        """Save ``DocumentProfile`` to ``05_canonical/page_profile.json``."""
        stage = "05_page_profile"
        try:
            d = self._stage_dir("05_canonical")
            _write_json_atomic(d / "page_profile.json", safe_serialize(profile))
            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_page_profile failed")

    # ── Checkpoint: Resolved policies ───────────────────────────────────────

    def save_resolved_policies(self, policies: Any) -> None:
        """Save resolved chunk policies to ``05_canonical/resolved_policies.json``."""
        stage = "05_resolved_policies"
        try:
            d = self._stage_dir("05_canonical")
            _write_json_atomic(
                d / "resolved_policies.json", safe_serialize(policies)
            )
            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_resolved_policies failed")

    # ── Checkpoint: Chunk plan ──────────────────────────────────────────────

    def save_chunk_plan(self, plan: Any) -> None:
        """Save ``CanonicalChunkPlan`` to ``06_chunk_plan/``."""
        stage = "06_chunk_plan"
        try:
            d = self._stage_dir(stage)

            parents = getattr(plan, "parents", []) or []
            _write_json_atomic(d / "parents.json", safe_serialize(list(parents)))

            children = getattr(plan, "children", []) or []
            _write_json_atomic(d / "children.json", safe_serialize(list(children)))

            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_chunk_plan failed")

    # ── Checkpoint: Relation graph ──────────────────────────────────────────

    def save_relation_graph(self, graph: Any) -> None:
        """Save ``CanonicalRelationGraph`` to ``07_relation_graph.json``."""
        stage = "07_relation_graph"
        try:
            serialized = safe_serialize(graph)
            target = self._ensure_run_dir() / "07_relation_graph.json"
            _write_json_atomic(target, serialized)
            self._mark_stage_completed(stage)
        except Exception:
            self._mark_stage_failed(stage, "save_relation_graph failed")

    def mark_relation_graph_disabled(self) -> None:
        """Record that relation graph generation was disabled."""
        try:
            m = self._ensure_manifest()
            if "relation_graph" not in m:
                m["relation_graph"] = {"status": "disabled"}
            self._write_manifest()
        except Exception:
            pass

    def update_info(
        self,
        *,
        actual_parser: str | None = None,
        fallback_used: bool | None = None,
        requested_parser: str | None = None,
    ) -> None:
        """Update manifest fields that may only be known after parsing."""
        try:
            m = self._ensure_manifest()
            if actual_parser is not None:
                m["actual_parser"] = actual_parser
            if fallback_used is not None:
                m["fallback_used"] = fallback_used
            if requested_parser is not None:
                m["requested_parser"] = requested_parser
            self._write_manifest()
        except Exception:
            pass

    # ── Finalisation ────────────────────────────────────────────────────────

    def finalize(self) -> None:
        """Flush the manifest and mark the run complete."""
        try:
            m = self._ensure_manifest()
            m["finalized_at"] = datetime.now(timezone.utc).isoformat()
            self._write_manifest()
            if self.run_dir is not None:
                logger.info(
                    "Debug artifacts saved: document_id=%s run_id=%s dir=%s stages=%s",
                    self.document_id,
                    self.run_id,
                    str(self.run_dir),
                    m.get("completed_stages", []),
                )
        except Exception:
            logger.warning(
                "Failed to finalize debug artifact manifest: document_id=%s run_id=%s",
                self.document_id,
                self.run_id,
                exc_info=True,
            )


# ── Module-level API (thin delegation to current DebugRun) ──────────────────


def init_debug_run(
    document_id: str,
    task_id: str | None = None,
    *,
    requested_parser: str = "",
    actual_parser: str = "",
    fallback_used: bool = False,
) -> DebugRun | None:
    """Create and register the current debug run (if the feature flag is on).

    Returns the ``DebugRun`` instance, or ``None`` when disabled.
    Must be called once per document before any ``save_debug_*`` calls.
    Call ``finalize_debug_run()`` when the pipeline finishes.
    """
    if not bool(settings.document_pipeline_debug_artifacts_enabled):
        _current_run.set(None)
        return None
    run = DebugRun(
        document_id=str(document_id),
        task_id=task_id,
        requested_parser=requested_parser,
        actual_parser=actual_parser,
        fallback_used=fallback_used,
    )
    _current_run.set(run)
    run._write_manifest()
    logger.debug(
        "Debug artifact run started: document_id=%s run_id=%s",
        run.document_id,
        run.run_id,
    )
    return run


def get_debug_run() -> DebugRun | None:
    """Return the current ``DebugRun`` or ``None``."""
    return _current_run.get()


def finalize_debug_run() -> None:
    """Finalize the current debug run (safe to call when disabled)."""
    run = _current_run.get()
    if run is not None:
        run.finalize()
    _current_run.set(None)


# ── Thin per-stage save helpers ─────────────────────────────────────────────


def save_debug_mineru_raw(
    *,
    payload: dict[str, Any],
    content_list: list[dict] | None = None,
    image_map: dict[str, Any] | None = None,
    markdown: str | None = None,
    source_dir: Path | None = None,
    source_file_name: str = "",
) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_mineru_raw(
        payload=payload,
        content_list=content_list,
        image_map=image_map,
        markdown=markdown,
        source_dir=source_dir,
        source_file_name=source_file_name,
    )


def save_debug_parsed_document(stage: str, doc: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_parsed_document(stage, doc)


def save_debug_canonical_document(canonical: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_canonical_document(canonical)


def save_debug_page_profile(profile: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_page_profile(profile)


def save_debug_resolved_policies(policies: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_resolved_policies(policies)


def save_debug_chunk_plan(plan: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_chunk_plan(plan)


def save_debug_relation_graph(graph: Any) -> None:
    """Thin wrapper — safe to call when debug artifacts are disabled."""
    run = _current_run.get()
    if run is None:
        return
    run.save_relation_graph(graph)


def mark_debug_relation_graph_disabled() -> None:
    """Record in manifest that relation graph was not generated (feature flag off)."""
    run = _current_run.get()
    if run is None:
        return
    run.mark_relation_graph_disabled()


def update_debug_run_info(
    *,
    actual_parser: str | None = None,
    fallback_used: bool | None = None,
    requested_parser: str | None = None,
) -> None:
    """Update manifest fields after parsing completes."""
    run = _current_run.get()
    if run is None:
        return
    run.update_info(
        actual_parser=actual_parser,
        fallback_used=fallback_used,
        requested_parser=requested_parser,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_mineru_image_path(
    img_path_str: str, source_dir: Path, source_file_name: str
) -> Path | None:
    """Resolve a MinerU image path string to an actual file on disk.

    MinerU returns paths that may be absolute (Windows or Linux), relative,
    or key-based.  We try several strategies.
    """
    if not img_path_str.strip():
        return None

    # Direct path (may be a backend-visible path already)
    direct = Path(img_path_str)
    if direct.exists():
        return direct

    # Relative to source directory
    relative = source_dir / Path(img_path_str).name
    if relative.exists():
        return relative

    # Search recursively
    name = Path(img_path_str).name
    for candidate in source_dir.rglob(name):
        if candidate.is_file():
            return candidate

    # Try parent of source_dir (images/ subdirectory)
    images_dir = source_dir / "images"
    if images_dir.exists():
        for candidate in images_dir.iterdir():
            if candidate.is_file():
                return candidate

    return None
