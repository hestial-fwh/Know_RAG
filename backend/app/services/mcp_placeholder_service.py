from app.core.response import placeholder_response, success_response


def health():
    return success_response({"status": "reserved", "implemented": False})


def placeholder(name: str, payload: dict | None = None):
    return placeholder_response(f"mcp placeholder {name}", payload or {})
