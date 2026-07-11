from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.retrieval import HybridSearchRequest, LLMContextRequest, RetrievalConfig
from app.services import retrieval_service as service


router = APIRouter(tags=["retrieval"])


@router.get("/retrieval-config")
async def get_retrieval_config(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.get_retrieval_config(db)


@router.put("/retrieval-config")
async def update_retrieval_config(
    payload: RetrievalConfig,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_retrieval_config(db, payload.model_dump(exclude_none=True))


@router.get("/knowledge-bases/{kb_id}/hybrid-search")
async def hybrid_search_get(
    kb_id: str,
    query: str | None = None,
    query_text: str | None = None,
    top_k: int | None = None,
    match_count: int | None = None,
    score_threshold: float | None = None,
    vector_threshold: float | None = None,
    keyword_threshold: float | None = None,
    use_vector: bool | None = None,
    use_keyword: bool | None = None,
    disable_vector_match: bool | None = None,
    disable_keywords_match: bool | None = None,
    include_content: bool = True,
    include_context: bool | None = None,
    include_parent_context: bool | None = None,
    parent_context_mode: str | None = None,
    parent_context_max_chars: int | None = None,
    neighbor_context_max_chars: int | None = None,
    parent_children_limit: int | None = None,
    max_parent_contexts: int | None = None,
    merge_hits_by_parent: bool | None = None,
    fusion_strategy: str | None = None,
    rrf_k: int | None = None,
    rrf_vector_weight: float | None = None,
    rrf_keyword_weight: float | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.hybrid_search(
        db,
        current_user,
        kb_id,
        {
            "query": query,
            "query_text": query_text,
            "top_k": top_k,
            "match_count": match_count,
            "score_threshold": score_threshold,
            "vector_threshold": vector_threshold,
            "keyword_threshold": keyword_threshold,
            "use_vector": use_vector,
            "use_keyword": use_keyword,
            "disable_vector_match": disable_vector_match,
            "disable_keywords_match": disable_keywords_match,
            "include_content": include_content,
            "include_context": include_context,
            "include_parent_context": include_parent_context,
            "parent_context_mode": parent_context_mode,
            "parent_context_max_chars": parent_context_max_chars,
            "neighbor_context_max_chars": neighbor_context_max_chars,
            "parent_children_limit": parent_children_limit,
            "max_parent_contexts": max_parent_contexts,
            "merge_hits_by_parent": merge_hits_by_parent,
            "fusion_strategy": fusion_strategy,
            "rrf_k": rrf_k,
            "rrf_vector_weight": rrf_vector_weight,
            "rrf_keyword_weight": rrf_keyword_weight,
            "method": "GET",
        },
    )


@router.post("/knowledge-bases/{kb_id}/hybrid-search")
async def hybrid_search_post(
    kb_id: str,
    payload: HybridSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.hybrid_search(db, current_user, kb_id, payload.model_dump(exclude_none=True))


@router.post("/knowledge-bases/{kb_id}/llm-context")
async def llm_context_post(
    kb_id: str,
    payload: LLMContextRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.build_llm_context(db, current_user, kb_id, payload.model_dump(exclude_none=True))
