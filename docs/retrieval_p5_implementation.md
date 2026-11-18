# P5 自动选级与有界探索

> Current，2026-11-06。工程实现与运行验证见本文末尾。

## 路线与接口

P5使用规则自动选级与Know_RAG自建的社区引导DRIFT，复用P2/P3来源、关系和P4社区报告。
自动路由先执行确定性意图规则，结合有效实体链接和索引能力，记录选择理由。
DRIFT将社区要点作为子问题规划依据，在同一KB内执行Local检索，再按完整支持集合选择最终原文。
最终回答由调用方模型生成。

HTTP hybrid-search GET/POST、llm-context及MCP使用相同strategy契约。

```json
{"family":"tiered","tier":"auto","fallback_policy":"error"}
```

```json
{"family":"tiered","tier":"graph","graph_method":"drift","fallback_policy":"error",
 "budget":{"max_rounds":2,"max_searches":4,"max_candidates":50,"max_model_calls":5,
           "timeout_seconds":120,"max_context_chars":12000}}
```

auto属于tiered策略家族；KB 级 family_scope 决定 auto 可选通道，B 家族的事件题由 auto 产出 event_graph。
KB可保存auto或drift默认策略，旧legacy配置保持兼容入口。
auto与底层use_vector/use_keyword/disable_*及retrieval_mode互斥。
graph_method适用于tier=graph，DRIFT的max_candidates至少为3。

## 自动路由

家族作用域先于索引就绪检查：A 只允许关系与社区通道，B 只允许事件通道。

| 意图 | A 家族 | B 家族 |
|---|---|---|
| 全量、总数或计数 | hybrid + 覆盖不足 | hybrid + 覆盖不足 |
| 深入探索或追问 | 社区、关系可用且预算足够时 DRIFT | hybrid |
| 全库主题、概览或共性 | 社区+关系可用时 fusion+背景，否则 Global 或 hybrid | hybrid |
| 关系、合作或比较 | 关系可用且存在有效实体匹配时 Local | hybrid |
| 事件、时间线、参与者 | hybrid | 事件索引可用时 event_graph |
| 关系+事件双意图 | Local | event_graph |
| 概念释义 | 无编号/术语提示时 semantic | semantic / hybrid |
| 其他及高级条件不足 | hybrid | hybrid |

规则按表中优先级执行，实体匹配复用当前有效提及与别名证据。
路由model_calls为0。基础向量与关键词的实际可用情况由执行端继续检查并记录。
跨家族降级理由区分“家族未启用”和“索引未就绪”，显式请求同样受家族边界约束。
meta.retrieval.routing保存provider、family_scope、intent、reason、capabilities与chosen_strategy。
requested_strategy保留auto，executed_strategy记录实际路线及通道降级。
全量问题在Context warnings中提示范围完整性要求。

## DRIFT执行与总预算

1. 读取当前有效社区报告，保存graph_revision。
2. 在实际序列化字符额度内发送社区要点与已有准入原文，调用本机Ollama生成待查子问题。
3. 校验每个子问题引用的社区要点ID，仅接受实际发送的ID。
4. 首轮检索原问题和规划子问题，后续轮次只检索新子问题。归一化空白、大小写后去重。
5. 子查询分别预分配两路基础候选与关系扩展额度，合计计入同一总账。
6. 各子查询暂缓图片和父窗口扩展，最后统一去重、融合、可选重排及完整证据链选择。
7. 在重排等异步阶段后重新验证社区revision与所有返回文档的准入快照，再构造Context。

| budget字段 | 默认值 | 范围与口径 |
|---|---|---|
| max_rounds | 2 | 1至3轮，设为1可关闭后续轮次补查 |
| max_searches | 4 | 1至8次基础检索调用，原问题也占一次 |
| max_candidates | 50 | 全请求累计入池额度，DRIFT至少3，最多100 |
| candidate_k | 20 | 每路基础召回上限，进一步受子查询预分配额度限制 |
| max_model_calls | 5 | 2至13次，规划尝试与最终重排请求共同计入 |
| max_reports | 4 | 最多发送的报告数，范围1至12 |
| max_summary_chars | 6000 | 社区要点输入上限，范围500至20000 |
| max_context_chars | 12000 | 所有规划请求累计的JSON字符额度，范围500至40000 |
| timeout_seconds | 120 | 全探索与最终重排共用，范围1至180秒 |

strategy.budget.max_context_chars限定规划输入，根级max_context_chars限定最终Context。
max_candidates按每次入池的向量候选、关键词候选和关联候选累计扣除，重复检索也消耗额度。
底层过滤预取沿用既有规则，raw_counts与入池数分别记录。最终原文共同受top_k限制。
Embedding与BM25的逻辑请求次数受max_searches约束，规划调用和可选重排有单独模型预算。
重排缺少配置时沿用原排名，预留重排请求仍按一次计账。

stop_reason可能为no_followups、no_new_queries、max_rounds、search_budget、candidate_budget、
model_budget、input_budget、timeout或partial_failure。
达到预算或超时返回已验证的部分证据并标记coverage_incomplete。
首次规划失败按fallback_policy执行一次受余量约束的混合回退，并记录失败调用与回退通道。
已开始检索后的补查失败在hybrid策略下返回经过验证的部分结果，error策略返回错误。
来源失效会拒绝结果发布，错误恢复遵守本次请求的剩余预算。

meta.retrieval.drift包含provider=know_rag_drift_v1、graph_revision、rounds、model_calls、
planning_calls、rerank_requests、searches、input_chars、candidates_consumed、unique_candidates、
trace、stop_reason和覆盖诊断。trace中的模型问题标记derived_query。
原文路径通过association.paths及Evidence Groups关联，引用仍按文档、父块和当前版本定位。
Global与DRIFT的token_usage记录Ollama实际返回的prompt_tokens、completion_tokens及measured_calls。
generation_requests表示需要计量的生成请求数，缺少服务端计数的调用可据此识别。

## 页面与评测

KB设置与调试页开放auto和DRIFT。调试页提供共享探索预算、理由、子问题、实际通道与终止原因。
查询子图用来源节点和带文字的路径区分关系、事件及共享实体线索，节点可跳转原文。
同题召回对比沿用最近一次完成查询的KB、问题、阈值和预算，点击后追加一次严格检索。
llm-context的debug返回增加retrieval_results，页面一次查询同时展示召回与Context。

人工标注评测入口位于backend/scripts/evaluate_retrieval.py。输入JSONL每行示例如下。

```json
{"id":"q1","knowledge_base_id":"kb-id","query":"甲校有哪些实践合作资源", "relevant_chunk_ids":["chunk-1","chunk-2"],"required_chunk_ids":["chunk-1","chunk-2"]}
```

无答案样本使用no_answer=true。运行环境使用项目已有httpx及Pydantic，认证令牌通过
KNOW_RAG_EVAL_TOKEN环境变量提供。命令在backend工作目录执行。

```powershell
python scripts/evaluate_retrieval.py --cases cases.jsonl --output result.json --base-url http://127.0.0.1:8003
```

默认依次评测semantic、hybrid、local、event、global、drift、auto，可通过--strategies限制。
所有路线使用相同top_k、候选预算、上下文字符和rerank_enabled=false。
输出区分原始召回指标与裁剪后Context指标，保留实际执行路线、覆盖、耗时和模型计量。
指标包含precision、recall、二元nDCG及required_evidence_complete，汇总P50/P95。
失败样本进入质量分母，未知调用成本单独记录。该工具使用人工证据ID，不生成业务正确率标注。

## 工程验收

2026-11-06已完成以下验证。

- 全量后端1352 passed、1 skipped，跳过项为需要PG临时schema的并发专项。
- 真实PostgreSQL的P2至P5专项112 passed、1 skipped，跳过项为SQLite专用迁移回滚样本。
  PostgreSQL使用一次性tmpfs测试容器，测试包含官方Leiden/Parquet兼容、持久报告、来源失效、
  共享预算、重复终止、首次回退计量、重排预算、重排期间来源变化、HTTP GET/POST与MCP。
- 前端类型检查、ESLint、12项Vitest及构建通过，Ruff与git diff --check通过。
- 评测脚本--help入口与证据指标回归通过，错误请求计入质量分母。
- 新实例backend、frontend、MCP、gateway及独立worker已更新，隔离依赖pip check通过。
- 登录态真实API的auto执行vector+keyword并返回2条结果，routing规则调用数为0。
  debug召回与Context的执行耗时相同，属于同次检索，非法auto/graph_method组合返回422。
- 3个业务库严格DRIFT均提示需要有效社区与关系，业务派生任务数为0。
  本轮读取到3个知识库、30篇文档、1337个切片，生成模型配置为DeepSeek，Embedding为本机Ollama。
- 浏览器连接超时，当前项目虚拟环境缺少Playwright，本轮页面交互尚未验收。

P0至P5工程实现已完成。当前生成入口要求本机Ollama，业务KB需具备关系及社区报告，
随后使用人工标注问题度量实际召回收益、回答支持率、延迟与成本。上述自动化规划响应为固定样本，
真实DRIFT业务收益和自动路由的质量/成本收益仍待该业务评测。
