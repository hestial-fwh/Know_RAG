from app.core.config import settings
from app.services.chunker.markdown_splitter import split_markdown
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.parent_child_splitter import split_parent_child
from app.utils.text import sanitize_text_for_postgres


def parse_separators(raw: str) -> list[str]:
    if not raw:
        return ["\n\n", "\n", "。", ".", "；", ";", "，", ",", " "]
    return [item.replace("\\n", "\n") for item in raw.split("|") if item != ""]


def build_config(
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    enable_parent_child: bool | None = None,
) -> ChunkerConfig:
    return ChunkerConfig(
        chunk_size=chunk_size or settings.default_chunk_size,
        chunk_overlap=settings.default_chunk_overlap if chunk_overlap is None else chunk_overlap,
        strategy=settings.chunk_strategy,
        separators=parse_separators(settings.chunk_separators),
        enable_parent_child=settings.enable_parent_child_chunking if enable_parent_child is None else enable_parent_child,
        parent_chunk_size=settings.parent_chunk_size,
        child_chunk_size=settings.child_chunk_size,
        child_chunk_overlap=settings.child_chunk_overlap,
        token_limit=0,
        languages=[],
        enable_structured_parent_child=True,
        enable_table_row_split=False,
        enable_image_ocr_child=True,
    )


def split_document(text: str, file_type: str, cfg: ChunkerConfig | None = None) -> list[ParsedChunk]:
    cfg = cfg or build_config()
    clean_text = sanitize_text_for_postgres(text)
    if not clean_text:
        return []
    if cfg.enable_parent_child:
        chunks = split_parent_child(clean_text, cfg)
    else:
        chunks = split_markdown(clean_text, cfg)
    for index, chunk in enumerate(chunks):
        chunk.metadata.setdefault("source", "parser")
        chunk.metadata.setdefault("parser", "basic")
        chunk.metadata.setdefault("file_type", file_type)
        chunk.metadata.setdefault("heading_path", [])
        chunk.metadata.setdefault("seq", chunk.seq if chunk.chunk_type == "text" else index)
        chunk.metadata.setdefault("strategy", (cfg.strategy or "auto").lower())
    return chunks
