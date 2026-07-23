from pathlib import Path
from types import SimpleNamespace

from app.services.chunk_service import _apply_figure_bindings
from app.services.document_parser.figure_linker import (
    FigureLink,
    FigureLinkResult,
    FigureReference,
)
from app.services.retrieval_service import _expand_figure_bindings


def _chunk(
    chunk_id: str,
    chunk_type: str,
    *,
    start: int | None = None,
    end: int | None = None,
    metadata: dict | None = None,
    document_id: str = "doc-a",
    knowledge_base_id: str = "kb-a",
):
    return SimpleNamespace(
        id=chunk_id,
        chunk_type=chunk_type,
        start_at=start,
        end_at=end,
        metadata_json=metadata or {},
        content=chunk_id,
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        image_info={},
        parent_chunk_id=None,
        context_header="",
    )


def _link(start: int, end: int, *, figure_id: str = "figure:1") -> FigureLink:
    return FigureLink(
        reference=FigureReference(
            figure_id=figure_id,
            raw_text=figure_id,
            start=start,
            end=end,
        ),
        status="bound",
        asset_ids=("asset-1",),
        binding_method="figure_id_exact",
        binding_score=0.9,
    )


def _plan(*links: FigureLink):
    return SimpleNamespace(figure_links=FigureLinkResult(links=list(links)))


def test_baseline_reference_is_materialized_only_to_smallest_full_covering_child():
    partial = _chunk("partial", "text", start=0, end=6)
    full = _chunk("full", "text", start=3, end=12)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})

    _apply_figure_bindings([partial, full, image], _plan(_link(5, 8)))

    assert "figure_references" not in partial.metadata_json
    assert len(full.metadata_json["figure_references"]) == 1


def test_phase1_materializer_uses_input_order_when_carrier_ranges_are_equal():
    first = _chunk("z-random-id", "text", start=0, end=12)
    second = _chunk("a-random-id", "text", start=0, end=12)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})

    _apply_figure_bindings([first, second, image], _plan(_link(5, 8)))

    assert len(first.metadata_json["figure_references"]) == 1
    assert "figure_references" not in second.metadata_json


def test_phase1_materializer_excludes_non_prose_carriers_and_deduplicates_occurrence():
    prose = _chunk("prose", "text", start=0, end=20)
    equation = _chunk(
        "equation",
        "text",
        start=4,
        end=10,
        metadata={"content_kind": "equation"},
    )
    table = _chunk("table", "table", start=4, end=10)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})
    link = _link(5, 8)

    _apply_figure_bindings([prose, equation, table, image], _plan(link, link))

    assert len(prose.metadata_json["figure_references"]) == 1
    assert "figure_references" not in equation.metadata_json
    assert "figure_references" not in table.metadata_json
    assert len(image.metadata_json["image_bindings"]) == 1


def test_baseline_repeated_occurrences_in_one_child_are_not_collapsed():
    text = _chunk("text", "text", start=0, end=30)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})

    _apply_figure_bindings([text, image], _plan(_link(2, 4), _link(20, 22)))

    assert [item["start"] for item in text.metadata_json["figure_references"]] == [2, 20]


def test_baseline_image_preserves_each_incoming_binding():
    first = _chunk("text-1", "text", start=0, end=10)
    second = _chunk("text-2", "text", start=10, end=30)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})

    _apply_figure_bindings(
        [first, second, image],
        _plan(_link(2, 4, figure_id="figure:1"), _link(12, 14, figure_id="figure:2")),
    )

    assert [item["figure_id"] for item in image.metadata_json["image_bindings"]] == [
        "figure:1",
        "figure:2",
    ]


class _FakeQuery:
    def __init__(self, rows, *, error: Exception | None = None):
        self.rows = rows
        self.error = error

    def filter(self, *args):
        return self

    def all(self):
        if self.error:
            raise self.error
        return self.rows


class _FakeDB:
    def __init__(self, rows=(), *, error: Exception | None = None):
        self.rows = rows
        self.error = error

    def query(self, model):
        return _FakeQuery(self.rows, error=self.error)


def _result(chunk_id: str, metadata: dict, *, document_id="doc-a", knowledge_base_id="kb-a"):
    return {
        "id": chunk_id,
        "document_id": document_id,
        "knowledge_base_id": knowledge_base_id,
        "metadata_json": metadata,
        "score": 0.8,
    }


def test_baseline_expansion_rejects_cross_scope_target():
    direct = _result("text-a", {"figure_references": [{"image_chunk_ids": ["image-b"]}]})
    polluted = _chunk(
        "image-b",
        "image",
        document_id="doc-b",
        knowledge_base_id="kb-b",
    )

    expanded = _expand_figure_bindings(_FakeDB([polluted]), [direct])

    assert [item["id"] for item in expanded] == ["text-a"]


def test_baseline_expansion_budget_is_independent_for_each_direct_hit():
    direct = [
        _result("text-a", {"figure_references": [{"image_chunk_ids": ["i1", "i2", "i3"]}]}),
        _result("text-b", {"figure_references": [{"image_chunk_ids": ["i4", "i5", "i6"]}]}),
    ]
    rows = [_chunk(f"i{index}", "image") for index in range(1, 7)]

    expanded = _expand_figure_bindings(_FakeDB(rows), direct)

    assert [item["id"] for item in expanded[2:]] == ["i1", "i2", "i3", "i4", "i5", "i6"]


def test_phase1_expansion_budget_counts_only_new_eligible_targets():
    direct = [
        _result(
            "text-a",
            {"figure_references": [{"image_chunk_ids": ["i1", "i2", "i3", "i4"]}]},
        ),
        _result("i1", {}),
        _result("i2", {}),
        _result("i3", {}),
    ]
    rows = [_chunk(f"i{index}", "image") for index in range(1, 5)]

    expanded = _expand_figure_bindings(_FakeDB(rows), direct)

    assert [item["id"] for item in expanded] == ["text-a", "i1", "i2", "i3", "i4"]


def test_phase1_text_budget_is_independent_for_each_direct_hit():
    direct = [
        _result("image-a", {"image_bindings": [{"reference_chunk_ids": [f"t{i}" for i in range(1, 7)]}]}),
        _result("image-b", {"image_bindings": [{"reference_chunk_ids": [f"t{i}" for i in range(7, 13)]}]}),
    ]
    rows = [_chunk(f"t{index}", "text") for index in range(1, 13)]

    expanded = _expand_figure_bindings(_FakeDB(rows), direct)

    assert [item["id"] for item in expanded[2:]] == [
        "t1",
        "t2",
        "t3",
        "t4",
        "t5",
        "t7",
        "t8",
        "t9",
        "t10",
        "t11",
    ]


def test_baseline_expansion_restores_requested_order():
    direct = _result(
        "text-a",
        {"figure_references": [{"image_chunk_ids": ["i1", "i2", "i3"]}]},
    )
    rows = [_chunk("i3", "image"), _chunk("i2", "image"), _chunk("i1", "image")]

    expanded = _expand_figure_bindings(_FakeDB(rows), [direct])

    assert [item["id"] for item in expanded[1:]] == ["i1", "i2", "i3"]
    assert [item["chunk_id"] for item in expanded[1:]] == ["i1", "i2", "i3"]
    assert all(item["match_type"] == "relation_expansion" for item in expanded[1:])


def test_baseline_expansion_db_error_returns_original_hits():
    direct = _result("text-a", {"figure_references": [{"image_chunk_ids": ["i1"]}]})

    assert _expand_figure_bindings(_FakeDB(error=RuntimeError("db unavailable")), [direct]) == [
        direct
    ]


def test_phase1_materialization_and_expansion_can_be_disabled_independently():
    text = _chunk("text", "text", start=0, end=10)
    image = _chunk("image", "image", metadata={"asset_id": "asset-1"})

    _apply_figure_bindings([text, image], _plan(_link(2, 4)), enabled=False)
    assert "figure_references" not in text.metadata_json
    assert "image_bindings" not in image.metadata_json

    direct = _result("text-a", {"figure_references": [{"image_chunk_ids": ["image"]}]})
    assert _expand_figure_bindings(_FakeDB(error=RuntimeError("must not query")), [direct], enabled=False) == [
        direct
    ]


def test_phase1_expansion_dual_reads_ordered_and_legacy_image_bindings():
    direct = _result(
        "image-a",
        {
            "image_bindings": [
                {"reference_chunk_ids": ["t2"]},
                {"reference_chunk_ids": ["t1"]},
            ],
            "image_binding": {"reference_chunk_ids": ["t2"]},
        },
    )
    rows = [_chunk("t1", "text"), _chunk("t2", "text")]

    expanded = _expand_figure_bindings(_FakeDB(rows), [direct])

    assert [item["id"] for item in expanded] == ["image-a", "t2", "t1"]


def test_phase1_expansion_failure_records_degradation_without_changing_hits():
    direct = _result("text-a", {"figure_references": [{"image_chunk_ids": ["i1"]}]})
    messages: list[str] = []

    expanded = _expand_figure_bindings(
        _FakeDB(error=RuntimeError("db unavailable")),
        [direct],
        degradation_messages=messages,
    )

    assert expanded == [direct]
    assert messages == ["图文关系扩展失败，已保留原始检索结果"]


def test_phase1_pure_document_relation_package_has_no_orm_dependency():
    package_dir = Path(__file__).parents[1] / "app" / "services" / "document_relation"

    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "sqlalchemy" not in source.lower()
