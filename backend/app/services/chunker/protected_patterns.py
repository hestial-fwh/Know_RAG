import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int


PROTECTED_PATTERNS = [
    re.compile(r"```[\s\S]*?```", re.MULTILINE),
    re.compile(r"\$\$[\s\S]*?\$\$", re.MULTILINE),
    re.compile(r"(?<!\\)\$(?!\$)[^\n$]+?(?<!\\)\$"),
    re.compile(r"\\\([\s\S]*?\\\)"),
    re.compile(r"\\\[[\s\S]*?\\\]"),
    re.compile(r"<(sup|sub|em|strong|span|a)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE),
    re.compile(r"<(?:table|pre|code)\b[\s\S]*?</(?:table|pre|code)>", re.IGNORECASE),
    re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);"),
    re.compile(r"!\[[^\]]*\]\([^)\n]+\)"),
    re.compile(r"\[[^\]]+\]\([^)\n]+\)"),
    re.compile(r"(?m)^[ \t]*\|.*\|[ \t]*\n[ \t]*\|[ \t:.-]+\|[ \t]*\n(?:[ \t]*\|.*\|[ \t]*(?:\n|$))+"),
]

TREE_MARKER_RE = re.compile(r"(?:[│├└]──|[│├└]|(?:^|\s)(?:\+--|\|--|`--)\s+)")
FILE_PATH_RE = re.compile(
    r"(?:^|[\s│├└+`\|-])[\w.-]+(?:/[\w.@+-]+)+/?(?:\s*(?:#|//|：|:)\s*.+)?$"
)
FILE_NAME_RE = re.compile(
    r"(?:^|[\s│├└+`\|-])[\w.@+-]+\.(?:py|ts|tsx|js|jsx|vue|md|json|ya?ml|go|java|sql|css|html|toml|ini|env|txt)\b",
    re.IGNORECASE,
)
DIRECTORY_LINE_RE = re.compile(r"(?:^|[\s│├└+`\|-])[\w.@+-]+/$")


def _line_structure_score(line: str) -> int:
    clean = (line or "").rstrip()
    if not clean:
        return 0
    score = 0
    if TREE_MARKER_RE.search(clean):
        score += 2
    if FILE_PATH_RE.search(clean):
        score += 2
    if FILE_NAME_RE.search(clean):
        score += 2
    if DIRECTORY_LINE_RE.search(clean):
        score += 1
    if re.search(r"(?:^|\s)(backend|frontend|app|src|services|tests|docs|api)/?$", clean, re.I):
        score += 1
    return score


def looks_like_directory_tree(text: str) -> bool:
    """Return True for tree-command or project-directory style text blocks."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    tree_lines = [line for line in lines if TREE_MARKER_RE.search(line)]
    scored = [line for line in lines if _line_structure_score(line) >= 2]
    return len(tree_lines) >= 2 or (len(scored) >= 3 and len(scored) / max(len(lines), 1) >= 0.45)


def looks_like_file_listing(text: str) -> bool:
    """Return True for plain path/file lists with optional inline comments."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    hits = [line for line in lines if FILE_PATH_RE.search(line) or FILE_NAME_RE.search(line)]
    return len(hits) >= 3 and len(hits) / max(len(lines), 1) >= 0.55


def is_structure_like_text(text: str) -> bool:
    """Detect directory-tree/code-structure text that must preserve line boundaries."""
    value = text or ""
    if value.strip().startswith("```") and value.strip().endswith("```"):
        inner = "\n".join(value.strip().splitlines()[1:-1])
        return looks_like_directory_tree(inner) or looks_like_file_listing(inner)
    return looks_like_directory_tree(value) or looks_like_file_listing(value)


def find_structure_spans(text: str) -> list[ProtectedSpan]:
    """Find whole-line spans for non-fenced directory trees and file listings."""
    if not text:
        return []
    spans: list[ProtectedSpan] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    run_start: int | None = None
    run_end = 0
    run_lines: list[str] = []

    def flush() -> None:
        nonlocal run_start, run_end, run_lines
        if run_start is None:
            return
        block = "".join(run_lines)
        non_empty = [line for line in run_lines if line.strip()]
        tree_hits = sum(1 for line in non_empty if TREE_MARKER_RE.search(line))
        scored_hits = sum(1 for line in non_empty if _line_structure_score(line) >= 2)
        if (len(non_empty) >= 3 and scored_hits >= 3) or (len(non_empty) >= 2 and tree_hits >= 2):
            if is_structure_like_text(block):
                spans.append(ProtectedSpan(run_start, run_end))
        run_start = None
        run_end = 0
        run_lines = []

    for line in lines:
        start = offset
        end = offset + len(line)
        offset = end
        if _line_structure_score(line) >= 2:
            if run_start is None:
                run_start = start
            run_end = end
            run_lines.append(line)
            continue
        if run_start is not None and not line.strip():
            run_end = end
            run_lines.append(line)
            continue
        flush()
    flush()
    return spans


def find_protected_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    for pattern in PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            if match.end() > match.start():
                spans.append(ProtectedSpan(match.start(), match.end()))
    spans.extend(find_structure_spans(text))
    if not spans:
        return []
    spans.sort(key=lambda item: (item.start, -(item.end - item.start)))
    merged: list[ProtectedSpan] = []
    for span in spans:
        if not merged or span.start >= merged[-1].end:
            merged.append(span)
        elif span.end > merged[-1].end:
            merged[-1] = ProtectedSpan(merged[-1].start, span.end)
    return merged


def is_protected_range(start: int, end: int, spans: list[ProtectedSpan]) -> bool:
    return any(start < span.end and end > span.start for span in spans)
