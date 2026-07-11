from pydantic import BaseModel


class ModelConfig(BaseModel):
    provider: str = "ollama"
    llm_model: str
    ollama_base_url: str
    api_key: str = ""

    embedding_provider: str = "ollama"
    embedding_model: str
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimension: int | None = None

    image2text_provider: str = ""
    image2text_model: str = ""
    image2text_base_url: str = ""
    image2text_api_key: str = ""

    ocr_provider: str = "deepdoc-builtin"
    ocr_model: str = "deepdoc-local-ocr"
    ocr_base_url: str = ""
    ocr_api_key: str = ""
