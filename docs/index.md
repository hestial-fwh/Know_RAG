# Know_RAG 文档索引

> 状态：Current
> 维护日期：2026-07-15
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

## 历史与专项记录

名称含 `phase_`、`analysis`、`report`、`fix`、`optimization` 的文档用于追溯设计与修复背景，
不作为当前能力清单。历史文档必须保留时，应在顶部标注 `Historical` 或 `Superseded`。

## 文档维护规则

- 修改运行时主链路时，同步更新架构和目录说明。
- 修改 API 返回字段时，同步更新 API 文档、前端类型和测试。
- 修改安全边界时，同步更新 `security.md` 与 `.env.example`。
- 文档命令必须可以在标准镜像或声明的开发环境中执行。
