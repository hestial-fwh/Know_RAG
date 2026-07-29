import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import (
    Chunk,
    Document,
    DocumentCanonicalArtifact,
    KnowledgeBase,
    User,
)
from app.db.session import SessionLocal
from app.services.audit_service import record_audit
from app.services.chunk_service import rebuild_document_chunks, update_knowledge_base_chunk_count
from app.services.parser_service import parse_local_document
from app.services.storage import delete_document_objects, object_key_for_source, save_upload_file as save_upload_file_to_minio
from app.services.storage import delete_document_cache

from app.services.document_parser.block_reflow import reflow_parsed_document
from app.services.pipeline_debug_artifacts import (
    finalize_debug_run,
    init_debug_run,
    save_debug_parsed_document,
    update_debug_run_info,
)
from app.services.vision_service import enhance_image_blocks, enhance_ocr_blocks
from app.services import parse_task_service
from app.utils.files import ensure_dir
from app.utils.ids import new_id
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


SUPPORTED_FILE_TYPES = None  # Phase N: accept all file suffixes; parser falls back safely for unknown formats.
PARSE_STORE_ERROR = "文档解析失败：文本包含非法字符或切片入库失败"


def document_to_dict(db: Session, document: Document) -> dict:
    chunk_count = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)) or 0
    type_rows = (
        db.query(Chunk.chunk_type, func.count())
        .filter(Chunk.document_id == document.id)
        .group_by(Chunk.chunk_type)
        .all()
    )
    chunk_type_counts = {str(chunk_type or "text"): int(count or 0) for chunk_type, count in type_rows}
    metadata = document.metadata_json or {}
    parse_statistics = {
        "parsed_block_count": metadata.get("parsed_block_count", 0),
        "text_block_count": metadata.get("text_block_count", 0),
        "table_block_count": metadata.get("table_block_count", 0),
        "image_block_count": metadata.get("image_block_count", 0),
        "enhanced_image_count": metadata.get("enhanced_image_count", 0),
        "ocr_enhanced_count": metadata.get("ocr_enhanced_count", 0),
        "chunk_type_counts": chunk_type_counts,
        "parser": metadata.get("parser", ""),
        "parser_actual": metadata.get("parser_actual", metadata.get("parser", "")),
        "parser_requested": metadata.get("parser_requested", metadata.get("parser_attempted", "")),
        "parser_attempted": metadata.get("parser_attempted", ""),
        "parser_preference": metadata.get("parser_preference", ""),
        "parser_fallback_reason": metadata.get("parser_fallback_reason", ""),
        "fallback_used": metadata.get("fallback_used", False),
        "fallback_reason": metadata.get("fallback_reason", metadata.get("parser_fallback_reason", "")),
        "document_parser_engine": metadata.get("document_parser_engine", ""),
        "pdf_engine": metadata.get("pdf_engine", ""),
        "mineru_pdf_enabled": metadata.get("mineru_pdf_enabled", False),
        "mineru_content_list_count": metadata.get("mineru_content_list_count", 0),
        "mineru_config_source": metadata.get("mineru_config_source", ""),
        "mineru_endpoint": metadata.get("mineru_endpoint", ""),
        "mineru_base_url": metadata.get("mineru_base_url", ""),
        "block_reflow_engine": metadata.get("block_reflow_engine", ""),
        "block_reflow_enabled": metadata.get("block_reflow_enabled", False),
        "reflow_input_blocks": metadata.get("reflow_input_blocks", 0),
        "reflow_output_blocks": metadata.get("reflow_output_blocks", 0),
        "reflow_merged_blocks": metadata.get("reflow_merged_blocks", 0),
        "ocr_repair_enabled": metadata.get("ocr_repair_enabled", False),
        "ocr_repaired_blocks": metadata.get("ocr_repaired_blocks", 0),
        "table_crop_count": metadata.get("table_crop_count", 0),
        "figure_crop_count": metadata.get("figure_crop_count", 0),
        "scanned_page_image_count": metadata.get("scanned_page_image_count", 0),
        "ocr_failed_count": metadata.get("ocr_failed_count", 0),
        "vlm_failed_count": metadata.get("vlm_failed_count", 0),
        "layout_recognizer": metadata.get("layout_recognizer", ""),
        "table_structure_recognizer": metadata.get("table_structure_recognizer", ""),
        "vertical_concat_engine": metadata.get("vertical_concat_engine", ""),
        "figure_caption_binding": metadata.get("figure_caption_binding", ""),
        "multi_column_ordering": metadata.get("multi_column_ordering", ""),
        "caption_bound_count": metadata.get("caption_bound_count", 0),
    }
    return {
        "id": document.id,
        "document_id": document.id,
        "knowledge_id": document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "title": document.title,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "source": document.source or "file",
        "channel": document.channel or "local",
        "metadata": metadata,
        "metadata_json": metadata,
        "enable_status": document.enable_status or "enabled",
        "file_size": document.file_size,
        "file_hash": document.file_hash,
        "storage_path": document.storage_path,
        "parse_status": document.parse_status,
        "error_message": document.error_message,
        "processed_at": document.processed_at,
        "chunk_count": chunk_count,
        "parse_statistics": parse_statistics,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def safe_filename(filename: str | None) -> str:
    return Path(filename or "").name.strip()


def update_knowledge_base_document_count(db: Session, knowledge_base_id: str) -> None:
    count = db.scalar(
        select(func.count()).select_from(Document).where(Document.knowledge_base_id == knowledge_base_id)
    ) or 0
    kb = db.get(KnowledgeBase, knowledge_base_id)
    if kb is not None:
        kb.document_count = count


def ensure_supported_file(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower().strip()
    if not suffix:
        return None
    # Keep potentially dangerous executable uploads out of the knowledge base,
    # but otherwise align with RAGFlow/WeKnora-style broad document ingestion.
    blocked = {".exe", ".dll", ".bat", ".cmd", ".msi", ".ps1", ".sh", ".com", ".scr"}
    if suffix in blocked:
        return None
    return suffix.lstrip(".")


def save_upload_file(upload: UploadFile, target: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    ensure_dir(target.parent)
    with target.open("wb") as output:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            hasher.update(chunk)
            output.write(chunk)
    return size, hasher.hexdigest()


def mark_document_failed(db: Session, doc_id: str, message: str) -> None:
    document = db.get(Document, doc_id)
    if document is None:
        return
    preserved_chunk_count = int(
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.document_id == document.id)
        )
        or 0
    )
    document.parse_status = "failed"
    document.error_message = message[:300]
    document.metadata_json = sanitize_json_for_postgres(
        {
            **(document.metadata_json or {}),
            "latest_parse_status": "failed",
            "latest_parse_error": message[:300],
            "previous_chunks_preserved": preserved_chunk_count > 0,
            "chunk_count": preserved_chunk_count,
        }
    )
    update_knowledge_base_chunk_count(db, document.knowledge_base_id)
    db.commit()


async def upload_document(
    db: Session,
    actor: User,
    upload: UploadFile,
    knowledge_base_id: str,
    title: str | None = None,
    parse_immediately: bool = True,
    background_tasks: BackgroundTasks | None = None,
    parser_engine: str | None = None,
    mineru_profile: str | None = None,
    enable_ocr: str | None = None,
    enable_vlm: bool | None = None,
    enable_media_crop: bool | None = None,
):
    kb = db.get(KnowledgeBase, knowledge_base_id)
    if kb is None:
        return error_response(f"知识库不存在：{knowledge_base_id}")
    filename = safe_filename(upload.filename)
    if not filename:
        return error_response("文件名不能为空")
    file_type = ensure_supported_file(filename)
    if file_type is None:
        return error_response("暂不支持该文件类型或该文件类型存在安全风险")

    document_id = new_id()
    object_key = object_key_for_source(knowledge_base_id, document_id, filename)
    try:
        stored = save_upload_file_to_minio(upload, object_key)
        file_size, file_hash = stored.size, stored.sha256
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        return error_response(f"文件上传到 MinIO 失败：{str(exc)[:200]}")

    parse_options = {
        "parser_engine": (parser_engine or "auto").strip().lower(),
        "mineru_profile": (mineru_profile or getattr(settings, "mineru_runtime_profile", "cpu") or "cpu").strip().lower(),
        "enable_ocr": (enable_ocr or "auto").strip().lower(),
        "enable_vlm": True if enable_vlm is None else bool(enable_vlm),
        "enable_media_crop": True if enable_media_crop is None else bool(enable_media_crop),
    }
    if parse_options["parser_engine"] == "deepdoc":
        parse_options["parser_engine"] = "ragflow"
    if parse_options["parser_engine"] not in {"auto", "mineru", "ragflow", "legacy"}:
        parse_options["parser_engine"] = "auto"
    if parse_options["mineru_profile"] not in {"cpu", "gpu"}:
        parse_options["mineru_profile"] = "cpu"

    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        title=sanitize_text_for_postgres(title or Path(filename).stem) or filename,
        file_name=filename,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        storage_path=stored.uri,
        parse_status="pending",
        error_message="",
        source="file",
        channel="minio",
        metadata_json={
            "parse_options": parse_options,
            "storage": {
                "backend": "minio",
                "bucket": stored.bucket,
                "object_key": stored.object_key,
                "uri": stored.uri,
            }
        },
        enable_status="enabled",
    )
    try:
        db.add(document)
        db.flush()
        update_knowledge_base_document_count(db, knowledge_base_id)
        record_audit(
            db,
            actor,
            "upload_document",
            "document",
            document.id,
            request_summary={
                "document_id": document.id,
                "knowledge_base_id": knowledge_base_id,
                "file_name": filename,
                "file_size": file_size,
                "parse_immediately": parse_immediately,
                "parse_options": parse_options,
            },
        )
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        try:
            delete_document_objects(knowledge_base_id, document_id)
        except Exception:
            pass
        return error_response("文档上传入库失败")

    data = document_to_dict(db, document)
    if parse_immediately:
        if background_tasks is not None:
            task = parse_task_service.create_parse_task(db, document, actor, task_type="parse", commit=True)
            background_tasks.add_task(run_parse_document_task, document.id, actor.id, task.id, False)
            data["parse_task"] = parse_task_service.task_to_dict(task)
            return success_response(data, "上传成功，解析任务已启动")
        return await parse_document(db, actor, document.id, reparse=False, audit_action="parse_document")
    return success_response(data, "上传成功")


def start_parse_document_task(
    db: Session,
    actor: User,
    doc_id: str,
    background_tasks: BackgroundTasks,
    *,
    reparse: bool = False,
):
    """Create a parse workbench task for an existing document and run it in background.

    This keeps the document detail page responsive: the API returns immediately
    with parse_task metadata, then the frontend navigates to /parse-tasks/{id}.
    """
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")
    try:
        document.parse_status = "parsing"
        document.error_message = ""
        db.flush()
        task = parse_task_service.create_parse_task(db, document, actor, task_type="reparse" if reparse else "parse", commit=False)
        db.commit()
        db.refresh(document)
        db.refresh(task)
    except Exception:
        db.rollback()
        return error_response("解析任务创建失败")

    background_tasks.add_task(run_parse_document_task, document.id, actor.id, task.id, reparse)
    data = document_to_dict(db, document)
    data["parse_task"] = parse_task_service.task_to_dict(task)
    return success_response(data, "解析任务已启动")


def list_documents(db: Session, kb_id: str, page: int = 1, page_size: int = 20):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    total = db.scalar(select(func.count()).select_from(Document).where(Document.knowledge_base_id == kb_id)) or 0
    items = db.scalars(
        select(Document)
        .where(Document.knowledge_base_id == kb_id)
        .order_by(Document.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return success_response(
        {
            "knowledge_base_id": kb_id,
            "items": [document_to_dict(db, item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


def get_document(db: Session, doc_id: str):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")
    return success_response(document_to_dict(db, document))


def get_document_visual_layout(
    db: Session,
    doc_id: str,
    *,
    page_no: int | None = None,
):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response("文档不存在")
    artifact = db.get(DocumentCanonicalArtifact, doc_id)
    if artifact is None:
        return success_response(
            {
                "document_id": doc_id,
                "available": False,
                "reason": "canonical_artifact_missing",
                "layout": None,
            }
        )
    layout = (artifact.stats_json or {}).get("document_visual_layout")
    if not isinstance(layout, dict):
        return success_response(
            {
                "document_id": doc_id,
                "available": False,
                "reason": "visual_layout_not_generated",
                "parse_revision": artifact.parse_revision,
                "layout": None,
            }
        )
    layout_pages = [
        page
        for page in layout.get("pages") or []
        if isinstance(page, dict) and page.get("page_no") is not None
    ]
    available_pages = [
        {
            "page_no": page.get("page_no"),
            "extent_source": page.get("extent_source"),
            "fallback_reason": page.get("fallback_reason"),
            "summary": page.get("summary") or {},
        }
        for page in layout_pages
    ]
    selected_pages = (
        [
            page
            for page in layout_pages
            if int(page.get("page_no")) == page_no
        ]
        if page_no is not None
        else []
    )
    selected_asset_ids = {
        str(asset_id)
        for page in selected_pages
        for item in page.get("visual_items") or []
        for asset_id in item.get("asset_ids") or []
    }
    assets = {
        str(item.get("asset_id") or ""): {
            "asset_id": item.get("asset_id"),
            "page_no": item.get("page_no"),
            "caption": item.get("caption"),
            "image_url": item.get("image_url"),
            "image_path": item.get("image_path"),
            "bbox": item.get("bbox"),
            "visual_role": (item.get("metadata") or {}).get("visual_role"),
        }
        for item in (artifact.assets_json or {}).get("items") or []
        if str(item.get("asset_id") or "") in selected_asset_ids
    }
    response_layout = {**layout, "pages": selected_pages}
    return success_response(
        {
            "document_id": doc_id,
            "available": True,
            "parse_revision": artifact.parse_revision,
            "algorithm_version": layout.get("algorithm_version"),
            "shadow_only": layout.get("shadow_only", True),
            "requested_page_no": page_no,
            "available_pages": available_pages,
            "layout": response_layout,
            "assets": assets,
        }
    )


def get_document_processing_profile(
    db: Session,
    doc_id: str,
    *,
    page_no: int | None = None,
):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response("文档不存在")
    artifact = db.get(DocumentCanonicalArtifact, doc_id)
    if artifact is None:
        return success_response(
            {
                "document_id": doc_id,
                "available": False,
                "reason": "canonical_artifact_missing",
            }
        )
    stats = artifact.stats_json or {}
    profile_v2 = stats.get("document_profile_v2")
    if not isinstance(profile_v2, dict):
        return success_response(
            {
                "document_id": doc_id,
                "available": False,
                "reason": "document_profile_v2_not_generated",
                "parse_revision": artifact.parse_revision,
            }
        )
    profile_v1 = stats.get("document_profile") or {}
    all_pages_v2 = profile_v2.get("pages") or []
    available_pages = [
        {
            "page_no": page.get("page_no"),
            "layout_kind": page.get("layout_kind"),
            "ocr_trust_band": page.get("ocr_trust_band"),
            "uncertain": page.get("uncertain", False),
            "visual_item_count": page.get("visual_item_count", 0),
        }
        for page in all_pages_v2
    ]
    pages_v2 = [
        page
        for page in all_pages_v2
        if page_no is not None
        and int(page.get("page_no") or 0) == page_no
    ]
    pages_v1 = [
        page
        for page in profile_v1.get("pages") or []
        if page_no is not None
        and int(page.get("page_no") or 0) == page_no
    ]
    return success_response(
        {
            "document_id": doc_id,
            "available": True,
            "parse_revision": artifact.parse_revision,
            "profile_version": profile_v2.get("profile_version"),
            "shadow_only": profile_v2.get("shadow_only", True),
            "requested_page_no": page_no,
            "available_pages": available_pages,
            "summary": {
                key: value
                for key, value in profile_v2.items()
                if key not in {"pages", "evidence"}
            },
            "evidence": profile_v2.get("evidence") or [],
            "pages": pages_v2,
            "legacy_pages": pages_v1,
        }
    )


def delete_document(db: Session, actor: User, doc_id: str):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")

    knowledge_base_id = document.knowledge_base_id
    file_name = document.file_name

    # 1. 先删除 MinIO 中该文档的全部对象
    try:
        delete_document_objects(knowledge_base_id, doc_id)
    except Exception as exc:
        return error_response(f"MinIO 文档对象删除失败：{str(exc)[:200]}")

    # 2. 同步删除 Elasticsearch BM25 索引中的文档切片（best-effort）
    try:
        from app.services.keyword_index_service import delete_document_from_index

        delete_document_from_index(doc_id)
    except Exception:
        pass

    # 3. 再删除数据库中的 chunk / document
    try:
        db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
        db.delete(document)

        record_audit(
            db,
            actor,
            "delete_document",
            "document",
            doc_id,
            result_summary={
                "document_id": doc_id,
                "knowledge_base_id": knowledge_base_id,
                "file_name": file_name,
            },
        )

        db.flush()
        update_knowledge_base_document_count(db, knowledge_base_id)
        update_knowledge_base_chunk_count(db, knowledge_base_id)
        db.commit()
    except Exception:
        db.rollback()
        return error_response("文档删除失败")

    # 4. 最后清理本地解析缓存，不影响主删除结果
    try:
        delete_document_cache(knowledge_base_id, doc_id)
    except Exception:
        pass

    return success_response(
        {
            "id": doc_id,
            "deleted": True,
            "storage_deleted": True,
            "cache_deleted": True,
        },
        "文档已删除",
    )


async def parse_document(
    db: Session,
    actor: User,
    doc_id: str,
    reparse: bool = False,
    audit_action: str | None = None,
    task_id: str | None = None,
):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")

    knowledge_base_id = document.knowledge_base_id

    if task_id:
        parse_task_service.update_task(
            db,
            task_id,
            status="running",
            stage="prepare",
            progress=5,
            completed_steps=1,
        )
        parse_task_service.add_event(
            db,
            task_id,
            "stage_started",
            "prepare",
            "文件已接收，准备进入解析流程",
            {
                "document_id": doc_id,
                "file_name": document.file_name,
            },
        )

    try:
        document.parse_status = "parsing"
        document.error_message = ""
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        return error_response("文档解析状态更新失败")

    action = audit_action or ("reparse_document" if reparse else "parse_document")

    try:
        try:
            if task_id:
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="parse",
                    progress=12,
                    completed_steps=2,
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_started",
                    "parse",
                    "正在调用文档解析器，识别文本、图片和表格区域",
                    {"file_type": document.file_type},
                )

            init_debug_run(
                document_id=doc_id,
                task_id=task_id,
                requested_parser=settings.document_parser_engine,
            )
            parsed_document = parse_local_document(document, db)
            save_debug_parsed_document("02_parsed_document", parsed_document)
            update_debug_run_info(
                actual_parser=(parsed_document.metadata or {}).get("parser", ""),
            )
            parse_options = (document.metadata_json or {}).get("parse_options") or {}
            if not isinstance(parse_options, dict):
                parse_options = {}
            ocr_mode = (parse_options.get("enable_ocr") or "auto").strip().lower()
            vlm_enabled = bool(parse_options.get("enable_vlm", True))

            if task_id:
                block_stats = {
                    "parsed_block_count": len(parsed_document.blocks),
                    "text_block_count": len([b for b in parsed_document.blocks if b.block_type == "text"]),
                    "table_block_count": len([b for b in parsed_document.blocks if b.block_type == "table"]),
                    "image_block_count": len([b for b in parsed_document.blocks if b.block_type == "image"]),
                    "parser": (parsed_document.metadata or {}).get("parser", ""),
                }
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="parse",
                    progress=36,
                    completed_steps=3,
                    stats=block_stats,
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "parse",
                    "文档结构解析完成，已获得初始 block",
                    block_stats,
                )

            if task_id:
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="ocr",
                    progress=45,
                    completed_steps=4,
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_started",
                    "ocr",
                    "正在执行 OCR 增强",
                    {},
                )

            if ocr_mode == "off":
                ocr_enhanced = 0
            else:
                ocr_enhanced = await enhance_ocr_blocks(
                    db,
                    parsed_document.blocks,
                    knowledge_base_id=document.knowledge_base_id,
                    document_id=document.id,
                )
            if task_id:
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "ocr",
                    f"OCR 增强完成：{ocr_enhanced} 个块",
                    {"ocr_enhanced_count": ocr_enhanced},
                )
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="vlm",
                    progress=58,
                    completed_steps=5,
                    stats={"ocr_enhanced_count": ocr_enhanced},
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_started",
                    "vlm",
                    "正在执行图片理解 / VLM 描述增强",
                    {},
                )

            enhanced_images = await enhance_image_blocks(db, parsed_document.blocks) if vlm_enabled else 0
            save_debug_parsed_document("03_enhanced_document", parsed_document)
            parsed_document = reflow_parsed_document(parsed_document)
            save_debug_parsed_document("04_reflowed_document", parsed_document)

            if task_id:
                reflow_stats = {
                    "block_reflow_engine": (parsed_document.metadata or {}).get("block_reflow_engine", ""),
                    "reflow_input_blocks": (parsed_document.metadata or {}).get("reflow_input_blocks", 0),
                    "reflow_output_blocks": (parsed_document.metadata or {}).get("reflow_output_blocks", 0),
                    "reflow_merged_blocks": (parsed_document.metadata or {}).get("reflow_merged_blocks", 0),
                    "ocr_repaired_blocks": (parsed_document.metadata or {}).get("ocr_repaired_blocks", 0),
                }
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "vlm",
                    f"图片理解完成：{enhanced_images} 个图片块",
                    {"enhanced_image_count": enhanced_images},
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "reflow",
                    "解析后处理完成：已执行 block merge / OCR reflow",
                    reflow_stats,
                )
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="chunk",
                    progress=70,
                    completed_steps=6,
                    stats={"enhanced_image_count": enhanced_images, **reflow_stats},
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_started",
                    "chunk",
                    "正在生成 text / table / image 切片并写入数据库",
                    {},
                )

            parsed_metadata = parsed_document.metadata or {}
            ocr_failed_count = len(
                [b for b in parsed_document.blocks if (b.metadata or {}).get("ocr_status") == "failed"]
            )
            vlm_failed_count = len(
                [b for b in parsed_document.blocks if (b.metadata or {}).get("vlm_status") == "failed"]
            )

            document.metadata_json = sanitize_json_for_postgres(
                {
                    **(document.metadata_json or {}),
                    "parser": parsed_metadata.get("parser", (document.metadata_json or {}).get("parser", "")),
                    "parsed_block_count": len(parsed_document.blocks),
                    "image_block_count": len([b for b in parsed_document.blocks if b.block_type == "image"]),
                    "table_block_count": len([b for b in parsed_document.blocks if b.block_type == "table"]),
                    "text_block_count": len([b for b in parsed_document.blocks if b.block_type == "text"]),
                    "enhanced_image_count": enhanced_images,
                    "ocr_enhanced_count": ocr_enhanced,
                    "ocr_failed_count": ocr_failed_count,
                    "vlm_failed_count": vlm_failed_count,
                    "parse_preview_available": False,
                    "auto_vectorize": False,
                    **parsed_metadata,
                }
            )

            db.commit()
            db.refresh(document)

            if parsed_metadata.get("content_available") is False:
                fallback_reason = sanitize_text_for_postgres(
                    str(parsed_metadata.get("fallback_reason") or "未抽取到可用正文")
                )[:500]
                existing_chunk_count = int(
                    db.scalar(
                        select(func.count())
                        .select_from(Chunk)
                        .where(Chunk.document_id == document.id)
                    )
                    or 0
                )
                document.metadata_json = sanitize_json_for_postgres(
                    {
                        **(document.metadata_json or {}),
                        "chunk_store_status": "skipped_non_indexable_fallback",
                        "latest_parse_content_available": False,
                        "latest_parse_indexable": False,
                        "previous_chunks_preserved": existing_chunk_count > 0,
                        "chunk_count": existing_chunk_count,
                    }
                )
                document.parse_status = "parsed_with_fallback"
                document.error_message = fallback_reason
                document.processed_at = datetime.now(timezone.utc)
                document.enable_status = document.enable_status or "enabled"
                record_audit(
                    db,
                    actor,
                    action,
                    "document",
                    document.id,
                    result_summary={
                        "document_id": document.id,
                        "knowledge_base_id": document.knowledge_base_id,
                        "file_name": document.file_name,
                        "parse_status": "parsed_with_fallback",
                        "fallback_reason": fallback_reason,
                        "previous_chunks_preserved": existing_chunk_count > 0,
                    },
                )
                db.commit()
                db.refresh(document)
                warning_message = f"文档仅作为附件保留，未生成可检索切片：{fallback_reason}"
                if task_id:
                    parse_task_service.update_task(
                        db,
                        task_id,
                        status="completed",
                        stage="done",
                        progress=100,
                        completed_steps=7,
                        stats={
                            "document": document_to_dict(db, document),
                            "chunk_store_status": "skipped_non_indexable_fallback",
                            "previous_chunks_preserved": existing_chunk_count > 0,
                        },
                        error_message=warning_message,
                    )
                    parse_task_service.add_event(
                        db,
                        task_id,
                        "task_finished_with_warning",
                        "done",
                        warning_message,
                        {
                            "document_id": document.id,
                            "content_available": False,
                            "indexable": False,
                        },
                    )
                return success_response(document_to_dict(db, document), warning_message)

            chunk_count = 0
            try:
                chunk_count = rebuild_document_chunks(db, document, parsed_document)
            except Exception as exc:
                db.rollback()
                chunk_error = sanitize_text_for_postgres(str(exc))[:500] or "切片入库失败"
                warning_message = f"解析结果已生成，但切片入库失败：{chunk_error}"

                document = db.get(Document, doc_id)
                if document is not None:
                    preserved_chunk_count = int(
                        db.scalar(
                            select(func.count())
                            .select_from(Chunk)
                            .where(Chunk.document_id == document.id)
                        )
                        or 0
                    )
                    metadata = sanitize_json_for_postgres(
                        {
                            **(document.metadata_json or {}),
                            "chunk_store_status": "failed",
                            "chunk_store_error": chunk_error,
                            "chunk_count": preserved_chunk_count,
                            "previous_chunks_preserved": preserved_chunk_count > 0,
                        }
                    )
                    document.metadata_json = metadata
                    document.parse_status = "chunk_failed"
                    document.error_message = warning_message[:1000]
                    document.processed_at = datetime.now(timezone.utc)
                    document.enable_status = document.enable_status or "enabled"
                    update_knowledge_base_chunk_count(db, document.knowledge_base_id)
                    db.commit()
                    db.refresh(document)

                if task_id:
                    with SessionLocal() as task_db:
                        final_doc = task_db.get(Document, doc_id)
                        final_data = document_to_dict(task_db, final_doc) if final_doc is not None else {"id": doc_id}

                        parse_task_service.update_task(
                            task_db,
                            task_id,
                            status="failed",
                            stage="failed",
                            progress=100,
                            completed_steps=7,
                            stats={
                                "chunk_store_status": "failed",
                                "chunk_store_error": chunk_error,
                                "document": final_data,
                            },
                            error_message=warning_message,
                        )
                        parse_task_service.add_event(
                            task_db,
                            task_id,
                            "stage_failed",
                            "chunk",
                            warning_message,
                            {
                                "document_id": doc_id,
                                "chunk_store_error": chunk_error,
                                "parse_preview_available": False,
                            },
                        )
                        parse_task_service.add_event(
                            task_db,
                            task_id,
                            "task_finished_with_warning",
                            "done",
                            "解析已完成，但切片入库失败；过程解析块未保留，本次未生成切片和向量。",
                            {
                                "document_id": doc_id,
                                "parse_statistics": final_data.get("parse_statistics")
                                if isinstance(final_data, dict)
                                else {},
                            },
                        )

                return error_response(warning_message)

            if task_id:
                type_rows = (
                    db.query(Chunk.chunk_type, func.count())
                    .filter(Chunk.document_id == document.id)
                    .group_by(Chunk.chunk_type)
                    .all()
                )
                type_counts = {chunk_type or "text": int(count) for chunk_type, count in type_rows}

                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="chunk",
                    progress=92,
                    completed_steps=7,
                    stats={
                        "chunk_count": chunk_count,
                        "chunk_type_counts": type_counts,
                    },
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "chunk",
                    f"切片入库完成：{chunk_count} 个",
                    {
                        "chunk_count": chunk_count,
                        "chunk_type_counts": type_counts,
                    },
                )

            document.parse_status = "parsed"
            document.error_message = "" if chunk_count > 0 else "解析完成，但未生成切片，请检查 chunk rebuild 日志"
            document.processed_at = datetime.now(timezone.utc)
            document.enable_status = document.enable_status or "enabled"
            document.metadata_json = sanitize_json_for_postgres({
                **(document.metadata_json or {}),
                "chunk_store_status": "success" if chunk_count > 0 else "empty",
                "chunk_count": int(chunk_count or 0),
            })

            record_audit(
                db,
                actor,
                action,
                "document",
                document.id,
                result_summary={
                    "document_id": document.id,
                    "knowledge_base_id": document.knowledge_base_id,
                    "file_name": document.file_name,
                    "chunk_count": chunk_count,
                },
            )

            db.commit()
            db.refresh(document)

            # Elasticsearch replacement is intentionally post-commit: a DB
            # rebuild failure must leave the last known-good keyword index in
            # place.  The sync helper is best-effort and locally retryable.
            try:
                from app.services.keyword_index_service import sync_document_index

                indexed_count = sync_document_index(db, document)
                document.metadata_json = sanitize_json_for_postgres(
                    {
                        **(document.metadata_json or {}),
                        "keyword_index_status": "synced",
                        "keyword_index_chunk_count": indexed_count,
                    }
                )
                db.commit()
                db.refresh(document)
            except Exception as exc:
                db.rollback()
                document = db.get(Document, doc_id)
                if document is not None:
                    document.metadata_json = sanitize_json_for_postgres(
                        {
                            **(document.metadata_json or {}),
                            "keyword_index_status": "pending_retry",
                            "keyword_index_error": sanitize_text_for_postgres(str(exc))[:500],
                        }
                    )
                    db.commit()
                    db.refresh(document)

        except ValueError as exc:
            db.rollback()
            message = str(exc)[:300] or "文档解析失败"
            mark_document_failed(db, doc_id, message)

            if task_id:
                with SessionLocal() as task_db:
                    parse_task_service.update_task(
                        task_db,
                        task_id,
                        status="failed",
                        stage="failed",
                        progress=100,
                        error_message=message,
                    )
                    parse_task_service.add_event(
                        task_db,
                        task_id,
                        "task_failed",
                        "failed",
                        message,
                        {"document_id": doc_id},
                    )

            return error_response(message)

        except Exception:
            db.rollback()
            mark_document_failed(db, doc_id, PARSE_STORE_ERROR)

            if task_id:
                with SessionLocal() as task_db:
                    parse_task_service.update_task(
                        task_db,
                        task_id,
                        status="failed",
                        stage="failed",
                        progress=100,
                        error_message=PARSE_STORE_ERROR,
                    )
                    parse_task_service.add_event(
                        task_db,
                        task_id,
                        "task_failed",
                        "failed",
                        PARSE_STORE_ERROR,
                        {"document_id": doc_id},
                    )

            return error_response(PARSE_STORE_ERROR)

        if settings.auto_embed_after_parse and not task_id:
            from app.services import embedding_service

            if task_id:
                parse_task_service.update_task(
                    db,
                    task_id,
                    stage="embedding",
                    progress=90,
                    completed_steps=8,
                )
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_started",
                    "embedding",
                    "正在生成向量索引",
                    {},
                )

            embed_result = await embedding_service.embed_document(db, actor, document.id)

            if not embed_result.get("success"):
                if task_id:
                    parse_task_service.update_task(
                        db,
                        task_id,
                        status="failed",
                        stage="failed",
                        progress=92,
                        error_message=f"解析完成，但自动向量化失败：{embed_result.get('message')}",
                    )
                    parse_task_service.add_event(
                        db,
                        task_id,
                        "task_failed",
                        "failed",
                        f"解析完成，但自动向量化失败：{embed_result.get('message')}",
                        embed_result,
                    )

                return error_response(f"解析完成，但自动向量化失败：{embed_result.get('message')}")

            if task_id:
                parse_task_service.add_event(
                    db,
                    task_id,
                    "stage_finished",
                    "embedding",
                    "向量索引生成完成",
                    embed_result.get("data") if isinstance(embed_result, dict) else {},
                )

        if task_id:
            final_data = document_to_dict(db, document)
            parse_task_service.update_task(
                db,
                task_id,
                status="completed",
                stage="done",
                progress=100,
                completed_steps=7,
                stats={
                    "document": final_data,
                    "auto_vectorize": False,
                },
            )
            parse_task_service.add_event(
                db,
                task_id,
                "task_finished",
                "done",
                "文档解析完成；工作台默认未执行向量化，可在文档详情页手动点击生成向量。",
                {
                    "document_id": document.id,
                    "chunk_count": final_data.get("chunk_count"),
                    "parse_statistics": final_data.get("parse_statistics"),
                    "auto_vectorize": False,
                },
            )

        return success_response(document_to_dict(db, document), "解析完成")

    finally:
        finalize_debug_run()
        if getattr(settings, "cleanup_parse_cache_after_parse", True):
            try:
                delete_document_cache(knowledge_base_id, doc_id)
            except Exception:
                pass

def document_status(db: Session, doc_id: str):
    document = db.get(Document, doc_id)
    if document is None:
        return error_response(f"文档不存在：{doc_id}")
    chunk_count = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == doc_id)) or 0
    return success_response(
        {
            "id": document.id,
            "parse_status": document.parse_status,
            "error_message": document.error_message,
            "chunk_count": chunk_count,
        }
    )


def run_parse_document_task(doc_id: str, actor_id: str, task_id: str, reparse: bool = False) -> None:
    """Background entrypoint used by the parse workbench.

    A fresh Session is required because FastAPI closes the request-scoped DB
    session before BackgroundTasks run.  The normal parse_document path remains
    available for synchronous actions.
    """
    import asyncio

    with SessionLocal() as bg_db:
        actor = bg_db.get(User, actor_id)
        if actor is None:
            actor = bg_db.query(User).first()
        if actor is None:
            parse_task_service.update_task(bg_db, task_id, status="failed", stage="failed", progress=100, error_message="解析任务无法找到执行用户")
            parse_task_service.add_event(bg_db, task_id, "task_failed", "failed", "解析任务无法找到执行用户", {"document_id": doc_id})
            return
        asyncio.run(parse_document(bg_db, actor, doc_id, reparse=reparse, audit_action="parse_document", task_id=task_id))
