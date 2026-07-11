import re
import unicodedata


def sanitize_text_for_postgres(text: str | None) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_chars: list[str] = []
    for ch in text:
        if ch in ("\n", "\t"):
            cleaned_chars.append(ch)
            continue
        category = unicodedata.category(ch)
        if category.startswith("C"):
            continue
        cleaned_chars.append(ch)

    text = "".join(cleaned_chars)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()



def sanitize_json_for_postgres(value, *, max_string_length: int = 20000):
    """Recursively clean JSON-like values before storing them in PostgreSQL JSON/JSONB.

    PostgreSQL rejects the NUL character even inside JSON strings.  Parser
    metadata may also contain bytes, Paths, tuples, or other small objects.  This
    helper keeps the useful diagnostic information while making payloads safe to
    persist and return to the frontend.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = sanitize_text_for_postgres(value)
        if max_string_length and len(text) > max_string_length:
            return text[:max_string_length] + "..."
        return text
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
        return sanitize_text_for_postgres(text)[:max_string_length]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            clean_key = sanitize_text_for_postgres(str(key))[:200]
            if not clean_key:
                continue
            cleaned[clean_key] = sanitize_json_for_postgres(item, max_string_length=max_string_length)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [sanitize_json_for_postgres(item, max_string_length=max_string_length) for item in value]
    try:
        return sanitize_text_for_postgres(str(value))[:max_string_length]
    except Exception:
        return ""
