# Phase G：RAGFlow-style 文档处理 + WeKnora-style 管理/API/入库

本阶段目标：

- 文档处理向 RAGFlow 看齐：PDF 走 layout/table/figure/caption/context 的多模态结构化解析思路。
- 前端、API 配置、数据入库、管理方式向 WeKnora 看齐：统一 provider/base_url/api_key/model_name 配置风格。
- 向量库可切换：PostgreSQL/pgvector、Qdrant、Milvus/Zilliz。

## 已实现能力

### 1. PDF 高级解析

入口仍为 `parse_pdf_ragflow_lite()`，但 Phase G 默认委托到：

```text
backend/app/services/document_parser/pdf_ragflow_advanced.py
```

实现内容：

- LayoutRecognizer-compatible：识别 text/title/equation/header/footer/figure caption/table caption。
- TableStructureRecognizer-compatible：表格提取、HTML 输出、简单 colspan 还原、表格区域裁剪。
- Figure 裁剪：从 PDF 图片区域裁剪 figure image chunk。
- Figure/Table caption 绑定：基于近邻 caption 和 bbox 位置绑定。
- 复杂多栏阅读顺序：按页检测单双栏，按 column/top/x 排序。
- XGBoost 纵向拼接兼容点：当前为启发式合并，元数据标记为 `heuristic_xgb_compatible`，后续可替换为真实 xgboost model。
- 图文上下文绑定：为 image/table block 写入 context_above/context_below/context_header。
- 扫描 PDF 兜底：无文本时渲染整页图片，由 OCR API 后续增强。

### 2. OCR / VLM 失败重试和状态

`vision_service.py` 新增 retry 包装：

- OCR 默认最多重试 3 次。
- Image2Text 默认最多重试 3 次。
- 每个 block 写入 `ocr_status` / `vlm_status` / error 信息。
- 文档详情页显示 OCR/VLM 失败数。

### 3. WeKnora-style 模型 API 配置

模型配置页支持以下预选供应商和默认 Base URL：

- Ollama
- OpenAI-Compatible
- OpenAI
- DeepSeek
- 阿里云百炼 / DashScope
- SiliconFlow
- 智谱 AI
- 腾讯混元
- 火山引擎 Ark
- xAI
- StepFun
- PaddleOCR / 自定义 OCR
- MinerU
- Custom

配置项按模型类型拆分：

- LLM
- Embedding
- Image2Text
- OCR

每类均支持 provider / base_url / api_key / model_name。

### 4. 向量数据库切换

新增：

```text
backend/app/services/vector_store_adapters.py
```

支持：

- PostgreSQL / pgvector：默认，本地元数据与向量备份。
- Qdrant：可选外部向量索引。
- Milvus / Zilliz：可选外部向量索引。

前端 `向量库配置` 页面支持选择 provider、填入 JSON 配置、测试连接。

注意：切换向量库后，需要重新向量化文档或知识库，才会把已有切片同步到新的外部向量库。

### 5. 前端完整图片预览

- 切片列表图片缩略图可点击预览。
- 切片详情图片可点击预览。
- 预览弹窗显示大图和 image_info 元数据。

## Qdrant 启动

默认 compose 不启动 Qdrant。需要时执行：

```bash
docker compose --profile qdrant up -d --build
```

然后在前端向量库配置中选择 Qdrant，默认 URL：

```text
http://qdrant:6333
```

## Milvus/Zilliz

Milvus standalone 依赖较多，未默认加入 compose。可在前端配置：

```json
{
  "uri": "http://milvus:19530",
  "token": "",
  "collection": "know_rag_chunks",
  "metric_type": "COSINE",
  "fallback_to_postgres": true,
  "strict_external_index": false
}
```

## 说明

当前 PDF 高级解析为 RAGFlow/DeepDoc 思路的项目内实现，不强依赖 RAGFlow 的完整工程依赖。真正的 DeepDoc ONNX layout/TSR/XGBoost 模型可在后续替换同一接口。
