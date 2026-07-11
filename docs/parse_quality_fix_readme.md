# Parse Quality Fix3 README

本次修复回到 Fix1 的稳定基线，并在其基础上实现 OCR 分层保真。

## 修复原则

1. 保留 Fix1 中有效的能力：
   - PDF table 强弱分类；
   - 扫描 PDF 生成 OCR text chunk；
   - chunk `page_no` / `source_page` 推断；
   - parse_eval 基础评测规则。

2. 撤销 Fix2 中可能造成回退的做法：
   - 不再把 OCR 字段级纠错结果直接写回 `chunk.content`；
   - 不再用 aggressive normalized text 替代原始 OCR 或轻清洗文本。

3. 新增 OCR 分层保存：
   - `ocr_raw_text`：DeepDoc/OCR 原始文本，不做纠错；
   - `ocr_cleaned_text`：轻量清洗文本，只修复明显空格/分隔符问题；
   - `ocr_normalized_text`：字段级归一化文本，仅用于评测可恢复命中，不用于覆盖入库正文；
   - `vlm_description`：VLM 图片描述，与 OCR 文本分开保存。

## 主要修改文件

```text
backend/app/services/chunk_service.py
backend/app/services/document_parser/ragflow_format_adapter.py
backend/app/services/vision_service.py
backend/app/plugins/parse_eval/diagnostics.py
backend/tests/test_parse_quality_fix.py
docs/parse_quality_fix_readme.md
```

## 1. chunk_service.py

### 1.1 OCR 分层写入

针对扫描 PDF 中 DeepDoc 输出为普通 text block 的情况，系统会识别 OCR-like 文本，并写入：

```json
{
  "ocr_raw_text": "OCR 原始文本",
  "ocr_cleaned_text": "轻清洗文本",
  "ocr_normalized_text": "字段级归一化文本，仅用于评测",
  "ocr_text": "兼容字段",
  "ocr_layered": true
}
```

### 1.2 content 只使用轻清洗结果

`chunk.content` 使用 `ocr_cleaned_text` 或原始文本，不使用 `ocr_normalized_text`。

这样可以保证：

```text
content 用于真实入库和人工复查；
ocr_normalized_text 只用于评测和字段恢复判断；
不会为了提高评测分数而破坏原始 OCR 证据。
```

### 1.3 扫描 PDF 继续生成 text chunk

如果 image block 或 OCR block 中存在 OCR 文本，仍会生成 text chunk，用于精确检索：

```text
INV-001-00
Raster-only OCR text line
OCR-CANDIDATE
SCAN-CALLOUT
```

同时保留 image chunk 用于图片预览和 VLM 多模态理解。

## 2. parse_eval diagnostics.py

### 2.1 严格命中与可恢复命中分离

扫描合同模板评测现在区分两类指标：

```text
严格命中：看 chunk.content + ocr_raw_text + ocr_cleaned_text；
可恢复命中：只看 ocr_normalized_text。
```

对应指标：

```text
page_recognition_rate
page_title_strict_rate
line_id_rate
line_id_recoverable_rate
invoice_rate
invoice_recoverable_rate
callout_rate
callout_recoverable_rate
```

### 2.2 含义

- `*_rate` 表示 OCR/清洗后的真实可用质量；
- `*_recoverable_rate` 表示经过字段级归一化后可以恢复的比例；
- 如果 recoverable 明显高于 strict，说明 OCR 存在 0/O、1/I/l、5/S 等可纠错问题；
- 如果两者都低，说明 OCR 原始识别质量不足，需要更强 OCR 或调整图像预处理。

## 3. generic_no_gt 诊断调整

`space_gluing`、`identifier_format_risk`、`ocr_noise` 优先基于最终 `chunk.content` 判断。

原因：metadata 中会保留 raw OCR 调试信息，raw OCR 中可能存在粘连，但这不一定代表最终入库文本仍然粘连。

## 4. 更新后需要重新解析

本次修改影响 chunk 生成和 metadata 写入，旧文档旧 chunk 不会自动变化。

更新后需要重新解析：

```text
06_noisy_skewed_scanned_contract_25p.pdf
14_chinese_english_mixed_policy_20p.pdf
```

然后重新运行评测模板。

## 5. 推荐验证命令

```bat
docker compose exec backend python -c "from app.db.session import SessionLocal; from app.db.models import Document, Chunk; db=SessionLocal(); doc=db.query(Document).filter(Document.file_name.like('%06_noisy_skewed%')).order_by(Document.created_at.desc()).first(); print('DOC', doc.id, doc.file_name); rows=db.query(Chunk).filter(Chunk.document_id==doc.id).order_by(Chunk.chunk_index).limit(30).all(); [print('\\nIDX', c.chunk_index, 'TYPE', c.chunk_type, 'PAGE', (c.metadata_json or {}).get('page_no'), '\\nCONTENT:', (c.content or '')[:500], '\\nOCR_RAW:', ((c.metadata_json or {}).get('ocr_raw_text') or '')[:300], '\\nOCR_CLEAN:', ((c.metadata_json or {}).get('ocr_cleaned_text') or '')[:300], '\\nOCR_NORM:', ((c.metadata_json or {}).get('ocr_normalized_text') or '')[:300]) for c in rows]"
```

应能看到：

```text
CONTENT：轻清洗后的入库文本；
OCR_RAW：原始 OCR 文本；
OCR_CLEAN：轻清洗文本；
OCR_NORM：字段级归一化文本。
```

## 6. 更新命令

```bat
cd D:\know_rag

docker compose build backend
docker compose up -d --force-recreate backend
docker compose exec backend alembic upgrade head
```

前端未修改时一般不需要重建 frontend。

## Fix5 回滚说明

Fix4 尝试在清洗和评测层进一步处理空格粘连，但实际测试显示 line_id_rate、invoice_rate、callout_rate 明显下降。因此 Fix5 回到 Fix3 的稳定基线：

- 保留 Fix1/Fix3 中有效的 table 强弱分类、扫描 PDF OCR text chunk、page_no 推断；
- 保留 OCR raw / clean / normalized 分层思路；
- 撤回 Fix4 中会影响扫描合同模板召回的额外 spacing/recovery 规则；
- 后续优化应以抽样 chunk 证据为依据，避免为了降低 space_gluing 指标而破坏 line/invoice/callout 命中。

当前建议：先以 Fix3/Fix5 作为稳定基线，再针对 invoice 字段单独做可选辅助索引，而不要覆盖 chunk.content。
