"""社区provider边界：本地确定性分区与固定版本官方Leiden分别记录。"""

import json
import os
from pathlib import Path
import subprocess


def cluster_graph(snapshot: dict) -> list[dict]:
    config = snapshot["settings"]
    if config["provider"] == "microsoft_graphrag_2_7_0":
        executable = os.environ.get("KNOW_RAG_GRAPHRAG_PYTHON")
        if not executable:
            raise RuntimeError("官方provider需要隔离GraphRAG运行环境")
        # 固定脚本和参数，不执行请求提供的命令，不让外部产物路径进入shell。
        result = subprocess.run([executable, str(Path(__file__).with_name("graphrag_runner.py"))],
            input=json.dumps(snapshot), capture_output=True, text=True, timeout=180, check=False,
            env={**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "HF_HUB_OFFLINE": "1",
                 "OTEL_SDK_DISABLED": "true"})
        if result.returncode:
            raise RuntimeError("官方社区构建失败；请检查隔离运行环境")
        payload = json.loads(result.stdout)
        if payload.get("version") != "2.7.0":
            raise ValueError("官方provider版本不匹配")
        communities = payload["communities"]
    else:
        # 不引入图依赖：连通分量内按确定性BFS分区。明确区别于官方层次Leiden。
        adjacency = {node["id"]: set() for node in snapshot["entities"]}
        for edge in snapshot["relations"]:
            adjacency[edge["subject_id"]].add(edge["object_id"])
            adjacency[edge["object_id"]].add(edge["subject_id"])
        remaining, communities = set(adjacency), []
        while remaining:
            queue, members = [min(remaining)], []
            while queue and len(members) < config["max_members"]:
                node = queue.pop(0)
                if node not in remaining:
                    continue
                remaining.remove(node)
                members.append(node)
                queue.extend(sorted(adjacency[node] & remaining))
            member_set = set(members)
            edges = [edge for edge in snapshot["relations"]
                     if {edge["subject_id"], edge["object_id"]} <= member_set]
            communities.append({"key": f"native-{len(communities)}", "level": 0,
                "entity_ids": members, "relationship_ids": [edge["id"] for edge in edges],
                "text_unit_ids": sorted({edge["chunk_id"] for edge in edges})})
    validate_communities(snapshot, communities)
    return communities


def validate_communities(snapshot: dict, communities: list) -> None:
    """导入和发布端共用schema校验，拒绝未知成员/来源及重复社区。"""
    entity_ids = {node["id"] for node in snapshot["entities"]}
    edges = {edge["id"]: edge for edge in snapshot["relations"]}
    sources = {source["chunk_id"] for source in snapshot["sources"]}
    if not isinstance(communities, list) or len(communities) > 10000:
        raise ValueError("社区产物结构无效")
    seen = set()
    for community in communities:
        if community["key"] in seen:
            raise ValueError("社区ID重复")
        seen.add(community["key"])
        members = set(community["entity_ids"])
        if not members or not members <= entity_ids or not set(community["text_unit_ids"]) <= sources:
            raise ValueError("社区包含未知实体或text unit")
        for edge_id in community["relationship_ids"]:
            edge = edges.get(edge_id)
            if edge is None or not {edge["subject_id"], edge["object_id"]} <= members:
                raise ValueError("社区关系与成员不一致")
            if edge["chunk_id"] not in community["text_unit_ids"]:
                raise ValueError("社区关系缺少来源映射")
