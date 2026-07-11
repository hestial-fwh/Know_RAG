from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_id: str = ""
    captcha_code: str = ""


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
