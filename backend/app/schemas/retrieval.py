from pydantic import BaseModel


class RetrievalConfig(BaseModel):
    embedding_top_k: int = 5
    score_threshold: float = 0.3
    use_vector: bool = True
    use_keyword: bool = True
    rerank_model_id: str = ""
    fusion_strategy: str = "rrf"
    rrf_k: int = 60
    rrf_vector_weight: float = 1.0
    rrf_keyword_weight: float = 1.0
    include_context: bool = True


class HybridSearchRequest(BaseModel):
    query: str | None = None
    query_text: str | None = None
    top_k: int | None = None
    match_count: int | None = None
    score_threshold: float | None = None
    vector_threshold: float | None = None
    keyword_threshold: float | None = None
    use_vector: bool | None = None
    use_keyword: bool | None = None
    disable_vector_match: bool | None = None
    disable_keywords_match: bool | None = None
    include_content: bool = True
    include_context: bool | None = None
    include_parent_context: bool | None = None
    parent_context_mode: str | None = None
    parent_context_max_chars: int | None = None
    neighbor_context_max_chars: int | None = None
    parent_children_limit: int | None = None
    max_parent_contexts: int | None = None
    merge_hits_by_parent: bool | None = None
    fusion_strategy: str | None = None
    rrf_k: int | None = None
    rrf_vector_weight: float | None = None
    rrf_keyword_weight: float | None = None


class LLMContextRequest(HybridSearchRequest):
    max_context_chars: int | None = None
    max_sources: int | None = None
    include_images: bool | None = True
    include_image_urls: bool | None = False
    include_tables: bool | None = True
    include_equations: bool | None = True
