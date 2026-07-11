"""Ordered Markdown block scanning helpers.

The previous Markdown ingestion path used RAGFlowMarkdownParser with
``separate_tables=True``.  That API removes tables from the body and returns
all tables in a second list, so the adapter inevitably appended every table to
the end of the document.  Its table regex also required a trailing newline,
which caused a valid table at EOF to be downgraded to plain text.

This module keeps the original source order and provides a project-local
Markdown element scanner, so Markdown ingestion no longer depends on DeepDoc
for headings, prose, lists, code blocks or tables.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MarkdownSegment:
    kind: str  # "text" | "table"
    content: str
    start_line: int
    end_line: int
    table_format: str = ""  # "pipe" | "html"


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_HTML_TABLE_START_RE = re.compile(r"^\s*<table\b", re.IGNORECASE)
_HTML_TABLE_END_RE = re.compile(r"</table\s*>", re.IGNORECASE)
_DELIMITER_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _fence_marker(line: str) -> str:
    match = _FENCE_RE.match(line)
    return match.group(1) if match else ""


def _is_matching_fence(line: str, marker: str) -> bool:
    if not marker:
        return False
    stripped = line.lstrip()
    return stripped.startswith(marker[0] * len(marker))


def _has_unescaped_pipe(line: str) -> bool:
    escaped = False
    for char in line:
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == "|" and not escaped:
            return True
        escaped = False
    return False


def split_markdown_table_cells(line: str) -> list[str]:
    """Split one Markdown table row while preserving escaped pipes in cells."""
    value = line.strip()
    cells: list[str] = []
    current: list[str] = []
    escaped = False

    for char in value:
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        escaped = False
    cells.append("".join(current).strip())

    # Outer pipes create empty sentinel cells; they are not real columns.
    if value.startswith("|") and cells and not cells[0]:
        cells = cells[1:]
    if value.endswith("|") and cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def is_markdown_table_delimiter(line: str) -> bool:
    if not _has_unescaped_pipe(line):
        return False
    cells = split_markdown_table_cells(line)
    return bool(cells) and all(_DELIMITER_CELL_RE.fullmatch(cell.replace(" ", "")) for cell in cells)


def is_markdown_table_header(line: str, delimiter_line: str) -> bool:
    if not line.strip() or not _has_unescaped_pipe(line):
        return False
    if not is_markdown_table_delimiter(delimiter_line):
        return False
    header_cells = split_markdown_table_cells(line)
    delimiter_cells = split_markdown_table_cells(delimiter_line)
    return bool(header_cells) and len(header_cells) == len(delimiter_cells)


def _is_pipe_table_row(line: str) -> bool:
    return bool(line.strip()) and _has_unescaped_pipe(line)


def _find_html_table_end(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if _HTML_TABLE_END_RE.search(lines[index]):
            return index + 1
    return None


def _find_pipe_table_end(lines: list[str], start: int) -> int:
    index = start + 2  # header + delimiter are already known to be valid
    while index < len(lines):
        line = lines[index]
        if not line.strip() or not _is_pipe_table_row(line):
            break
        if _fence_marker(line) or _HTML_TABLE_START_RE.match(line):
            break
        index += 1
    return index



_HEADING_RE = re.compile(r"^#{1,6}\s+.*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+).*$")
_INDENTED_LIST_RE = re.compile(r"^\s{2,}(?:[-*+]\s+|\d+\.\s+).*$")


def _is_text_block_start(line: str) -> bool:
    return bool(
        _HEADING_RE.match(line)
        or _fence_marker(line)
        or _LIST_RE.match(line)
        or line.strip().startswith(">")
    )


def extract_markdown_text_elements(text: str, *, line_offset: int = 0) -> list[dict]:
    """Extract non-table Markdown elements without importing DeepDoc.

    The scanner intentionally mirrors the useful subset of the previous
    ``MarkdownElementExtractor`` while adding tilde-fence support and absolute
    source-line metadata.  Tables must already have been separated by
    :func:`split_markdown_preserving_tables`.
    """
    lines = text.splitlines()
    elements: list[dict] = []
    index = 0

    def append(kind: str, start: int, end: int) -> None:
        content = "\n".join(lines[start : end + 1]).strip()
        if content:
            elements.append(
                {
                    "type": kind,
                    "content": content,
                    "start_line": line_offset + start,
                    "end_line": line_offset + end,
                }
            )

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        if _HEADING_RE.match(line):
            append("header", index, index)
            index += 1
            continue

        marker = _fence_marker(line)
        if marker:
            start = index
            index += 1
            while index < len(lines):
                current_marker = _fence_marker(lines[index])
                if (
                    current_marker
                    and current_marker[0] == marker[0]
                    and _is_matching_fence(lines[index], marker)
                ):
                    index += 1
                    break
                index += 1
            append("code_block", start, max(start, index - 1))
            continue

        if _LIST_RE.match(line):
            start = index
            index += 1
            while index < len(lines):
                current = lines[index]
                if (
                    _LIST_RE.match(current)
                    or _INDENTED_LIST_RE.match(current)
                    or (current.strip() == "" and index + 1 < len(lines) and (
                        _LIST_RE.match(lines[index + 1])
                        or _INDENTED_LIST_RE.match(lines[index + 1])
                    ))
                    or (current.startswith((" ", "\t")) and current.strip())
                ):
                    index += 1
                    continue
                break
            append("list_block", start, max(start, index - 1))
            continue

        if line.strip().startswith(">"):
            start = index
            index += 1
            while index < len(lines):
                current = lines[index]
                if current.strip().startswith(">"):
                    index += 1
                    continue
                if not current.strip() and index + 1 < len(lines) and lines[index + 1].strip().startswith(">"):
                    index += 1
                    continue
                break
            append("blockquote", start, max(start, index - 1))
            continue

        # Paragraph / plain text block.  Stop before the next explicit Markdown
        # block; blank lines are retained only when they connect plain prose.
        start = index
        index += 1
        while index < len(lines):
            current = lines[index]
            if _is_text_block_start(current):
                break
            if not current.strip():
                next_index = index + 1
                while next_index < len(lines) and not lines[next_index].strip():
                    next_index += 1
                if next_index >= len(lines) or _is_text_block_start(lines[next_index]):
                    break
                index = next_index
                continue
            index += 1
        append("text_block", start, max(start, index - 1))

    return elements

def split_markdown_preserving_tables(text: str) -> list[MarkdownSegment]:
    """Return text/table segments in exact source order.

    Supported table forms:
    - GFM/Markdown pipe tables, with or without outer pipes;
    - raw HTML ``<table>...</table>`` blocks.

    Fenced code blocks are protected, so table-looking rows inside code are not
    misclassified.  ``splitlines(keepends=True)`` means EOF tables do not need a
    final newline.
    """
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    segments: list[MarkdownSegment] = []
    text_start = 0
    index = 0
    fence = ""

    def emit_text(start: int, end: int) -> None:
        if end <= start:
            return
        content = "".join(lines[start:end])
        if content.strip():
            segments.append(
                MarkdownSegment(
                    kind="text",
                    content=content,
                    start_line=start,
                    end_line=end - 1,
                )
            )

    while index < len(lines):
        line = lines[index]
        marker = _fence_marker(line)

        if fence:
            if marker and marker[0] == fence[0] and _is_matching_fence(line, fence):
                fence = ""
            index += 1
            continue

        if marker:
            fence = marker
            index += 1
            continue

        if _HTML_TABLE_START_RE.match(line):
            end = _find_html_table_end(lines, index)
            if end is not None:
                emit_text(text_start, index)
                segments.append(
                    MarkdownSegment(
                        kind="table",
                        content="".join(lines[index:end]),
                        start_line=index,
                        end_line=end - 1,
                        table_format="html",
                    )
                )
                index = end
                text_start = end
                continue

        if index + 1 < len(lines) and is_markdown_table_header(line, lines[index + 1]):
            end = _find_pipe_table_end(lines, index)
            emit_text(text_start, index)
            segments.append(
                MarkdownSegment(
                    kind="table",
                    content="".join(lines[index:end]),
                    start_line=index,
                    end_line=end - 1,
                    table_format="pipe",
                )
            )
            index = end
            text_start = end
            continue

        index += 1

    emit_text(text_start, len(lines))
    return segments
