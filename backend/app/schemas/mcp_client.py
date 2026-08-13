from pydantic import BaseModel, Field


class McpClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    scopes: list[str] = Field(default_factory=list)
    # None / omitted = 永久有效；N = 从当前时间起 N 天
    expires_in_days: int | None = Field(default=None, ge=1)


class McpClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    scopes: list[str] | None = None
    # None = 永久有效；N = 从当前时间起 N 天
    expires_in_days: int | None = Field(default=None, ge=1)


class McpVerifyRequest(BaseModel):
    client_id: str
    timestamp: str
    nonce: str
    signature: str
    method: str = "POST"
    path: str = "/mcp"
    body_sha256: str
    tool_name: str | None = None
