"""Minimal RAGFlow NLP compatibility layer for know_rag.

Only functions required by the vendored DeepDoc/parser modules are implemented
here.  know_rag keeps its own chunking, embedding and retrieval pipeline; this
module exists only to satisfy RAGFlow parser-side imports.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

import chardet
from PIL import Image

try:
    from common.token_utils import num_tokens_from_string
except Exception:  # pragma: no cover
    def num_tokens_from_string(text: str) -> int:
        return max(1, len(str(text or "")) // 2)


class _Tokenizer:
    def tokenize(self, text: Any) -> str:
        return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", str(text or "")))

    def fine_grained_tokenize(self, text: Any) -> str:
        return self.tokenize(text)

    def tag(self, token: Any) -> str:
        return "n"

    def is_chinese(self, ch: str) -> bool:
        return bool(ch) and "\u4e00" <= ch[0] <= "\u9fff"


rag_tokenizer = _Tokenizer()

all_codecs = [
    "utf-8", "gb2312", "gbk", "gb18030", "utf_16", "utf_16_le", "utf_16_be",
    "utf_32", "ascii", "big5", "big5hkscs", "latin_1", "cp936", "cp950",
    "cp1250", "cp1251", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256",
    "cp1257", "cp1258", "shift_jis", "euc_jp", "euc_kr", "iso8859_2",
    "iso8859_3", "iso8859_4", "iso8859_5", "iso8859_6", "iso8859_7",
    "iso8859_8", "iso8859_9", "windows-1250", "windows-1251", "windows-1252",
]


def find_codec(blob: bytes | bytearray | str | None) -> str:
    """Best-effort encoding detection compatible with RAGFlow parsers."""
    if blob is None:
        return "utf-8"
    if isinstance(blob, str):
        return "utf-8"
    data = bytes(blob)
    try:
        detected = chardet.detect(data[:4096] or data)
        enc = detected.get("encoding")
        if enc and (detected.get("confidence") or 0) >= 0.5:
            return "utf-8" if enc.lower() == "ascii" else enc
    except Exception:
        pass
    for codec in all_codecs:
        try:
            data.decode(codec)
            return codec
        except Exception:
            continue
    return "utf-8"


def _as_pil(img: Any):
    if img is None:
        return None
    try:
        from rag.utils.lazy_image import LazyImage, ensure_pil_image
        if isinstance(img, LazyImage):
            return img
        pil = ensure_pil_image(img)
        if pil is not None:
            return pil
    except Exception:
        pass
    return img


def concat_img(img1: Any, img2: Any):
    """Vertically concatenate two PIL/LazyImage instances.

    RAGFlow uses this helper to merge related images.  The implementation keeps
    LazyImage behavior when both inputs are lazy and otherwise returns a PIL RGB
    image.
    """
    try:
        from rag.utils.lazy_image import LazyImage
        if (img1 is None or isinstance(img1, LazyImage)) and (img2 is None or isinstance(img2, LazyImage)):
            if img1 and not img2:
                return img1
            if img2 and not img1:
                return img2
            if not img1 and not img2:
                return None
            return LazyImage.merge(img1, img2)
    except Exception:
        pass

    img1 = _as_pil(img1)
    img2 = _as_pil(img2)
    if img1 is None:
        return img2
    if img2 is None:
        return img1
    if img1 is img2:
        return img1
    if not isinstance(img1, Image.Image) or not isinstance(img2, Image.Image):
        return img1
    if img1.mode != "RGB":
        img1 = img1.convert("RGB")
    if img2.mode != "RGB":
        img2 = img2.convert("RGB")
    try:
        if img1.size == img2.size and img1.tobytes() == img2.tobytes():
            return img1
    except Exception:
        pass
    width = max(img1.width, img2.width)
    height = img1.height + img2.height
    merged = Image.new("RGB", (width, height), (255, 255, 255))
    merged.paste(img1, (0, 0))
    merged.paste(img2, (0, img1.height))
    return merged


def _clean_position_tags(text: str) -> str:
    return re.sub(r"@@[0-9\-]+\t[0-9.\t]+##", "", str(text or "")).strip()


def _parse_section_positions(item: Any):
    """Return (text, positions) from RAGFlow section variants."""
    if isinstance(item, (tuple, list)):
        txt = item[0] if item else ""
        poss = item[2] if len(item) > 2 else (item[1] if len(item) > 1 else "")
    else:
        txt = item
        poss = ""
    if isinstance(poss, list):
        positions = poss
    else:
        raw = str(poss or txt or "")
        matches = re.findall(r"@@([0-9\-]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##", raw)
        positions = []
        for page, left, right, top, bottom in matches:
            try:
                positions.append((int(str(page).split("-")[0]), float(left), float(right), float(top), float(bottom)))
            except Exception:
                continue
    return _clean_position_tags(str(txt or "")), positions


def append_context2table_image4pdf(sections: list, tabls: list, table_context_size: int = 0, return_context: bool = False):
    """Attach nearby section text to PDF table/image items.

    This is a compact compatibility implementation of RAGFlow's context-binding
    helper.  It keeps the original table/image structure and appends/returns
    surrounding text for better caption/context quality.
    """
    if not tabls:
        return [] if return_context else tabls
    if table_context_size is None or table_context_size <= 0:
        return ["" for _ in tabls] if return_context else tabls

    page_bucket: dict[int, list[tuple[tuple[float, float, float, float], str]]] = defaultdict(list)
    for item in sections or []:
        text, positions = _parse_section_positions(item)
        if not text:
            continue
        for pos in positions or []:
            try:
                page, left, right, top, bottom = pos[:5]
                page_bucket[int(page)].append(((float(left), float(right), float(top), float(bottom)), text))
            except Exception:
                continue
    for page in list(page_bucket):
        page_bucket[page].sort(key=lambda x: (x[0][2], x[0][0]))

    def _context_for(positions: list | tuple | None) -> str:
        if not positions:
            return ""
        try:
            first = positions[0]
            page = int(first[0])
            top = float(first[3])
            bottom = float(first[4])
        except Exception:
            return ""
        candidates = []
        for box, text in page_bucket.get(page, []):
            _left, _right, btop, bbottom = box
            # Prefer text immediately above and below the table/figure.
            distance = min(abs(bottom - btop), abs(top - bbottom))
            candidates.append((distance, text))
        candidates.sort(key=lambda x: x[0])
        ctx = ""
        for _dist, text in candidates[:8]:
            if text and text not in ctx:
                ctx = (ctx + "\n" + text).strip()
            if num_tokens_from_string(ctx) >= table_context_size:
                break
        return ctx

    contexts = []
    output = []
    for item in tabls:
        try:
            payload, positions = item
        except Exception:
            payload, positions = item, []
        ctx = _context_for(positions)
        contexts.append(ctx)
        if ctx and not return_context:
            try:
                img_text = payload[1]
                if isinstance(img_text, str):
                    payload = (payload[0], f"{ctx}\n{img_text}".strip())
                elif isinstance(img_text, list):
                    payload = (payload[0], [ctx] + img_text)
            except Exception:
                pass
        output.append((payload, positions) if isinstance(item, (tuple, list)) else item)
    return contexts if return_context else output


_COMMON_SURNAMES = set("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")


def surname(name: Any) -> str:
    """Small stub used by the optional resume parser."""
    text = str(name or "").strip()
    if not text:
        return ""
    if text[0] in _COMMON_SURNAMES:
        return text[0]
    return text[:1]


__all__ = [
    "rag_tokenizer",
    "find_codec",
    "concat_img",
    "append_context2table_image4pdf",
    "surname",
]
