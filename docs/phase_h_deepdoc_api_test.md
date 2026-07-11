# Phase H：DeepDoc 生产模型链路与 API 测试

本阶段在 Phase G 基础上继续增强：

## 1. DeepDoc 生产级模型接入

新增后端模块：

```text
backend/app/services/document_parser/deepdoc_runtime.py
```

支持以下模型资产：

| 资产 | 环境变量 | 默认搜索路径 |
|---|---|---|
| ONNX LayoutRecognizer | `DEEPDOC_LAYOUT_MODEL` | `/data/models/deepdoc/layout.onnx`、`layout.paper.onnx`、`layout/model.onnx` |
| ONNX TableStructureRecognizer | `DEEPDOC_TSR_MODEL` | `/data/models/deepdoc/tsr.onnx`、`table_structure.onnx`、`tsr/model.onnx` |
| XGBoost 文本框纵向合并模型 | `DEEPDOC_XGB_MODEL` | `/data/models/deepdoc/updown_concat_xgb.model`、`text_concat_xgb.model` |
| 外部 DeepDoc 服务 | `DEEPDOC_SERVICE_URL` | 例如 `http://host.docker.internal:9000` |

模型文件不会放进代码包。将模型文件放到 Docker volume：

```text
/data/models/deepdoc/
```

或通过环境变量指定绝对路径。

## 2. PDF 解析链路增强

`pdf_ragflow_advanced.py` 已接入 `DeepDocRuntime`：

```text
PDF 页面渲染
  ↓
ONNX LayoutRecognizer / 外部 DeepDoc layout 服务
  ↓
PDF 文本框按 layout 类型标注
  ↓
多栏阅读顺序恢复
  ↓
XGBoost 模型判断上下文本框是否纵向合并
  ↓
Table 区域裁剪
  ↓
ONNX TSR / 外部 DeepDoc tsr 服务
  ↓
按 row/column/header/spanning-cell 恢复表格 HTML
  ↓
Figure / Equation 区域裁剪
  ↓
Caption + 上下文绑定
```

若模型文件不存在，系统会自动回退到 Phase G 的启发式 RAGFlow-style 解析，不会影响上传文档。

## 3. API 配置测试功能

模型配置页面新增：

```text
测试 LLM API
测试 Embedding API
测试 Image2Text API
测试 OCR API
批量测试全部 API
刷新 DeepDoc 状态
```

后端新增接口：

```text
POST /api/v1/model-config/test/{model_type}
POST /api/v1/model-config/test
GET  /api/v1/model-config/deepdoc/status?reload=true
```

其中 `model_type` 支持：

```text
llm / embedding / image2text / ocr
```

## 4. Provider 切换联动修复

前端 `ModelConfigView.vue` 已修复：

- LLM、Embedding、Image2Text、OCR 都会在切换供应商时同步刷新默认 Base URL；
- 各类型模型名会跟随供应商的 `model_examples` 自动更新；
- 不再只有 VLM 下拉菜单生效。

## 5. Docker 环境变量

`docker-compose.yml` 已新增：

```yaml
DEEPDOC_MODEL_DIR: /data/models/deepdoc
DEEPDOC_LAYOUT_MODEL: ""
DEEPDOC_TSR_MODEL: ""
DEEPDOC_XGB_MODEL: ""
DEEPDOC_SERVICE_URL: ""
DEEPDOC_SCORE_THRESHOLD: 0.35
DEEPDOC_XGB_MERGE_THRESHOLD: 0.52
```

并新增模型 volume：

```text
know-rag-models:/data/models
```
