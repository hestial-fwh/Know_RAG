import httpx

from app.core.config import settings


def _model_names(payload: dict) -> list[str]:
    return [item.get("name", "") for item in payload.get("models", [])]


async def ollama_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/version")
        return {
            "available": response.is_success,
            "base_url": settings.ollama_base_url,
            "version": response.json().get("version") if response.is_success else None,
        }
    except Exception:
        return {"available": False, "base_url": settings.ollama_base_url, "error": "Ollama 服务不可用，请确认本机已启动 Ollama"}


async def list_ollama_models() -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
        return response.json() if response.is_success else {"models": [], "error": "Ollama 模型列表获取失败"}
    except Exception:
        return {"models": [], "error": "Ollama 服务不可用，请确认本机已启动 Ollama"}


async def ensure_model_available(model_name: str) -> tuple[bool, str]:
    payload = await list_ollama_models()
    names = _model_names(payload)
    if not names and payload.get("error"):
        return False, payload["error"]
    if model_name in names or any(name.startswith(f"{model_name}:") for name in names):
        return True, ""
    return False, f"Embedding 模型不可用，请先执行：ollama pull {model_name}"


async def embed_text_with_ollama(text: str, model_name: str | None = None, check_model: bool = True) -> list[float]:
    model = model_name or settings.ollama_embedding_model
    text = (text or "").strip()
    if not text:
        raise ValueError("文本不能为空")

    if check_model:
        available, message = await ensure_model_available(model)
        if not available:
            raise RuntimeError(message)

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": model, "input": text},
            )
            if response.is_success:
                payload = response.json()
                embeddings = payload.get("embeddings") or []
                if embeddings and isinstance(embeddings[0], list):
                    return [float(item) for item in embeddings[0]]
        except httpx.RequestError as exc:
            raise RuntimeError("Ollama 服务不可用，请确认本机已启动 Ollama") from exc

        response = await client.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        if not response.is_success:
            raise RuntimeError("Ollama embedding 调用失败，请确认模型可用")
        payload = response.json()
        embedding = payload.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Ollama embedding 返回格式不正确")
        return [float(item) for item in embedding]


async def test_embedding(text: str) -> dict:
    vector = await embed_text_with_ollama(text or "这是一个本地知识库向量测试")
    return {
        "model": settings.ollama_embedding_model,
        "dimension": len(vector),
        "sample_size": len(vector),
    }
