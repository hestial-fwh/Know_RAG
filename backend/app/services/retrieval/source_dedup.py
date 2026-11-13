"""旧表格副本的召回兜底：以精确来源归一化，不对相似内容做语义去重。"""

from copy import deepcopy


def source_key(item: dict) -> tuple:
    """缺少来源版本、准确区间或内容指纹时退回 chunk ID，避免误删独立表格。"""
    meta = item.get("metadata") or item.get("metadata_json") or {}
    start, end = item.get("start_at"), item.get("end_at")
    if (
        item.get("chunk_type") == "table" and meta.get("source_position_is_exact") is True
        and item.get("knowledge_base_id") and item.get("document_id")
        and meta.get("canonical_parse_revision") and item.get("content_hash")
        and type(start) is int and type(end) is int and 0 <= start < end
    ):
        return (
            "table", item["knowledge_base_id"], item["document_id"],
            meta["canonical_parse_revision"], start, end, item["content_hash"],
            tuple(sorted(meta.get("source_node_ids") or [])),
        )
    return ("chunk", item["chunk_id"])


def normalize_channels(*channels: list[dict]) -> tuple[list[list[dict]], dict[str, str]]:
    """每路同源只保留最佳名次，各路使用同一代表 ID；保留旧 ID 供图路径规范化。"""
    representatives, aliases, source_ids = {}, {}, {}
    for channel in channels:
        for item in channel:
            key = source_key(item)
            representatives.setdefault(key, item)
            ids = source_ids.setdefault(key, [])
            for chunk_id in [item["chunk_id"], *(item.get("source_chunk_ids") or [])]:
                aliases[chunk_id] = representatives[key]["chunk_id"]
                if chunk_id not in ids:
                    ids.append(chunk_id)
    normalized = []
    for channel in channels:
        output, seen = [], {}
        for item in channel:
            key = source_key(item)
            if key not in seen:
                canonical = dict(representatives[key])
                # 分数/名次属于本通道，其余来源信息始终跟随代表 Chunk，避免 ID 与 parent 不一致。
                channel_fields = (
                    "score", "vector_score", "vector_rank", "keyword_score", "keyword_rank",
                    "keyword_raw_score", "keyword_backend", "match_type", "retrieval_sources",
                    "rrf_score", "rerank_score", "vector_original_rank", "keyword_original_rank",
                )
                for field in channel_fields:
                    canonical.pop(field, None)
                for field in channel_fields:
                    if field in item:
                        canonical[field] = item[field]
                canonical["source_chunk_ids"] = list(source_ids[key])
                canonical.pop("association_paths", None)
                seen[key] = canonical
                output.append(canonical)
            if item.get("association_paths"):
                paths = seen[key].setdefault("association_paths", [])
                for path in item["association_paths"]:
                    if path not in paths:
                        paths.append(deepcopy(path))
        normalized.append(output)
    return normalized, aliases


def remap_path(path: dict, aliases: dict[str, str]) -> dict:
    """完整证据链与候选使用相同的来源 ID，原始步骤 ID 留作可追溯信息。"""
    result = deepcopy(path)
    result["support_chunk_ids"] = list(dict.fromkeys(
        aliases.get(chunk_id, chunk_id) for chunk_id in path["support_chunk_ids"]
    ))
    for step in result.get("steps", []):
        chunk_id = step.get("chunk_id")
        if chunk_id in aliases and aliases[chunk_id] != chunk_id:
            step["source_chunk_id"] = chunk_id
            step["chunk_id"] = aliases[chunk_id]
    return result
