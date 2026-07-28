"""Tests for pipeline debug artifacts module.

Covers: default-off, explicit-off, enabled saves, safe serializer,
sensitive field filtering, write-failure resilience.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.pipeline_debug_artifacts import (
    _is_sensitive_field,
    finalize_debug_run,
    get_debug_run,
    init_debug_run,
    safe_serialize,
    save_debug_canonical_document,
    save_debug_chunk_plan,
    save_debug_mineru_raw,
    save_debug_parsed_document,
    save_debug_relation_graph,
)


# ── Test data classes ──────────────────────────────────────────────────────


class Color(Enum):
    RED = "red"
    BLUE = "blue"


@dataclasses.dataclass
class NestedData:
    name: str
    value: int
    created: datetime


@dataclasses.dataclass
class HasBytes:
    label: str
    payload: bytes


@dataclasses.dataclass
class HasSensitive:
    username: str
    api_key: str
    secret_token: str
    password: str
    authorization: str
    access_key: str
    secret_key: str
    cookie: str
    normal_field: str


@dataclasses.dataclass
class SelfRef:
    name: str
    other: "SelfRef | None" = None


# ── Helpers ─────────────────────────────────────────────────────────────────


def _enable_debug(monkeypatch) -> None:
    """Set the debug flag to True on the already-imported settings singleton."""
    monkeypatch.setattr(
        "app.services.pipeline_debug_artifacts.settings."
        "document_pipeline_debug_artifacts_enabled",
        True,
    )


# ── safe_serialize ────────────────────────────────────────────────────────


class TestSafeSerialize:
    def test_primitives_pass_through(self):
        assert safe_serialize(None) is None
        assert safe_serialize(True) is True
        assert safe_serialize(42) == 42
        assert safe_serialize(3.14) == 3.14
        assert safe_serialize("hello") == "hello"

    def test_dict_serialized(self):
        result = safe_serialize({"a": 1, "b": [2, 3]})
        assert result == {"a": 1, "b": [2, 3]}

    def test_list_serialized(self):
        assert safe_serialize([1, "x", None]) == [1, "x", None]

    def test_tuple_converts_to_list(self):
        assert safe_serialize((1, 2, 3)) == [1, 2, 3]

    def test_set_converts_to_list(self):
        result = safe_serialize({1, 2})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2]

    def test_enum_to_value(self):
        assert safe_serialize(Color.RED) == "red"
        assert safe_serialize(Color.BLUE) == "blue"

    def test_uuid_to_string(self):
        uid = uuid.uuid4()
        assert safe_serialize(uid) == str(uid)

    def test_datetime_to_isoformat(self):
        dt = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
        assert safe_serialize(dt) == "2026-07-27T12:00:00+00:00"

    def test_naive_datetime_to_isoformat(self):
        dt = datetime(2026, 7, 27, 12, 0, 0)
        assert safe_serialize(dt) == "2026-07-27T12:00:00"

    def test_date_to_isoformat(self):
        d = date(2026, 7, 27)
        assert safe_serialize(d) == "2026-07-27"

    def test_path_to_string(self):
        p = Path("/tmp/foo/bar.txt")
        assert safe_serialize(p) == str(p)

    def test_bytes_records_length_only(self):
        result = safe_serialize(HasBytes(label="test", payload=b"hello world"))
        assert result["label"] == "test"
        assert result["payload"] == {"__bytes_length__": 11}

    def test_dataclass_recursive(self):
        nested = NestedData(
            name="test", value=42, created=datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
        )
        result = safe_serialize(nested)
        assert result["name"] == "test"
        assert result["value"] == 42
        assert result["created"] == "2026-07-27T12:00:00+00:00"

    def test_large_int_converts_to_string(self):
        large = 2**60
        result = safe_serialize(large)
        assert isinstance(result, str)

    def test_circular_reference_handled(self):
        a = SelfRef(name="a")
        b = SelfRef(name="b", other=a)
        a.other = b
        result = safe_serialize(a)
        assert result["name"] == "a"
        # b was not yet visited when we entered it from a, so it serializes fully
        assert result["other"]["name"] == "b"
        # b.other points back to a, which IS already visited → circular ref
        assert result["other"]["other"] == "<circular reference>"

    def test_max_depth_truncated(self):
        obj: dict = {}
        current = obj
        for _ in range(20):
            current["next"] = {}
            current = current["next"]
        current["value"] = "bottom"
        result = safe_serialize(obj)
        assert isinstance(result, dict)


# ── Sensitive field filtering ──────────────────────────────────────────────


class TestSensitiveFields:
    def test_is_sensitive_field(self):
        assert _is_sensitive_field("api_key") is True
        assert _is_sensitive_field("API_KEY") is True
        assert _is_sensitive_field("my_api_key_here") is True
        assert _is_sensitive_field("secret") is True
        assert _is_sensitive_field("password") is True
        assert _is_sensitive_field("access_key") is True
        assert _is_sensitive_field("secret_key") is True
        assert _is_sensitive_field("Authorization") is True
        assert _is_sensitive_field("token") is True
        assert _is_sensitive_field("cookie") is True

    def test_normal_field_not_sensitive(self):
        assert _is_sensitive_field("name") is False
        assert _is_sensitive_field("document_id") is False
        assert _is_sensitive_field("file_type") is False

    def test_sensitive_dataclass_redacted(self):
        obj = HasSensitive(
            username="alice",
            api_key="sk-12345",
            secret_token="st-67890",
            password="hunter2",
            authorization="Bearer xyz",
            access_key="AKIA...",
            secret_key="wJalrX...",
            cookie="session=abc",
            normal_field="safe",
        )
        result = safe_serialize(obj)
        assert result["username"] == "alice"
        assert result["api_key"] == "<redacted>"
        assert result["secret_token"] == "<redacted>"
        assert result["password"] == "<redacted>"
        assert result["authorization"] == "<redacted>"
        assert result["access_key"] == "<redacted>"
        assert result["secret_key"] == "<redacted>"
        assert result["cookie"] == "<redacted>"
        assert result["normal_field"] == "safe"

    def test_sensitive_dict_keys_redacted(self):
        obj = {"api_key": "sk-abc", "name": "safe-value"}
        result = safe_serialize(obj)
        assert result["api_key"] == "<redacted>"
        assert result["name"] == "safe-value"


# ── DebugRun lifecycle (with temp dir) ────────────────────────────────────


@pytest.fixture
def temp_cache_dir():
    """Provide a temporary cache directory and patch LOCAL_CACHE_DIR."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch(
            "app.services.pipeline_debug_artifacts.settings.local_cache_dir", tmp
        ):
            yield tmp


class TestDebugRunDefaultOff:
    def test_init_returns_none_when_flag_false(self):
        run = init_debug_run("doc-1")
        assert run is None
        assert get_debug_run() is None

    def test_save_functions_noop_when_disabled(self):
        init_debug_run("doc-1")
        # These should not raise
        save_debug_parsed_document("02", MagicMock())
        save_debug_canonical_document(MagicMock())
        save_debug_chunk_plan(MagicMock())
        save_debug_relation_graph(MagicMock())
        save_debug_mineru_raw(payload={})
        finalize_debug_run()
        assert get_debug_run() is None


class TestDebugRunEnabled:
    def test_enabled_creates_directory_structure(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run(
            "doc-test-1", task_id="task-abc", requested_parser="mineru"
        )
        assert run is not None
        assert run.document_id == "doc-test-1"
        assert run.run_id == "task-abc"

        run_dir = run._ensure_run_dir()
        assert Path(run_dir).exists()
        assert (Path(run_dir) / "00_manifest.json").exists()

        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["document_id"] == "doc-test-1"
        assert manifest["run_id"] == "task-abc"
        assert manifest["requested_parser"] == "mineru"
        assert "feature_flags" in manifest
        assert "completed_stages" in manifest
        # Sensitive information must not be in the manifest
        manifest_str = json.dumps(manifest)
        assert "jwt_secret" not in manifest_str.lower()
        assert "admin_password" not in manifest_str.lower()

        finalize_debug_run()
        assert get_debug_run() is None

    def test_parsed_document_saved_as_json(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-test-2")
        assert run is not None

        from app.services.document_parser.types import ParsedBlock, ParsedDocument

        doc = ParsedDocument(
            content="# Test\nHello world",
            blocks=[
                ParsedBlock(
                    block_type="text", text="Hello world", order_index=0, page_no=1
                ),
                ParsedBlock(
                    block_type="image",
                    image_path="/tmp/img.png",
                    order_index=1,
                    page_no=1,
                ),
            ],
            metadata={"parser": "mineru", "version": "4.0"},
        )
        save_debug_parsed_document("02_parsed_document", doc)

        run_dir = run._ensure_run_dir()
        out_file = Path(run_dir) / "02_parsed_document" / "02_parsed_document.json"
        assert out_file.exists()

        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["content"] == "# Test\nHello world"
        assert len(data["blocks"]) == 2
        assert data["blocks"][0]["block_type"] == "text"
        assert data["blocks"][1]["block_type"] == "image"
        raw = out_file.read_text(encoding="utf-8")
        assert "Hello world" in raw

        finalize_debug_run()

    def test_unicode_preserved_not_escaped(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-cn")
        assert run is not None

        from app.services.document_parser.types import ParsedBlock, ParsedDocument

        doc = ParsedDocument(
            content="王羲之 草书寒切帖卷",
            blocks=[ParsedBlock(block_type="text", text="书法作品", order_index=0)],
            metadata={"标题": "测试文档"},
        )
        save_debug_parsed_document("02_parsed_document", doc)

        run_dir = run._ensure_run_dir()
        out_file = Path(run_dir) / "02_parsed_document" / "02_parsed_document.json"
        raw = out_file.read_text(encoding="utf-8")
        assert "王羲之" in raw
        assert "草书寒切帖卷" in raw
        assert "书法作品" in raw
        assert "\\u738b" not in raw

        finalize_debug_run()

    def test_mineru_raw_saves_payload_and_markdown(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-mineru-1")
        assert run is not None

        save_debug_mineru_raw(
            payload={"backend": "pipeline", "version": "4.0"},
            content_list=[{"type": "text", "text": "test"}],
            image_map={"img_0": "images/img_0.png"},
            markdown="# Title\n\nContent",
            source_file_name="test.pdf",
        )

        run_dir = run._ensure_run_dir()
        stage = Path(run_dir) / "01_mineru_raw"
        assert stage.exists()
        assert (stage / "payload.json").exists()
        assert (stage / "content_list.json").exists()
        assert (stage / "full.md").exists()
        md = (stage / "full.md").read_text(encoding="utf-8")
        assert md == "# Title\n\nContent"

        finalize_debug_run()

    def test_canonical_saves_markdown_and_json(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-canon-1")
        assert run is not None

        fake_canonical = MagicMock()
        fake_canonical.markdown_content = "# Canonical\n\nBody"
        fake_canonical.provenance_nodes = [
            MagicMock(node_id="n1", node_type="text", source_order=0),
        ]
        fake_canonical.assets = {
            "a1": MagicMock(asset_id="a1", asset_type="image"),
        }

        save_debug_canonical_document(fake_canonical)

        run_dir = run._ensure_run_dir()
        stage = Path(run_dir) / "05_canonical"
        assert (stage / "canonical.md").exists()
        assert (stage / "source_nodes.json").exists()
        assert (stage / "assets.json").exists()

        md = (stage / "canonical.md").read_text(encoding="utf-8")
        assert md == "# Canonical\n\nBody"

        finalize_debug_run()

    def test_chunk_plan_saves_parents_and_children(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-plan-1")
        assert run is not None

        fake_plan = MagicMock()
        fake_plan.parents = [
            MagicMock(parent_index=0, content="Parent 1", start=0, end=100),
        ]
        fake_plan.children = [
            MagicMock(
                content="Child 1", chunk_type="text", parent_index=0, start=0, end=50
            ),
        ]

        save_debug_chunk_plan(fake_plan)

        run_dir = run._ensure_run_dir()
        stage = Path(run_dir) / "06_chunk_plan"
        assert (stage / "parents.json").exists()
        assert (stage / "children.json").exists()

        finalize_debug_run()

    def test_relation_graph_saved(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-rel-1")
        assert run is not None

        fake_graph = MagicMock()
        fake_graph.edges = []
        fake_graph.groups = []
        fake_graph.schema_version = 1

        save_debug_relation_graph(fake_graph)

        run_dir = run._ensure_run_dir()
        assert (Path(run_dir) / "07_relation_graph.json").exists()

        finalize_debug_run()

    def test_manifest_completed_stages_tracked(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-manifest-1")
        assert run is not None

        from app.services.document_parser.types import ParsedDocument

        doc = ParsedDocument(content="test", blocks=[])
        save_debug_parsed_document("02_parsed_document", doc)

        run_dir = run._ensure_run_dir()
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert "02_parsed_document" in manifest["completed_stages"]

        finalize_debug_run()


class TestDebugRunFailureResilience:
    def test_write_failure_does_not_raise(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-fail-1")
        assert run is not None

        from app.services.document_parser.types import ParsedDocument

        doc = ParsedDocument(content="test", blocks=[])

        # Simulate safe_serialize raising — the save method must not propagate
        with patch(
            "app.services.pipeline_debug_artifacts.safe_serialize",
            side_effect=PermissionError("Permission denied"),
        ):
            # Should not raise
            save_debug_parsed_document("02_parsed_document", doc)

        # Manifest should record the failure
        run_dir = run._ensure_run_dir()
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert "02_parsed_document" in manifest["failed_debug_stages"]

        finalize_debug_run()

    def test_serialization_error_does_not_crash(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-serialize-fail-1")
        assert run is not None

        # Object that will fail during safe_serialize
        class Unserializable:
            pass

        # Mock safe_serialize to raise
        with patch(
            "app.services.pipeline_debug_artifacts.safe_serialize",
            side_effect=RuntimeError("cannot serialize"),
        ):
            save_debug_parsed_document("02_parsed_document", Unserializable())

        run_dir = run._ensure_run_dir()
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert "02_parsed_document" in manifest["failed_debug_stages"]

        finalize_debug_run()

    def test_finalize_always_clears_context(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        init_debug_run("doc-clear-1")
        assert get_debug_run() is not None
        finalize_debug_run()
        assert get_debug_run() is None


class TestDebugRunEdgeCases:
    def test_run_id_falls_back_to_timestamp(self, temp_cache_dir, monkeypatch):
        """When task_id is None, run_id should be a timestamp."""
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-no-task")
        assert run is not None
        assert "T" in run.run_id
        finalize_debug_run()

    def test_update_info_after_init(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        run = init_debug_run("doc-update-1", requested_parser="auto")
        assert run is not None

        run.update_info(actual_parser="mineru", fallback_used=False)
        run_dir = run._ensure_run_dir()
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["actual_parser"] == "mineru"
        assert manifest["fallback_used"] is False

        run.update_info(fallback_used=True)
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["fallback_used"] is True

        finalize_debug_run()

    def test_mark_relation_graph_disabled(self, temp_cache_dir, monkeypatch):
        _enable_debug(monkeypatch)
        from app.services.pipeline_debug_artifacts import mark_debug_relation_graph_disabled

        run = init_debug_run("doc-norel-1")
        assert run is not None
        mark_debug_relation_graph_disabled()

        run_dir = run._ensure_run_dir()
        manifest = json.loads(
            (Path(run_dir) / "00_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["relation_graph"]["status"] == "disabled"

        finalize_debug_run()
