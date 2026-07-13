"""Elasticsearch BM25 keyword index service for Know_RAG.

This module replaces the old SQL ILIKE keyword recall path with a real
Elasticsearch inverted index / BM25 retrieval path.  The service is intentionally
best-effort for indexing so document parsing is not blocked by a temporary ES
outage, while retrieval fails loudly when keyword search is requested and ES is
unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Chunk, Document
from app.utils.text import sanitize_text_for_postgres
from app.services.content_cleaning import clean_text_for_embedding, should_skip_text_chunk

logger = logging.getLogger(__name__)

INDEXABLE_CHUNK_TYPES = {"text", "table", "image"}
NON_INDEXABLE_CHUNK_TYPES = {"parent_text"}


def _is_indexable_chunk(chunk: Chunk | None) -> bool:
    if chunk is None:
        return False
    chunk_type = (chunk.chunk_type or "text").lower()
    if chunk_type not in INDEXABLE_CHUNK_TYPES:
        return False
    if should_skip_text_chunk(
        chunk.content or "",
        chunk_type=chunk_type,
        flags=chunk.flags or {},
        metadata=chunk.metadata_json or {},
    ):
        return False
    return True


@dataclass
class KeywordHit:
    chunk_id: str
    score: float
    raw_score: float
    rank: int
    highlight: str = ""


def keyword_search_enabled() -> bool:
    return (getattr(settings, "keyword_search_backend", "elasticsearch") or "").lower() in {
        "elasticsearch",
        "es",
    }


def _base_url() -> str:
    return (getattr(settings, "elasticsearch_url", "http://elasticsearch:9200") or "").rstrip("/")


def _index_name() -> str:
    return (getattr(settings, "elasticsearch_index", "know_rag_chunks") or "know_rag_chunks").strip()


def _timeout() -> float:
    try:
        return float(getattr(settings, "elasticsearch_timeout_seconds", 10) or 10)
    except Exception:
        return 10.0


def _request(method: str, path: str, *, json_body: Any | None = None, params: dict | None = None) -> httpx.Response:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=_timeout()) as client:
        response = client.request(method, url, json=json_body, params=params)
    return response


def elasticsearch_available() -> bool:
    if not keyword_search_enabled():
        return False
    try:
        response = _request("GET", "/")
        return 200 <= response.status_code < 300
    except Exception:
        return False


def ensure_index() -> None:
    """Create the ES index if it does not exist.

    The mapping uses real ES text fields and BM25 scoring.  For Chinese text we
    use a built-in ngram analyzer so the project does not depend on an external
    IK plugin.  If a deployment later installs IK, the analyzer can be changed
    via index recreation without changing the application retrieval flow.
    """
    if not keyword_search_enabled():
        return

    index = _index_name()
    response = _request("HEAD", f"/{index}")
    if response.status_code == 200:
        return
    if response.status_code not in {404}:
        raise RuntimeError(f"Elasticsearch index check failed: status={response.status_code}, body={response.text[:300]}")

    body = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "tokenizer": {
                    "knowrag_ngram_tokenizer": {
                        "type": "ngram",
                        "min_gram": 2,
                        "max_gram": 3,
                        "token_chars": ["letter", "digit"],
                    }
                },
                "analyzer": {
                    "knowrag_text": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase"],
                    },
                    "knowrag_ngram": {
                        "type": "custom",
                        "tokenizer": "knowrag_ngram_tokenizer",
                        "filter": ["lowercase"],
                    },
                },
            },
        },
        "mappings": {
            "dynamic": False,
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "knowledge_base_id": {"type": "keyword"},
                "knowledge_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "chunk_type": {"type": "keyword"},
                "parent_chunk_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "is_enabled": {"type": "boolean"},
                "page_no": {"type": "integer"},
                "title": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "file_name": {"type": "text", "analyzer": "knowrag_text", "fields": {"keyword": {"type": "keyword"}, "ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "context_header": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "content": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "keywords": {"type": "text", "analyzer": "knowrag_text", "fields": {"keyword": {"type": "keyword"}, "ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "ocr_text": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "vlm_description": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
                "table_text": {"type": "text", "analyzer": "knowrag_text", "fields": {"ngram": {"type": "text", "analyzer": "knowrag_ngram"}}},
            },
        },
    }
    response = _request("PUT", f"/{index}", json_body=body)
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Elasticsearch index create failed: status={response.status_code}, body={response.text[:500]}")
    logger.info("Elasticsearch keyword index created: %s", index)


def _walk_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield sanitize_text_for_postgres(value.strip())
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_strings(item)
        return


def _first_text_deep(mapping: dict | None, keys: list[str]) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_postgres(value.strip())
    for value in mapping.values():
        if isinstance(value, dict):
            found = _first_text_deep(value, keys)
            if found:
                return found
    return ""


def _metadata_text(metadata: dict | None, keys: list[str]) -> str:
    value = _first_text_deep(metadata or {}, keys)
    return value[:4000]


def _page_no(metadata: dict | None) -> int | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("page_no", "source_page", "page", "page_idx"):
        value = metadata.get(key)
        try:
            if value is None or value == "":
                continue
            page = int(value)
            if key == "page_idx" and page >= 0:
                page += 1
            return page
        except Exception:
            continue
    return None


_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}|\d+(?:\.\d+)?")
_STOPWORDS = {
    "一个",
    "一种",
    "以及",
    "因此",
    "所以",
    "这个",
    "这些",
    "进行",
    "通过",
    "可以",
    "由于",
    "其中",
    "图像",
    "图片",
    "表格",
    "文件",
}


def extract_basic_keywords(*texts: str, limit: int = 20) -> list[str]:
    """Cheap deterministic keyword candidates used as an ES boost field.

    This is not the BM25 core.  BM25 is still computed over the indexed text
    fields.  These terms only provide an additional boosted field and are safe
    to replace later with LLM/TF-IDF/domain dictionary keywords.
    """
    scores: dict[str, int] = {}
    for text in texts:
        for term in _TERM_RE.findall(text or ""):
            term = term.strip().lower()
            if len(term) < 2 or term in _STOPWORDS:
                continue
            scores[term] = scores.get(term, 0) + 1
    return [term for term, _ in sorted(scores.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[:limit]]


def chunk_to_es_doc(chunk: Chunk, document: Document) -> dict:
    metadata = chunk.metadata_json or {}
    image_info = chunk.image_info or {}
    ocr_text = clean_text_for_embedding(_metadata_text(metadata, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]))
    vlm_description = clean_text_for_embedding(_metadata_text(metadata, ["vlm_description", "description", "image_description"]))
    table_text = clean_text_for_embedding(_metadata_text(metadata, ["table_body", "table_html", "table_text"]))
    caption = clean_text_for_embedding(_metadata_text(metadata, ["caption", "figure_caption", "image_caption", "table_caption", "title"]))
    index_content = clean_text_for_embedding("\n".join(part for part in [chunk.content, caption] if part).strip())
    keywords = extract_basic_keywords(
        document.title or "",
        document.file_name or "",
        chunk.context_header or "",
        index_content,
        ocr_text,
        vlm_description,
        table_text,
    )
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "knowledge_id": chunk.knowledge_id or document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "chunk_index": int(chunk.chunk_index or 0),
        "chunk_type": chunk.chunk_type or "text",
        "parent_chunk_id": chunk.parent_chunk_id or "",
        "status": chunk.status or "ready",
        "is_enabled": bool(chunk.is_enabled),
        "page_no": _page_no(metadata),
        "title": clean_text_for_embedding(document.title or ""),
        "file_name": sanitize_text_for_postgres(document.file_name or ""),
        "context_header": clean_text_for_embedding(chunk.context_header or ""),
        "content": index_content,
        "keywords": keywords,
        "ocr_text": ocr_text,
        "vlm_description": vlm_description,
        "table_text": table_text,
    }


def index_document_chunks(
    db: Session,
    document: Document,
    chunks: Iterable[Chunk],
    *,
    strict: bool = False,
) -> int:
    """Index newly built chunks into Elasticsearch.

    Called after SQLAlchemy flush in rebuild_document_chunks.  Exceptions are
    logged and swallowed so parsing/chunking is not blocked by ES boot delay.
    """
    if not keyword_search_enabled():
        return 0
    chunks = [chunk for chunk in chunks if chunk and chunk.status == "ready" and chunk.is_enabled and _is_indexable_chunk(chunk)]
    if not chunks:
        return 0
    try:
        ensure_index()
        index = _index_name()
        lines: list[str] = []
        for chunk in chunks:
            lines.append(json.dumps({"index": {"_index": index, "_id": chunk.id}}, ensure_ascii=False))
            lines.append(json.dumps(chunk_to_es_doc(chunk, document), ensure_ascii=False))
        payload = "\n".join(lines) + "\n"
        url = f"{_base_url()}/_bulk"
        headers = {"Content-Type": "application/x-ndjson"}
        with httpx.Client(timeout=_timeout()) as client:
            response = client.post(url, content=payload.encode("utf-8"), headers=headers, params={"refresh": "true"})
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"bulk index failed: status={response.status_code}, body={response.text[:500]}")
        data = response.json()
        if data.get("errors"):
            failed = [item for item in data.get("items", []) if item.get("index", {}).get("error")]
            raise RuntimeError(
                f"Elasticsearch keyword index partial failures: count={len(failed)} sample={failed[:2]}"
            )
        else:
            logger.info("Elasticsearch keyword indexed chunks: document_id=%s count=%s", document.id, len(chunks))
        return len(chunks)
    except Exception as exc:
        if strict:
            raise
        logger.warning("Elasticsearch keyword indexing skipped: document_id=%s error=%s", document.id, exc)
        return 0


def delete_document_from_index(document_id: str, *, strict: bool = False) -> int:
    if not keyword_search_enabled():
        return 0
    try:
        ensure_index()
        body = {"query": {"term": {"document_id": document_id}}}
        response = _request("POST", f"/{_index_name()}/_delete_by_query", json_body=body, params={"conflicts": "proceed", "refresh": "true"})
        if response.status_code not in {200, 201}:
            logger.warning("Elasticsearch document delete failed: status=%s body=%s", response.status_code, response.text[:300])
            return 0
        return int((response.json() or {}).get("deleted") or 0)
    except Exception as exc:
        if strict:
            raise
        logger.warning("Elasticsearch document delete skipped: document_id=%s error=%s", document_id, exc)
        return 0


def sync_document_index(db: Session, document: Document) -> int:
    """Replace one document's keyword index from committed database rows.

    Callers must invoke this only after the SQL transaction that replaced the
    chunks has committed.  This prevents an unsuccessful DB rebuild from
    deleting the last known-good keyword index.
    """
    if not keyword_search_enabled():
        return 0
    chunks = (
        db.query(Chunk)
        .filter(
            Chunk.document_id == document.id,
            Chunk.is_enabled == True,  # noqa: E712
            Chunk.status == "ready",
        )
        .order_by(Chunk.chunk_index.asc())
        .all()
    )
    indexable_chunks = [chunk for chunk in chunks if _is_indexable_chunk(chunk)]
    # Index the committed replacement first.  If this fails, the old index is
    # untouched.  Only after every new chunk is accepted do we remove stale
    # chunk ids from the previous generation.
    indexed = index_document_chunks(db, document, indexable_chunks, strict=True)
    keep_ids = [chunk.id for chunk in indexable_chunks]
    if not keep_ids:
        delete_document_from_index(document.id, strict=True)
        return 0

    ensure_index()
    body = {
        "query": {
            "bool": {
                "filter": [{"term": {"document_id": document.id}}],
                "must_not": [{"ids": {"values": keep_ids}}],
            }
        }
    }
    response = _request(
        "POST",
        f"/{_index_name()}/_delete_by_query",
        json_body=body,
        params={"conflicts": "proceed", "refresh": "true"},
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"stale keyword chunk cleanup failed: status={response.status_code}, "
            f"body={response.text[:300]}"
        )
    return indexed


def delete_knowledge_base_from_index(knowledge_base_id: str) -> int:
    if not keyword_search_enabled():
        return 0
    try:
        ensure_index()
        body = {"query": {"term": {"knowledge_base_id": knowledge_base_id}}}
        response = _request("POST", f"/{_index_name()}/_delete_by_query", json_body=body, params={"conflicts": "proceed", "refresh": "true"})
        if response.status_code not in {200, 201}:
            logger.warning("Elasticsearch knowledge base delete failed: status=%s body=%s", response.status_code, response.text[:300])
            return 0
        return int((response.json() or {}).get("deleted") or 0)
    except Exception as exc:
        logger.warning("Elasticsearch knowledge base delete skipped: kb_id=%s error=%s", knowledge_base_id, exc)
        return 0


def search_bm25(knowledge_base_id: str, query: str, top_k: int) -> list[KeywordHit]:
    if not query.strip():
        return []
    ensure_index()
    size = max(int(top_k or 10), 1) * 5
    body = {
        "size": size,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"knowledge_base_id": knowledge_base_id}},
                    {"term": {"is_enabled": True}},
                    {"term": {"status": "ready"}},
                    {"terms": {"chunk_type": sorted(INDEXABLE_CHUNK_TYPES)}},
                ],
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "type": "best_fields",
                            "fields": [
                                "keywords^6",
                                "keywords.ngram^5",
                                "title^5",
                                "title.ngram^4",
                                "file_name^3",
                                "file_name.ngram^2",
                                "context_header^4",
                                "context_header.ngram^3",
                                "content^2",
                                "content.ngram^2",
                                "ocr_text^3",
                                "ocr_text.ngram^2",
                                "vlm_description^3",
                                "vlm_description.ngram^2",
                                "table_text^3",
                                "table_text.ngram^2",
                            ],
                            "operator": "or",
                        }
                    }
                ],
            }
        },
        "highlight": {
            "pre_tags": [""],
            "post_tags": [""],
            "fields": {
                "content": {"fragment_size": 180, "number_of_fragments": 2},
                "context_header": {"fragment_size": 120, "number_of_fragments": 1},
                "keywords": {"fragment_size": 120, "number_of_fragments": 1},
                "ocr_text": {"fragment_size": 180, "number_of_fragments": 2},
                "vlm_description": {"fragment_size": 180, "number_of_fragments": 2},
                "table_text": {"fragment_size": 180, "number_of_fragments": 2},
            },
        },
    }
    response = _request("POST", f"/{_index_name()}/_search", json_body=body)
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Elasticsearch BM25 search failed: status={response.status_code}, body={response.text[:500]}")
    data = response.json()
    hits = (data.get("hits") or {}).get("hits") or []
    max_score = float((data.get("hits") or {}).get("max_score") or 0.0) or 0.0
    results: list[KeywordHit] = []
    for rank, hit in enumerate(hits, start=1):
        source = hit.get("_source") or {}
        raw_score = float(hit.get("_score") or 0.0)
        normalized = raw_score / max_score if max_score > 0 else 0.0
        highlight = ""
        hl = hit.get("highlight") or {}
        for fragments in hl.values():
            if isinstance(fragments, list) and fragments:
                highlight = " ... ".join(str(x) for x in fragments[:2])
                break
        chunk_id = str(source.get("chunk_id") or hit.get("_id") or "")
        if chunk_id:
            results.append(KeywordHit(chunk_id=chunk_id, score=normalized, raw_score=raw_score, rank=rank, highlight=highlight))
    return results


def rebuild_index_for_knowledge_base(db: Session, knowledge_base_id: str) -> int:
    """Rebuild ES keyword index for one knowledge base from existing DB chunks."""
    delete_knowledge_base_from_index(knowledge_base_id)
    rows = (
        db.query(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .filter(Chunk.knowledge_base_id == knowledge_base_id, Chunk.is_enabled == True, Chunk.status == "ready", Chunk.chunk_type.in_(sorted(INDEXABLE_CHUNK_TYPES)))  # noqa: E712
        .order_by(Chunk.document_id.asc(), Chunk.chunk_index.asc())
        .all()
    )
    by_doc: dict[str, tuple[Document, list[Chunk]]] = {}
    for chunk, document in rows:
        if document.id not in by_doc:
            by_doc[document.id] = (document, [])
        by_doc[document.id][1].append(chunk)
    total = 0
    for document, chunks in by_doc.values():
        indexable_chunks = [chunk for chunk in chunks if _is_indexable_chunk(chunk)]
        index_document_chunks(db, document, indexable_chunks)
        total += len(indexable_chunks)
    return total
