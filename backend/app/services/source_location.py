"""来源位置投影：只使用解析账本，不从正文页码或父块猜测子块位置。"""


def _positive_page(value):
    if isinstance(value, (bool, float)):
        return None
    try:
        page = int(value)
        return page if page > 0 else None
    except (TypeError, ValueError):
        return None


def project_source_location(start: int, end: int, nodes: list[dict], markdown: str | None = None) -> dict:
    """把规范化原文区间映射到来源块；bbox 始终是整块精度，不能冒充字符框。"""
    spans = []
    if type(start) is int and type(end) is int and 0 <= start < end:
        for node in nodes:
            left, right = node.get("normalized_start"), node.get("normalized_end")
            if type(left) is not int or type(right) is not int or not 0 <= left < right or not start < right or not end > left:
                continue
            spans.append({
                "source_node_id": node["node_id"], "page_no": _positive_page(node.get("page_no")),
                "start": max(start, left), "end": min(end, right),
                "coordinate_space": "normalized_canonical_markdown",
                "bbox": node.get("bbox"), "bbox_precision": "source_block" if node.get("bbox") else None,
                "bbox_coordinate_space": "parser_page" if node.get("bbox") else None,
                **({"page_label": node["page_label"]} if node.get("page_label") else {}),
            })
    pages = sorted({span["page_no"] for span in spans if span["page_no"] is not None})
    complete = bool(spans) and all(span["page_no"] is not None for span in spans)
    if complete and markdown is not None:
        # 未被账本覆盖的非空白正文说明定位不完整，不能只报其中已知的一页。
        cursor = start
        for span in sorted(spans, key=lambda span: span["start"]):
            if markdown[cursor:span["start"]].strip():
                complete = False
            cursor = max(cursor, span["end"])
        complete = complete and not markdown[cursor:end].strip()
    return {
        "source_pages": pages, "source_spans": spans,
        "page_no": pages[0] if complete and len(pages) == 1 else None,
        "page_range": format_page_range(pages),
        "page_location_status": "resolved" if complete else "partial" if pages else "unavailable",
    }


def format_page_range(pages: list[int]) -> str:
    """仅连续页合并为范围，2、4 不得显示为覆盖第3页的 2-4。"""
    if not pages:
        return ""
    groups, first, last = [], pages[0], pages[0]
    for page in pages[1:]:
        if page == last + 1:
            last = page
        else:
            groups.append(str(first) if first == last else f"{first}-{last}")
            first = last = page
    groups.append(str(first) if first == last else f"{first}-{last}")
    return "、".join(groups)


def location_fields(item: dict | None) -> dict:
    """统一 HTTP/Context 的单页及跨页输出；旧数据仅兼容显式页码字段。"""
    item = item if isinstance(item, dict) else {}
    meta = item.get("metadata") or item.get("metadata_json") or item.get("chunk_metadata") or {}
    sources = (item, meta, item.get("image_info") or {})
    for source in sources:
        if "source_pages" in source:
            pages = sorted({page for value in source["source_pages"] if (page := _positive_page(value))})
            status = source.get("page_location_status", "resolved" if pages else "unavailable")
            return {
                "source_pages": pages, "source_spans": source.get("source_spans") or [],
                "page_no": pages[0] if len(pages) == 1 and status in {"resolved", "legacy_page"} else None,
                "page_range": format_page_range(pages), "page_location_status": status,
            }
    for source in sources:
        for key in ("page_no", "source_page", "page", "page_idx", "page_index"):
            value = source.get(key)
            if key in {"page_idx", "page_index"} and value is not None and not isinstance(value, bool):
                try:
                    value = int(value) + 1 if int(value) >= 0 else None
                except (TypeError, ValueError):
                    value = None
            if page := _positive_page(value):
                return {"page_no": page, "page_range": str(page), "source_pages": [page],
                        "source_spans": [], "page_location_status": "legacy_page"}
    return {"page_no": None, "page_range": "", "source_pages": [],
            "source_spans": [], "page_location_status": "unavailable"}


def enrich_existing_locations(db, kb_id: str, results: list[dict], parent_groups: list[dict]) -> None:
    """只读补齐旧响应位置；必须匹配当前解析版本，失效账本不用于定位。"""
    from sqlalchemy import select
    from app.db.models import DocumentCanonicalArtifact

    pending = []

    def visit(value, document_id=""):
        if isinstance(value, list):
            for child in value:
                visit(child, document_id)
        elif isinstance(value, dict):
            document_id = value.get("document_id") or document_id
            if not document_id and value.get("matched_children"):
                document_id = value["matched_children"][0].get("document_id", "")
            meta = value.get("metadata") or value.get("metadata_json") or {}
            if value.get("chunk_id") and document_id and "source_pages" not in meta and meta.get("canonical_parse_revision") and meta.get("source_position_is_exact") is True:
                pending.append((value, document_id, meta))
            # 只遍历检索结构，禁止递归进入任意 metadata/原始账本。
            for key in ("context", "parent", "previous", "next", "parent_context", "parent_children",
                        "matched_children", "binding_related_chunks"):
                if key in value:
                    visit(value[key], document_id)

    visit(results)
    visit(parent_groups)
    if not pending:
        return
    artifacts = {a.document_id: a for a in db.scalars(select(DocumentCanonicalArtifact).where(
        DocumentCanonicalArtifact.knowledge_base_id == kb_id,
        DocumentCanonicalArtifact.document_id.in_({doc_id for _, doc_id, _ in pending}),
    ))}
    for item, document_id, meta in pending:
        artifact = artifacts.get(document_id)
        if artifact is None or meta["canonical_parse_revision"] != artifact.parse_revision:
            continue
        start, end = item.get("start_at"), item.get("end_at")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(artifact.canonical_markdown):
            continue
        meta = {**meta, **project_source_location(start, end, (artifact.provenance_json or {}).get("nodes", []), artifact.canonical_markdown)}
        for key in ("metadata", "metadata_json", "chunk_metadata"):
            if key in item:
                item[key] = meta
        item.update(location_fields({"metadata": meta}))
