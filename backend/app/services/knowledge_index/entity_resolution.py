"""实体名称对齐与有界消歧；只归一化格式，保留原文名称及来源证据。"""

import unicodedata

from sqlalchemy import select

from app.db.models import EntityAlias, KnowledgeEntity, KnowledgeEvidence


def normalized_name(value: str) -> str:
    """全半角和连续空白视为格式差异，保留大小写及名称内部词语边界。"""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def resolve_entity(db, kb_id: str, candidate: dict, scope: str):
    """只在同库、同类型和同消歧范围内复用唯一身份；不做子串或相似度合并。"""
    rows = db.scalars(select(KnowledgeEntity).where(
        KnowledgeEntity.knowledge_base_id == kb_id,
        KnowledgeEntity.entity_type == candidate["entity_type"],
        KnowledgeEntity.scope == scope,
    )).all()
    name = normalized_name(candidate["name"])
    exact = [row for row in rows if normalized_name(row.canonical_name) == name]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    # 别名还需通过当前来源回查，禁用/过期证据不能把两个实体连接起来。
    aliases = db.execute(select(EntityAlias.alias, EntityAlias.entity_id).join(
        KnowledgeEvidence, EntityAlias.evidence_id == KnowledgeEvidence.id,
    ).where(
        KnowledgeEvidence.knowledge_base_id == kb_id,
        EntityAlias.entity_id.in_([row.id for row in rows]),
    )).all() if rows else []
    from app.services.knowledge_index.service import valid_evidence

    names = {normalized_name(candidate["name"]), normalized_name(candidate["mention"])}
    matched_aliases = {alias for alias, _ in aliases if normalized_name(alias) in names}
    matches = {item["entity_id"] for alias in matched_aliases
               for item in valid_evidence(db, kb_id, name=alias, limit=1000)
               if item["entity_id"] in {row.id for row in rows}}
    return next((row for row in rows if row.id in matches), None) if len(matches) == 1 else None
