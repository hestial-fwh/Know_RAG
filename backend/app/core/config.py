from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "know_rag"
    app_version: str = "0.1.0"
    environment: str = "development"

    database_url: str = Field(
        "postgresql+psycopg://know_rag:know_rag@postgres:5432/know_rag",
        alias="DATABASE_URL",
    )
    redis_url: str = Field("redis://redis:6379/0", alias="REDIS_URL")
    ollama_base_url: str = Field(
        "http://host.docker.internal:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_llm_model: str = Field("qwen2.5:3b", alias="OLLAMA_LLM_MODEL")
    ollama_embedding_model: str = Field("bge-m3", alias="OLLAMA_EMBEDDING_MODEL")
    ollama_embedding_dimension: int = Field(1024, alias="OLLAMA_EMBEDDING_DIMENSION")
    auto_embed_after_parse: bool = Field(False, alias="AUTO_EMBED_AFTER_PARSE")
    hybrid_vector_weight: float = Field(0.7, alias="HYBRID_VECTOR_WEIGHT")
    hybrid_keyword_weight: float = Field(0.3, alias="HYBRID_KEYWORD_WEIGHT")
    hybrid_fusion_strategy: str = Field("rrf", alias="HYBRID_FUSION_STRATEGY")
    rrf_k: int = Field(60, alias="RRF_K")
    rrf_vector_weight: float = Field(1.0, alias="RRF_VECTOR_WEIGHT")
    rrf_keyword_weight: float = Field(1.0, alias="RRF_KEYWORD_WEIGHT")
    embedding_content_max_chars: int = Field(6000, alias="EMBEDDING_CONTENT_MAX_CHARS")
    retrieval_parent_context_max_chars: int = Field(
        2000, alias="RETRIEVAL_PARENT_CONTEXT_MAX_CHARS"
    )
    retrieval_neighbor_context_max_chars: int = Field(
        300, alias="RETRIEVAL_NEIGHBOR_CONTEXT_MAX_CHARS"
    )
    retrieval_parent_children_limit: int = Field(
        30, alias="RETRIEVAL_PARENT_CHILDREN_LIMIT"
    )
    retrieval_max_parent_contexts: int = Field(5, alias="RETRIEVAL_MAX_PARENT_CONTEXTS")
    retrieval_merge_hits_by_parent: bool = Field(
        True, alias="RETRIEVAL_MERGE_HITS_BY_PARENT"
    )
    retrieval_parent_group_hit_bonus: float = Field(
        0.05, alias="RETRIEVAL_PARENT_GROUP_HIT_BONUS"
    )
    retrieval_parent_context_mode: str = Field(
        "standard", alias="RETRIEVAL_PARENT_CONTEXT_MODE"
    )
    llm_context_max_chars: int = Field(8000, alias="LLM_CONTEXT_MAX_CHARS")
    llm_context_max_sources: int = Field(8, alias="LLM_CONTEXT_MAX_SOURCES")
    llm_context_include_images: bool = Field(True, alias="LLM_CONTEXT_INCLUDE_IMAGES")
    llm_context_include_image_urls: bool = Field(
        False, alias="LLM_CONTEXT_INCLUDE_IMAGE_URLS"
    )

    default_embedding_top_k: int = Field(10, alias="DEFAULT_EMBEDDING_TOP_K")
    default_score_threshold: float = Field(0.3, alias="DEFAULT_SCORE_THRESHOLD")
    default_chunk_size: int = Field(512, alias="DEFAULT_CHUNK_SIZE")
    default_chunk_overlap: int = Field(80, alias="DEFAULT_CHUNK_OVERLAP")
    chunk_strategy: str = Field("auto", alias="CHUNK_STRATEGY")
    chunk_separators: str = Field(
        "\\n\\n|\\n|。|.|；|;|，|,| ", alias="CHUNK_SEPARATORS"
    )
    enable_parent_child_chunking: bool = Field(
        False, alias="ENABLE_PARENT_CHILD_CHUNKING"
    )
    parent_chunk_size: int = Field(4096, alias="PARENT_CHUNK_SIZE")
    child_chunk_size: int = Field(384, alias="CHILD_CHUNK_SIZE")
    child_chunk_overlap: int = Field(64, alias="CHILD_CHUNK_OVERLAP")
    document_relation_graph_enabled: bool = Field(
        False,
        alias="DOCUMENT_RELATION_GRAPH_ENABLED",
    )
    document_relation_materialization_enabled: bool = Field(
        False,
        alias="DOCUMENT_RELATION_MATERIALIZATION_ENABLED",
    )
    document_relation_retrieval_expansion_enabled: bool = Field(
        False,
        alias="DOCUMENT_RELATION_RETRIEVAL_EXPANSION_ENABLED",
    )
    document_asset_group_enabled: bool = Field(
        False,
        alias="DOCUMENT_ASSET_GROUP_ENABLED",
    )
    document_page_visual_preservation_enabled: bool = Field(
        False,
        alias="DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED",
    )
    document_page_visual_min_coverage: float = Field(
        0.70,
        alias="DOCUMENT_PAGE_VISUAL_MIN_COVERAGE",
    )
    document_page_visual_render_dpi: int = Field(
        144,
        alias="DOCUMENT_PAGE_VISUAL_RENDER_DPI",
    )
    local_storage_dir: str = Field("/data/files", alias="LOCAL_STORAGE_DIR")
    local_cache_dir: str = Field("/data/cache", alias="LOCAL_CACHE_DIR")

    # --------------配置临时文件缓存和最终解析结果的存储位置，当前项目设计为：临时文件缓存使用本地目录，最终解析结果使用对象存储。----------------
    storage_backend: str = Field("minio", alias="STORAGE_BACKEND")

    mineru_output_dir: str = Field("", alias="MINERU_OUTPUT_DIR")
    mineru_send_output_dir: bool = Field(True, alias="MINERU_SEND_OUTPUT_DIR")
    # Optional compatibility path for old Windows-local MinerU output.  In the
    # dockerized MinerU deployment this should normally be empty/unused.
    mineru_legacy_output_dir: str = Field("", alias="MINERU_LEGACY_OUTPUT_DIR")
    cleanup_parse_cache_after_parse: bool = Field(
        True, alias="CLEANUP_PARSE_CACHE_AFTER_PARSE"
    )
    cleanup_mineru_legacy_output_after_parse: bool = Field(
        True, alias="CLEANUP_MINERU_LEGACY_OUTPUT_AFTER_PARSE"
    )
    # ── Object storage ─────────────────────────────────────────────────
    # 当前项目直接使用 MinIO 作为正式文件存储；本地目录仅作为解析缓存。
    storage_backend: str = Field("minio", alias="STORAGE_BACKEND")
    minio_endpoint: str = Field("minio:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field("minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field("minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field("know-rag", alias="MINIO_BUCKET")
    minio_secure: bool = Field(False, alias="MINIO_SECURE")
    pdf_parser_preference: str = Field("auto", alias="PDF_PARSER_PREFERENCE")
    # ── MinerU service/runtime selection ─────────────────────────────────
    # ``mineru_runtime_profile`` is used when a document does not carry an
    # explicit per-upload parse option.  CPU mode must use the pipeline backend;
    # GPU mode may use vlm-auto-engine / hybrid-auto-engine depending on the
    # deployment.
    mineru_runtime_profile: str = Field("cpu", alias="MINERU_RUNTIME_PROFILE")
    mineru_cpu_base_url: str = Field(
        "http://mineru-cpu:8000", alias="MINERU_CPU_BASE_URL"
    )
    mineru_gpu_base_url: str = Field(
        "http://mineru-gpu:8000", alias="MINERU_GPU_BASE_URL"
    )
    mineru_cpu_backend: str = Field("pipeline", alias="MINERU_CPU_BACKEND")
    mineru_gpu_backend: str = Field("vlm-auto-engine", alias="MINERU_GPU_BACKEND")
    mineru_cpu_parse_method: str = Field("auto", alias="MINERU_CPU_PARSE_METHOD")
    mineru_gpu_parse_method: str = Field("auto", alias="MINERU_GPU_PARSE_METHOD")
    mineru_base_url: str = Field("", alias="MINERU_BASE_URL")
    mineru_api_key: str = Field("", alias="MINERU_API_KEY")

    # ── Document parser engine ──────────────────────────────────────────
    # Options: auto | deepdoc | ragflow | mineru
    # auto: select by file type; mineru-supported formats prefer mineru,
    #       fallback to deepdoc/ragflow on failure.
    # mineru: mineru-supported formats prefer mineru, fallback to deepdoc.
    # deepdoc / ragflow: keep original parse chain.
    document_parser_engine: str = Field("auto", alias="DOCUMENT_PARSER_ENGINE")
    # Comma-separated priority list: mineru,ragflow,legacy
    parser_priority: str = Field("mineru,ragflow,legacy", alias="PARSER_PRIORITY")
    # MinerU parse endpoint path (appended to MINERU_BASE_URL)
    mineru_parse_endpoint: str = Field("/file_parse", alias="MINERU_PARSE_ENDPOINT")
    # Comma-separated extensions that MinerU can handle
    mineru_supported_extensions: str = Field(
        "pdf,png,jpg,jpeg,webp,doc,docx,ppt,pptx,xls,xlsx,html,txt,md,json,epub",
        alias="MINERU_SUPPORTED_EXTENSIONS",
    )
    mineru_backend: str = Field("pipeline", alias="MINERU_BACKEND")
    mineru_parse_method: str = Field("auto", alias="MINERU_PARSE_METHOD")
    mineru_lang: str = Field("ch", alias="MINERU_LANG")
    mineru_enable_formula: bool = Field(True, alias="MINERU_ENABLE_FORMULA")
    mineru_enable_table: bool = Field(True, alias="MINERU_ENABLE_TABLE")
    mineru_timeout_seconds: int = Field(300, alias="MINERU_TIMEOUT_SECONDS")
    mineru_fallback_to_deepdoc: bool = Field(True, alias="MINERU_FALLBACK_TO_DEEPDOC")

    # ── Elasticsearch BM25 keyword retrieval ─────────────────────────────
    keyword_search_backend: str = Field("elasticsearch", alias="KEYWORD_SEARCH_BACKEND")
    elasticsearch_url: str = Field(
        "http://elasticsearch:9200", alias="ELASTICSEARCH_URL"
    )
    elasticsearch_index: str = Field("know_rag_chunks", alias="ELASTICSEARCH_INDEX")
    elasticsearch_timeout_seconds: int = Field(
        10, alias="ELASTICSEARCH_TIMEOUT_SECONDS"
    )
    elasticsearch_query_max_chars: int = Field(
        256, alias="ELASTICSEARCH_QUERY_MAX_CHARS"
    )
    elasticsearch_query_max_terms: int = Field(
        24, alias="ELASTICSEARCH_QUERY_MAX_TERMS"
    )
    elasticsearch_ngram_query_max_chars: int = Field(
        64, alias="ELASTICSEARCH_NGRAM_QUERY_MAX_CHARS"
    )
    elasticsearch_safe_query_max_chars: int = Field(
        96, alias="ELASTICSEARCH_SAFE_QUERY_MAX_CHARS"
    )
    elasticsearch_fallback_to_sql: bool = Field(
        False, alias="ELASTICSEARCH_FALLBACK_TO_SQL"
    )

    admin_username: str = Field("admin", alias="ADMIN_USERNAME")
    admin_password: str = Field("change-me", alias="ADMIN_PASSWORD")
    jwt_secret: str = Field("dev-only-change-me", alias="JWT_SECRET")
    know_rag_service_token: str = Field("", alias="KNOW_RAG_SERVICE_TOKEN")

    # ── Login captcha ────────────────────────────────────────────────────
    login_captcha_enabled: bool = Field(True, alias="LOGIN_CAPTCHA_ENABLED")
    login_captcha_ttl_seconds: int = Field(300, alias="LOGIN_CAPTCHA_TTL_SECONDS")
    cors_origins: list[str] = ["http://localhost:5174", "http://127.0.0.1:5174"]

    # ── Frontend ──────────────────────────────────────────────────────────
    frontend_base_url: str = Field(
        "http://localhost:5174",
        alias="FRONTEND_BASE_URL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
