from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


BlockType = Literal["text", "image", "table"]


@dataclass
class ParsedBlock:
    """A single ordered block produced by a document parser.

    The block model is intentionally small and project-local.  It borrows the
    RAGFlow idea of keeping text/table/image as different retrieval objects,
    while keeping the rest of know_rag independent from RAGFlow internals.
    """

    block_type: BlockType
    text: str = ""
    order_index: int = 0
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    image_path: str = ""
    image_url: str = ""
    image_base64: str = ""
    table_html: str = ""
    context_header: str = ""
    metadata: dict = field(default_factory=dict)

    def index_text(self) -> str:
        if self.block_type == "table":
            return self.text or self.table_html
        return self.text


@dataclass
class ParsedDocument:
    content: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # Exact source text when the input format has an authoritative textual
    # representation (currently Markdown).  ``content`` is allowed to be a
    # parser/reflow projection; this field must never be rebuilt from blocks.
    source_content: str | None = None

    @classmethod
    def from_text(cls, text: str, *, metadata: dict | None = None) -> "ParsedDocument":
        return cls(
            content=text,
            blocks=[ParsedBlock(block_type="text", text=text, order_index=0)] if text else [],
            metadata=metadata or {},
            source_content=text,
        )


def storage_relative_path(path: Path, storage_root: Path) -> str:
    try:
        return path.resolve().relative_to(storage_root.resolve()).as_posix()
    except Exception:
        return path.as_posix()
