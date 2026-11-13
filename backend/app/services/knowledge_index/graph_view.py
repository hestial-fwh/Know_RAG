"""知识库只读子图，复用检索的版本和引文门禁，限制单次节点与边数量。"""

from sqlalchemy import or_, select

from app.db.models import Document, KnowledgeBase, KnowledgeEntity, KnowledgeEvidence, KnowledgeRelation, KnowledgeEvent, EventEntity
from app.services.knowledge_index.service import enabled
from app.services.retrieval.associations import EvidenceReader, _event_entities


def knowledge_graph(db, kb_id, *, query="", focus=None, limit=80):
    kb = db.get(KnowledgeBase, kb_id)
    flags = (kb.retrieval_profile or {}).get("build_indexes", [])
    meta = {"enabled": enabled(kb), "limit": limit, "truncated": False,
            "scope": "bounded_current_subgraph", "build_indexes": flags}
    if not enabled(kb):
        return {"nodes": [], "edges": [], "meta": {**meta, "state": "disabled"}}
    reader = EvidenceReader(db, kb_id)
    meta.update(coverage=reader.coverage, truncated=reader.truncated)
    seeds = None
    if query or focus:
        statement = select(KnowledgeEntity.id).where(KnowledgeEntity.knowledge_base_id == kb_id)
        if query:
            statement = statement.where(KnowledgeEntity.canonical_name.icontains(query, autoescape=True))
        if focus:
            statement = statement.where(KnowledgeEntity.id == focus)
        matches = db.scalars(statement.order_by(KnowledgeEntity.id).limit(limit + 1)).all()
        meta["truncated"] |= len(matches) > limit
        seeds = set(matches[:limit])
        if not seeds:
            return {"nodes": [], "edges": [], "meta": {**meta, "state": "empty"}}
    nodes, edges = {}, []
    edge_limit = limit * 2

    def evidence(row):
        document = db.get(Document, row.document_id)
        snapshot = reader.manifests[row.manifest_id]
        source = reader.sources[(row.manifest_id, row.chunk_id)]
        return {"chunk_id": row.chunk_id, "document_id": row.document_id,
                "document_title": document.title, "parse_revision": snapshot["parse_revision"],
                "quote": row.quote[:1000], "quote_truncated": len(row.quote) > 1000,
                "page_no": source.get("page_no"), "span_basis": "admitted_projection"}

    def add_entity(entity_id):
        if entity_id in nodes:
            return True
        if len(nodes) >= limit:
            meta["truncated"] = True
            return False
        entity = db.get(KnowledgeEntity, entity_id)
        if entity is None or entity.knowledge_base_id != kb_id:
            return False
        proofs = db.scalars(select(KnowledgeEvidence).where(
            KnowledgeEvidence.knowledge_base_id == kb_id, KnowledgeEvidence.entity_id == entity_id,
            KnowledgeEvidence.manifest_id.in_(reader.manifests),
        ).order_by(KnowledgeEvidence.id).limit(21)).all()
        meta["truncated"] |= len(proofs) > 20
        valid = [proof for proof in proofs if reader.valid(proof)]
        if not valid:
            return False
        nodes[entity_id] = {"id": entity_id, "kind": "entity", "label": entity.canonical_name,
            "entity_type": entity.entity_type, "scope": entity.scope, "derived": True,
            "evidence": [evidence(proof) for proof in valid[:3]],
            "evidence_truncated": len(proofs) > 20 or len(valid) > 3}
        return True

    # 搜索与展开在SQL中限定端点，避免全图截断后再过滤导致邻接节点永远无法访问。
    relations = select(KnowledgeRelation).where(KnowledgeRelation.knowledge_base_id == kb_id,
        KnowledgeRelation.manifest_id.in_(reader.ids["relations"]))
    if seeds is not None:
        relations = relations.where(or_(KnowledgeRelation.subject_id.in_(seeds), KnowledgeRelation.object_id.in_(seeds)))
    rows = db.scalars(relations.order_by(KnowledgeRelation.id).limit(edge_limit + 1)).all()
    meta["truncated"] |= len(rows) > edge_limit
    for row in rows[:edge_limit]:
        if not reader.valid(row) or not all(reader.entity_proof(eid, row) for eid in (row.subject_id, row.object_id)):
            continue
        if add_entity(row.subject_id) and add_entity(row.object_id):
            edges.append({"id": row.id, "source": row.subject_id, "target": row.object_id,
                "kind": "relation", "label": row.predicate, "qualifiers": row.qualifiers,
                "derived": True, "evidence": [evidence(row)]})
    events = select(KnowledgeEvent).where(KnowledgeEvent.knowledge_base_id == kb_id,
        KnowledgeEvent.manifest_id.in_(reader.ids["events"]))
    if seeds is not None:
        events = events.where(KnowledgeEvent.id.in_(select(EventEntity.event_id).where(EventEntity.entity_id.in_(seeds))))
    event_rows = db.scalars(events.order_by(KnowledgeEvent.id).limit(limit + 1)).all()
    meta["truncated"] |= len(event_rows) > limit
    for event in event_rows[:limit]:
        if not reader.valid(event):
            continue
        participants = _event_entities(reader, event, include_generic=True)
        if not participants:
            continue
        if len(nodes) + len(set(participants) - nodes.keys()) + 1 > limit or len(edges) + len(participants) > edge_limit:
            meta["truncated"] = True
            continue
        if not all(add_entity(entity_id) for entity_id in sorted(participants)):
            continue
        event_id = f"event:{event.id}"
        nodes[event_id] = {"id": event_id, "kind": "event", "label": event.text,
            "derived": True, "qualifiers": event.qualifiers, "evidence": [evidence(event)]}
        for entity_id in sorted(participants):
            edges.append({"id": f"{event_id}:{entity_id}", "source": event_id, "target": entity_id,
                "kind": "participation", "label": "事件参与", "derived": True, "evidence": [evidence(event)]})
    # 仅实体底座也能浏览有来源的节点，尚无关系时保持孤立节点的真实状态。
    proof_query = select(KnowledgeEvidence.entity_id).where(KnowledgeEvidence.knowledge_base_id == kb_id,
        KnowledgeEvidence.manifest_id.in_(reader.manifests)).distinct()
    if seeds is not None:
        proof_query = proof_query.where(KnowledgeEvidence.entity_id.in_(seeds))
    entity_ids = db.scalars(proof_query.order_by(KnowledgeEvidence.entity_id).limit(limit + 1)).all()
    meta["truncated"] |= len(entity_ids) > limit
    for entity_id in entity_ids[:limit]:
        add_entity(entity_id)
    meta.update(state="ready" if nodes else "empty" if reader.manifests else "not_ready",
                displayed_nodes=len(nodes), displayed_edges=len(edges))
    return {"nodes": list(nodes.values()), "edges": edges, "meta": meta}
