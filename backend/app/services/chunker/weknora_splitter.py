"""Runtime Python port of WeKnora's chunker used by Know_RAG.

Source mapping in the uploaded WeKnora project:
- ``internal/infrastructure/chunker/strategy.go``: ``Split``,
  ``SplitParentChild`` and ``mergeBreadcrumbs``.
- ``internal/infrastructure/chunker/splitter.go``: protected recursive splitting
  and the legacy parent-child behavior.
- ``internal/application/service/knowledge_process.go``:
  ``buildParentChildConfigs`` defaults (parent 4096, child 384, child overlap
  ~= 20%).

The backend is Python, so the Go package cannot be imported directly.  This
module is the runtime Python port of that algorithm.  The parent-child path is
kept aligned with the current WeKnora source and covered by regression tests.

Runtime contract:
1. Split the document into large parent candidates.
2. Re-split every candidate into child chunks.
3. Materialize a parent only when real subdivision occurred.
4. Keep child sequence numbers global across the document.
5. Shift child offsets back to document coordinates.
6. Merge parent/child breadcrumbs while removing a duplicated seam.
7. Index children; use parents only as larger retrieval context.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_SEPARATORS = ["\n\n", "\n", "。"]

STRATEGY_AUTO = "auto"
STRATEGY_HEADING = "heading"
STRATEGY_HEURISTIC = "heuristic"
STRATEGY_RECURSIVE = "recursive"
STRATEGY_LEGACY = "legacy"

TIER_HEADING = "heading"
TIER_HEURISTIC = "heuristic"
TIER_LEGACY = "legacy"


@dataclass
class SplitterConfig:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    separators: List[str] = field(default_factory=lambda: list(DEFAULT_SEPARATORS))
    strategy: str = STRATEGY_AUTO
    token_limit: int = 0
    languages: List[str] = field(default_factory=list)

    def normalized(self) -> "SplitterConfig":
        return SplitterConfig(
            chunk_size=self.chunk_size if self.chunk_size > 0 else DEFAULT_CHUNK_SIZE,
            chunk_overlap=max(0, self.chunk_overlap),
            separators=self.separators or list(DEFAULT_SEPARATORS),
            strategy=(self.strategy or STRATEGY_LEGACY).lower(),
            token_limit=max(0, self.token_limit),
            languages=self.languages or [],
        )


@dataclass
class Chunk:
    content: str
    seq: int
    start: int
    end: int
    context_header: str = ""

    def embedding_content(self, document_title: str = "") -> str:
        parts = []
        if document_title and document_title.strip():
            parts.append(document_title.strip())
        if self.context_header and self.context_header.strip():
            parts.append(self.context_header.strip())
        if self.content and self.content.strip():
            parts.append(self.content.strip())
        return "\n\n".join(parts)


@dataclass
class ChildChunk(Chunk):
    parent_index: int = -1


@dataclass
class ParentChildResult:
    parents: List[Chunk]
    children: List[ChildChunk]


@dataclass
class SplitUnit:
    text: str
    start: int
    end: int


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


@dataclass
class DocProfile:
    total_chars: int = 0
    total_lines: int = 0
    avg_line_len: float = 0.0
    std_line_len: float = 0.0

    md_heading_counts: dict = field(default_factory=lambda: {i: 0 for i in range(1, 7)})
    md_heading_total: int = 0

    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    blank_paragraph_breaks: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    german_chapter_count: int = 0
    english_chapter_count: int = 0
    chinese_chapter_count: int = 0
    repeated_footer_count: int = 0

    has_tables: bool = False
    has_code: bool = False
    code_ratio: float = 0.0
    detected_langs: List[str] = field(default_factory=list)

    def heading_density(self) -> float:
        return self.md_heading_total / self.total_lines if self.total_lines else 0.0

    def dominant_heading_level(self) -> int:
        if self.md_heading_total == 0:
            return 0
        # Like WeKnora: prefer the shallowest heading level with >=3 occurrences.
        for level in range(1, 7):
            if self.md_heading_counts.get(level, 0) >= 3:
                return level
        # Otherwise use the deepest present level for small documents.
        for level in range(6, 0, -1):
            if self.md_heading_counts.get(level, 0) > 0:
                return level
        return 0

    def heuristic_marker_total(self) -> int:
        return (
            self.numbered_section_count
            + self.german_chapter_count
            + self.english_chapter_count
            + self.chinese_chapter_count
            + self.all_caps_short_line_count
            + self.visual_sep_count
            + self.form_feed_count
        )


# WeKnora-like multilingual structure patterns.
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*#*\s*$")
NUMBERED_SECTION_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$"
)
ALL_CAPS_HEADING_PATTERN = re.compile(r"(?m)^[ \t]*([A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}):?\s*$")
VISUAL_SEPARATOR_PATTERN = re.compile(r"(?m)^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$")
EXCESSIVE_BLANKS_PATTERN = re.compile(r"\n{3,}")
PAGE_FOOTER_PATTERN = re.compile(
    r"(?mi)^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$"
)
GERMAN_CHAPTER_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$"
)
ENGLISH_CHAPTER_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:Chapter|Section|Part)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$"
)
CHINESE_CHAPTER_PATTERN = re.compile(
    r"(?m)^[ \t]*第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}$"
)

# Protected spans. These should not be split casually.
PROTECTED_PATTERNS = [
    re.compile(r"(?s)\$\$.*?\$\$"),  # LaTeX block math
    re.compile(r"(?<!\\)\$(?!\$)[^\n$]+?(?<!\\)\$"),  # inline LaTeX
    re.compile(r"(?s)\\\(.*?\\\)"),  # \(inline LaTeX\)
    re.compile(r"(?s)\\\[.*?\\\]"),  # \[display LaTeX\]
    re.compile(r"(?s)```(?:\w+)?[\r\n].*?```"),  # fenced code blocks
    re.compile(r"(?is)<(sup|sub|em|strong|span|a)\b[^>]*>.*?</\1>"),  # inline HTML atoms
    re.compile(r"(?is)<(?:table|pre|code)\b[^>]*>.*?</(?:table|pre|code)>"),  # HTML structures
    re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);"),  # HTML entities
    re.compile(r"!\[[^\]]*\]\([^)]+\)"),  # Markdown images
    re.compile(r"\[[^\]]*\]\([^)]+\)"),  # Markdown links
    re.compile(r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|[\r\n]+"),  # table header
    re.compile(r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+"),  # table rows
]

TABLE_HEADER_PATTERN = re.compile(
    r"(?si)^\s*(?:\|[^|\n]*)+[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|?[\r\n]+$"
)
TABLE_ROW_PATTERN = re.compile(r"(?m)^\s*\|.*\|\s*$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _structural_only_content(text: str) -> bool:
    """Return True for heading/separator fragments with no semantic body.

    WeKnora's protected recursive splitter can flush ``--- + heading`` before
    a large code/table unit.  Persisting that fragment creates the one-line
    title children seen in the chunk tree.  Treat it as context for the next
    semantic child instead of a retrieval unit.
    """
    meaningful = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(?:-{3,}|\*{3,}|_{3,})$", line):
            continue
        if re.match(r"^#{1,6}\s+.+?\s*#*$", line):
            continue
        meaningful.append(line)
    return not meaningful


def _last_markdown_heading(text: str) -> str:
    heading = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if re.match(r"^#{1,6}\s+.+?\s*#*$", line):
            heading = line
    return heading


def repair_semantic_chunk_boundaries(chunks: List[Chunk], chunk_size: int) -> List[Chunk]:
    """Remove blank/tiny structural chunks and carry headings forward.

    This is a Know_RAG safety layer around the WeKnora runtime port.  It does
    not change parent/child policy; it only prevents separators/headings from
    becoming standalone retrieval chunks when the next protected unit forces a
    flush.
    """
    items = [c for c in chunks if c.content and c.content.strip()]
    if not items:
        return []

    out: List[Chunk] = []
    pending_context = ""
    for chunk in items:
        if _structural_only_content(chunk.content):
            heading = _last_markdown_heading(chunk.content)
            pending_context = merge_breadcrumbs(pending_context, heading) if heading else pending_context
            if out and not heading:
                # A trailing horizontal rule belongs to the preceding chunk.
                out[-1].end = max(out[-1].end, chunk.end)
            continue

        if pending_context:
            chunk.context_header = merge_breadcrumbs(pending_context, chunk.context_header)
            pending_context = ""
        out.append(chunk)

    if pending_context and out:
        out[-1].context_header = merge_breadcrumbs(out[-1].context_header, pending_context)
    return resequence(out)


def split(text: str, cfg: Optional[SplitterConfig] = None) -> List[Chunk]:
    """Split text with WeKnora-like strategy fallback and semantic cleanup."""
    if not text:
        return []
    cfg = (cfg or SplitterConfig()).normalized()
    chain, profile = resolve_chain_with_profile(text, cfg)
    total_chars = len(text)

    last_out: List[Chunk] = []
    for tier in chain:
        out = repair_semantic_chunk_boundaries(run_tier(tier, text, cfg, profile), cfg.chunk_size)
        verdict = validate_chunks(out, total_chars, cfg.chunk_size)
        if verdict.ok:
            return resequence(out)
        if tier == TIER_LEGACY:
            last_out = out

    fallback = last_out or split_text_legacy(text, cfg)
    return repair_semantic_chunk_boundaries(fallback, cfg.chunk_size)


def split_parent_child(
    text: str,
    parent_cfg: Optional[SplitterConfig] = None,
    child_cfg: Optional[SplitterConfig] = None,
) -> ParentChildResult:
    """
    WeKnora-like parent-child splitting.

    Important for Know_RAG:
    - Keep strategy/token_limit/languages on both parent and child configs.
    - Store parents in DB only.
    - Index children only.
    - Resolve child -> parent after retrieval.
    """
    if not text:
        return ParentChildResult(parents=[], children=[])

    parent_cfg = (parent_cfg or SplitterConfig(chunk_size=4096)).normalized()
    child_cfg = (child_cfg or SplitterConfig(chunk_size=384, chunk_overlap=384 // 5)).normalized()

    parents = [parent for parent in split(text, parent_cfg) if parent.content and parent.content.strip()]
    if not parents:
        return ParentChildResult(parents=[], children=[])

    stored_parents: List[Chunk] = []
    children: List[ChildChunk] = []
    child_seq = 0

    for parent in parents:
        subs = [sub for sub in split(parent.content, child_cfg) if sub.content and sub.content.strip()]
        if not subs:
            continue
        # Same idea as WeKnora: only materialize a parent when the parent was actually subdivided.
        parent_index = -1
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(stored_parents)
            stored_parents.append(parent)

        for sub in subs:
            merged_header = merge_breadcrumbs(parent.context_header, sub.context_header)
            children.append(
                ChildChunk(
                    content=sub.content,
                    seq=child_seq,
                    start=parent.start + sub.start,
                    end=parent.start + sub.end,
                    context_header=merged_header,
                    parent_index=parent_index,
                )
            )
            child_seq += 1

    return ParentChildResult(parents=stored_parents, children=children)


def build_parent_child_configs(
    base: SplitterConfig,
    parent_chunk_size: int = 4096,
    child_chunk_size: int = 384,
) -> Tuple[SplitterConfig, SplitterConfig]:
    """Mirror WeKnora knowledge_process.go::buildParentChildConfigs.

    The current WeKnora implementation derives parent/child configs from only
    size, overlap and separators.  It does *not* propagate the base strategy,
    token limit or languages.  Because an empty strategy resolves to legacy in
    strategy.go, the runtime behavior is the protected recursive splitter for
    both parent and child passes.
    """
    base = base.normalized()
    parent = SplitterConfig(
        chunk_size=parent_chunk_size if parent_chunk_size > 0 else 4096,
        chunk_overlap=base.chunk_overlap,
        separators=list(base.separators),
        strategy=STRATEGY_LEGACY,
        token_limit=0,
        languages=[],
    )
    child_size = child_chunk_size if child_chunk_size > 0 else 384
    child = SplitterConfig(
        chunk_size=child_size,
        chunk_overlap=child_size // 5,
        separators=list(base.separators),
        strategy=STRATEGY_LEGACY,
        token_limit=0,
        languages=[],
    )
    return parent, child


# ---------------------------------------------------------------------------
# Strategy selection / validation
# ---------------------------------------------------------------------------

def resolve_chain_with_profile(text: str, cfg: SplitterConfig) -> Tuple[List[str], Optional[DocProfile]]:
    strategy = (cfg.strategy or STRATEGY_LEGACY).lower()
    if strategy == STRATEGY_HEADING:
        return [TIER_HEADING, TIER_LEGACY], None
    if strategy == STRATEGY_HEURISTIC:
        return [TIER_HEURISTIC, TIER_LEGACY], None
    if strategy in (STRATEGY_RECURSIVE, STRATEGY_LEGACY, ""):
        return [TIER_LEGACY], None

    # auto or unknown strategy: profile and choose.
    profile = profile_document(text)
    return select_strategy(profile), profile


def select_strategy(profile: DocProfile) -> List[str]:
    chain: List[str] = []
    if (
        profile.md_heading_total >= 3
        and profile.heading_density() > 0.005
        and profile.dominant_heading_level() > 0
    ):
        chain.append(TIER_HEADING)

    if (
        profile.heuristic_marker_total() >= 5
        or profile.form_feed_count > 0
        or (profile.german_chapter_count + profile.english_chapter_count + profile.chinese_chapter_count) > 0
    ):
        chain.append(TIER_HEURISTIC)

    chain.append(TIER_LEGACY)
    return chain


def run_tier(tier: str, text: str, cfg: SplitterConfig, profile: Optional[DocProfile]) -> List[Chunk]:
    if tier == TIER_HEADING:
        return split_by_headings(text, cfg, profile)
    if tier == TIER_HEURISTIC:
        return split_by_heuristics(text, cfg)
    return split_text_legacy(text, cfg)


def validate_chunks(chunks: List[Chunk], total_chars: int, chunk_size: int) -> ValidationResult:
    if not chunks:
        return ValidationResult(False, "no chunks produced")
    if len(chunks) == 1 and total_chars > 2 * chunk_size:
        return ValidationResult(False, "single chunk for large document")

    lengths = [len(c.content) for c in chunks]
    max_len = max(lengths)
    tiny_count = sum(1 for i, c in enumerate(chunks) if i != len(chunks) - 1 and len(c.content) < 50)
    if tiny_count > len(chunks) // 4 and tiny_count > 2:
        return ValidationResult(False, "too many tiny chunks")
    if max_len < chunk_size // 4 and total_chars > chunk_size:
        return ValidationResult(False, "all chunks far below target size")
    if chunk_size > 0 and max_len > 2 * chunk_size:
        return ValidationResult(False, "chunk exceeds 2x target size")
    return ValidationResult(True, "")


def resequence(chunks: List[Chunk]) -> List[Chunk]:
    for i, c in enumerate(chunks):
        c.seq = i
    return chunks


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

def profile_document(text: str) -> DocProfile:
    p = DocProfile(total_chars=len(text), form_feed_count=text.count("\f"))
    if not text:
        return p

    lines = text.split("\n")
    p.total_lines = len(lines)
    lengths: List[int] = []
    code_chars = 0
    in_fence = False
    footer_counter = {}

    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
            p.has_code = True
            continue
        if in_fence:
            code_chars += len(line)
            continue

        lengths.append(len(line))

        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            level = len(m.group(1))
            p.md_heading_counts[level] = p.md_heading_counts.get(level, 0) + 1
            p.md_heading_total += 1

        if NUMBERED_SECTION_PATTERN.match(line):
            p.numbered_section_count += 1
        if ALL_CAPS_HEADING_PATTERN.match(line):
            p.all_caps_short_line_count += 1
        if VISUAL_SEPARATOR_PATTERN.match(line):
            p.visual_sep_count += 1
        if GERMAN_CHAPTER_PATTERN.match(line):
            p.german_chapter_count += 1
        if ENGLISH_CHAPTER_PATTERN.match(line):
            p.english_chapter_count += 1
        if CHINESE_CHAPTER_PATTERN.match(line):
            p.chinese_chapter_count += 1
        if PAGE_FOOTER_PATTERN.match(line):
            footer_counter[trimmed] = footer_counter.get(trimmed, 0) + 1
        if TABLE_ROW_PATTERN.match(line):
            p.has_tables = True

    p.blank_paragraph_breaks = len(EXCESSIVE_BLANKS_PATTERN.findall(text))
    p.repeated_footer_count = sum(1 for _, count in footer_counter.items() if count >= 2)

    if lengths:
        p.avg_line_len = sum(lengths) / len(lengths)
        p.std_line_len = math.sqrt(sum((x - p.avg_line_len) ** 2 for x in lengths) / len(lengths))
    if p.total_chars:
        p.code_ratio = code_chars / p.total_chars

    if re.search(r"[\u4e00-\u9fff]", text):
        p.detected_langs.append("zh")
    if re.search(r"[A-Za-z]", text):
        p.detected_langs.append("en")
    return p


# ---------------------------------------------------------------------------
# Heading-aware splitter
# ---------------------------------------------------------------------------

@dataclass
class HeadingBoundary:
    start: int
    line: str
    level: int


class HeadingHierarchy:
    def __init__(self) -> None:
        self._levels = {i: "" for i in range(1, 7)}

    def observe(self, line: str) -> None:
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
        if not m:
            return
        level = len(m.group(1))
        title = m.group(2).strip()
        self._levels[level] = title
        for deeper in range(level + 1, 7):
            self._levels[deeper] = ""

    def breadcrumb_with_hashes(self) -> str:
        parts = []
        for level in range(1, 7):
            title = self._levels.get(level, "")
            if title:
                parts.append("#" * level + " " + title)
        return "\n".join(parts)


def find_heading_boundaries(text: str, primary_level: int) -> List[HeadingBoundary]:
    bounds: List[HeadingBoundary] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        raw_line = line.rstrip("\r\n")
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", raw_line.strip())
        if m and len(m.group(1)) == primary_level:
            bounds.append(HeadingBoundary(start=offset, line=raw_line.strip(), level=primary_level))
        offset += len(line)
    return bounds


def observe_subheadings(section: str, primary_level: int, hierarchy: HeadingHierarchy) -> None:
    for line in section.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
        if m and len(m.group(1)) > primary_level:
            hierarchy.observe(line.strip())


def split_by_headings(text: str, cfg: SplitterConfig, profile: Optional[DocProfile] = None) -> List[Chunk]:
    if not text:
        return []
    profile = profile or profile_document(text)
    primary_level = profile.dominant_heading_level()
    if primary_level == 0:
        return split_text_legacy(text, cfg)

    bounds = find_heading_boundaries(text, primary_level)
    if len(bounds) <= 1:
        return split_text_legacy(text, cfg)

    hierarchy = HeadingHierarchy()
    out: List[Chunk] = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1].start if i + 1 < len(bounds) else len(text)
        if b.line:
            hierarchy.observe(b.line)
        breadcrumb = hierarchy.breadcrumb_with_hashes()
        section = text[b.start:end]
        observe_subheadings(section, primary_level, hierarchy)

        if len(breadcrumb) + 2 + len(section) <= cfg.chunk_size:
            out.append(Chunk(content=section, seq=len(out), start=b.start, end=end, context_header=breadcrumb))
        else:
            for sub in split_text_legacy(section, cfg):
                out.append(
                    Chunk(
                        content=sub.content,
                        seq=len(out),
                        start=b.start + sub.start,
                        end=b.start + sub.end,
                        context_header=breadcrumb,
                    )
                )
    return coalesce_tiny_chunks(out, cfg.chunk_size)


def coalesce_tiny_chunks(chunks: List[Chunk], chunk_size: int) -> List[Chunk]:
    if not chunks:
        return []
    out: List[Chunk] = []
    i = 0
    tiny_threshold = max(80, chunk_size // 5)
    while i < len(chunks):
        cur = chunks[i]
        if i + 1 < len(chunks) and len(cur.content.strip()) < tiny_threshold:
            nxt = chunks[i + 1]
            if len(cur.content) + len(nxt.content) <= chunk_size:
                merged_header = merge_breadcrumbs(cur.context_header, nxt.context_header)
                out.append(
                    Chunk(
                        content=cur.content.rstrip() + "\n\n" + nxt.content.lstrip(),
                        seq=len(out),
                        start=cur.start,
                        end=nxt.end,
                        context_header=merged_header,
                    )
                )
                i += 2
                continue
        out.append(cur)
        i += 1
    return resequence(out)


# ---------------------------------------------------------------------------
# Heuristic splitter
# ---------------------------------------------------------------------------

@dataclass(order=True)
class Boundary:
    start: int


def match_line_boundaries(text: str, pattern: re.Pattern) -> List[Boundary]:
    return [Boundary(m.start()) for m in pattern.finditer(text)]


def find_heuristic_boundaries(text: str, languages: Sequence[str] = ()) -> List[Boundary]:
    starts = set()
    for pat in [
        NUMBERED_SECTION_PATTERN,
        ALL_CAPS_HEADING_PATTERN,
        VISUAL_SEPARATOR_PATTERN,
        GERMAN_CHAPTER_PATTERN,
        ENGLISH_CHAPTER_PATTERN,
        CHINESE_CHAPTER_PATTERN,
    ]:
        for m in pat.finditer(text):
            starts.add(m.start())

    for m in EXCESSIVE_BLANKS_PATTERN.finditer(text):
        starts.add(m.end())
    start = 0
    while True:
        idx = text.find("\f", start)
        if idx == -1:
            break
        starts.add(idx + 1)
        start = idx + 1

    return [Boundary(x) for x in sorted(starts) if 0 <= x < len(text)]


def split_by_heuristics(text: str, cfg: SplitterConfig) -> List[Chunk]:
    if not text:
        return []
    if len(text) <= cfg.chunk_size:
        return split_text_legacy(text, cfg)

    bounds = find_heuristic_boundaries(text, cfg.languages)
    protected = protected_spans(text)
    bounds = drop_bounds_inside_spans(bounds, protected)
    if not bounds:
        return split_text_legacy(text, cfg)

    if bounds[0].start != 0:
        bounds = [Boundary(0)] + bounds
    if bounds[-1].start != len(text):
        bounds.append(Boundary(len(text)))

    out: List[Chunk] = []
    chunk_start = bounds[0].start
    cur_end = chunk_start
    min_chunk_size = max(50, cfg.chunk_size // 4)

    for i in range(1, len(bounds)):
        next_end = bounds[i].start
        block_len = next_end - cur_end

        if block_len > cfg.chunk_size:
            if cur_end - chunk_start > 0:
                out.append(make_chunk(text, chunk_start, cur_end, len(out)))
                chunk_start = cur_end
            out.extend(append_oversize_block(text, cur_end, next_end, cfg, len(out)))
            cur_end = next_end
            chunk_start = next_end
            continue

        accumulated = next_end - chunk_start
        if accumulated > cfg.chunk_size and cur_end - chunk_start >= min_chunk_size:
            out.append(make_chunk(text, chunk_start, cur_end, len(out)))
            chunk_start = apply_overlap_aligned(text, cur_end, cfg.chunk_overlap, bounds)

        cur_end = next_end

    if cur_end > chunk_start:
        out.append(make_chunk(text, chunk_start, cur_end, len(out)))
    return resequence(out)


def apply_overlap_aligned(text: str, boundary_pos: int, overlap: int, bounds: Sequence[Boundary]) -> int:
    if overlap <= 0:
        return boundary_pos
    target = max(0, boundary_pos - overlap)
    candidates = [b.start for b in bounds if target <= b.start < boundary_pos]
    if candidates:
        return candidates[0]
    # Fall back to previous newline near target.
    nl = text.rfind("\n", target, boundary_pos)
    if nl >= 0:
        return nl + 1
    return target


def append_oversize_block(text: str, start: int, end: int, cfg: SplitterConfig, seq_start: int) -> List[Chunk]:
    local = split_text_legacy(text[start:end], cfg)
    out = []
    for i, c in enumerate(local):
        out.append(Chunk(c.content, seq_start + i, start + c.start, start + c.end, c.context_header))
    return out


def make_chunk(text: str, start: int, end: int, seq: int) -> Chunk:
    return Chunk(content=text[start:end], seq=seq, start=start, end=end)


# ---------------------------------------------------------------------------
# Legacy recursive splitter with protected spans
# ---------------------------------------------------------------------------

def protected_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for pat in PROTECTED_PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end()))
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def drop_bounds_inside_spans(bounds: Iterable[Boundary], spans: Sequence[Tuple[int, int]]) -> List[Boundary]:
    kept = []
    for b in bounds:
        inside = any(s < b.start < e for s, e in spans)
        if not inside:
            kept.append(b)
    return kept


def split_text_legacy(text: str, cfg: SplitterConfig) -> List[Chunk]:
    if not text:
        return []
    cfg = cfg.normalized()
    protected = protected_spans(text)
    units = build_units_with_protection(text, protected, cfg.separators, cfg.chunk_size)
    return merge_units(units, cfg.chunk_size, cfg.chunk_overlap)


def build_units_with_protection(
    text: str,
    protected: Sequence[Tuple[int, int]],
    separators: Sequence[str],
    chunk_size: int,
) -> List[SplitUnit]:
    max_protected_size = 7500
    units: List[SplitUnit] = []
    pos = 0

    def add_non_protected(segment: str, offset: int) -> None:
        for piece, p_start, p_end in split_by_separators_with_offsets(segment, separators, chunk_size):
            if piece:
                units.append(SplitUnit(piece, offset + p_start, offset + p_end))

    for s, e in protected:
        if s > pos:
            add_non_protected(text[pos:s], pos)

        prot = text[s:e]
        if len(prot) > max_protected_size:
            add_non_protected(prot, s)
        else:
            units.append(SplitUnit(prot, s, e))
        pos = e

    if pos < len(text):
        add_non_protected(text[pos:], pos)

    return units


def split_by_separators_with_offsets(
    text: str,
    separators: Sequence[str],
    chunk_size: int,
    base: int = 0,
) -> List[Tuple[str, int, int]]:
    if not text:
        return []
    if chunk_size > 0 and len(text) <= chunk_size:
        return [(text, base, base + len(text))]

    for idx, sep in enumerate(separators):
        if not sep or sep not in text:
            continue
        parts: List[Tuple[str, int, int]] = []
        start = 0
        while True:
            j = text.find(sep, start)
            if j == -1:
                if start < len(text):
                    piece = text[start:]
                    parts.extend(split_by_separators_with_offsets(piece, separators[idx + 1 :], chunk_size, base + start))
                break
            # Keep separator with the previous piece to preserve paragraphs/newlines/sentence punctuation.
            end = j + len(sep)
            piece = text[start:end]
            parts.extend(split_by_separators_with_offsets(piece, separators[idx + 1 :], chunk_size, base + start))
            start = end
        return parts

    # Hard split only as last resort.
    out = []
    step = max(1, chunk_size)
    for i in range(0, len(text), step):
        out.append((text[i : i + step], base + i, base + min(i + step, len(text))))
    return out


def merge_units(units: List[SplitUnit], chunk_size: int, chunk_overlap: int) -> List[Chunk]:
    if not units:
        return []
    absolute_max_size = 7500
    tracker = HeaderTracker()
    chunks: List[Chunk] = []
    current: List[SplitUnit] = []
    cur_len = 0

    for u in units:
        u_len = len(u.text)
        tracker.update(u.text)
        headers = tracker.get_headers()
        headers_len = len(headers)
        if headers_len > chunk_size:
            headers = ""
            headers_len = 0

        if current and cur_len + u_len + headers_len > chunk_size:
            chunks.append(build_chunk(current, len(chunks)))
            current, cur_len = compute_overlap(current, chunk_overlap, chunk_size, u_len)
            if headers and headers_len + u_len <= chunk_size:
                h = SplitUnit(headers, current[0].start if current else u.start, current[0].start if current else u.start)
                current = [h] + current
                cur_len += headers_len

        if u_len > absolute_max_size:
            # Extremely large unit should not blow up memory/context. Split it hard.
            for piece, ps, pe in split_by_separators_with_offsets(u.text, DEFAULT_SEPARATORS, chunk_size, u.start):
                pu = SplitUnit(piece, ps, pe)
                if current and cur_len + len(pu.text) > chunk_size:
                    chunks.append(build_chunk(current, len(chunks)))
                    current, cur_len = compute_overlap(current, chunk_overlap, chunk_size, len(pu.text))
                current.append(pu)
                cur_len += len(pu.text)
        else:
            current.append(u)
            cur_len += u_len

    if current:
        chunks.append(build_chunk(current, len(chunks)))
    return resequence(chunks)


def build_chunk(units: Sequence[SplitUnit], seq: int) -> Chunk:
    content = "".join(u.text for u in units)
    real_starts = [u.start for u in units if u.start != u.end]
    real_ends = [u.end for u in units if u.start != u.end]
    start = min(real_starts) if real_starts else units[0].start
    end = max(real_ends) if real_ends else units[-1].end
    return Chunk(content=content, seq=seq, start=start, end=end)


def compute_overlap(
    current: Sequence[SplitUnit],
    chunk_overlap: int,
    chunk_size: int,
    next_len: int,
) -> Tuple[List[SplitUnit], int]:
    if chunk_overlap <= 0 or not current:
        return [], 0
    selected: List[SplitUnit] = []
    total = 0
    max_allowed = max(0, min(chunk_overlap, chunk_size - next_len))
    for u in reversed(current):
        u_len = len(u.text)
        if selected and total + u_len > max_allowed:
            break
        if total + u_len > chunk_size // 2:
            break
        selected.append(u)
        total += u_len
    selected.reverse()
    return selected, total


class HeaderTracker:
    """Small table header tracker. Keeps table headers available across chunk boundaries."""

    def __init__(self) -> None:
        self.active_header = ""
        self.ended = False

    def update(self, split: str) -> None:
        if self.active_header:
            if not TABLE_ROW_PATTERN.search(split) or not split.strip():
                self.active_header = ""
                self.ended = True
                return
        if not self.active_header and not self.ended:
            if TABLE_HEADER_PATTERN.match(split):
                self.active_header = split

        if self.ended and TABLE_ROW_PATTERN.search(split):
            # Once table ended, stay ended until non-table text resets naturally.
            pass
        if self.ended and not TABLE_ROW_PATTERN.search(split):
            self.ended = False

    def get_headers(self) -> str:
        return self.active_header or ""


# ---------------------------------------------------------------------------
# Helpers for migration / retrieval simulation
# ---------------------------------------------------------------------------

def merge_breadcrumbs(parent: str, child: str) -> str:
    """Mirror WeKnora strategy.go::mergeBreadcrumbs exactly.

    When a parent is split again, the child heading detector commonly emits
    the parent's last breadcrumb line as the child's first line.  Remove only
    that duplicated seam; otherwise preserve both breadcrumb chains.
    """
    if not parent:
        return child
    if not child:
        return parent

    parent_lines = parent.split("\n")
    child_lines = child.split("\n")
    if (
        parent_lines
        and child_lines
        and parent_lines[-1].strip() == child_lines[0].strip()
    ):
        child_lines = child_lines[1:]

    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)


def resolve_parent_content(child: ChildChunk, parents: Sequence[Chunk]) -> str:
    """Simulate WeKnora's retrieval merge: child hit -> parent content for LLM context."""
    if child.parent_index >= 0 and child.parent_index < len(parents):
        return parents[child.parent_index].content
    return child.content


def chunks_to_jsonable(chunks: Sequence[Chunk]) -> List[dict]:
    return [asdict(c) for c in chunks]


def parent_child_to_jsonable(result: ParentChildResult) -> dict:
    return {
        "parents": chunks_to_jsonable(result.parents),
        "children": chunks_to_jsonable(result.children),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WeKnora-like chunker Python reference")
    p.add_argument("input", help="Markdown/text file to split")
    p.add_argument("--strategy", default=STRATEGY_AUTO, choices=[STRATEGY_AUTO, STRATEGY_HEADING, STRATEGY_HEURISTIC, STRATEGY_RECURSIVE, STRATEGY_LEGACY])
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    p.add_argument("--parent-child", action="store_true")
    p.add_argument("--parent-size", type=int, default=4096)
    p.add_argument("--child-size", type=int, default=384)
    p.add_argument("--title", default="")
    p.add_argument("--json", action="store_true", help="Print full JSON instead of compact summary")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    base = SplitterConfig(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap, strategy=args.strategy)
    if args.parent_child:
        parent_cfg, child_cfg = build_parent_child_configs(base, args.parent_size, args.child_size)
        result = split_parent_child(text, parent_cfg, child_cfg)
        if args.json:
            print(json.dumps(parent_child_to_jsonable(result), ensure_ascii=False, indent=2))
        else:
            print(f"parents={len(result.parents)} children={len(result.children)}")
            for c in result.children[:10]:
                emb = c.embedding_content(args.title)
                print(f"child#{c.seq} parent={c.parent_index} chars={len(c.content)} embedding_chars={len(emb)}")
        return

    chunks = split(text, base)
    if args.json:
        print(json.dumps(chunks_to_jsonable(chunks), ensure_ascii=False, indent=2))
    else:
        print(f"chunks={len(chunks)}")
        for c in chunks[:10]:
            emb = c.embedding_content(args.title)
            print(f"chunk#{c.seq} chars={len(c.content)} embedding_chars={len(emb)} header={bool(c.context_header)}")


if __name__ == "__main__":
    main()
