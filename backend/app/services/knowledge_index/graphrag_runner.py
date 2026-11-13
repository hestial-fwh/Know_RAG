"""仅由隔离解释器运行的官方2.7.0适配；stdin/stdout为有界JSON，不依赖主应用包。"""

import contextlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile


def main():
    if version("graphrag") != "2.7.0":
        raise RuntimeError("仅支持graphrag==2.7.0")
    snapshot = json.load(sys.stdin)
    import pandas as pd

    # title使用稳定实体ID而非同名字符串，防止官方基于title建图时合并不同scope。
    entities = pd.DataFrame([{"id": node["id"], "title": node["id"], "type": node["type"],
        "description": node["name"]} for node in snapshot["entities"]])
    relationships = pd.DataFrame([{"id": edge["id"], "source": edge["subject_id"],
        "target": edge["object_id"], "weight": 1.0, "text_unit_ids": [edge["chunk_id"]]}
        for edge in snapshot["relations"]])
    text_units = pd.DataFrame([{"id": source["chunk_id"], "text": source["content"],
        "document_id": source["document_id"], "parse_revision": source["parse_revision"]}
        for source in snapshot["sources"]])
    with contextlib.redirect_stdout(sys.stderr):
        from graphrag.index.workflows.create_communities import create_communities

        # 官方默认存储是Parquet；真实往返以验证schema和列表字段，不重切分原文。
        with tempfile.TemporaryDirectory(prefix="know-rag-graphrag-") as directory:
            root = Path(directory)
            for name, table in (("entities", entities), ("relationships", relationships), ("text_units", text_units)):
                table.to_parquet(root / f"{name}.parquet", index=False)
            result = create_communities(pd.read_parquet(root / "entities.parquet"),
                pd.read_parquet(root / "relationships.parquet"),
                max_cluster_size=snapshot["settings"]["max_members"], use_lcc=False, seed=0xDEADBEEF)
            result.to_parquet(root / "communities.parquet", index=False)
            rows = json.loads(pd.read_parquet(root / "communities.parquet").to_json(orient="records"))
    communities = [{"key": str(row["community"]), "level": int(row["level"]),
        "entity_ids": row["entity_ids"], "relationship_ids": row["relationship_ids"],
        "text_unit_ids": row["text_unit_ids"]} for row in rows if row["level"] == snapshot["settings"]["level"]]
    print(json.dumps({"version": "2.7.0", "communities": communities}))


if __name__ == "__main__":
    main()
