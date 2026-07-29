from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.response import error_response, success_response
from app.db.models import Chunk, Document, User
from app.plugins.parse_eval.diagnostics import (
    diagnose_no_gt,
    diagnose_scanned_contract,
    issue_to_dict,
    safe_text,
)
from app.plugins.parse_eval.models import ParseEvalItem, ParseEvalRun
from app.plugins.parse_eval.report_builder import build_report
from app.plugins.parse_eval.templates import TEMPLATES, get_template
from app.services.chunk_service import chunk_to_dict
from app.utils.ids import new_id
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


def _now():
    return datetime.now(timezone.utc)


def run_to_dict(run: ParseEvalRun, *, item_count: int | None = None) -> dict:
    data = {
        "id": run.id,
        "evaluation_id": run.id,
        "document_id": run.document_id,
        "knowledge_base_id": run.knowledge_base_id,
        "mode": run.mode,
        "template_id": run.template_id,
        "status": run.status,
        "score": float(run.score or 0),
        "score_10": round(float(run.score or 0) / 10, 2),
        "risk_level": run.risk_level,
        "recommendation": run.recommendation,
        "summary": sanitize_json_for_postgres(run.summary_json or {}),
        "scores": sanitize_json_for_postgres(run.score_json or {}),
        "options": sanitize_json_for_postgres(run.options_json or {}),
        "report_markdown": run.report_markdown or "",
        "created_by": run.created_by or "",
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
    }
    if item_count is not None:
        data["item_count"] = item_count
    return data


def item_to_dict(item: ParseEvalItem, *, include_chunk: bool = False, db: Session | None = None) -> dict:
    data = {
        "id": item.id,
        "evaluation_id": item.evaluation_id,
        "document_id": item.document_id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "page_no": item.page_no,
        "chunk_index": item.chunk_index,
        "score": item.score,
        "issue_level": item.issue_level,
        "issue_type": item.issue_type,
        "message": item.message,
        "evidence": sanitize_json_for_postgres(item.evidence_json or {}),
        "suggestion": item.suggestion,
        "created_at": item.created_at,
    }
    if include_chunk and db is not None and item.target_type in {"chunk", "image", "table", "text"} and item.target_id:
        chunk = db.get(Chunk, item.target_id)
        if chunk is not None:
            data["chunk"] = chunk_to_dict(chunk, include_content=False)
    return data


def list_templates():
    return success_response({"items": TEMPLATES})


def document_profile_dataset_report(dataset_root: Path | None = None):
    """Return the generated DCP-0.1 report without mutating evaluation data."""

    if dataset_root is None:
        backend_root = Path(__file__).resolve().parents[3]
        dataset_root = backend_root / ".generated" / "document_profile_dcp0"
    report_path = dataset_root / "reports" / "coverage.json"
    manifest_path = dataset_root / "corpus_manifest.json"
    command = "python -m scripts.generate_document_profile_dataset"
    if not report_path.is_file() or not manifest_path.is_file():
        return success_response(
            {
                "available": False,
                "dataset_root": str(dataset_root),
                "generate_command": command,
                "report": None,
                "manifest": None,
            }
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return error_response(f"DCP-0.1 数据集报告不可读：{exc}")
    return success_response(
        {
            "available": True,
            "dataset_root": str(dataset_root),
            "generate_command": command,
            "html_report_path": str(dataset_root / "reports" / "coverage.html"),
            "report": report,
            "manifest": {
                "dataset_id": manifest.get("dataset_id"),
                "generator_seed": manifest.get("generator_seed"),
                "document_count": manifest.get("document_count"),
                "page_count": manifest.get("page_count"),
                "adversarial_page_count": manifest.get("adversarial_page_count"),
            },
        }
    )


def _collect_chunks(db: Session, document_id: str) -> list[Chunk]:
    return list(db.scalars(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index.asc())).all())


def _create_item(db: Session, run_id: str, doc_id: str, issue: dict) -> ParseEvalItem:
    item = ParseEvalItem(
        id=new_id(),
        evaluation_id=run_id,
        document_id=doc_id,
        target_type=safe_text(issue.get("target_type") or "document")[:50],
        target_id=safe_text(issue.get("target_id") or "")[:100],
        page_no=issue.get("page_no") if isinstance(issue.get("page_no"), int) else None,
        chunk_index=issue.get("chunk_index") if isinstance(issue.get("chunk_index"), int) else None,
        score=float(issue.get("score")) if isinstance(issue.get("score"), (int, float)) else None,
        issue_level=safe_text(issue.get("issue_level") or "info")[:50],
        issue_type=safe_text(issue.get("issue_type") or "info")[:100],
        message=sanitize_text_for_postgres(issue.get("message") or "")[:2000],
        evidence_json=sanitize_json_for_postgres(issue.get("evidence") or {}),
        suggestion=sanitize_text_for_postgres(issue.get("suggestion") or "")[:2000],
    )
    db.add(item)
    return item


def run_evaluation(
    db: Session,
    actor: User,
    document_id: str,
    *,
    mode: str = "no_gt",
    template_id: str = "generic_no_gt",
    options: dict[str, Any] | None = None,
):
    document = db.get(Document, document_id)
    if document is None:
        return error_response(f"文档不存在：{document_id}")
    template = get_template(template_id)
    effective_template_id = template["id"]
    effective_mode = mode or template.get("mode") or "no_gt"
    options = sanitize_json_for_postgres(options or {})

    chunks = _collect_chunks(db, document.id)
    if effective_template_id == "scanned_contract_ocr":
        summary, score_json, issues = diagnose_scanned_contract(document, chunks, options)
    else:
        effective_template_id = "generic_no_gt"
        effective_mode = "no_gt"
        summary, score_json, issues = diagnose_no_gt(document, chunks)

    # Remove stale runs only if caller requested it; default keeps history. Limit history by deleting very old duplicate latest? Keep for audit.
    run = ParseEvalRun(
        id=new_id(),
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        mode=effective_mode,
        template_id=effective_template_id,
        status="completed",
        score=float(score_json.get("score") or 0),
        risk_level=safe_text(score_json.get("risk_level") or "unknown"),
        recommendation=sanitize_text_for_postgres(score_json.get("recommendation") or ""),
        summary_json=sanitize_json_for_postgres(summary),
        score_json=sanitize_json_for_postgres(score_json),
        options_json=options,
        report_markdown="",
        created_by=getattr(actor, "id", "") or "",
        finished_at=_now(),
    )
    db.add(run)
    db.flush()

    item_dicts: list[dict] = []
    for issue in issues[:1000]:
        issue_dict = issue_to_dict(issue)
        item = _create_item(db, run.id, document.id, issue_dict)
        item_dicts.append(item_to_dict(item))

    run_dict_for_report = run_to_dict(run, item_count=len(item_dicts))
    report = build_report(run_dict_for_report, item_dicts)
    run.report_markdown = sanitize_text_for_postgres(report)
    db.commit()
    db.refresh(run)
    return success_response(run_to_dict(run, item_count=len(item_dicts)), "评测完成")


def latest_for_document(db: Session, document_id: str):
    run = db.scalars(
        select(ParseEvalRun)
        .where(ParseEvalRun.document_id == document_id)
        .order_by(ParseEvalRun.created_at.desc())
        .limit(1)
    ).first()
    if run is None:
        return success_response(None)
    count = db.scalar(select(func.count()).select_from(ParseEvalItem).where(ParseEvalItem.evaluation_id == run.id)) or 0
    return success_response(run_to_dict(run, item_count=int(count)))


def get_run(db: Session, run_id: str):
    run = db.get(ParseEvalRun, run_id)
    if run is None:
        return error_response(f"评测记录不存在：{run_id}")
    count = db.scalar(select(func.count()).select_from(ParseEvalItem).where(ParseEvalItem.evaluation_id == run.id)) or 0
    return success_response(run_to_dict(run, item_count=int(count)))


def list_items(
    db: Session,
    run_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    level: str = "all",
    issue_type: str = "all",
    target_type: str = "all",
):
    run = db.get(ParseEvalRun, run_id)
    if run is None:
        return error_response(f"评测记录不存在：{run_id}")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    filters = [ParseEvalItem.evaluation_id == run_id]
    if level and level != "all":
        filters.append(ParseEvalItem.issue_level == level)
    if issue_type and issue_type != "all":
        filters.append(ParseEvalItem.issue_type == issue_type)
    if target_type and target_type != "all":
        filters.append(ParseEvalItem.target_type == target_type)

    total = db.scalar(select(func.count()).select_from(ParseEvalItem).where(*filters)) or 0
    rows = db.scalars(
        select(ParseEvalItem)
        .where(*filters)
        .order_by(ParseEvalItem.issue_level.desc(), ParseEvalItem.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return success_response({
        "evaluation_id": run_id,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "items": [item_to_dict(item, include_chunk=True, db=db) for item in rows],
    })


def report(db: Session, run_id: str):
    run = db.get(ParseEvalRun, run_id)
    if run is None:
        return error_response(f"评测记录不存在：{run_id}")
    return success_response({"evaluation_id": run.id, "markdown": run.report_markdown or ""})
