import base64
from io import BytesIO
from types import SimpleNamespace

import httpx
from PIL import Image, ImageDraw

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import ModelConfig
from app.utils.ids import new_id
from app.services.document_parser.deepdoc_runtime import get_deepdoc_runtime, reload_deepdoc_runtime

PROVIDER_PRESETS = {
    "deepdoc-builtin": {
        "label": "DeepDoc 内置 OCR（默认，无需 API）",
        "default_base_url": "",
        "api_style": "builtin_ocr",
        "model_examples": {"ocr": "deepdoc-local-ocr"},
        "model_types": ["ocr"],
    },
    "ollama": {
        "label": "Ollama / 本地模型",
        "default_base_url": "http://host.docker.internal:11434",
        "api_style": "ollama",
        "model_examples": {"llm": "qwen2.5:3b", "embedding": "bge-m3", "image2text": "qwen2.5vl:7b", "ocr": "qwen2.5vl:7b", "rerank": ""},
    },
    "openai-compatible": {
        "label": "OpenAI-Compatible",
        "default_base_url": "https://api.example.com/v1",
        "api_style": "openai",
        "model_examples": {"llm": "gpt-4o-mini", "embedding": "text-embedding-3-small", "image2text": "gpt-4o-mini", "ocr": "gpt-4o-mini", "rerank": ""},
    },
    "openai": {
        "label": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "api_style": "openai",
        "model_examples": {"llm": "gpt-4o-mini", "embedding": "text-embedding-3-small", "image2text": "gpt-4o-mini", "ocr": "gpt-4o-mini"},
    },
    "deepseek": {
        "label": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/v1",
        "api_style": "openai",
        "model_examples": {"llm": "deepseek-chat", "embedding": "", "image2text": "", "ocr": ""},
    },
    "dashscope": {
        "label": "阿里云百炼 / DashScope",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_style": "openai",
        "model_examples": {"llm": "qwen-plus", "embedding": "text-embedding-v4", "image2text": "qwen-vl-plus", "ocr": "qwen-vl-plus", "rerank": "gte-rerank"},
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "api_style": "openai",
        "model_examples": {"llm": "Qwen/Qwen2.5-72B-Instruct", "embedding": "BAAI/bge-m3", "image2text": "Qwen/Qwen2.5-VL-72B-Instruct", "ocr": "Qwen/Qwen2.5-VL-72B-Instruct", "rerank": "BAAI/bge-reranker-v2-m3"},
    },
    "jina": {
        "label": "Jina AI",
        "default_base_url": "https://api.jina.ai/v1",
        "api_style": "jina",
        "model_examples": {"rerank": "jina-reranker-v2-base-multilingual"},
    },
    "zhipu": {
        "label": "智谱 AI",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_style": "openai",
        "model_examples": {"llm": "glm-4-flash", "embedding": "embedding-3", "image2text": "glm-4v-flash", "ocr": "glm-4v-flash"},
    },
    "hunyuan": {
        "label": "腾讯混元",
        "default_base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "api_style": "openai",
        "model_examples": {"llm": "hunyuan-lite", "embedding": "", "image2text": "hunyuan-vision", "ocr": "hunyuan-vision"},
    },
    "volcengine": {
        "label": "火山引擎 Ark",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_style": "openai",
        "model_examples": {"llm": "doubao-1-5-pro-32k", "embedding": "doubao-embedding-text", "image2text": "doubao-vision-pro", "ocr": "doubao-vision-pro"},
    },
    "xai": {
        "label": "xAI",
        "default_base_url": "https://api.x.ai/v1",
        "api_style": "openai",
        "model_examples": {"llm": "grok-3", "embedding": "", "image2text": "grok-2-vision", "ocr": "grok-2-vision"},
    },
    "stepfun": {
        "label": "阶跃星辰 StepFun",
        "default_base_url": "https://api.stepfun.com/v1",
        "api_style": "openai",
        "model_examples": {"llm": "step-2-mini", "embedding": "", "image2text": "step-1v-8k", "ocr": "step-1v-8k"},
    },
    "paddleocr": {
        "label": "PaddleOCR / 自定义 OCR 服务",
        "default_base_url": "http://host.docker.internal:8868/ocr",
        "api_style": "custom_ocr",
        "model_examples": {"ocr": "paddleocr"},
    },
    "mineru": {
        "label": "MinerU 文档解析引擎",
        "default_base_url": "http://mineru-cpu:8000",
        "api_style": "document_parser",
        "model_examples": {"ocr": "mineru"},
    },
    "custom": {
        "label": "自定义 API",
        "default_base_url": "",
        "api_style": "custom",
        "model_examples": {},
    },
}


def provider_presets():
    return success_response({"providers": [{"value": k, **v} for k, v in PROVIDER_PRESETS.items()]})


def model_to_dict(row: ModelConfig) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "model_type": row.model_type,
        "model_name": row.model_name,
        "base_url": row.base_url,
        "api_key": row.api_key,
        "dimension": row.dimension,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _upsert_model(db, *, model_type: str, provider: str, model_name: str, base_url: str, api_key: str = "", dimension: int | None = None):
    row = db.query(ModelConfig).filter(ModelConfig.model_type == model_type).first()
    if row is None:
        row = ModelConfig(id=new_id(), model_type=model_type)
        db.add(row)
    row.provider = provider or ""
    row.model_name = model_name or ""
    row.base_url = base_url or ""
    row.api_key = api_key or ""
    if dimension is not None:
        row.dimension = dimension
    return row


def _normalize_ocr_row(row: ModelConfig | None) -> bool:
    """Normalize legacy OCR config to RAGFlow-like DeepDoc default.

    Earlier builds stored OCR as provider="" while model_name was
    "deepdoc-local-ocr".  That made the UI show an empty select and made
    the API test treat DeepDoc builtin OCR as an external API.  DeepDoc OCR
    should be the default builtin path, so we normalize legacy/empty OCR rows
    here and whenever payloads are tested/saved.
    """
    if row is None or row.model_type != "ocr":
        return False
    provider = (row.provider or "").strip().lower()
    model = (row.model_name or "").strip().lower()
    base_url = (row.base_url or "").strip()
    if provider in {"", "disabled", "none", "off"} and (model in {"", "deepdoc-local-ocr"} or not base_url):
        row.provider = "deepdoc-builtin"
        row.model_name = "deepdoc-local-ocr"
        row.base_url = ""
        row.api_key = ""
        return True
    return False


def _normalize_ocr_cfg(cfg: dict) -> dict:
    provider = (cfg.get("provider") or "").strip()
    model = (cfg.get("model_name") or "").strip()
    base_url = (cfg.get("base_url") or "").strip()
    if provider.lower() in {"", "disabled", "none", "off"} and (model.lower() in {"", "deepdoc-local-ocr"} or not base_url):
        cfg = dict(cfg)
        cfg["provider"] = "deepdoc-builtin"
        cfg["model_name"] = "deepdoc-local-ocr"
        cfg["base_url"] = ""
        cfg["api_key"] = ""
    return cfg


def get_or_create_model_configs(db) -> list[ModelConfig]:
    rows = db.query(ModelConfig).order_by(ModelConfig.model_type.asc()).all()
    by_type = {row.model_type: row for row in rows}
    changed = False
    if "llm" not in by_type:
        _upsert_model(db, model_type="llm", provider="ollama", model_name=settings.ollama_llm_model, base_url=settings.ollama_base_url)
        changed = True
    if "embedding" not in by_type:
        _upsert_model(
            db,
            model_type="embedding",
            provider="ollama",
            model_name=settings.ollama_embedding_model,
            base_url=settings.ollama_base_url,
            dimension=settings.ollama_embedding_dimension,
        )
        changed = True
    if "ocr" not in by_type:
        _upsert_model(db, model_type="ocr", provider="deepdoc-builtin", model_name="deepdoc-local-ocr", base_url="", api_key="")
        changed = True
    else:
        changed = _normalize_ocr_row(by_type.get("ocr")) or changed
    if changed:
        db.commit()
    return db.query(ModelConfig).order_by(ModelConfig.model_type.asc()).all()


def get_model_config(db):
    rows = get_or_create_model_configs(db)
    by_type = {row.model_type: row for row in rows}
    llm = by_type.get("llm")
    emb = by_type.get("embedding")
    image2text = by_type.get("image2text")
    ocr = by_type.get("ocr")
    rerank = by_type.get("rerank")
    return success_response(
        {
            "provider": llm.provider if llm else "ollama",
            "llm_model": llm.model_name if llm else "",
            "ollama_base_url": llm.base_url if llm else settings.ollama_base_url,
            "api_key": llm.api_key if llm else "",
            "embedding_provider": emb.provider if emb else "ollama",
            "embedding_model": emb.model_name if emb else "",
            "embedding_base_url": emb.base_url if emb else settings.ollama_base_url,
            "embedding_api_key": emb.api_key if emb else "",
            "embedding_dimension": emb.dimension if emb else settings.ollama_embedding_dimension,
            "image2text_provider": image2text.provider if image2text else "",
            "image2text_model": image2text.model_name if image2text else "",
            "image2text_base_url": image2text.base_url if image2text else "",
            "image2text_api_key": image2text.api_key if image2text else "",
            "ocr_provider": ocr.provider if ocr else "deepdoc-builtin",
            "ocr_model": ocr.model_name if ocr else "deepdoc-local-ocr",
            "ocr_base_url": ocr.base_url if ocr else "",
            "ocr_api_key": ocr.api_key if ocr else "",
            "rerank_provider": rerank.provider if rerank else "",
            "rerank_model": rerank.model_name if rerank else "",
            "rerank_base_url": rerank.base_url if rerank else "",
            "rerank_api_key": rerank.api_key if rerank else "",
            "models": [model_to_dict(row) for row in rows],
            "provider_presets": [{"value": k, **v} for k, v in PROVIDER_PRESETS.items()],
        }
    )


def _validate_required_model(name: str, model: str, base_url: str, provider: str = ""):
    if (provider or "").lower() == "deepdoc-builtin":
        return ""
    if not model or not base_url:
        return f"{name} 的模型名称和 Base URL 都不能为空"
    return ""


def update_model_config(db, payload: dict):
    llm_provider = payload.get("provider") or "ollama"
    llm_base_url = payload.get("ollama_base_url") or PROVIDER_PRESETS.get(llm_provider, {}).get("default_base_url") or settings.ollama_base_url
    llm_name = (payload.get("llm_model") or "").strip()
    if not llm_name:
        return error_response("LLM 模型名称不能为空")

    emb_provider = payload.get("embedding_provider") or payload.get("provider") or "ollama"
    emb_base_url = payload.get("embedding_base_url") or payload.get("ollama_base_url") or PROVIDER_PRESETS.get(emb_provider, {}).get("default_base_url") or settings.ollama_base_url
    embedding_name = (payload.get("embedding_model") or "").strip()
    if not embedding_name:
        return error_response("Embedding 模型名称不能为空")

    get_or_create_model_configs(db)
    _upsert_model(db, model_type="llm", provider=llm_provider, model_name=llm_name, base_url=llm_base_url, api_key=payload.get("api_key") or "")
    _upsert_model(
        db,
        model_type="embedding",
        provider=emb_provider,
        model_name=embedding_name,
        base_url=emb_base_url,
        api_key=payload.get("embedding_api_key") or payload.get("api_key") or "",
        dimension=payload.get("embedding_dimension") or settings.ollama_embedding_dimension,
    )

    for model_type, label in (("image2text", "Image2Text API"), ("ocr", "OCR API")):
        model = (payload.get(f"{model_type}_model") or "").strip()
        base_url = (payload.get(f"{model_type}_base_url") or "").strip()
        provider = (payload.get(f"{model_type}_provider") or "").strip()
        api_key = payload.get(f"{model_type}_api_key") or ""
        if model_type == "ocr":
            cfg = _normalize_ocr_cfg({"provider": provider, "model_name": model, "base_url": base_url, "api_key": api_key})
            provider, model, base_url, api_key = cfg["provider"], cfg["model_name"], cfg["base_url"], cfg.get("api_key") or ""

        disabled = provider.lower() in {"", "disabled", "none", "off"}
        if disabled:
            row = db.query(ModelConfig).filter(ModelConfig.model_type == model_type).first()
            if row:
                row.provider = ""
                row.model_name = ""
                row.base_url = ""
                row.api_key = ""
            continue

        msg = _validate_required_model(label, model, base_url, provider)
        if msg:
            return error_response(msg)
        _upsert_model(db, model_type=model_type, provider=provider, model_name=model, base_url=base_url, api_key=api_key)

    # Rerank is optional — only save if explicitly configured
    rerank_provider = (payload.get("rerank_provider") or "").strip()
    rerank_model = (payload.get("rerank_model") or "").strip()
    rerank_base_url = (payload.get("rerank_base_url") or "").strip()
    rerank_api_key = payload.get("rerank_api_key") or ""
    if rerank_provider.lower() in {"", "disabled", "none", "off"}:
        row = db.query(ModelConfig).filter(ModelConfig.model_type == "rerank").first()
        if row:
            row.provider = ""
            row.model_name = ""
            row.base_url = ""
            row.api_key = ""
    else:
        _upsert_model(db, model_type="rerank", provider=rerank_provider, model_name=rerank_model, base_url=rerank_base_url, api_key=rerank_api_key)

    db.commit()
    return get_model_config(db)



def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _embeddings_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


def _cfg_from_payload(payload: dict, model_type: str) -> dict:
    if model_type == "llm":
        return {
            "provider": payload.get("provider") or "ollama",
            "model_name": payload.get("llm_model") or "",
            "base_url": payload.get("ollama_base_url") or "",
            "api_key": payload.get("api_key") or "",
        }

    prefix = model_type
    cfg = {
        "provider": payload.get(f"{prefix}_provider") or "",
        "model_name": payload.get(f"{prefix}_model") or "",
        "base_url": payload.get(f"{prefix}_base_url") or "",
        "api_key": payload.get(f"{prefix}_api_key") or payload.get("api_key") or "",
    }

    if model_type == "embedding":
        cfg["dimension"] = payload.get("embedding_dimension")

    if model_type == "ocr":
        cfg = _normalize_ocr_cfg(cfg)

    return cfg


def _headers(api_key: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _models_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/models"):
        return base
    return f"{base}/models"


def _provider_api_style(provider: str) -> str:
    return PROVIDER_PRESETS.get((provider or "").lower(), {}).get("api_style", "")


def _model_label(model_id: str) -> str:
    return str(model_id or "").strip()


async def list_available_models(cfg: dict, model_type: str = "") -> dict:
    """Return models actually reported by the selected provider.

    Ollama is read from /api/tags, while OpenAI-compatible providers are read
    from /models.  Custom OCR endpoints may not expose a model-list API, so
    they return an empty list with list_available=False.
    """
    provider = (cfg.get("provider") or "").strip().lower()
    base_url = (cfg.get("base_url") or "").strip().rstrip("/")
    api_key = cfg.get("api_key") or ""
    if provider in {"", "disabled", "none", "off"}:
        return {"models": [], "list_available": True, "provider": provider, "message": "未启用"}
    if provider == "deepdoc-builtin":
        return {
            "models": [{"id": "deepdoc-local-ocr", "name": "DeepDoc 内置 OCR"}],
            "list_available": True,
            "provider": provider,
        }
    if provider == "mineru":
        return {"models": [], "list_available": False, "provider": provider,
                "message": "该供应商暂无模型列表接口"}
    if not base_url:
        return {"models": [], "list_available": False, "provider": provider, "message": "Base URL 为空"}

    if provider in {"ollama", "local_ollama"}:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = []
        for item in data.get("models", []) or []:
            name = _model_label(item.get("name"))
            if not name:
                continue
            models.append({
                "id": name,
                "name": name,
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
                "digest": item.get("digest"),
            })
        return {"models": models, "list_available": True, "provider": provider}

    api_style = _provider_api_style(provider)
    if api_style in {"openai", "custom"} or provider in {
        "openai", "openai-compatible", "dashscope", "siliconflow", "zhipu", "hunyuan", "volcengine", "xai", "stepfun", "deepseek", "custom"
    }:
        headers = _headers(api_key)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(_models_url(base_url), headers=headers)
        if resp.status_code in {401, 403}:
            raise RuntimeError("API Key 无效或无权访问模型列表")
        if resp.status_code == 404:
            return {"models": [], "list_available": False, "provider": provider, "message": "该服务未提供 /models 列表接口"}
        resp.raise_for_status()
        data = resp.json()
        raw_models = data.get("data") if isinstance(data, dict) else data
        if not isinstance(raw_models, list):
            return {"models": [], "list_available": False, "provider": provider, "message": "模型列表返回格式不支持"}
        models = []
        for item in raw_models:
            if isinstance(item, str):
                mid = item
                label = item
            elif isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
                label = item.get("name") or item.get("id") or item.get("model")
            else:
                continue
            mid = _model_label(mid)
            if not mid:
                continue
            models.append({"id": mid, "name": label or mid})
        return {"models": models, "list_available": True, "provider": provider}

    return {"models": [], "list_available": False, "provider": provider, "message": "该供应商暂无模型列表接口"}


def _model_exists_in_list(model_name: str, models: list[dict]) -> bool:
    wanted = (model_name or "").strip()
    if not wanted:
        return False
    names = {str(item.get("id") or "").strip() for item in models}
    names |= {str(item.get("name") or "").strip() for item in models}
    return wanted in names


async def _ensure_model_available(cfg: dict, model_type: str) -> dict:
    provider = (cfg.get("provider") or "").lower()
    if provider in {"", "disabled", "none", "off", "deepdoc-builtin"}:
        return {"validated": provider == "deepdoc-builtin", "list_available": provider == "deepdoc-builtin"}
    info = await list_available_models(cfg, model_type)
    models = info.get("models") or []
    list_available = bool(info.get("list_available"))
    if list_available:
        if not _model_exists_in_list(cfg.get("model_name") or "", models):
            sample = "、".join([str(m.get("id") or m.get("name")) for m in models[:8]]) or "空列表"
            raise ValueError(f"{model_type} 模型不存在或未下载：{cfg.get('model_name') or '-'}。可用模型：{sample}")
        return {"validated": True, "list_available": True, "model_count": len(models)}
    # For custom endpoints without /models, we cannot pre-validate.  The real
    # inference call below still decides whether the configured model works.
    return {"validated": False, "list_available": False, "message": info.get("message")}


def _test_image_base64() -> str:
    img = Image.new("RGB", (420, 160), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((12, 12, 408, 148), outline="black", width=2)
    draw.text((28, 45), "know_rag API test", fill="black")
    draw.text((28, 88), "OCR TEST 123", fill="black")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def _test_llm(cfg: dict) -> dict:
    provider = (cfg["provider"] or "").lower()
    model = cfg["model_name"]
    base_url = (cfg["base_url"] or "").rstrip("/")
    if not model or not base_url:
        raise ValueError("LLM 的模型名称和 Base URL 不能为空")
    validation = await _ensure_model_available(cfg, "llm")
    if provider == "ollama":
        payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK only."}], "stream": False}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = (data.get("message") or {}).get("content") or data.get("response") or ""
        return {"response_preview": str(content)[:120], **validation}
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply OK only."}], "temperature": 0}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(_chat_completions_url(base_url), headers=_headers(cfg.get("api_key") or ""), json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"response_preview": str(content)[:120], **validation}


async def _test_embedding(cfg: dict) -> dict:
    provider = (cfg["provider"] or "").lower()
    model = cfg["model_name"]
    base_url = (cfg["base_url"] or "").rstrip("/")
    if not model or not base_url:
        raise ValueError("Embedding 的模型名称和 Base URL 不能为空")

    try:
        validation = await _ensure_model_available(cfg, "embedding")
    except Exception as exc:
        validation = {
            "validated": False,
            "validation_warning": str(exc)[:300],
        }

    if provider == "ollama":
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/api/embeddings",
                json={"model": model, "prompt": "know rag embedding test"},
            )
            if resp.status_code == 404:
                resp = await client.post(
                    f"{base_url}/api/embed",
                    json={"model": model, "input": "know rag embedding test"},
                )
            resp.raise_for_status()
            data = resp.json()
        vector = data.get("embedding") or (data.get("embeddings") or [[]])[0]
        return {"dimension": len(vector or []), "sample": (vector or [])[:3], **validation}

    payload = {
        "model": model,
        "input": "know rag embedding test",
        "encoding_format": "float",
    }

    dimension = cfg.get("dimension")
    if dimension:
        try:
            payload["dimensions"] = int(dimension)
        except (TypeError, ValueError):
            raise ValueError(f"Embedding 维度不合法：{dimension}")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _embeddings_url(base_url),
            headers=_headers(cfg.get("api_key") or ""),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    vector = data.get("data", [{}])[0].get("embedding") or []
    if not vector:
        raise ValueError(f"Embedding API 返回为空或格式不支持：{str(data)[:300]}")

    return {
        "dimension": len(vector),
        "sample": vector[:3],
        "requested_dimension": payload.get("dimensions"),
        **validation,
    }


async def _test_rerank(cfg: dict) -> dict:
    provider = (cfg["provider"] or "").lower()
    model = cfg["model_name"]
    base_url = (cfg["base_url"] or "").rstrip("/")
    if not model or not base_url:
        raise ValueError("Rerank 的模型名称和 Base URL 不能为空")

    validation = await _ensure_model_available(cfg, "rerank")

    payload = {
        "model": model,
        "query": "know rag rerank test",
        "documents": ["测试文档片段 A", "测试文档片段 B", "测试文档片段 C"],
        "top_n": 2,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/rerank",
            headers=_headers(cfg.get("api_key") or ""),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results") or []
    if not results:
        raise ValueError(f"Rerank API 返回为空或格式不支持：{str(data)[:300]}")

    first = results[0]
    return {
        "response_preview": f"Rerank 成功，返回 {len(results)} 条结果（最高分 {first.get('relevance_score', '-')}）",
        "result_count": len(results),
        **validation,
    }


async def _test_vision(cfg: dict, *, ocr: bool = False) -> dict:
    from app.services.vision_service import describe_image_with_config, recognize_image_with_config

    provider = (cfg.get("provider") or "").lower()
    model = cfg["model_name"]
    base_url = cfg["base_url"]
    if ocr and provider == "deepdoc-builtin":
        runtime = get_deepdoc_runtime()
        return {"response_preview": f"DeepDoc 内置 OCR 已启用（mode={runtime.mode}, ocr_available={runtime.ocr_available}）。无需配置 OCR API。"}
    if ocr and provider == "mineru":
        resolved_url = (cfg.get("base_url") or "").rstrip("/")
        if not resolved_url:
            raise ValueError("MinerU 的 Base URL 不能为空")
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(f"{resolved_url}/health")
            except Exception as exc:
                raise RuntimeError(f"MinerU API unavailable: {resolved_url}/health 无法连接 ({exc})")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"MinerU API unavailable: {resolved_url}/health returned {resp.status_code}"
                )
            try:
                data = resp.json()
            except Exception:
                raise RuntimeError(f"MinerU API unavailable: /health 返回非 JSON")
        health_status = data.get("status", "")
        if health_status != "healthy":
            raise RuntimeError(f"MinerU API unhealthy: status={health_status}")
        return {"response_preview": f"MinerU API healthy ({resolved_url})", "available": True}
    if not model or not base_url:
        raise ValueError(("OCR" if ocr else "Image2Text") + " 的模型名称和 Base URL 不能为空")
    validation = await _ensure_model_available(cfg, "ocr" if ocr else "image2text")
    obj = SimpleNamespace(
        provider=cfg.get("provider") or "openai-compatible",
        model_name=model,
        base_url=base_url,
        api_key=cfg.get("api_key") or "",
    )
    image_b64 = _test_image_base64()
    if ocr:
        text = await recognize_image_with_config(obj, image_b64)
    else:
        text = await describe_image_with_config(obj, image_b64, prompt="请简洁描述这张测试图片，并读出其中的文字。")
        if not str(text or "").strip():
            raise ValueError("Image2Text API 返回为空，请确认模型是视觉模型且支持 OpenAI-compatible image_url 输入。")
    return {"response_preview": str(text or "")[:300], **validation}


async def test_single_model_config(payload: dict, model_type: str):
    if model_type not in {"llm", "embedding", "image2text", "ocr", "rerank"}:
        return error_response(f"不支持的测试类型：{model_type}")
    cfg = _cfg_from_payload(payload, model_type)
    try:
        if model_type == "llm":
            detail = await _test_llm(cfg)
        elif model_type == "embedding":
            detail = await _test_embedding(cfg)
        elif model_type == "image2text":
            detail = await _test_vision(cfg, ocr=False)
        elif model_type == "rerank":
            detail = await _test_rerank(cfg)
        else:
            detail = await _test_vision(cfg, ocr=True)
        return success_response({"available": True, "model_type": model_type, "provider": cfg.get("provider"), "model": cfg.get("model_name"), **detail}, "API 测试成功")
    except Exception as exc:
        return error_response(f"{model_type} API 测试失败：{str(exc)[:500]}")


async def test_model_config(payload: dict):
    results = {}
    for model_type in ("llm", "embedding", "image2text", "ocr", "rerank"):
        cfg = _cfg_from_payload(payload, model_type)
        if model_type == "image2text" and not (cfg.get("model_name") or cfg.get("base_url")):
            results[model_type] = {"skipped": True, "reason": "未配置"}
            continue
        if model_type == "rerank" and not (cfg.get("model_name") or cfg.get("base_url")):
            results[model_type] = {"skipped": True, "reason": "未配置"}
            continue
        if model_type == "ocr" and not (cfg.get("model_name") or cfg.get("base_url") or (cfg.get("provider") == "deepdoc-builtin")):
            results[model_type] = {"skipped": True, "reason": "未配置"}
            continue
        resp = await test_single_model_config(payload, model_type)
        results[model_type] = resp
    return success_response({"available": True, "results": results})


def deepdoc_status(reload: bool = False):
    runtime = reload_deepdoc_runtime() if reload else get_deepdoc_runtime()
    status = runtime.status_dict()
    return success_response(status)
