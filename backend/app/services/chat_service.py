from app.core.response import placeholder_response


def knowledge_chat(session_id: str, payload: dict):
    return placeholder_response("knowledge chat", {"session_id": session_id, "payload": payload})
