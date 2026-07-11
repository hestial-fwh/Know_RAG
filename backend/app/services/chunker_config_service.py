from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import ChunkerConfig
from app.services.chunker.models import ChunkerConfig as RuntimeChunkerConfig
from app.services.chunker.service import parse_separators
from app.utils.ids import new_id


ALLOWED_STRATEGIES = {"auto", "heading", "heuristic", "recursive", "legacy"}


def default_separators() -> list[str]:
    return parse_separators(settings.chunk_separators)


def config_to_dict(row: ChunkerConfig) -> dict:
    return {
        "id": row.id,
        "strategy": row.strategy,
        "chunk_size": row.chunk_size,
        "chunk_overlap": row.chunk_overlap,
        "separators": row.separators or default_separators(),
        "enable_parent_child": row.enable_parent_child,
        "parent_chunk_size": row.parent_chunk_size,
        "child_chunk_size": row.child_chunk_size,
        "child_chunk_overlap": row.child_chunk_overlap,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_or_create_chunker_config(db: Session) -> ChunkerConfig:
    row = db.query(ChunkerConfig).first()
    if row is not None:
        return row
    row = ChunkerConfig(
        id=new_id(),
        strategy=settings.chunk_strategy,
        chunk_size=settings.default_chunk_size,
        chunk_overlap=settings.default_chunk_overlap,
        separators=default_separators(),
        enable_parent_child=settings.enable_parent_child_chunking,
        parent_chunk_size=settings.parent_chunk_size,
        child_chunk_size=settings.child_chunk_size,
        child_chunk_overlap=settings.child_chunk_overlap,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_chunker_config(db: Session):
    return success_response(config_to_dict(get_or_create_chunker_config(db)))


def validate_payload(payload: dict) -> str | None:
    strategy = (payload.get("strategy") or "auto").lower()
    if strategy not in ALLOWED_STRATEGIES:
        return "切分策略必须是 auto、heading、heuristic、recursive 或 legacy"
    chunk_size = int(payload.get("chunk_size") or settings.default_chunk_size)
    chunk_overlap = int(payload.get("chunk_overlap") or 0)
    if chunk_overlap >= chunk_size:
        return "切片重叠长度必须小于切片大小"
    child_chunk_size = int(payload.get("child_chunk_size") or settings.child_chunk_size)
    child_chunk_overlap = int(payload.get("child_chunk_overlap") or 0)
    if child_chunk_overlap >= child_chunk_size:
        return "子切片重叠长度必须小于子切片大小"
    parent_chunk_size = int(payload.get("parent_chunk_size") or settings.parent_chunk_size)
    if parent_chunk_size <= child_chunk_size:
        return "父切片大小必须大于子切片大小"
    return None


def update_chunker_config(db: Session, payload: dict):
    error = validate_payload(payload)
    if error:
        return error_response(error)
    row = get_or_create_chunker_config(db)
    row.strategy = (payload.get("strategy") or "auto").lower()
    row.chunk_size = int(payload.get("chunk_size") or settings.default_chunk_size)
    row.chunk_overlap = int(payload.get("chunk_overlap") or settings.default_chunk_overlap)
    row.separators = payload.get("separators") or default_separators()
    row.enable_parent_child = bool(payload.get("enable_parent_child", False))
    row.parent_chunk_size = int(payload.get("parent_chunk_size") or settings.parent_chunk_size)
    row.child_chunk_size = int(payload.get("child_chunk_size") or settings.child_chunk_size)
    row.child_chunk_overlap = int(payload.get("child_chunk_overlap") or settings.child_chunk_overlap)
    db.commit()
    db.refresh(row)
    return success_response(config_to_dict(row), "切分配置已保存")


def runtime_config_from_db(db: Session) -> RuntimeChunkerConfig:
    row = get_or_create_chunker_config(db)
    return RuntimeChunkerConfig(
        chunk_size=row.chunk_size,
        chunk_overlap=row.chunk_overlap,
        strategy=row.strategy,
        separators=row.separators or default_separators(),
        enable_parent_child=row.enable_parent_child,
        parent_chunk_size=row.parent_chunk_size,
        child_chunk_size=row.child_chunk_size,
        child_chunk_overlap=row.child_chunk_overlap,
        token_limit=0,
        languages=[],
        enable_structured_parent_child=True,
        enable_table_row_split=False,
        enable_image_ocr_child=True,
    )
