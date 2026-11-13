"""P3 受限 SQL 的 Local/事件检索；返回有效原文切片与可追溯路径。"""

from collections import defaultdict
import json
from types import SimpleNamespace
import re
from time import perf_counter

from sqlalchemy import func, literal, or_, select

from app.db.models import (
    Chunk, Document, EntityAlias, EventEntity, KnowledgeBase, KnowledgeEntity,
    KnowledgeEvent, KnowledgeEvidence, KnowledgeIndexManifest, KnowledgeRelation,
)
from app.services.knowledge_index.service import digest, enabled, snapshot_document

GENERIC_ENTITIES = {"学校", "公司", "机构", "研究", "项目", "专业", "人员", "系统"}


class EvidenceReader:
    """查询共享同一来源缓存；仅 current manifest 进入 SQL 扩展，最多检查1000篇文档。"""

    def __init__(self, db, kb_id: str):
        self.db, self.kb_id = db, kb_id
        self.sources, self.manifests = {}, {}
        self.ids = {"relations": [], "events": []}
        self.coverage = {kind: {"ready": 0, "partial": 0, "total": 0} for kind in self.ids}
        kb = db.get(KnowledgeBase, kb_id)
        self.truncated = False
        if not enabled(kb):
            return
        flags = (kb.retrieval_profile or {}).get("build_indexes", [])
        documents = db.scalars(select(Document).where(
            Document.knowledge_base_id == kb_id,
        ).order_by(Document.id).limit(1001)).all()
        self.truncated = len(documents) > 1000
        for document in documents[:1000]:
            snapshot = snapshot_document(db, document)
            for kind in self.ids:
                if kind in flags:
                    self.coverage[kind]["total"] += 1
            if not snapshot or not snapshot["sources"]:
                continue
            key = digest(snapshot)
            manifest = db.scalar(select(KnowledgeIndexManifest).where(
                KnowledgeIndexManifest.knowledge_base_id == kb_id,
                KnowledgeIndexManifest.document_id == document.id,
                KnowledgeIndexManifest.input_hash == key,
                KnowledgeIndexManifest.state.in_(["ready", "partial"]),
            ))
            if manifest is None or not manifest.coverage_json.get("processed_chunks"):
                continue
            self.manifests[manifest.id] = snapshot
            for source in snapshot["sources"]:
                self.sources[(manifest.id, source["chunk_id"])] = source
            for kind in snapshot.get("capabilities", []):
                self.ids[kind].append(manifest.id)
                self.coverage[kind][manifest.state] += 1

    def capabilities(self):
        return {kind: bool(ids) for kind, ids in self.ids.items()}

    def valid(self, row) -> bool:
        snapshot = self.manifests.get(row.manifest_id)
        source = self.sources.get((row.manifest_id, row.chunk_id))
        if not source or snapshot["document_id"] != row.document_id or row.knowledge_base_id != self.kb_id:
            return False
        if hasattr(row, "parse_revision") and row.parse_revision != snapshot["parse_revision"]:
            return False
        if hasattr(row, "content_hash") and row.content_hash != source["content_hash"]:
            return False
        return (0 <= row.start_at < row.end_at <= len(source["content"])
                and source["content"][row.start_at:row.end_at] == row.quote)

    def entity_proof(self, entity_id: str, claim) -> bool:
        entity = self.db.get(KnowledgeEntity, entity_id)
        if entity is None or entity.knowledge_base_id != self.kb_id:
            return False
        proofs = self.db.scalars(select(KnowledgeEvidence).where(
            KnowledgeEvidence.manifest_id == claim.manifest_id,
            KnowledgeEvidence.chunk_id == claim.chunk_id, KnowledgeEvidence.entity_id == entity_id,
            KnowledgeEvidence.knowledge_base_id == self.kb_id,
        )).all()
        for proof in proofs:
            aliases = self.db.scalars(select(EntityAlias.alias).where(EntityAlias.evidence_id == proof.id)).all()
            if self.valid(proof) and any(alias in claim.quote for alias in aliases):
                return True
        return False


def _seeds(reader: EvidenceReader, query: str, direct: list[dict], limit: int) -> dict:
    db, kb_id = reader.db, reader.kb_id
    active = list(reader.manifests)
    if not active:
        return {}
    direct_ids = [item["chunk_id"] for item in direct[:100]]
    rows = db.execute(select(EntityAlias.alias, KnowledgeEvidence, KnowledgeEntity).join(
        KnowledgeEvidence, EntityAlias.evidence_id == KnowledgeEvidence.id,
    ).join(KnowledgeEntity, EntityAlias.entity_id == KnowledgeEntity.id).where(
        KnowledgeEvidence.knowledge_base_id == kb_id, KnowledgeEntity.knowledge_base_id == kb_id,
        KnowledgeEvidence.manifest_id.in_(active), func.length(EntityAlias.alias) > 1,
        or_(literal(query.lower()).contains(func.lower(EntityAlias.alias)), KnowledgeEvidence.chunk_id.in_(direct_ids)),
    ).order_by(EntityAlias.alias, KnowledgeEvidence.id).limit(401)).all()
    reader.truncated |= len(rows) > 400
    ranked = []
    for alias, proof, entity in rows[:400]:
        if entity.canonical_name in GENERIC_ENTITIES or not reader.valid(proof):
            continue
        exact = alias.lower() in query.lower()
        if not exact and proof.chunk_id not in direct_ids:
            continue
        ranked.append((not exact, -len(alias), entity.id, proof.chunk_id))
    seeds = {}
    for _, _, entity_id, chunk_id in sorted(ranked):
        seeds.setdefault(entity_id, {"support_chunk_ids": [chunk_id], "steps": []})
        if len(seeds) >= limit:
            break
    return seeds


def _local(reader, seeds, budget, deadline=None):
    frontier, visited, paths, fanout = dict(seeds), set(), [], defaultdict(int)
    candidates = set()
    truncated = False
    for hop in range(budget["max_hops"]):
        if not frontier:
            break
        if deadline is not None and perf_counter() > deadline:
            truncated = True
            break
        rows = reader.db.scalars(select(KnowledgeRelation).where(
            KnowledgeRelation.knowledge_base_id == reader.kb_id,
            KnowledgeRelation.manifest_id.in_(reader.ids["relations"]),
            or_(KnowledgeRelation.subject_id.in_(frontier), KnowledgeRelation.object_id.in_(frontier)),
        ).order_by(KnowledgeRelation.id).limit(501)).all()
        truncated |= len(rows) > 500
        next_frontier = {}
        for row in rows[:500]:
            if len(visited) >= budget["max_candidates"]:
                truncated = True
                break
            if row.id in visited or not reader.valid(row):
                continue
            if not all(reader.entity_proof(entity_id, row) for entity_id in (row.subject_id, row.object_id)):
                continue
            origin = next((eid for eid in sorted(frontier) if eid in (row.subject_id, row.object_id)), None)
            if origin is None or fanout[origin] >= budget["max_fanout"]:
                truncated = True
                continue
            previous = frontier[origin]
            support = list(dict.fromkeys(previous["support_chunk_ids"] + [row.chunk_id]))
            if len(candidates | set(support)) > budget["max_candidates"]:
                truncated = True
                continue
            visited.add(row.id)
            fanout[origin] += 1
            candidates.update(support)
            step = {"kind": "extracted_relation", "relation_id": row.id, "subject_id": row.subject_id,
                    "object_id": row.object_id, "predicate": row.predicate, "qualifiers": row.qualifiers,
                    "chunk_id": row.chunk_id, "document_id": row.document_id,
                    "source_channels": reader.sources[(row.manifest_id, row.chunk_id)]["sources"]}
            path = {"support_chunk_ids": support, "steps": previous["steps"] + [step], "hop": hop + 1}
            paths.append(path)
            target = row.object_id if origin == row.subject_id else row.subject_id
            if target not in seeds:
                next_frontier.setdefault(target, path)
        frontier = next_frontier
    return paths, truncated


def _event_entities(reader, event, *, include_generic=False) -> set[str]:
    if not reader.valid(event):
        return set()
    members = reader.db.execute(select(EventEntity, KnowledgeEvidence).join(
        KnowledgeEvidence, EventEntity.evidence_id == KnowledgeEvidence.id,
    ).where(EventEntity.event_id == event.id)).all()
    result = set()
    for member, proof in members:
        if (proof.entity_id != member.entity_id or proof.chunk_id != event.chunk_id
                or proof.manifest_id != event.manifest_id or not reader.valid(proof)):
            continue
        if reader.entity_proof(member.entity_id, event):
            entity = reader.db.get(KnowledgeEntity, member.entity_id)
            # 图谱浏览保留有来源的泛称节点，检索仍默认抑制泛称扇出。
            if include_generic or entity.canonical_name not in GENERIC_ENTITIES:
                result.add(member.entity_id)
    return result


def _entity_degrees(reader, entity_ids) -> dict:
    if not entity_ids or not reader.ids["events"]:
        return {}
    rows = reader.db.execute(select(EventEntity.entity_id, func.count()).join(
        KnowledgeEvent, EventEntity.event_id == KnowledgeEvent.id,
    ).where(
        KnowledgeEvent.manifest_id.in_(reader.ids["events"]),
        EventEntity.entity_id.in_(entity_ids),
    ).group_by(EventEntity.entity_id)).all()
    return dict(rows)


def _specificity(degrees: dict, members) -> float:
    degree = min((degrees.get(entity_id, 0) for entity_id in members), default=0)
    return round(max(0.0, 1.0 - degree / 20.0), 4)


def _event_relevance(row, members, seed_ids, terms, degrees, query) -> float:
    """事件相关性只使用可解释特征；direct_hit 只做平票裁决，不进入公式。"""
    participant = min(1.0, len(members & seed_ids) / max(1, min(len(seed_ids), 2)))
    text = (row.text or "").lower()
    hits = sum(1 for term in terms if term in text)
    term_overlap = min(1.0, hits / max(1, min(len(terms), 4)))
    specificity = _specificity(degrees, members)
    qualifier_text = json.dumps(row.qualifiers or {}, ensure_ascii=False).lower()
    query_lower = query.lower()
    time_condition = 0.0
    if any(token in query_lower for token in ("时间", "何时", "条件", "如果", "年", "月", "日")):
        markers = ("年", "月", "日", "条件", "如果", "期间", "截至")
        if any(marker in qualifier_text for marker in markers) or any(marker in text for marker in markers):
            time_condition = 1.0
    return round(
        0.40 * participant + 0.25 * term_overlap + 0.20 * specificity + 0.15 * time_condition,
        4,
    )


def _events(reader, seeds, query, direct, budget, deadline=None):
    # 语义种子来自原文向量/BM25；事件表示额外提供有界词法种子，不在此伪装事件向量检索。
    terms = re.findall(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query.lower())
    terms = list(dict.fromkeys(term[i:i + 2] for term in terms for i in range(max(1, len(term) - 1))))[:24]
    criteria = [KnowledgeEvent.chunk_id.in_([item["chunk_id"] for item in direct[:100]])]
    if seeds:
        criteria.append(KnowledgeEvent.id.in_(select(EventEntity.event_id).where(EventEntity.entity_id.in_(seeds))))
    criteria += [func.lower(KnowledgeEvent.text).contains(term, autoescape=True) for term in terms]
    rows = reader.db.scalars(select(KnowledgeEvent).where(
        KnowledgeEvent.knowledge_base_id == reader.kb_id,
        KnowledgeEvent.manifest_id.in_(reader.ids["events"]), or_(*criteria),
    ).order_by(KnowledgeEvent.id).limit(201)).all()
    truncated = len(rows) > 200
    seed_events = []
    for row in rows[:200]:
        members = _event_entities(reader, row)
        if members:
            seed_events.append((row, members))
    all_members = set().union(*(members for _, members in seed_events)) if seed_events else set()
    degrees = _entity_degrees(reader, all_members)
    seed_ids = set(seeds)
    scored = []
    for row, members in seed_events:
        relevance = _event_relevance(row, members, seed_ids, terms, degrees, query)
        if relevance > 0:
            scored.append((relevance, row, members))
    # 事件通道按事件相关性排序；direct_hit 不参与主排序，只用于相同得分的稳定裁决。
    scored.sort(key=lambda item: (-item[0], item[1].id))
    direct_ids = {item["chunk_id"] for item in direct}
    paths, frontier, visited, candidates, fanout = [], [], set(), set(), defaultdict(int)
    for relevance, row, members in scored[:budget["max_seeds"]]:
        if len(candidates | {row.chunk_id}) > budget["max_candidates"]:
            truncated = True
            break
        path = {"support_chunk_ids": [row.chunk_id], "steps": [{"kind": "event_seed", "event_id": row.id,
                "chunk_id": row.chunk_id, "qualifiers": row.qualifiers,
                "event_text": row.text, "event_time": (row.qualifiers or {}).get("time")}],
                "hop": 0, "event_ids": [row.id], "event_relevance": relevance,
                "specificity_norm": _specificity(degrees, members)}
        paths.append(path)
        frontier.append((members, path))
        candidates.add(row.chunk_id)
        visited.add(row.id)
    expanded = set()
    for hop in range(budget["max_hops"]):
        if deadline is not None and perf_counter() > deadline:
            truncated = True
            break
        next_frontier = []
        for members, previous in frontier:
            if deadline is not None and perf_counter() > deadline:
                truncated = True
                break
            missing = members - degrees.keys()
            if missing:
                degrees.update(_entity_degrees(reader, missing))
            # 先展开辨识度较高的实体；高频实体全查询仍只享有同一份扇出预算。
            for entity_id in sorted(members, key=lambda eid: (degrees.get(eid, 0), eid)):
                if entity_id in expanded:
                    continue
                if len(expanded) >= budget["max_candidates"]:
                    truncated = True
                    break
                expanded.add(entity_id)
                rows = reader.db.scalars(select(KnowledgeEvent).join(EventEntity).where(
                    KnowledgeEvent.knowledge_base_id == reader.kb_id,
                    KnowledgeEvent.manifest_id.in_(reader.ids["events"]), EventEntity.entity_id == entity_id,
                ).order_by(KnowledgeEvent.id).limit(51)).all()
                truncated |= len(rows) > 50
                for row in rows[:50]:
                    if len(visited) >= budget["max_candidates"]:
                        truncated = True
                        break
                    if row.id in visited:
                        continue
                    if fanout[entity_id] >= budget["max_fanout"]:
                        truncated = True
                        break
                    targets = _event_entities(reader, row)
                    if entity_id not in targets:
                        continue
                    support = list(dict.fromkeys(previous["support_chunk_ids"] + [row.chunk_id]))
                    if len(candidates | set(support)) > budget["max_candidates"]:
                        truncated = True
                        continue
                    visited.add(row.id)
                    candidates.update(support)
                    fanout[entity_id] += 1
                    relevance = _event_relevance(row, targets, seed_ids, terms, degrees, query)
                    path = {"support_chunk_ids": support, "steps": previous["steps"] + [{
                        "kind": "shared_entity", "entity_id": entity_id, "event_id": row.id,
                        "chunk_id": row.chunk_id, "qualifiers": row.qualifiers,
                        "shared_entity_degree": degrees.get(entity_id, 0),
                        "event_text": row.text, "event_time": (row.qualifiers or {}).get("time"),
                        "source_channels": reader.sources[(row.manifest_id, row.chunk_id)]["sources"]}],
                        "hop": hop + 1,
                        "event_ids": list(dict.fromkeys(previous["event_ids"] + [row.id])),
                        "event_relevance": max(previous["event_relevance"], relevance),
                        "specificity_norm": max(previous["specificity_norm"], _specificity(degrees, targets)),
                        "direct_hit": row.chunk_id in direct_ids}
                    paths.append(path)
                    next_frontier.append((targets, path))
        frontier = next_frontier
    return paths, truncated


def _validated_paths(reader, paths):
    """扩展后再检查涉及文档，避免查询期间的重解析/禁用把旧投影送进最终上下文。"""
    support_ids = {chunk_id for path in paths for chunk_id in path["support_chunk_ids"]}
    reader.db.expire_all()
    valid_chunks, checked = set(), {}
    for (manifest_id, chunk_id), _source in reader.sources.items():
        if chunk_id not in support_ids:
            continue
        snapshot = reader.manifests[manifest_id]
        document_id = snapshot["document_id"]
        if document_id not in checked:
            document = reader.db.get(Document, document_id)
            current = snapshot_document(reader.db, document) if document else None
            checked[document_id] = current is not None and digest(current) == digest(snapshot)
        if checked[document_id]:
            valid_chunks.add(chunk_id)
    source_changed = bool(support_ids - valid_chunks)
    return [path for path in paths if set(path["support_chunk_ids"]) <= valid_chunks], source_changed


def _materialize(reader, paths, kind, include_content):
    from app.services.retrieval_service import _row_to_result

    candidates = {}
    # 长路径先参与覆盖选择，避免第二跳总是被种子块占满预算。
    for path in sorted(paths, key=lambda path: (-path["hop"], path["support_chunk_ids"])):
        for chunk_id in path["support_chunk_ids"]:
            if chunk_id not in candidates:
                chunk = reader.db.get(Chunk, chunk_id)
                document = reader.db.get(Document, chunk.document_id)
                source = next(source for (mid, cid), source in reader.sources.items() if cid == chunk_id)
                row = SimpleNamespace(**{column.name: getattr(chunk, column.name) for column in Chunk.__table__.columns})
                row.chunk_id, row.title, row.file_name = chunk.id, document.title, document.file_name
                row.knowledge_source, row.knowledge_channel = document.source, document.channel
                row.parse_status, row.file_hash = document.parse_status, document.file_hash
                row.content = row.matched_content = source["content"]
                row.association_score = 0.0
                item = _row_to_result(row, kind, include_content, "association_score")
                item["source_projection"] = {"span_basis": "admitted_projection", "channels": source["sources"]}
                item["association_paths"] = []
                candidates[chunk_id] = item
            candidates[chunk_id]["association_paths"].append(path)
    return list(candidates.values())


def _annotate_events(candidates, paths):
    """同一 chunk 多事件支持时取最大相关性；路径只做解释，不重复计票。"""
    by_chunk = defaultdict(list)
    for path in paths:
        if not path.get("event_ids"):
            continue
        for chunk_id in path["support_chunk_ids"]:
            by_chunk[chunk_id].append(path)
    for item in candidates:
        related = by_chunk.get(item["chunk_id"], [])
        item["event_ids"] = sorted({event_id for path in related for event_id in path["event_ids"]})
        item["event_relevance"] = max((path.get("event_relevance", 0.0) for path in related), default=0.0)
        item["specificity_norm"] = max((path.get("specificity_norm", 0.0) for path in related), default=0.0)
        item["direct_hit"] = any(path.get("direct_hit") for path in item.get("association_paths", []))


def _channel_meta(kind, seeds, candidates, paths, truncated, budget, source_changed):
    return {
        "provider": "know_rag_local_v1" if kind == "relations" else "know_rag_event_sql_v1",
        "kind": kind, "seed_count": len(seeds), "candidate_count": len(candidates),
        "budget": budget, "budget_truncated": truncated,
        "coverage": None,  # 由调用方补入 reader.coverage，保持旧包装可复用
        "paths": paths,
        "coverage_incomplete": source_changed,
        "source_changed_during_query": source_changed,
        "aggregation_supported": False,
    }


def _run_channel(reader, seeds, query, direct, kind, budget, include_content, deadline=None):
    started = perf_counter()
    paths, truncated = (_local(reader, seeds, budget, deadline=deadline) if kind == "relations"
                        else _events(reader, seeds, query, direct, budget, deadline=deadline))
    paths, source_changed = _validated_paths(reader, paths)
    candidates = _materialize(reader, paths, kind, include_content)
    if kind == "events":
        _annotate_events(candidates, paths)
    meta = _channel_meta(kind, seeds, candidates, paths, truncated or reader.truncated,
                         budget, source_changed)
    meta["coverage"] = reader.coverage[kind]
    meta["coverage_incomplete"] |= reader.truncated or (
        reader.coverage[kind]["ready"] != reader.coverage[kind]["total"]
    )
    meta["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)
    return candidates, meta


def search_associations(reader, query, direct, kind, budget, include_content):
    """旧单通道路径；event_graph 与 graph/local 继续走这里，行为保持不变。"""
    seeds = _seeds(reader, query, direct, budget["max_seeds"])
    return _run_channel(reader, seeds, query, direct, kind, budget, include_content)


def _slice_budget(budget: dict, kind: str, cap: int | None = None) -> dict:
    quota_key = "relation_quota" if kind == "relations" else "event_quota"
    quota = int(budget.get(quota_key) or (30 if kind == "relations" else 20))
    total = int(cap if cap is not None else (
        budget.get("max_structure_candidates") or budget.get("max_candidates") or 50
    ))
    current = int(budget.get("max_candidates") or total)
    return {**budget, "max_candidates": max(1, min(quota, total, current))}


def rank_events(candidates: list[dict]) -> list[dict]:
    """事件通道独立排序：相关性 -> 特异性 -> 跳数 -> direct 平票 -> 事件/切片 ID。"""
    def key(item):
        paths = item.get("association_paths", [])
        min_hop = min((path.get("hop", 0) for path in paths), default=0)
        return (
            -float(item.get("event_relevance") or 0),
            -float(item.get("specificity_norm") or 0),
            min_hop,
            -int(item.get("direct_hit") or 0),
            str((item.get("event_ids") or [""])[0]),
            item["chunk_id"],
        )
    return sorted(candidates, key=key)


def search_relation_channel(reader, query, direct, budget, include_content, seeds):
    candidates, meta = _run_channel(reader, seeds, query, direct, "relations", budget, include_content)
    quota = budget.get("relation_quota")
    if quota:
        candidates = candidates[:quota]
    meta["relation_quota"] = quota
    return candidates, meta


def search_event_channel(reader, query, direct, budget, include_content, seeds, threshold=0.0, deadline=None):
    candidates, meta = _run_channel(
        reader, seeds, query, direct, "events", budget, include_content, deadline=deadline,
    )
    ranked = rank_events(candidates)
    ranked = [item for item in ranked if float(item.get("event_relevance") or 0) >= threshold]
    quota = budget.get("event_quota")
    if quota:
        ranked = ranked[:quota]
    direct_ids = {item["chunk_id"] for item in direct}
    meta.update({
        "event_quota": quota,
        "events_scored": sum(1 for path in meta["paths"] if path.get("event_ids")),
        "direct_overlap_count": sum(1 for item in ranked if item["chunk_id"] in direct_ids),
        "top_event_relevance": ranked[0]["event_relevance"] if ranked else 0.0,
    })
    return ranked, meta


def search_structure_channels(reader, query, direct, kinds, budget, include_content, event_threshold=0.0):
    """关系/事件只计算一次 seeds，共享来源缓存与总超时；通道各自打标签。"""
    seeds = _seeds(reader, query, direct, budget["max_seeds"])
    deadline = perf_counter() + max(1, int(budget.get("structure_timeout_ms") or 800)) / 1000
    total_cap = int(budget.get("max_structure_candidates") or budget.get("max_candidates") or 50)
    batches, metas = {}, {}
    if "relations" in kinds:
        relation_budget = _slice_budget(budget, "relations", cap=total_cap)
        try:
            relation_candidates, relation_meta = search_relation_channel(
                reader, query, direct, relation_budget, include_content, seeds,
            )
            relation_meta["timeout"] = perf_counter() > deadline
        except Exception as exc:
            relation_candidates = []
            relation_meta = {
                "provider": "know_rag_local_v1", "kind": "relations",
                "seed_count": len(seeds), "candidate_count": 0, "paths": [],
                "timeout": False, "failed": True, "error": type(exc).__name__,
                "coverage": reader.coverage["relations"], "coverage_incomplete": True,
                "budget_truncated": True, "aggregation_supported": False,
            }
        batches["relations"], metas["relations"] = relation_candidates, relation_meta
    if "events" in kinds:
        remaining = max(0, total_cap - len(batches.get("relations", [])))
        if remaining <= 0:
            batches["events"] = []
            metas["events"] = {
                "provider": "know_rag_event_sql_v1", "kind": "events", "seed_count": len(seeds),
                "candidate_count": 0, "paths": [], "timeout": False, "budget_exhausted": True,
                "coverage": reader.coverage["events"], "coverage_incomplete": True,
                "budget_truncated": True, "aggregation_supported": False,
                "top_event_relevance": 0.0, "events_scored": 0, "direct_overlap_count": 0,
            }
        elif perf_counter() > deadline:
            batches["events"] = []
            metas["events"] = {
                "provider": "know_rag_event_sql_v1", "kind": "events", "seed_count": len(seeds),
                "candidate_count": 0, "paths": [], "timeout": True,
                "coverage": reader.coverage["events"], "coverage_incomplete": True,
                "budget_truncated": True, "aggregation_supported": False,
                "top_event_relevance": 0.0, "events_scored": 0, "direct_overlap_count": 0,
            }
        else:
            event_budget = _slice_budget(budget, "events", cap=remaining)
            try:
                event_candidates, event_meta = search_event_channel(
                    reader, query, direct, event_budget, include_content, seeds,
                    threshold=event_threshold, deadline=deadline,
                )
                event_meta["timeout"] = perf_counter() > deadline
            except Exception as exc:
                event_candidates = []
                event_meta = {
                    "provider": "know_rag_event_sql_v1", "kind": "events",
                    "seed_count": len(seeds), "candidate_count": 0, "paths": [],
                    "timeout": False, "failed": True, "error": type(exc).__name__,
                    "coverage": reader.coverage["events"], "coverage_incomplete": True,
                    "budget_truncated": True, "aggregation_supported": False,
                    "top_event_relevance": 0.0, "events_scored": 0, "direct_overlap_count": 0,
                }
            batches["events"], metas["events"] = event_candidates, event_meta
    return batches, metas, seeds


def fuse_associations(base, associations, top_k, rrf_k):
    """两份排名做 RRF；每个来源只计一票，证据链以完整原文集合占用最终预算。"""
    ordered = rank_associations(base, associations, rrf_k)
    return select_association_evidence(ordered, top_k, {item["chunk_id"] for item in base})


def rank_associations(base, associations, rrf_k):
    """先保留全部融合候选供重排，不在候选阶段提前消耗最终 Top K。"""
    merged = {}
    for channel, items in (("hybrid", base), ("association", associations)):
        for rank, item in enumerate(items, 1):
            key = item["chunk_id"]
            if key not in merged:
                merged[key] = {**item, "score": 0.0, "retrieval_sources": []}
            entry = merged[key]
            entry["score"] += 1 / (rrf_k + rank)
            entry["retrieval_sources"].append(channel)
            if channel == "association":
                entry["association_paths"] = item["association_paths"]
    for item in merged.values():
        item["rrf_score"] = item["score"]
    return sorted(merged.values(), key=lambda item: (-item["score"], item["chunk_id"]))


def select_association_evidence(ordered, top_k, base_ids):
    """重排后按完整来源集合装入输出预算；链放不下时不伪造完整路径。"""
    merged = {item["chunk_id"]: item for item in ordered}
    selected = {}
    for item in ordered:
        if item["chunk_id"] in selected:
            continue
        paths = sorted(item.get("association_paths", []), key=lambda path: (-path["hop"], path["support_chunk_ids"]))
        requirements = next((path["support_chunk_ids"] for path in paths
                             if set(path["support_chunk_ids"]) <= merged.keys()
                             and len(set(path["support_chunk_ids"]) | set(selected)) <= top_k), None)
        if requirements is None:
            if item["chunk_id"] not in base_ids or len(selected) >= top_k:
                continue
            requirements = [item["chunk_id"]]
        for chunk_id in requirements:
            selected.setdefault(chunk_id, merged[chunk_id])
    for item in selected.values():
        item["association_paths"] = [path for path in item.get("association_paths", [])
                                     if set(path["support_chunk_ids"]) <= set(selected)]
    return list(selected.values())


def _path_support(items: list[dict]) -> set[str]:
    return {
        chunk_id for item in items
        for path in item.get("association_paths", [])
        for chunk_id in path.get("support_chunk_ids", [])
    }


def select_fusion_evidence(
    ordered: list[dict],
    top_k: int,
    base_ids: set[str],
    *,
    event_pool: list[dict] | tuple = (),
    relation_pool: list[dict] | tuple = (),
    event_reserved_slots: int = 0,
    relation_reserved_slots: int = 0,
    event_threshold: float = 0.0,
    relation_threshold: float = 0.0,
):
    """完整链先装填，再给事件/关系独有证据保留位；未用完的保留位自动归还通用池。"""
    merged = {item["chunk_id"]: item for item in ordered}
    selected: dict[str, dict] = {}
    # 1. 完整证据链优先：即使结构通道权重较低，也不能在通用候选填满后才被截断。
    for item in ordered:
        paths = sorted(
            item.get("association_paths", []),
            key=lambda path: (-path["hop"], path["support_chunk_ids"]),
        )
        requirements = next((
            path["support_chunk_ids"] for path in paths
            if set(path["support_chunk_ids"]) <= merged.keys()
            and len(set(path["support_chunk_ids"]) | set(selected)) <= top_k
        ), None)
        if requirements is None:
            continue
        for chunk_id in requirements:
            selected.setdefault(chunk_id, merged[chunk_id])
        if len(selected) >= top_k:
            break
    # 2. 通用池按当前排名填满剩余席位。
    for item in ordered:
        if len(selected) >= top_k:
            break
        selected.setdefault(item["chunk_id"], item)
    selected_list = list(selected.values())
    original_order = [item["chunk_id"] for item in ordered]
    protected = _path_support(selected_list)
    used = {"event": 0, "relation": 0}
    pools = (
        ("event", event_pool, event_reserved_slots, event_threshold,
         lambda item: float(item.get("event_relevance") or 0)),
        ("relation", relation_pool, relation_reserved_slots, relation_threshold,
         lambda item: float(item.get("relation_score") or item.get("association_score") or 0)),
    )
    for label, pool, slots, threshold, scorer in pools:
        if slots <= 0:
            continue
        for candidate in sorted(pool, key=lambda item: (-scorer(item), item["chunk_id"])):
            if used[label] >= slots:
                break
            chunk_id = candidate["chunk_id"]
            # 保留位只给通道独有证据；已进入主排名的重合候选不占位。
            if chunk_id in selected or scorer(candidate) < threshold:
                continue
            if len(selected) < top_k:
                selected[chunk_id] = candidate
                used[label] += 1
                continue
            removable = next((cid for cid in reversed(original_order)
                              if cid in selected and cid not in protected), None)
            if removable is None:
                break
            selected.pop(removable)
            selected[chunk_id] = candidate
            used[label] += 1
    ordered_ids = {item["chunk_id"] for item in ordered}
    final = [item for item in ordered if item["chunk_id"] in selected]
    final += [item for chunk_id, item in selected.items() if chunk_id not in ordered_ids]
    returned = {item["chunk_id"] for item in final}
    for item in final:
        if "association_paths" in item:
            item["association_paths"] = [
                path for path in item["association_paths"]
                if set(path["support_chunk_ids"]) <= returned
            ]
    return final[:top_k], used


def _merge_channel_entry(merged: dict, item: dict) -> dict:
    entry = merged.get(item["chunk_id"])
    if entry is None:
        entry = {**item, "score": 0.0, "retrieval_sources": []}
        merged[item["chunk_id"]] = entry
    for source in item.get("retrieval_sources") or []:
        if source not in entry["retrieval_sources"]:
            entry["retrieval_sources"].append(source)
    paths = entry.setdefault("association_paths", [])
    for path in item.get("association_paths") or []:
        if path not in paths:
            paths.append(path)
    for key in ("event_relevance", "specificity_norm"):
        if key in item:
            entry[key] = max(float(entry.get(key) or 0), float(item[key] or 0))
    if item.get("event_ids"):
        entry["event_ids"] = sorted(set(entry.get("event_ids") or []) | set(item["event_ids"]))
    if item.get("direct_hit"):
        entry["direct_hit"] = True
    return entry


def rank_structure_channels(
    relation: list[dict],
    event: list[dict],
    rrf_k: int = 60,
    weights: dict | None = None,
) -> list[dict]:
    """第一级融合：relation/event 先内部成一份 structure 排名。"""
    weights = {"relation": 1.0, "event": 1.0, **(weights or {})}
    merged: dict[str, dict] = {}
    for label, items in (("relation", relation), ("event", event)):
        weight = float(weights.get(label, 1.0))
        for rank, item in enumerate(items, 1):
            entry = _merge_channel_entry(merged, item)
            entry["score"] += weight / (rrf_k + rank)
    ordered = sorted(merged.values(), key=lambda item: (-item["score"], item["chunk_id"]))
    for index, item in enumerate(ordered, 1):
        item["structure_rank"] = index
        item["structure_score"] = item["score"]
    return ordered


def rank_fusion_channels(
    base: list[dict],
    structure: list[dict],
    rrf_k: int = 60,
    weights: dict | None = None,
) -> list[dict]:
    """第二级融合：base 与 structure 整体比较，结构内部票数不再直接叠加到通道数。"""
    weights = {"base": 1.0, "structure": 0.8, **(weights or {})}
    merged: dict[str, dict] = {}
    for label, items in (("base", base), ("structure", structure)):
        weight = float(weights.get(label, 1.0))
        for rank, item in enumerate(items, 1):
            entry = _merge_channel_entry(merged, item)
            entry["score"] += weight / (rrf_k + rank)
    ordered = sorted(merged.values(), key=lambda item: (-item["score"], item["chunk_id"]))
    for index, item in enumerate(ordered, 1):
        item["fusion_rank"] = index
    return ordered


def apply_graph_features(
    base: list[dict],
    relation: list[dict],
    event: list[dict],
    *,
    graph_weight: float = 0.15,
    event_weight: float = 0.25,
    rrf_k: int = 60,
) -> list[dict]:
    """图只作为已入池基础候选的重排特征，不新增候选。"""
    relation_rank = {item["chunk_id"]: rank for rank, item in enumerate(relation, 1)}
    event_rank = {item["chunk_id"]: rank for rank, item in enumerate(event, 1)}
    for item in base:
        chunk_id = item["chunk_id"]
        base_score = float(item.get("score") or 0)
        feature = 0.0
        sources = list(item.get("retrieval_sources") or [])
        if chunk_id in relation_rank:
            feature += graph_weight / (rrf_k + relation_rank[chunk_id])
            if "relation" not in sources:
                sources.append("relation")
        if chunk_id in event_rank:
            feature += event_weight / (rrf_k + event_rank[chunk_id])
            if "event" not in sources:
                sources.append("event")
        item["graph_feature"] = round(feature, 6)
        item["score"] = base_score + feature
        item["retrieval_sources"] = sources
    ordered = sorted(base, key=lambda item: (-float(item.get("score") or 0), item["chunk_id"]))
    for index, item in enumerate(ordered, 1):
        item["fusion_rank"] = index
    return ordered
