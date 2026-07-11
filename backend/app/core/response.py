from typing import Any


def success_response(data: Any = None, message: str = "") -> dict[str, Any]:
    return {"success": True, "data": data, "message": message}


def error_response(message: str, data: Any = None) -> dict[str, Any]:
    return {"success": False, "data": data, "message": message}


def placeholder_response(feature: str, data: Any = None) -> dict[str, Any]:
    payload = {"feature": feature, "implemented": False}
    if data is not None:
        payload["request"] = data
    return success_response(payload, "placeholder only; not implemented in phase 1")
