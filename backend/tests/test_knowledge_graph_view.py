"""浏览图谱复用真实派生证据，验证来源、边界、失效与HTTP契约。"""

import pytest
from sqlalchemy import select

from test_association_retrieval import indexed, foundation
from app.db.models import Chunk, KnowledgeBase, KnowledgeEntity, DocumentCanonicalArtifact
from app.services.knowledge_index.graph_view import knowledge_graph


@pytest.mark.asyncio
async def test_graph_contains_grounded_entities_relations_and_events(indexed):
    with indexed.sessions() as db:
        graph = knowledge_graph(db, "kb")
        assert graph["meta"]["state"] == "ready"
        assert len([node for node in graph["nodes"] if node["kind"] == "event"]) == 3
        assert len([edge for edge in graph["edges"] if edge["kind"] == "relation"]) == 3
        ids = {node["id"] for node in graph["nodes"]}
        assert all({edge["source"], edge["target"]} <= ids for edge in graph["edges"])
        assert all(item["evidence"][0]["quote"] for item in graph["nodes"] + graph["edges"])
        assert all(item["derived"] for item in graph["nodes"] + graph["edges"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "revision", "profile"])
async def test_graph_drops_invalid_sources_immediately(indexed, change):
    with indexed.sessions() as db:
        if change == "disable":
            db.get(Chunk, "second-chunk").is_enabled = False
        elif change == "revision":
            db.get(DocumentCanonicalArtifact, "second").parse_revision = "changed"
        else:
            db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": []}
        db.commit()
        graph = knowledge_graph(db, "kb")
        assert all(proof["document_id"] != "second" for item in graph["nodes"] + graph["edges"] for proof in item["evidence"])
        if change == "profile":
            assert not graph["nodes"] and graph["meta"]["state"] == "disabled"


@pytest.mark.asyncio
async def test_search_focus_and_limits_are_kb_scoped(indexed):
    with indexed.sessions() as db:
        db.add(KnowledgeBase(id="private", name="另一库"))
        db.commit()
        entity = db.scalar(select(KnowledgeEntity).where(KnowledgeEntity.canonical_name == "人工智能专业"))
        graph = knowledge_graph(db, "kb", focus=entity.id)
        assert any(node["label"] == "甲校" for node in graph["nodes"])
        assert all(node["label"] != "丙公司" for node in graph["nodes"])
        assert not knowledge_graph(db, "private", focus=entity.id)["nodes"]
        assert not knowledge_graph(db, "kb", query="不存在实体")["nodes"]
        assert not knowledge_graph(db, "kb", query="%")["nodes"]
        small = knowledge_graph(db, "kb", limit=2)
        assert len(small["nodes"]) <= 2 and small["meta"]["truncated"]
        assert all(edge["source"] in {node["id"] for node in small["nodes"]} and edge["target"] in {node["id"] for node in small["nodes"]} for edge in small["edges"])


@pytest.mark.asyncio
async def test_entity_only_index_displays_grounded_isolated_nodes(indexed):
    from app.services.knowledge_index import service, worker

    with indexed.sessions() as db:
        db.get(KnowledgeBase, "kb").retrieval_profile = {"build_indexes": ["entities"]}
        service.reconcile(db)
        db.commit()

    async def entities(content, config):
        name = "甲校" if "甲校" in content else "丙公司"
        return {"entities": [{"name": name, "mention": name, "entity_type": "organization", "scope": "", "quote": content}]}

    for _ in range(3):
        assert await worker.run_once(indexed.sessions, entities)
    with indexed.sessions() as db:
        graph = knowledge_graph(db, "kb")
        assert graph["nodes"] and not graph["edges"]
        assert all(node["kind"] == "entity" for node in graph["nodes"])


@pytest.mark.asyncio
async def test_graph_http_auth_and_parameter_bounds(indexed):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes_knowledge_index import router
    from app.core.security import get_current_user
    from app.db.session import get_db

    app = FastAPI()
    app.include_router(router)

    def database():
        with indexed.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        assert client.get('/knowledge-bases/kb/knowledge-index/graph').status_code == 401
        app.dependency_overrides[get_current_user] = lambda: None
        result = client.get('/knowledge-bases/kb/knowledge-index/graph', params={'q': '甲校', 'limit': 40}).json()
        assert result['success'] and result['data']['nodes']
        assert client.get('/knowledge-bases/kb/knowledge-index/graph?limit=151').status_code == 422
        assert not client.get('/knowledge-bases/missing/knowledge-index/graph').json()['success']
