from types import SimpleNamespace

from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan, normalize_canonical_markdown
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.weknora_splitter import SplitterConfig, split_text_legacy
from app.services.document_parser.canonical_document import CanonicalDocument


def _cfg(*, child_size: int = 220) -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        chunk_size=512,
        chunk_overlap=40,
        parent_chunk_size=900,
        child_chunk_size=child_size,
        child_chunk_overlap=40,
        separators=["\\n\\n", "\\n", "。", ". "],
    )


def test_runtime_plan_preserves_short_structural_children_without_pairwise_merge(monkeypatch):
    import app.services.chunker.canonical_parent_child as module

    fake_result = SimpleNamespace(
        parents=[
            SimpleNamespace(
                content="## 结论\n\n完整短结论。\n\n补充说明。",
                context_header="## 结论",
                start=0,
                end=30,
                metadata={},
            )
        ],
        children=[
            SimpleNamespace(
                content="## 结论\n\n完整短结论。",
                context_header="## 结论",
                start=0,
                end=16,
                seq=0,
                parent_index=0,
                metadata={},
            ),
            SimpleNamespace(
                content="补充说明。",
                context_header="## 结论",
                start=18,
                end=24,
                seq=1,
                parent_index=0,
                metadata={},
            ),
        ],
    )
    monkeypatch.setattr(module, "split_parent_child_result", lambda markdown, cfg: fake_result)

    plan = build_canonical_parent_child_plan(
        CanonicalDocument(
            markdown_content="## 结论\n\n完整短结论。\n\n补充说明。",
            metadata={"canonical_source": "native_markdown"},
            source_format="md",
        ),
        _cfg(),
    )

    assert [child.content for child in plan.children] == ["## 结论\n\n完整短结论。", "补充说明。"]
    assert len(plan.parents) == 1
    assert plan.parents[0].metadata["children_count_planned"] == 2
    assert not any(child.metadata.get("short_sibling_merged") for child in plan.children)


def test_inline_html_and_math_are_atomic_for_legacy_splitter():
    text = (
        "前文说明。" * 8
        + "化合物 <sup>13</sup> 的结构式为 $E=mc^2$，随后继续进行分析。"
        + "后文说明。" * 12
    )
    chunks = split_text_legacy(
        text,
        SplitterConfig(
            chunk_size=90,
            chunk_overlap=20,
            separators=["\\n\\n", "\\n", "。", "，", " "],
            strategy="legacy",
        ),
    )
    contents = [chunk.content for chunk in chunks]

    assert any("<sup>13</sup>" in content for content in contents)
    assert any("$E=mc^2$" in content for content in contents)
    assert not any(content.endswith("<sup") or content.startswith(">13</sup>") for content in contents)
    assert not any(content.endswith("$E=") or content.startswith("mc^2$") for content in contents)


def test_long_code_line_is_not_character_sliced_during_canonical_normalization():
    long_line = "TOKEN_" + "A" * 500
    markdown = f"## 代码\\n\\n```text\\n{long_line}\\n```\\n"

    normalized = normalize_canonical_markdown(markdown, child_size=120)

    assert long_line in normalized
    # One physical source line stays one physical source line; only fences may
    # be repeated when a multi-line code block is split.
    assert normalized.count(long_line) == 1
    assert "\\n" not in long_line


def test_parent_integrity_does_not_modify_child_boundaries(monkeypatch):
    import app.services.chunker.canonical_parent_child as module

    fake_result = SimpleNamespace(
        parents=[
            SimpleNamespace(content="父块内容", context_header="## 节", start=0, end=20, metadata={})
        ],
        children=[
            SimpleNamespace(
                content="唯一正文。",
                context_header="## 节",
                start=5,
                end=10,
                seq=0,
                parent_index=0,
                metadata={},
            )
        ],
    )
    monkeypatch.setattr(module, "split_parent_child_result", lambda markdown, cfg: fake_result)

    plan = build_canonical_parent_child_plan(
        CanonicalDocument(markdown_content="唯一正文。", metadata={"canonical_source": "native_markdown"}, source_format="md"),
        _cfg(),
    )

    assert plan.parents == []
    assert len(plan.children) == 1
    assert plan.children[0].parent_index is None
    assert plan.children[0].content == "唯一正文。"
    assert plan.children[0].start == 5
    assert plan.children[0].end == 10
