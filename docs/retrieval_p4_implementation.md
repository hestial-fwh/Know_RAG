# P4 社区报告与Global检索

> 2026-11-05；实现与验收记录。测试通过情况以本文末尾的验证记录为准。

## 实现边界

P4提供KB级图revision任务、版本化社区、带原文引文的派生报告，以及Global map-reduce检索。
Global先处理社区报告，再回查支持原文，报告ID不进入切片RRF。最终回答仍由调用方模型生成。
原有semantic/hybrid、Local和事件检索继续使用原有预算、精确表格去重及来源页码逻辑。

两种社区provider独立记录：

| provider | 实际使用的算法 | 报告和查询 |
|---|---|---|
| native | 有界连通分量/BFS确定性分区，level=0 | Know_RAG带证据报告与Global map-reduce |
| microsoft_graphrag_2_7_0 | 官方2.7.0 create_communities/层次Leiden，输入输出真实Parquet往返 | Know_RAG带证据报告与Global map-reduce |

官方选项只适配其社区构建工作流，不宣称运行了官方全套索引、报告生成或GlobalSearch类。
原文不被官方引擎重新切分。实体title使用稳定实体ID，防止不同scope的同名实体被合并；
text unit始终映射chunk_id、document_id和parse_revision。未知成员、关系、来源或版本拒绝发布。
官方默认产物和社区字段参考[官方Outputs](https://microsoft.github.io/graphrag/index/outputs/)，
Global的社区报告map-reduce路线参考[官方Global说明](https://microsoft.github.io/graphrag/query/global_search/)。

## 配置与使用

知识库profile示例：

```json
{
  "version":"new_profile_v2",
  "build_indexes":["entities","relations","communities"],
  "community":{"provider":"native","max_communities":16,"max_members":50,"max_source_chars":10000,"level":0},
  "strategy":{"family":"tiered","tier":"graph","graph_method":"global","fallback_policy":"error"}
}
```

社区依赖relations，选择检索模式不会自动开启抽取。按KB启用后，P3关系任务发布时同事务记录
KB社区任务；补偿扫描也可恢复任务。社区任务独立租约、重试、幂等键，原文和模型配置变化使旧报告失效。
查询不隐式构建整库图，也不自动改写或切换家族。

新增接口（路径前缀/api/v1/knowledge-bases/{kb_id}/knowledge-index）：

- POST /communities/build：空对象补建；{"retry":true}显式重试失败/部分任务，不能指定document_id。
- GET /communities：只返回当前有效报告，标记derived_summary；来源、模型地址不通过此列表透传。
- GET /status：增加global_retrieval_ready、community_status，区分未构建、等待关系、构建中、部分、失败、过期。

Global的budget新增max_reports（默认4，上限12）、max_model_calls（默认5，上限13）、
max_summary_chars（默认6000，上限20000）。查询最多120秒；生成长度有限制，报告选择、输入和输出
裁剪均记录覆盖不足。原文支持集合共同消耗top_k，不能用缺少支持材料的生成要点填充结果。
Global不要求切片向量/BM25就绪，不额外调用这两个通道；报告未就绪/错误按策略显式失败或回退混合。

## 证据与版本

0020_graph_communities增加knowledge_graph_jobs、graph_communities、community_reports。
图输入指纹覆盖P3有效manifest、关系与支持原文、模型配置、社区参数及算法/提示词版本。
报告每条要点必须引用给定chunk中的逐字引文。生成text与引文分开，所有生成内容均标记derived_summary。
这保证定位和依赖有效，不等于自动证明生成陈述语义正确；真实抽取与主题质量仍需业务评测。

读取报告和Global生成结束后均重验依赖。删除、禁用、重解析、准入/模型变化会使旧报告立即不可返回。
无关系覆盖的文档/切片不算完整覆盖，超出目录/关系/社区/输入预算也必须标记不完整。
Context再逐字检查支持引文是否进入返回片段；没有完整支持的派生要点被移除，并记录withheld_summaries。
跨文档仍按原边界分Evidence Group，通过related_evidence_group_ids链接，绝不伪造跨文档父块。
所有、总计、精确枚举等请求仍不支持确定性聚合；Global主题综合不能替代完整性/统计计划。

## 隔离运行环境

用户已授权安装graphrag==2.7.0。官方依赖仅安装到独立镜像的/opt/graphrag虚拟环境，
主后端解释器和依赖清单不更改。安装后的版本快照保存在backend/requirements-graphrag.lock.txt。
依赖层和应用源码分开；日常源码更新只构建runtime镜像，不重新下载官方包。
graspologic要求NumPy1.x，需固定兼容的NumPy/pandas/PyArrow组合，避免新版PyArrow的ABI冲突。

```powershell
# 仅首次或升级依赖时；需要包安装授权。
docker build -f docker/graphrag-deps.Dockerfile -t know_rag_new-graphrag-deps:2.7.0 .
# 应用源码更新，不安装依赖。
docker build -f docker/graphrag-runtime.Dockerfile -t know_rag_new-graphrag:2.7.0 .
# 官方能力worker复用完整后端环境，不需要给主后端安装GraphRAG。
docker compose -p know_rag_new -f docker-compose.yml -f docker-compose.new.yml -f docker-compose.graphrag.yml run -d --no-deps --name Know_RAG_new-knowledge-index-worker --entrypoint python backend -m app.services.knowledge_index.cli --reconcile
```

两个provider都只使用已配置的本机Ollama生成报告/查询要点，不自动下载模型或调用云供应商。
官方SDK的安装不代表启用了其Azure/OpenAI调用。官方社区专项测试应在禁用网络的容器中运行。
现存同名worker需先停止并替换，持久任务仍保存在数据库；不应同时运行会争抢任务的旧版本worker。

## 验证与部署记录（2026-11-05）

- 全量后端：1318 passed、1 skipped，包含真实官方provider；跳过项为需PG临时schema的并发专项。
  此后补充的Global同源表格用例已在下列PostgreSQL专项通过。
- P2/P3/P4真实PostgreSQL专项：79 passed、1 skipped，包含官方Leiden/Parquet到持久报告的链路；
  此处跳过的是只适合SQLite的主迁移回滚样本。数据库为tmpfs一次性测试容器，未使用业务库做测试。
- 官方provider另以`--network none`独立运行：1 passed。报告/查询模型响应在自动化测试中为固定样本，
  不把算法/事务验证等同于真实模型主题质量验证。
- Ruff通过；前端npm run check（类型/ESLint/9项测试）及npm run build通过。
- 隔离环境pip check通过，graphrag=2.7.0、numpy=1.26.4、pandas=2.2.3、pyarrow=17.0.0；
  主backend的find_spec('graphrag')为None，没有修改其Python依赖。
- Know_RAG_new已迁移0020并更新后端、前端、MCP、gateway和隔离worker。
  登录态检查社区status/list、严格Global未就绪错误与匿名401通过；业务库均未启用高级索引。
- 本次运行时快照：3个知识库、30篇文档、1337个切片；P2任务/P4任务/社区/报告均0。
  没有调用真实业务模型；未完成浏览器交互验收、约100题业务标注、质量/成本/规模延迟评测。

工程交付已完成；业务启用应先配置本机Ollama并对指定KB开启关系/社区构建，再按实际状态等待报告就绪。
2026-11-06补充，DRIFT与自动策略路由已在P5实现，当前契约见retrieval_p5_implementation.md。
