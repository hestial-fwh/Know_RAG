"""规范台账的关系按同一行生成，原文引文与时间限定可复验。"""

from app.services.knowledge_index.table_claims import table_claims
from app.services.knowledge_index.claims import ground_claims


def test_table_rows_keep_entity_pairs_column_predicates_and_dates():
    source = "| 单位 | 对应专业 | 协议期限 | 容量 |\n| --- | --- | --- | --- |\n| 星河公司 | 机械制造专业 | 2024-03-01至2027-02-28 | 40 |\n| 云岭公司 | 人工智能专业 | 2025-07-01至2028-06-30 | 30 |\n"
    raw = table_claims(source)
    claims = ground_claims(source, raw, ["relations"])
    assert len(claims["relations"]) == 2
    pairs = [(claims["entities"][edge["subject"]]["name"], claims["entities"][edge["object"]]["name"])
             for edge in claims["relations"]]
    assert pairs == [("星河公司", "机械制造专业"), ("云岭公司", "人工智能专业")]
    assert claims["relations"][1]["qualifiers"]["time"] == "2025-07-01至2028-06-30"
    assert all(edge["quote"] in source for edge in claims["relations"])


def test_negative_responsibility_header_keeps_negative_polarity():
    source = "| 部门 | 不承担的事项 |\n| --- | --- |\n| 教务处 | 不直接发放奖助学金 |\n"
    raw = table_claims(source)
    claims = ground_claims(source, raw, ["relations"])
    assert claims["relations"][0]["qualifiers"]["polarity"] == "negated"


def test_numeric_subjects_and_ambiguous_single_columns_use_model_path():
    assert table_claims("| 年份 | 金额 |\n| --- | --- |\n| 2025 | 30 |\n") is None
    assert table_claims("| 事项 责任方 |\n| --- |\n| 审核 学院 |\n") is None
