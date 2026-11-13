"""知识库独立抽取模型与默认模型的来源指纹隔离。"""

import pytest
from test_knowledge_index_foundation import foundation
from app.db.models import Document, KnowledgeBase, ModelConfig
from app.schemas.retrieval_strategy import RetrievalProfile
from app.services.knowledge_index.service import digest, snapshot_document


def test_kb_model_overrides_only_its_own_snapshot(foundation):
    with foundation.sessions() as db:
        global_model = db.get(ModelConfig, "model")
        global_model.provider = "deepseek"
        kb = db.get(KnowledgeBase, "kb")
        profile = RetrievalProfile(build_indexes=["entities", "relations"], index_model={"model_name": "qwen2.5:3b"}).model_dump()
        kb.retrieval_profile = profile
        db.commit()
        first = snapshot_document(db, db.get(Document, "doc"))
        assert first["model"]["provider"] == "ollama"
        assert first["model"]["model_name"] == "qwen2.5:3b"
        assert db.get(ModelConfig, "model").provider == "deepseek"
        kb.retrieval_profile = {**profile, "index_model": {**profile["index_model"], "model_name": "other-local"}}
        db.commit()
        assert digest(snapshot_document(db, db.get(Document, "doc"))) != digest(first)


def test_existing_kb_still_uses_global_model(foundation):
    with foundation.sessions() as db:
        result = snapshot_document(db, db.get(Document, "doc"))
        assert result["model"]["model_name"] == "local-test"


@pytest.mark.asyncio
async def test_claim_generation_sends_required_capability_schema(monkeypatch):
    from app.services.knowledge_index import claims

    async def generate(content, config, prompt):
        schema = config["_json_schema"]
        assert schema["required"] == ["entities", "relations"]
        assert "organization" in schema["$defs"]["EntityCandidate"]["properties"]["entity_type"]["enum"]
        return {"entities": [], "relations": []}

    monkeypatch.setattr(claims, "generate_json", generate)
    assert await claims.extract_claims("资料", {"_capabilities": ["relations"]}) == {"entities": [], "relations": []}


def test_ollama_schema_keeps_required_types_and_nonempty_quotes():
    from app.services.knowledge_index.extractor import _ollama_schema
    schema = {"type": "string", "minLength": 1, "maxLength": 10000}
    assert _ollama_schema(schema) == {"type": "string", "minLength": 1}


@pytest.mark.asyncio
async def test_named_relation_uses_existing_entities_and_keeps_grounding(monkeypatch):
    from app.services.knowledge_index import claims
    text = "甲校开设计算机专业。"

    async def generate(content, config, prompt):
        return {"entities": [
            {"name": name, "mention": name, "entity_type": kind, "scope": "", "quote": text}
            for name, kind in (("甲校", "organization"), ("计算机专业", "concept"))],
            "relations": [{"subject": "甲校", "object": "计算机专业", "predicate": "开设", "quote": text}]}

    monkeypatch.setattr(claims, "generate_json", generate)
    raw = await claims.extract_claims(text, {"reference_mode": "names", "_capabilities": ["relations"]})
    grounded = claims.ground_claims(text, raw, ["relations"])
    assert grounded["relations"][0]["subject"] == 0 and grounded["relations"][0]["object"] == 1


@pytest.mark.asyncio
async def test_named_relation_rejects_unknown_endpoints(monkeypatch):
    from app.services.knowledge_index import claims

    async def generate(*args):
        return {"entities": [], "relations": [{"subject": "未知", "object": "其他", "predicate": "关系", "quote": "未知关系"}]}

    monkeypatch.setattr(claims, "generate_json", generate)
    with pytest.raises(ValueError, match="未提供"):
        await claims.extract_claims("资料", {"reference_mode": "names", "_capabilities": ["relations"]})


@pytest.mark.asyncio
async def test_named_quotes_are_restored_from_actual_source_ids(monkeypatch):
    from app.services.knowledge_index import claims
    text = "甲校开设计算机专业。"

    async def generate(content, config, prompt):
        assert '"quote_id": 0' in content
        return {"entities": [{"name": name, "entity_type": "concept", "quote_id": 0}
                              for name in ("甲校", "计算机专业")],
                "relations": [{"subject": "甲校", "object": "计算机专业", "predicate": "开设", "quote_id": 0}]}

    monkeypatch.setattr(claims, "generate_json", generate)
    raw = await claims.extract_claims(text, {"reference_mode": "names", "_capabilities": ["relations"]})
    result = claims.ground_claims(text, raw, ["relations"])
    assert result["relations"][0]["quote"] == text
    assert raw["entities"][0]["mention"] == "甲校"
