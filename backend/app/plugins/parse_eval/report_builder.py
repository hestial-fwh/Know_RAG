from __future__ import annotations

from typing import Any


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "-"


def build_report(run: dict, items: list[dict]) -> str:
    summary = run.get("summary", {}) or {}
    scores = run.get("scores", {}) or {}
    metrics = scores.get("metrics", {}) if isinstance(scores.get("metrics"), dict) else {}
    lines = [
        f"# 解析质量评测报告",
        "",
        f"- 文档：{summary.get('file_name') or run.get('document_id')}",
        f"- 模板：{run.get('template_id')}",
        f"- 模式：{run.get('mode')}",
        f"- 综合评分：{run.get('score', 0):.1f} / 100",
        f"- 风险等级：{run.get('risk_level')}",
        f"- 建议：{run.get('recommendation')}",
        "",
        "## 核心指标",
        "",
    ]
    if metrics:
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and 0 <= value <= 1:
                lines.append(f"- {key}: {_percent(value)}")
            else:
                lines.append(f"- {key}: {value}")
    else:
        for key in ["chunk_count", "short_chunks", "noisy_chunks", "space_glued_chunks", "identifier_risk_chunks", "weak_tables"]:
            if key in summary:
                lines.append(f"- {key}: {summary.get(key)}")

    level_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in items:
        level_counts[item.get("issue_level", "info")] = level_counts.get(item.get("issue_level", "info"), 0) + 1
        type_counts[item.get("issue_type", "info")] = type_counts.get(item.get("issue_type", "info"), 0) + 1

    lines.extend(["", "## 问题统计", ""])
    lines.append(f"- 问题总数：{len(items)}")
    lines.append(f"- 按级别：{level_counts}")
    lines.append(f"- 按类型：{type_counts}")

    lines.extend(["", "## 代表性问题", ""])
    for item in items[:30]:
        lines.append(
            f"- [{item.get('issue_level')}] {item.get('issue_type')} · "
            f"{item.get('message')} "
            f"(target={item.get('target_type')}#{item.get('chunk_index') or item.get('target_id')})"
        )
        suggestion = item.get("suggestion")
        if suggestion:
            lines.append(f"  - 建议：{suggestion}")
    return "\n".join(lines).strip() + "\n"
