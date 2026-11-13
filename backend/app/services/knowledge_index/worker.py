"""持久任务消费者；每块续租，原文或配置变化时拒绝发布 staging 结果。"""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import and_, delete, or_, select, update

from app.db.models import (
    Document, EntityAlias, EventEntity, KnowledgeBase, KnowledgeEntity, KnowledgeEvidence,
    KnowledgeEvent, KnowledgeIndexJob, KnowledgeIndexManifest, KnowledgeRelation,
)
from app.services.knowledge_index.entity_resolution import normalized_name, resolve_entity
from app.services.knowledge_index.extractor import extract_entities, grounded_candidates
from app.services.knowledge_index.service import digest, enabled, prune_orphan_entities, snapshot_document
from app.utils.ids import new_id

LEASE_SECONDS = 300
MAX_ATTEMPTS = 3


def now():
    return datetime.now(timezone.utc)


def claim_job(db, job_model=KnowledgeIndexJob) -> dict | None:
    """PG SKIP LOCKED 配合条件更新；崩溃后的租约可回收，重试次数有上限。"""
    Job = job_model
    current = now()
    db.execute(update(Job).where(
        Job.state == "running", Job.attempts >= MAX_ATTEMPTS,
        Job.lease_until <= current,
    ).values(state="failed", error="worker 租约失效且重试次数已耗尽", lease_token=""))
    job = db.scalar(select(Job).where(
        Job.attempts < MAX_ATTEMPTS,
        or_(Job.state == "pending", and_(
            Job.state.in_(["running", "failed", "partial"]),
            Job.lease_until <= current,
        )),
    ).order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
    if job is None:
        db.commit()
        return None
    token = new_id()
    result = db.execute(update(Job).where(
        Job.id == job.id, Job.state == job.state,
        Job.attempts == job.attempts, Job.lease_token == job.lease_token,
    ).values(state="running", attempts=job.attempts + 1, lease_token=token,
             lease_until=current + timedelta(seconds=LEASE_SECONDS), error=""),
        execution_options={"synchronize_session": False})
    work = {"id": job.id, "key": job.idempotency_key, "input": job.input_json, "token": token}
    db.commit()
    return work if result.rowcount == 1 else None


def heartbeat(db, work: dict, job_model=KnowledgeIndexJob) -> bool:
    Job = job_model
    result = db.execute(update(Job).where(
        Job.id == work["id"], Job.lease_token == work["token"],
        Job.state == "running", Job.lease_until > now(),
    ).values(lease_until=now() + timedelta(seconds=LEASE_SECONDS)),
        execution_options={"synchronize_session": False})
    db.commit()
    return result.rowcount == 1


def publish(db, work: dict, staged: dict, failures: dict, rejected: dict | None = None) -> bool:
    """发布与最终版本检查同事务；过期 worker、禁用 profile 和旧 revision 一律拒绝。"""
    # 所有写入路径统一先锁 KB 再锁 job，避免重解析失效任务与发布形成锁顺序环。
    kb = db.scalar(select(KnowledgeBase).where(
        KnowledgeBase.id == work["input"]["knowledge_base_id"],
    ).with_for_update())
    job = db.scalar(select(KnowledgeIndexJob).where(
        KnowledgeIndexJob.id == work["id"], KnowledgeIndexJob.lease_token == work["token"],
        KnowledgeIndexJob.state == "running", KnowledgeIndexJob.lease_until > now(),
    ).with_for_update())
    if job is None:
        db.rollback()
        return False
    # 同 KB 的实体物化串行，避免两个 worker 同时创建相同身份节点。
    document = db.get(Document, job.document_id)
    current = snapshot_document(db, document) if document and enabled(kb) else None
    if current is None or digest(current) != work["key"]:
        job.state, job.error, job.lease_token = "stale", "来源、准入或模型配置已变化", ""
        db.commit()
        return False
    if set(staged) | set(failures) != {s["chunk_id"] for s in current["sources"]}:
        raise ValueError("staging 覆盖集合与输入不一致")
    if set(staged) & set(failures):
        raise ValueError("成功和失败切片不能重叠")
    rejected = rejected or {}
    if not set(rejected) <= set(staged):
        raise ValueError("条目错误必须对应已保留结果的切片")
    # 同输入重试时替换原产物，不重复写证据；旧 manifest 由外键清理提及与别名。
    db.execute(delete(KnowledgeIndexManifest).where(KnowledgeIndexManifest.id == job.id))
    state = "partial" if failures or rejected or not staged else "ready"
    manifest = KnowledgeIndexManifest(
        id=job.id, document_id=document.id, knowledge_base_id=document.knowledge_base_id,
        input_hash=work["key"], state=state,
        coverage_json={"eligible_chunks": len(current["sources"]), "processed_chunks": len(staged),
                       "failed_chunks": failures, "excluded_chunks": current["excluded_chunks"],
                       "rejected_items": rejected, "retained_chunk_ids": sorted(staged),
                       "completed_chunk_ids": sorted(set(staged) - set(rejected))},
    )
    db.add(manifest)
    db.flush()
    for source in current["sources"]:
        claims = None
        items = staged.get(source["chunk_id"], [])
        if current.get("capabilities") and source["chunk_id"] in staged:
            from app.services.knowledge_index.claims import ground_claims

            # 发布时重新校验原始结构，拒绝绕过抽取阶段伪造的 staging。
            raw = {kind: [{k: v for k, v in item.items() if k not in {"start_at", "end_at"}}
                          for item in items[kind]] for kind in ("entities", "relations", "events")}
            claims = ground_claims(source["content"], raw, current["capabilities"],
                                   allow_empty_events=bool(rejected.get(source["chunk_id"])))
            candidates = claims["entities"]
        else:
            candidates = grounded_candidates(source["content"], {"entities": [
                {key: item[key] for key in ("name", "mention", "entity_type", "scope", "quote")}
                for item in items
            ]})
        evidence_rows = []
        for candidate in candidates:
            # 无可靠 scope 时按文档隔离，同名跨文档不会自动变成同一个实体。
            scope = normalized_name(candidate["scope"]) or f"document:{document.id}"
            identity = digest([normalized_name(candidate["name"]), candidate["entity_type"], scope])
            entity_id = str(uuid5(NAMESPACE_URL, f"know-rag:{document.knowledge_base_id}:{identity}"))
            entity = resolve_entity(db, document.knowledge_base_id, candidate, scope)
            if entity is None:
                entity = db.get(KnowledgeEntity, entity_id)
            if entity is None:
                entity = KnowledgeEntity(id=entity_id, knowledge_base_id=document.knowledge_base_id,
                    identity_key=identity, canonical_name=candidate["name"],
                    entity_type=candidate["entity_type"], scope=scope)
                db.add(entity)
                db.flush()
            evidence = KnowledgeEvidence(
                id=new_id(), manifest_id=job.id, entity_id=entity.id,
                knowledge_base_id=document.knowledge_base_id, document_id=document.id,
                chunk_id=source["chunk_id"], parse_revision=current["parse_revision"],
                content_hash=source["content_hash"], quote=candidate["quote"],
                start_at=candidate["start_at"], end_at=candidate["end_at"],
                source_json={"kind": "grounded_entity_candidate" if claims is not None else "model_entity_candidate", "span_basis": "admitted_projection",
                             "index_sources": source["sources"], "page_no": source["page_no"],
                             "candidate": {key: candidate[key] for key in ("name", "mention", "entity_type", "scope")}},
            )
            db.add(evidence)
            db.flush()
            evidence_rows.append(evidence)
            for alias in sorted({candidate["name"], candidate["mention"]}):
                db.add(EntityAlias(id=new_id(), entity_id=entity.id, evidence_id=evidence.id, alias=alias))
            # 生产 Session 关闭 autoflush，后续候选消歧前须让本次别名可查询。
            db.flush()
        if claims is not None:
            from app.services.knowledge_index.claims import publish_claims

            publish_claims(db, manifest, source, claims, evidence_rows)
    job.state, job.lease_token = state, ""
    job.error = "部分切片或条目抽取失败，已保留有效结果" if state == "partial" else ""
    job.lease_until = now() + timedelta(seconds=60) if state == "partial" and current["sources"] else None
    db.flush()
    prune_orphan_entities(db)
    from app.services.knowledge_index.communities import enqueue_graph, is_enabled

    if is_enabled(kb):
        enqueue_graph(db, kb.id)
    db.commit()
    return True


def load_staged(db, work: dict) -> tuple[dict, set]:
    """从同输入 manifest 恢复已校验结果，旧格式缺少候选元数据时重新抽取。"""
    manifest = db.get(KnowledgeIndexManifest, work["id"])
    if manifest is None or manifest.input_hash != work["key"]:
        return {}, set()
    coverage = manifest.coverage_json
    staged = {}
    for chunk_id in coverage.get("retained_chunk_ids", []):
        rows = db.scalars(select(KnowledgeEvidence).where(
            KnowledgeEvidence.manifest_id == manifest.id, KnowledgeEvidence.chunk_id == chunk_id,
        ).order_by(KnowledgeEvidence.id)).all()
        if any("candidate" not in row.source_json for row in rows):
            continue
        entities = [{**row.source_json["candidate"], "quote": row.quote,
                     "start_at": row.start_at, "end_at": row.end_at} for row in rows]
        if not work["input"].get("capabilities"):
            staged[chunk_id] = entities
            continue
        claims = {"entities": entities, "relations": [], "events": []}
        evidence_indices = {row.id: index for index, row in enumerate(rows)}
        for relation in db.scalars(select(KnowledgeRelation).where(
            KnowledgeRelation.manifest_id == manifest.id, KnowledgeRelation.chunk_id == chunk_id,
        )):
            endpoints = [next(index for index, row in enumerate(rows)
                              if row.entity_id == entity_id and entities[index]["mention"] in relation.quote)
                         for entity_id in (relation.subject_id, relation.object_id)]
            claims["relations"].append({"subject": endpoints[0], "object": endpoints[1],
                **{key: getattr(relation, key) for key in ("predicate", "quote", "qualifiers", "start_at", "end_at")}})
        for event in db.scalars(select(KnowledgeEvent).where(
            KnowledgeEvent.manifest_id == manifest.id, KnowledgeEvent.chunk_id == chunk_id,
        )):
            participants = db.scalars(select(EventEntity.evidence_id).where(EventEntity.event_id == event.id)).all()
            claims["events"].append({"entities": [evidence_indices[value] for value in participants],
                **{key: getattr(event, key) for key in ("text", "quote", "qualifiers", "start_at", "end_at")}})
        staged[chunk_id] = claims
    return staged, set(coverage.get("completed_chunk_ids", [])) & set(staged)


def merge_staged(previous: dict, current: dict, errors: list) -> dict:
    """重试只追加通过原文校验的结果，重映射端点并去重，避免覆盖掉先前成功条目。"""
    result = {kind: list(previous[kind]) for kind in ("entities", "relations", "events")}
    indices = {}
    for index, entity in enumerate(current["entities"]):
        if entity not in result["entities"]:
            if len(result["entities"]) >= 100:
                errors.append({"kind": "entities", "index": index, "code": "retention_limit"})
                continue
            result["entities"].append(entity)
        indices[index] = result["entities"].index(entity)
    for kind in ("relations", "events"):
        for item in current[kind]:
            item = dict(item)
            participants = [item["subject"], item["object"]] if kind == "relations" else item["entities"]
            if any(index not in indices for index in participants):
                continue
            if kind == "relations":
                item.update(subject=indices[item["subject"]], object=indices[item["object"]])
            else:
                item["entities"] = [indices[value] for value in item["entities"]]
            if item not in result[kind]:
                if len(result[kind]) >= (100 if kind == "relations" else 30):
                    errors.append({"kind": kind, "index": -1, "code": "retention_limit"})
                    continue
                result[kind].append(item)
    return result


async def run_once(session_factory, extractor=None) -> bool:
    """模型调用位于数据库事务之外；失败退避可重试，单块失败形成部分覆盖。"""
    with session_factory() as db:
        work = claim_job(db)
        staged, completed = load_staged(db, work) if work else ({}, set())
    if work is None:
        return False
    failures, rejected = {}, {}
    try:
        capabilities = work["input"].get("capabilities", [])
        from app.services.knowledge_index.claims import extract_claims, ground_claims

        selected_extractor = extractor or (extract_claims if capabilities else extract_entities)
        for source in work["input"]["sources"]:
            if source["chunk_id"] in completed:
                continue
            errors = []
            try:
                model_config = work["input"]["model"]
                if capabilities:
                    model_config = {**model_config, "_capabilities": capabilities, "_item_errors": errors}
                raw = await selected_extractor(source["content"], model_config)
                items = (
                    ground_claims(source["content"], raw, capabilities, errors=errors) if capabilities
                    else grounded_candidates(source["content"], raw, errors=errors)
                )
                previous = staged.get(source["chunk_id"])
                if previous is not None:
                    if capabilities:
                        items = merge_staged(previous, items, errors)
                    else:
                        items = merge_staged(
                            {"entities": previous, "relations": [], "events": []},
                            {"entities": items, "relations": [], "events": []}, errors,
                        )["entities"]
                staged[source["chunk_id"]] = items
                if errors:
                    rejected[source["chunk_id"]] = errors
            except Exception as exc:
                # 不存储模型响应或 URL 等潜在敏感信息，只存类型供诊断。
                if source["chunk_id"] in staged:
                    rejected[source["chunk_id"]] = [{"kind": "chunk", "index": -1, "code": type(exc).__name__}]
                else:
                    failures[source["chunk_id"]] = type(exc).__name__
            with session_factory() as db:
                if not heartbeat(db, work):
                    return True
        if failures and not staged:
            raise ValueError("所有可用切片抽取失败；请检查本地模型和输出格式")
        with session_factory() as db:
            publish(db, work, staged, failures, rejected)
    except Exception as exc:
        with session_factory() as db:
            db.execute(update(KnowledgeIndexJob).where(
                KnowledgeIndexJob.id == work["id"], KnowledgeIndexJob.lease_token == work["token"],
                KnowledgeIndexJob.state == "running",
            ).values(state="failed", error=f"抽取/发布失败：{type(exc).__name__}；请检查本机 Ollama 配置及输出",
                     lease_until=now() + timedelta(seconds=60)))
            db.commit()
    return True
