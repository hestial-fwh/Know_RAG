import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import (
    routes_auth,
    routes_chat,
    routes_chunker_config,
    routes_chunks,
    routes_documents,
    routes_faq,
    routes_files,
    routes_initialization,
    routes_knowledge_base,
    routes_mcp_placeholder,
    routes_parse_tasks,
    routes_model_config,
    routes_retrieval,
    routes_vector_store,
)
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.response import error_response, success_response
from app.plugins.parse_eval.router import router as parse_eval_router


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Local knowledge-base building and maintenance foundation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=error_response("request validation failed", exc.errors()),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=error_response(f"internal server error: {exc}"),
    )




@app.on_event("startup")
async def startup_initialize_services():
    """Initialize optional infrastructure that should exist before requests."""
    try:
        from app.services.keyword_index_service import ensure_index, keyword_search_enabled

        if keyword_search_enabled():
            ensure_index()
            logger.info("Elasticsearch keyword index initialized")
    except Exception as exc:
        logger.warning("Elasticsearch keyword index initialization skipped: %s", exc)

    try:
        from app.db.session import SessionLocal
        from app.services.vector_store_adapters import (
            _milvus_client,
            _milvus_ensure_collection,
            get_active_vector_config,
            is_external_provider,
        )

        db = SessionLocal()
        try:
            config = get_active_vector_config(db)
            if is_external_provider(config) and (
                (config.provider or "").lower() in {"milvus", "zilliz"}
            ):
                from app.db.models import ModelConfig

                row = (
                    db.query(ModelConfig)
                    .filter(ModelConfig.model_type == "embedding")
                    .first()
                )
                dim = row.dimension if row and row.dimension else 1024
                client = _milvus_client(config)
                _milvus_ensure_collection(client, config, dim)
                logger.info("Milvus collection initialized")
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "Milvus collection initialization skipped: %s", exc
        )

@app.get("/health")
async def health():
    return success_response(
        {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.app_version,
        }
    )


API_PREFIX = "/api/v1"
app.include_router(routes_auth.router, prefix=API_PREFIX)
app.include_router(routes_knowledge_base.router, prefix=API_PREFIX)
app.include_router(routes_documents.router, prefix=API_PREFIX)
app.include_router(routes_chunks.router, prefix=API_PREFIX)
app.include_router(routes_faq.router, prefix=API_PREFIX)
app.include_router(routes_files.router)
app.include_router(routes_model_config.router, prefix=API_PREFIX)
app.include_router(routes_chunker_config.router, prefix=API_PREFIX)
app.include_router(routes_vector_store.router, prefix=API_PREFIX)
app.include_router(routes_retrieval.router, prefix=API_PREFIX)
app.include_router(routes_initialization.router, prefix=API_PREFIX)
app.include_router(routes_chat.router, prefix=API_PREFIX)
app.include_router(routes_mcp_placeholder.router, prefix=API_PREFIX)
app.include_router(routes_parse_tasks.router, prefix=API_PREFIX)
app.include_router(parse_eval_router, prefix=API_PREFIX)
