# Know_RAG 文档索引

> 状态：Current
> 维护日期：2026-07-29
> 适用范围：当前 `know_rag` 主分支

## 权威阅读顺序

1. 根目录 `README.md`：定位、快速启动和最常用命令。
2. `AGENTS.md`：AI/工程师修改边界和最低验证要求。
3. `docs/architecture.md`：当前服务拓扑与主数据流。
4. `know_rag_目录结构说明_最新维护版.md`：文件定位、真实调用链和维护陷阱。
5. `docs/api.md`、`docs/database.md`、`docs/security.md`、`docs/deployment.md`：专项现状。

发生冲突时，以当前代码、自动化测试、上述 Current 文档的顺序为准。

## 当前文档

- `architecture.md`：系统边界、服务拓扑、解析到检索的主链路。
- `backend.md`：后端模块与兼容说明。
- `frontend.md`：前端页面、API 客户端和质量命令。
- `api.md`：主要 API 约定；完整契约以 FastAPI `/docs` 为准。
- `database.md`：数据表、迁移、pgvector 与 Elasticsearch 关系。
- `security.md`：本地优先边界、远程供应商和 MCP 风险。
- `deployment.md`：构建、迁移、测试和服务检查。
- `backend_module_map.md`：后端主调用链地图。
- `elasticsearch_hybrid_search.md`：Elasticsearch BM25 与混合检索配置。
- `minio_storage.md`：MinIO 对象存储边界。
- `mineru_docker.md`：MinerU 容器部署说明。
- `image_chunk_binding_plan.md`：MinerU 主链路显式图文绑定 Current 契约，包含 P4 occurrence metadata、P5 scoped expansion 与双开关。
- `document_profile_parse_chunk_plan.md`：多模态文档融合的权威路线图，覆盖页面主视觉、页内多作品
  区域与文本归属、内容来源、多轴画像、MinerU block 级 OCR 生产投影、差异化切分、字段级索引
  准入、前端验收和自动 replay；
  DCP-0～3、DCP-4R、DCP-0.1、DCP-1.1、DCP-2.1 已通过，DCP-4 初版保留为 FAIL 记录，
  下一阶段为 DCP-3.1。
- `pipeline_audit.md`：文档解析与切片流水线健壮性及可用性审计报告，包含 SPOF 分析、风险矩阵和优先修复建议。
- `document_content_view_plan.md`：文档内容查看页面（DocumentContentView）规划，从 MinIO 直接展示原始文档（PDF/图片/文本内联渲染），支持下载。
- `document_relation_graph_feasibility.md`：通用文档关系层的代码审查、问题清单、可行性评级、目标架构与统一数据模型（Draft）。
- `document_relation_graph_phased_plan.md`：关系图 Phase 0-6 的依赖、开关、测试、迁移、回滚与
  PASS/FAIL 计划；Phase 3 已通过，Phase 4 的代码前置条件已满足，但按多模态融合 DCP-7 门禁暂缓。
- `document_relation_graph_acceptance_tests.md`：18 类固定标注样例、关系状态/evidence 契约与分层验收矩阵；合成契约已冻结。
- `document_relation_phase0_baseline.md`：160 页确定性生成语料、五层 replay 与覆盖率；现包含 Phase 3 group/shared 合成指标。
- `backend/app/integrations/mcp/README.md`：MCP 工具与传输层契约。
- `backend/app/plugins/parse_eval/README.md`：解析质量评测插件。

- `explain/document_classification_pipeline.md`：文档类型判断全链路，从 PDF 视觉分析到三条切分路径的完整决策流程。
- `visual_section_cross_page_binding_fix.md`：视觉类文档跨页绑定修复方案，三 Pass 算法设计（同页分类→跨页孤儿绑定→统一物化）。

已完成的 Phase 记录、一次性修复日志和重复架构报告不在仓库中长期保留；历史变更通过 Git
提交记录追溯。

## 文档维护规则

- 修改运行时主链路时，同步更新架构和目录说明。
- 修改 API 返回字段时，同步更新 API 文档、前端类型和测试。
- 修改安全边界时，同步更新 `security.md` 与 `.env.example`。
- 文档命令必须可以在标准镜像或声明的开发环境中执行。
