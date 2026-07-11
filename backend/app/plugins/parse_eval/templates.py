TEMPLATES = [
    {
        "id": "generic_no_gt",
        "name": "无 GT 通用规则诊断",
        "mode": "no_gt",
        "description": "不依赖标准答案，基于文本噪声、清洗破坏、chunk 可用性、图表/VLM 元数据做风险诊断。",
        "file_types": ["pdf", "docx", "ppt", "pptx", "xlsx", "csv", "md", "html", "json", "txt", "epub", "png", "jpg", "jpeg", "webp"],
        "needs_gt": False,
    },
    {
        "id": "scanned_contract_ocr",
        "name": "扫描合同 OCR 模板评测",
        "mode": "with_template",
        "description": "适用于 noisy/skewed scanned contract 测试 PDF，自动生成 page/line/invoice/callout 标准规则并统计命中率。",
        "file_types": ["pdf"],
        "needs_gt": True,
        "defaults": {"lines_per_page": 33, "callouts_per_page": 5},
    },
]


def get_template(template_id: str | None) -> dict:
    normalized = template_id or "generic_no_gt"
    for template in TEMPLATES:
        if template["id"] == normalized:
            return template
    return TEMPLATES[0]
