"""P2 生命周期：来源快照、事务 outbox、补偿扫描和有效证据回查。"""

import hashlib
import json

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Chunk, CommunityReport, Document, DocumentCanonicalArtifact, EntityAlias, GraphCommunity,
    KnowledgeBase, KnowledgeEntity, KnowledgeEvent, KnowledgeEvidence, KnowledgeIndexJob,
    KnowledgeIndexManifest, KnowledgeRelation, ModelConfig,
)
from app.services.embedding.embedding_content_builder import (
    CONTENT_ADMISSION_VERSION, build_content_admission,
)
from app.utils.ids import new_id

ALGORITHM_VERSION = "entity-foundation-v2"
PROMPT_VERSION = "grounded-entities-v1"


def digest(value) -> str:
    """确定性内容指纹；不使用 Python 的进程随机 hash。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def enabled(kb) -> bool:
    profile = getattr(kb, "retrieval_profile", None)
    return isinstance(profile, dict) and bool(set(profile.get("build_indexes", [])) & {"entities", "relations", "events"})


def lock_source_scope(db, kb_id: str) -> None:
    """与发布端按相同顺序锁 KB；避免删除来源和插入其外键证据互相等待。"""
    db.scalar(select(KnowledgeBase.id).where(KnowledgeBase.id == kb_id).with_for_update())


def snapshot_document(db, document: Document) -> dict | None:
    """只读取当前 revision 和已准入 child；父块与隔离 OCR 不进入抽取输入。"""
    artifact = db.get(DocumentCanonicalArtifact, document.id)
    if artifact is None or document.enable_status != "enabled":
        return None
    kb = db.get(KnowledgeBase, document.knowledge_base_id)
    index_model = (kb.retrieval_profile or {}).get("index_model") if kb else None
    if index_model:
        # 独立模型写入输入指纹，变更只使本知识库产物失效。
        model_config = {key: index_model.get(key, "") for key in ("provider", "model_name", "base_url")}
        if index_model.get("think") is not None:
            model_config["think"] = index_model["think"]
        if index_model.get("reference_mode") == "names":
            model_config["reference_mode"] = "names"
    else:
        model = db.scalar(select(ModelConfig).where(ModelConfig.model_type == "llm").order_by(ModelConfig.id))
        model_config = {key: getattr(model, key, "") for key in ("provider", "model_name", "base_url")}
    chunks = db.scalars(select(Chunk).where(
        Chunk.document_id == document.id, Chunk.knowledge_base_id == document.knowledge_base_id,
        Chunk.chunk_type != "parent_text",
    ).order_by(Chunk.id)).all()
    sources = []
    excluded = 0
    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        if not chunk.is_enabled or metadata.get("canonical_parse_revision") != artifact.parse_revision:
            excluded += 1
            continue
        admitted = build_content_admission(chunk, document)
        if not admitted.content:
            excluded += 1
            continue
        sources.append({
            "chunk_id": chunk.id, "content": admitted.content,
            "content_hash": digest(admitted.content), "sources": list(admitted.index_sources),
            "page_no": metadata.get("page_no"),
        })
    snapshot = {
        "knowledge_base_id": document.knowledge_base_id, "document_id": document.id,
        "parse_revision": artifact.parse_revision, "canonical_hash": artifact.canonical_hash,
        "algorithm": ALGORITHM_VERSION, "prompt": PROMPT_VERSION,
        "admission": CONTENT_ADMISSION_VERSION, "model": model_config,
        "sources": sources, "excluded_chunks": excluded, "total_chunks": len(chunks),
    }
    capabilities = sorted(set((kb.retrieval_profile or {}).get("build_indexes", [])) & {"relations", "events"}) if kb else []
    if capabilities:
        from app.services.knowledge_index.claims import ALGORITHM_VERSION as claims_algorithm
        from app.services.knowledge_index.claims import PROMPT_VERSION as claims_prompt

        snapshot.update(algorithm=claims_algorithm, prompt=claims_prompt, capabilities=capabilities)
    return snapshot


def enqueue_document(db, document: Document, *, retry: bool = False):
    """只 flush 不 commit，让 outbox 与 chunks/Canonical 一起提交或回滚。"""
    if not enabled(db.get(KnowledgeBase, document.knowledge_base_id)):
        return None, False
    lock_source_scope(db, document.knowledge_base_id)
    snapshot = snapshot_document(db, document)
    if snapshot is None:
        return None, False
    key = digest(snapshot)
    # 新输入立即撤下旧 manifest；正在运行的旧任务在发布端再次校验，不可复活旧来源。
    db.execute(delete(KnowledgeIndexManifest).where(
        KnowledgeIndexManifest.document_id == document.id, KnowledgeIndexManifest.input_hash != key,
    ))
    db.execute(update(KnowledgeIndexJob).where(
        KnowledgeIndexJob.document_id == document.id, KnowledgeIndexJob.idempotency_key != key,
        KnowledgeIndexJob.state.in_(["pending", "ready", "partial", "failed"]),
    ).values(state="stale"))
    # 领取任务与显式重试可能并发；锁后读取最新状态，避免把已运行任务重置为 pending。
    db.flush()
    job = db.scalar(select(KnowledgeIndexJob).where(
        KnowledgeIndexJob.idempotency_key == key,
    ).with_for_update().execution_options(populate_existing=True))
    if job is not None:
        manifest_missing = job.state in {"ready", "partial"} and db.get(KnowledgeIndexManifest, job.id) is None
        if job.state == "stale" or manifest_missing or (retry and job.state in {"failed", "partial"}):
            job.state, job.attempts, job.error = "pending", 0, ""
            job.lease_token, job.lease_until = "", None
        return job, False
    job = KnowledgeIndexJob(
        id=new_id(), knowledge_base_id=document.knowledge_base_id,
        document_id=document.id, idempotency_key=key, input_json=snapshot,
    )
    # 并发上传/补偿扫描可能同时发现同一输入，只回滚本次插入，不回滚来源事务。
    inserted = True
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        inserted = False
        job = db.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.idempotency_key == key))
        if job is None:
            raise
    return job, inserted


def reconcile(db, kb_id: str | None = None, *, limit: int = 100) -> int:
    """补偿未入队/版本变化/准入变化；每次最多新增 limit 个任务，无进程内队列依赖。"""
    query = select(Document).join(KnowledgeBase).order_by(Document.id)
    if kb_id:
        query = query.where(Document.knowledge_base_id == kb_id)
    created = 0
    for document in db.scalars(query).yield_per(100):
        _, inserted = enqueue_document(db, document)
        if inserted:
            created += 1
        if created >= limit:
            break
    prune_orphan_entities(db)
    # 已有P3输入可立即创建KB级社区任务；模型生成仍由独立worker负责。
    from app.services.knowledge_index.communities import enqueue_graph, is_enabled

    kbs = select(KnowledgeBase)
    if kb_id:
        kbs = kbs.where(KnowledgeBase.id == kb_id)
    for kb in db.scalars(kbs):
        if is_enabled(kb):
            enqueue_graph(db, kb.id)
    return created


def retry_incomplete(db, kb_id: str, *, limit: int = 100) -> int:
    """仅重置当前输入的失败/部分任务，批量上限也约束显式重试的模型工作量。"""
    count = 0
    for document in db.scalars(select(Document).where(
        Document.knowledge_base_id == kb_id,
    ).order_by(Document.id)).yield_per(100):
        if count >= limit:
            break
        snapshot = snapshot_document(db, document)
        if snapshot is None:
            continue
        lock_source_scope(db, kb_id)
        db.flush()
        job = db.scalar(select(KnowledgeIndexJob).where(
            KnowledgeIndexJob.idempotency_key == digest(snapshot),
        ).with_for_update().execution_options(populate_existing=True))
        if job and job.state in {"partial", "failed"}:
            enqueue_document(db, document, retry=True)
            count += 1
    return count


def prune_orphan_entities(db) -> None:
    """只有无任何物理证据的节点才清理；过期证据的读取另有严格检查。"""
    db.execute(delete(KnowledgeEntity).where(~exists().where(
        KnowledgeEvidence.entity_id == KnowledgeEntity.id,
    )))


def valid_evidence(db, kb_id: str, *, name: str | None = None, limit: int = 100) -> list[dict]:
    """所有实体/别名出口统一经来源回查，禁止跨 KB、禁用、旧版本和准入绕过。"""
    if not enabled(db.get(KnowledgeBase, kb_id)):
        return []
    query = select(KnowledgeEvidence, KnowledgeEntity).join(
        KnowledgeEntity, KnowledgeEvidence.entity_id == KnowledgeEntity.id,
    ).where(KnowledgeEvidence.knowledge_base_id == kb_id, KnowledgeEntity.knowledge_base_id == kb_id)
    if name:
        query = query.where(exists().where(
            EntityAlias.entity_id == KnowledgeEntity.id,
            EntityAlias.evidence_id == KnowledgeEvidence.id, EntityAlias.alias == name,
        ))
    results, snapshots, manifests = [], {}, {}
    for evidence, entity in db.execute(query.order_by(KnowledgeEvidence.id)).yield_per(100):
        if evidence.document_id not in snapshots:
            doc = db.get(Document, evidence.document_id)
            snapshots[evidence.document_id] = (
                snapshot_document(db, doc) if doc and doc.knowledge_base_id == kb_id else None
            )
        current = snapshots[evidence.document_id]
        if evidence.manifest_id not in manifests:
            manifests[evidence.manifest_id] = db.get(KnowledgeIndexManifest, evidence.manifest_id)
        manifest = manifests[evidence.manifest_id]
        if not current or not manifest or manifest.state not in {"ready", "partial"} or manifest.input_hash != digest(current):
            continue
        source = next((s for s in current["sources"] if s["chunk_id"] == evidence.chunk_id), None)
        if not source or source["content_hash"] != evidence.content_hash:
            continue
        if current["parse_revision"] != evidence.parse_revision:
            continue
        if not 0 <= evidence.start_at < evidence.end_at <= len(source["content"]):
            continue
        if source["content"][evidence.start_at:evidence.end_at] != evidence.quote:
            continue
        results.append({
            "entity_id": entity.id, "name": entity.canonical_name, "type": entity.entity_type,
            "scope": entity.scope, "chunk_id": evidence.chunk_id, "document_id": evidence.document_id,
            "parse_revision": evidence.parse_revision, "quote": evidence.quote,
            "span_basis": "admitted_projection", "start_at": evidence.start_at, "end_at": evidence.end_at,
            "source": evidence.source_json,
        })
        if len(results) >= limit:
            break
    return results


def index_status(db, kb_id: str) -> dict:
    """按当前输入计算覆盖，部分抽取/过期产物不能显示为全库 ready。"""
    kb = db.get(KnowledgeBase, kb_id)
    counts = {state: 0 for state in ("ready", "partial", "pending", "running", "failed", "stale", "missing", "unavailable")}
    valid_manifest_ids: list[str] = []
    for doc in db.scalars(select(Document).where(Document.knowledge_base_id == kb_id)):
        current = snapshot_document(db, doc) if enabled(kb) else None
        if current is None:
            counts["unavailable"] += 1
            continue
        job = db.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.idempotency_key == digest(current)))
        state = job.state if job else "missing"
        if job is None and db.scalar(select(KnowledgeIndexJob.id).where(
            KnowledgeIndexJob.document_id == doc.id,
        ).limit(1)) is not None:
            state = "stale"
        if job and state in {"ready", "partial"}:
            if db.get(KnowledgeIndexManifest, job.id) is None:
                state = "missing"
            else:
                valid_manifest_ids.append(job.id)
        counts[state] += 1
    total = sum(counts.values())
    artifact_counts = {"entities": 0, "relations": 0, "events": 0, "communities": 0, "reports": 0}
    if valid_manifest_ids:
        artifact_counts["entities"] = db.scalar(
            select(func.count(func.distinct(KnowledgeEvidence.entity_id))).where(
                KnowledgeEvidence.knowledge_base_id == kb_id,
                KnowledgeEvidence.manifest_id.in_(valid_manifest_ids),
            )
        ) or 0
        build_flags = set((kb.retrieval_profile or {}).get("build_indexes", []))
        if "relations" in build_flags:
            artifact_counts["relations"] = db.scalar(
                select(func.count()).select_from(KnowledgeRelation).where(
                    KnowledgeRelation.knowledge_base_id == kb_id,
                    KnowledgeRelation.manifest_id.in_(valid_manifest_ids),
                )
            ) or 0
        if "events" in build_flags:
            artifact_counts["events"] = db.scalar(
                select(func.count()).select_from(KnowledgeEvent).where(
                    KnowledgeEvent.knowledge_base_id == kb_id,
                    KnowledgeEvent.manifest_id.in_(valid_manifest_ids),
                )
            ) or 0
    status = {
        "enabled": enabled(kb), "capability": "entity_foundation", "documents": counts,
        "total_documents": total, "covered_documents": counts["ready"],
        "state": "disabled" if not enabled(kb) else "ready" if total and counts["ready"] == total else "partial",
        "graph_retrieval_ready": False, "event_retrieval_ready": False,
        "counts": artifact_counts,
    }
    if enabled(kb) and set((kb.retrieval_profile or {}).get("build_indexes", [])) & {"relations", "events"}:
        from app.services.retrieval.associations import EvidenceReader

        reader = EvidenceReader(db, kb_id)
        status.update(graph_retrieval_ready=bool(reader.ids["relations"]),
                      event_retrieval_ready=bool(reader.ids["events"]), capability_coverage=reader.coverage)
    if isinstance(getattr(kb, "retrieval_profile", None), dict) and "communities" in kb.retrieval_profile.get("build_indexes", []):
        from app.services.knowledge_index.communities import current_reports

        reports, info = current_reports(db, kb_id)
        status.update(global_retrieval_ready=bool(reports), community_status={
            key: value for key, value in info.items() if key != "model"})
        artifact_counts["reports"] = len(reports)
        graph_revision = info.get("graph_revision")
        if graph_revision:
            artifact_counts["communities"] = db.scalar(
                select(func.count()).select_from(GraphCommunity).where(
                    GraphCommunity.knowledge_base_id == kb_id,
                    GraphCommunity.graph_revision == graph_revision,
                )
            ) or 0
    return status
