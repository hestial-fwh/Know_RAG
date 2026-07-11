from app.core.response import placeholder_response, success_response


def list_faqs(kb_id: str):
    return success_response({"kb_id": kb_id, "items": []})


def create_faq(kb_id: str, payload: dict):
    return placeholder_response("create faq", {"kb_id": kb_id, "payload": payload})


def get_faq(faq_id: str):
    return placeholder_response("get faq", {"id": faq_id})


def update_faq(faq_id: str, payload: dict):
    return placeholder_response("update faq", {"id": faq_id, "payload": payload})


def delete_faq(faq_id: str):
    return placeholder_response("delete faq", {"id": faq_id})


def search_faqs(kb_id: str, payload: dict):
    return placeholder_response("search faqs", {"kb_id": kb_id, "payload": payload})
