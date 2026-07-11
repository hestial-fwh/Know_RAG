# Phase AA Parse Evaluation Plugin

低耦合解析质量评测插件。它只读取 know_rag 已有的 Document、Chunk、ParseTaskEvent 等结果，不接管上传、解析、切片、向量化、检索主流程。

## 后端目录

```text
backend/app/plugins/parse_eval/
```

## 前端目录

```text
frontend/src/plugins/parse-eval/
```

## 数据表

```text
parse_eval_runs
parse_eval_items
```

## API

```text
GET  /api/v1/parse-eval/templates
POST /api/v1/parse-eval/documents/{doc_id}/run
GET  /api/v1/parse-eval/documents/{doc_id}/latest
GET  /api/v1/parse-eval/runs/{run_id}
GET  /api/v1/parse-eval/runs/{run_id}/items
GET  /api/v1/parse-eval/runs/{run_id}/report
```

## 当前模板

1. `generic_no_gt`：无 GT 通用规则诊断。
2. `scanned_contract_ocr`：扫描合同 OCR 模板评测。

## 解耦原则

- 不修改文档解析主链路。
- 不修改 chunk 入库逻辑。
- 不自动向量化。
- 不删除主业务数据。
- 后续升级评测模块时，主要替换本插件目录和少量挂载文件。
