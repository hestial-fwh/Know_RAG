from dataclasses import dataclass, field


@dataclass
class ParsedChunk:
    content: str
    context_header: str
    seq: int
    start: int
    end: int
    metadata: dict = field(default_factory=dict)
    chunk_type: str = "text"
    parent_index: int | None = None
    source_section: str | None = None


@dataclass
class ChunkerConfig:
    chunk_size: int = 512
    chunk_overlap: int = 80
    strategy: str = "auto"
    separators: list[str] = field(default_factory=list)
    enable_parent_child: bool = False
    parent_chunk_size: int = 4096
    child_chunk_size: int = 384
    child_chunk_overlap: int = 64
    token_limit: int = 0
    languages: list[str] = field(default_factory=list)
    enable_structured_parent_child: bool = True
    enable_table_row_split: bool = False
    enable_image_ocr_child: bool = True
