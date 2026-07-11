from app.core.security import create_access_token


def issue_token(subject: str) -> str:
    return create_access_token(subject)
