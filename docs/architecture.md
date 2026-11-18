# Architecture

## 意图识别：规则保底 + Jev 兜底（2026-11-18）

自动策略（`strategy.tier=auto`）的意图判定分两层：

1. `auto_router.classify_rule_signals()` 先算全部命中意图、优先级 winner、过宽触发和短查询。
2. `intent_resolver` 只在强单信号时直接采用规则；零信号、多信号、过宽单信号交给 Jev。
   Jev 返回非法标签、低置信、超时或服务错误时一律回退规则。

默认 `INTENT_PROVIDER=rules`，不产生外部请求。`shadow` 只把抽样结果写日志，不影响请求；`hybrid` 才把 Jev 结论作为 `capabilities["intent_hint"]` 传给 `route_auto`。外部调用按 `INTENT_JEV_KB_IDS` 白名单启用，只发送问题文本，不发送知识库内容。

意图只决定策略标签；A/B 家族隔离和降级逻辑不变。即使意图是 `fusion`，家族 A 仍然只执行关系、家族 B 仍然只执行事件，不会因为 Jev 接入而变成真正的双通道融合。

> 状态：Current
> 维护日期：2026-08-13

`know_rag` 是本地优先的 RAG 知识库构建、解析、切分、索引和检索系统。

## 页码来源与图片图题贯通（2026-11-05）

2026-11-06新增知识库图谱浏览。knowledge_index/graph_view.py复用EvidenceReader的当前manifest、
引文与实体证据门禁，返回有界的实体/关系/事件子图。名称搜索与实体邻接在SQL侧限定范围，
过期、禁用和跨库来源排除。知识库详情的“知识图谱”进入独立页面，支持节点搜索、邻接展开、
缩放、节点列表及原文证据面板。浏览按显式索引开关提供有效数据。

图谱展示采用系统统一的浅色大画布与紧凑工具栏，图谱路由使用窄导航。列表和2D视图共享当前子图，
来源与节点类型筛选在已加载数据内执行。默认精简名称标签，选择节点后高亮邻接关系，
原文证据通过浮层按需展开。画布支持拖动、滚轮缩放、键盘平移和全屏。
连线端点与节点圆心对齐，节点覆盖线头，以无箭头连线呈现实体现有关系。

Canonical 最终父子草稿在续接、重组和策略应用之后，通过 `services/source_location.py`
把规范化原文区间投影到来源节点，保存 `source_pages`、`source_spans`、`page_location_status`。
PDF使用1基物理页码；解析器明确提供的印刷 `page_label` 独立保留在来源节点与span，不从正文猜测。
单页且定位完整时才填 `page_no`；跨页保留集合，不连续的2、4页显示为“2、4”。bbox标为来源块精度，
不冒充字符框。长表重复表头规范化通过完整原始行序列重新定位，避免整表来源节点失联。

新切片在现有JSON metadata落库，无数据库迁移。旧切片查询时可只读补偿：必须匹配同KB、文档、
当前parse_revision、精确坐标标记和有效范围；批量读取Canonical账本，补入响应及相关上下文，
不修改数据库或触发重解析。缺失/过期账本不强行填页码。Context legacy、flat chunks、
Evidence Groups与MCP透传相同位置语义；跨页不能用父块首张页码代替子块位置。

图片的“PDF 图片”等通用占位caption不再阻止空间图题恢复。MinerU可靠输入仍优先使用有效解析器
图题和既有空间候选规则；双方都缺bbox时，才允许同页最多三块范围内的唯一阅读顺序图题兜底。
章节/图片边界会终止搜索，竞争候选保留歧义；有效几何被拒绝时不会降级为强制顺序绑定。
资产保存绑定方法、候选序号与图号；占位标题不作为章节上下文。

关键词索引复用既有 `keywords.keyword`：仅准入caption中存在的可靠图号进入精确匹配，显式图号
查询可与普通BM25共同召回，仍受原KB/启用/状态/候选预算约束。semantic仍为纯向量。
Context保留可靠的短图片图题，不再套正文长度阈值；禁用/低质量标记优先生效，直接图片命中也携带图号。
正文引用扩展继续遵守现有关系生成、物化与Context开关，本次不修改运行配置。

位置补偿可直接用于已有可靠账本；旧图片图题及检索内容需要通过原解析重建流程更新相应文档与
Embedding/BM25索引。源码/字段回放验证不代表运行实例已部署或线上图片召回已验收。

## 双模式检索接入（2026-11-03，P0/P1 基础）

HTTP GET/POST、LLM Context 和 MCP 共用 `retrieval_service.hybrid_search` 兼容入口，
经 `services/retrieval/orchestrator.py` 与 `policy_resolver.py` 解析策略后调用原切片召回。
显式请求 `strategy` 优先于知识库 `retrieval_profile`；legacy_v1 继续使用旧开关和全局配置。
新建知识库采用 new_profile_v2 的 tiered/hybrid；迁移 0016 将既有库保留为 legacy_v1。
首阶段将 profile 直接存于 knowledge_bases JSON 列，不另建一对一 profile 表。

基础策略为 tiered/semantic 和 tiered/hybrid。semantic 禁用文档关系候选扩展，
不禁止独立的父窗口与图片展示。P3 已接入 graph/local 与 event_graph；索引未就绪时
fallback_policy=error 返回失败，hybrid 显式回退并记录原因。查询不触发建图，未接入外部图引擎。
执行事实在 `meta.retrieval` 返回；Context 保留旧 `retrieval_meta` 投影，MCP 透传同一诊断。
P2/P3 的实体、关系及事件基础见下节；P4社区/Global与P5自动选级/DRIFT已接入。

### 精确表格去重与候选预算（2026-11-04）

Canonical 父子切分在落库前核对表格的原文区间与完整内容，同一区间的完整表格仅保留首个
子块，其他窗口的序号与父草稿索引存入审计 metadata。不同行组、独立位置的同文表格和无法
核对的区间均保留。既有数据库无需删除重建：v2 召回在融合前由 `retrieval/source_dedup.py`
按知识库、文档、解析版本、精确区间、内容指纹与来源节点归一化表格；旧 ID 保留为
`source_chunk_ids`。每路同源只投一票，通道排名重新连续编号，图/事件路径同步映射到代表 ID。

四条 v2 策略共用 `strategy.budget.candidate_k`，默认每路20、上限100；它与最终 `top_k`
分开。底层保留最多5倍预取以支持过滤和去重，去重后显式截到每路预算：semantic 最多保留20个
向量候选，hybrid/Local/event 最多各保留20个向量与关键词候选，
基础并集至多40个。Local/event 再按独立的全查询关联预算扩展。融合候选全部进入可选重排，
之后选择最终 Top K；关联链按完整支持集合装入该预算。唯一证据不足时可少于 K，不无限补抓。
`meta.retrieval.candidate_budget` 返回实际每路数量、去重数量、融合数与最终选择数；图像展示
扩展及 Context 长度裁剪发生在选择后。legacy_v1 且未指定新策略的请求保持原召回数量与行为。

## P3 Local 与事件关联 MVP（2026-11-04）

2026-11-06增加retrieval_profile.index_model，KB可独立指定本机Ollama抽取模型，未指定时继承
既有全局模型选择。名称引用模式由服务器映射参与者下标和来源片段ID，再经过完整证据校验发布。
抽取使用结构化JSON输出，模型语法省去大范围长度展开，完整长度限制在发布前由Pydantic校验。
当前关系算法与提示词版本为local-event-mvp-v5、grounded-claims-v5，纯实体算法为entity-foundation-v2。
names关系模式优先从规范表格恢复有原文行列依据的属性关系，其余内容通过本机模型抽取。

迁移0019增加 knowledge_relations、knowledge_events 和 event_entities。profile.build_indexes
可选择 relations/events，均复用实体底座。输入指纹包含对应算法/提示词版本和 capabilities，
升级实体对齐算法后重建当前输入，旧 manifest 不会冒充新能力就绪。
worker 在同一次本机 Ollama 调用中抽取实体、关系和事件，按开启的能力校验和物化；
关系参与者、关系词、支持引文、非空时间/条件均需定位原文。事件正文是派生检索表示，
原文 quote 单独保存，空事件不能表示该 child 已完成。否定/条件/时间随候选保存。

Local 使用 know_rag_local_v1（自建 Local 图增强，并非完整 Microsoft GraphRAG），
事件使用 know_rag_event_sql_v1（参考 SAG 的事件—实体组织，不声称复现其核心算法）。
原文向量/BM25 和精确实体别名提供种子，事件另用受限词法候选，未新增实体/事件向量索引。
关系按邻接扩展，事件通过共享实体连接；参与者回查同一 source/manifest 的有效实体提及。
没有 scope 的同名对象仍按文档隔离。整个查询共用候选、种子、跳数和扇出预算，最多两跳；
泛化实体不扩展，高频事件实体优先级降低。有效来源目录最多检查1000篇文档，SQL另有行数上限。

基础混合排名与关联排名在 chunk 粒度做 RRF，每条来源只计一票；证据链按完整支持切片集合
占用最终 Top K，重排发生在融合后。图/事件候选使用准入投影原文，生成事件文本不当作原文输出。
扩展后再次核验当前输入，失效路径不能进入上下文。meta.retrieval.association 返回 provider、
覆盖、预算、候选数及路径；共享实体仅表示线索，不推出合作或因果事实。Evidence Groups
保留文档/parent 边界，通过 related_evidence_group_ids 关联；Context限长后标记 context_complete。
枚举/总计问题标记覆盖不足，本阶段不提供确定性聚合。

## P4 社区与Global（2026-11-05）

P5在2026-11-06增加auto规则路由与know_rag_drift_v1。auto根据意图、当前实体链接和派生索引
能力在tiered内选级。DRIFT由有效社区要点规划子问题，在同一KB内多轮调用Local，各轮共享
检索次数、候选、模型调用、输入字符及时间预算。重复问题、无新线索及预算耗尽均终止。
原文先转换为准入投影，最终重排后重验文档和社区版本，再按完整证据链及top_k返回。
模型计量记录Ollama实际token计数。debug Context同时提供retrieval_results，调试页一次检索
复用召回和Context结果。细节见retrieval_p5_implementation.md。

迁移0020增加KB级knowledge_graph_jobs、graph_communities和community_reports。
profile.build_indexes显式启用communities且必须包含relations；关系发布同事务记录社区任务，
独立worker通过租约、幂等指纹和依赖重验发布报告。任何来源、准入、模型或社区参数变化使旧摘要不可读。
native使用有界连通分量/BFS；可选microsoft_graphrag_2_7_0在独立虚拟环境调用官方Leiden并真实读写Parquet。
官方适配仅涵盖社区构建；报告及Global由Know_RAG实现，未使用官方全套索引/报告/GlobalSearch工作流。

Global按报告map-reduce生成查询要点，再返回支持原文；不调用基础向量/BM25或混合报告ID与chunk ID做RRF。
每条派生要点附逐字支持引文、报告/revision和来源，原文共同消耗top_k；Context裁剪后缺支持则移除该要点。
同源表格支持材料沿用精确来源去重，并保留原始ID与returned_chunk_id映射。
查询受报告数、模型调用数、输入/输出字符与120秒总预算约束，裁剪、失败、无关系覆盖都标记不完整。
报告和查询仅允许已配置的本机Ollama；启用检索策略不会自动开启索引或下载模型。
该流程提供主题综合，不提供精确统计/完整枚举。详见retrieval_p4_implementation.md。

## P2 共用派生索引底座（2026-11-04）

迁移 0018 增加 `knowledge_index_jobs`、`index_manifests`、`knowledge_entities`、
`knowledge_evidence` 与 `entity_aliases`。知识库 profile 的 `build_indexes: ["entities"]`
显式开启实体抽取，默认空数组。解析写 chunks/artifact 后在同事务写 outbox；更新 KB 开关
和 `/knowledge-index/build` 也能为已有资料补偿入队。独立 CLI worker 领取持久任务，
模型调用在事务外执行，每块续租，失败退避 60 秒，最多自动尝试 3 次；显式重试可重置次数。
worker 的 `--reconcile` 反复比较当前来源和 manifest，补偿漏任务与版本/准入/模型变化。

输入来自与 embedding/BM25 相同的 ContentAdmissionResult，只接收当前 parse_revision 的
有效 child。幂等键包含文档版本、Canonical hash、准入后的输入、模型配置及提示词/算法版本。
模型只允许现有本机 Ollama LLM，不下载模型或新增云调用。实体提及是模型候选，不代表关系事实。
规范名、提及、非空 scope 和引文都必须逐字定位于准入投影；无 scope 时按文档隔离同名实体。
证据偏移明确标注 admitted_projection，保存准入来源通道，不冒充 Canonical 原始偏移。

2026-11-06增加实体名称对齐和条目级恢复。names模式按全半角、空白及有证据的别名对齐关系端点，
漏列端点仅在该条引文中逐字出现时补为other类型、空scope实体；歧义名称不做相似度或子串合并。
入库只在同KB、同类型、同scope内复用唯一规范名/当前有效别名；原文名称、提及与引文保持原样。
worker逐条校验并保留有效实体/关系/事件，拒绝项以固定错误码存入manifest.coverage_json.rejected_items，
含拒绝项的文档仍为partial。partial与failed任务均退避60秒，最多自动尝试3次；重试复用完整切片，
重新抽取有错误的切片，并合并其先前有效条目。coverage中的processed_chunks表示已保留结果的切片数，
completed_chunk_ids表示无拒绝项的完整切片。新版候选元数据保存在证据source_json中供同输入恢复，
旧格式缺少该元数据时重新抽取。算法指纹更新使已有产物按新输入重建。

staging 在最终版本核验后与 manifest/实体/证据一起发布；KB 行锁统一写入锁顺序，
lease_token 防止过期 worker 发布。证据读取再次验证 KB、有效文档、切片、revision、
准入内容 hash、manifest 和引文范围；删除/禁用/重解析后旧证据立即不可返回。
外键级联清理来源证据，补偿扫描清理孤立实体。覆盖率按当前输入计算，区分完整、部分、
失败、过期和不可用；entity_foundation ready 不表示图检索或事件检索就绪。

## 融合检索 fusion（2026-11-11）

新增 `tier="fusion"`：同一请求内执行基础召回（vector+BM25）、关系扩展（Local）和事件关联（SAG），共享一次
`EvidenceReader` 与同一批 seeds；结构通道内部先成一份排名，再与基础排名做第二级 RRF。事件默认独立计分并
保留席位，避免被主排名淹没；`event_policy=fused` 时才并入结构。`rank_mode=feature` 仅对已入池基础候选做
图特征重排，图独有候选只走保留位。基础与结构通道串行执行，结构总 deadline 默认 800ms，单通道失败保留
其余通道，两类图都不可用时按 `fallback_policy` 回退 hybrid。

Global 在 fusion 中退化为背景层：摘要只写入 `background`（`derived=true`、`citation_allowed=false`），
支持 chunk 仅在未被基础/结构覆盖时低权重并入；Global 失败或超时不影响证据。`include_event_timeline`
从已返回证据的事件路径生成事件线，同样不占引用编号。`meta.retrieval.fusion` 返回通道状态、候选数、
结构路径、权重、配额、超时与覆盖；旧 `association` 投影保留，融合时 `kind="fusion"`。新增预算字段全部
存于 `retrieval_profile` JSON，无数据库迁移。A 家族的 fusion 只组合关系与背景；关系/事件双意图在 A
走 Local、在 B 走事件关联。auto 在家族作用域内路由，overview 且社区+关系就绪时选 fusion+背景；
短查询、枚举、无实体链接或覆盖不足时保持 hybrid。
新建知识库必须显式选择检索家族且创建后不可更改：A 构建 entities/relations/communities，
B 构建 entities/events；A 禁用事件通道，B 禁用关系与社区通道，跨家族问题降级 hybrid。
独立 `index-worker` 服务消费文档任务与 KB 级社区任务。

## 服务拓扑

```text
Vue 3 / Vite (:5174)
        │ /api、/files
        ▼
FastAPI (:8000) ─────── PostgreSQL 16 / pgvector
        │               Redis
        │               Elasticsearch 8 / BM25
        │               MinIO
        ├────────────── MinerU CPU（默认）/ GPU（可选）
        ├────────────── DeepDoc / RAGFlow vendor runtime
        └────────────── Ollama（默认）或显式配置的 OpenAI-compatible provider

MCP (:8010) ──HTTP API──▶ FastAPI KnowledgeContextService
Gateway (:8483) ────────▶ FastAPI + MCP
```

## 文档主链路

上传入口先流式计算原文件 SHA-256，在同一知识库内按内容查重，改名不绕过查重；
不同知识库允许保存相同文件，同名不同内容也允许上传。已有文档不论解析状态或启用状态，
都占用其内容指纹。普通重复请求不写 MinIO、不建解析任务；迁移 0017 的
`uq_documents_kb_file_hash` 部分唯一索引兜底并发冲突，失败方回滚并清理自己的对象。
旧记录缺失指纹（空字符串）暂不参与唯一性校验；迁移遇到已有非空指纹重复会中止，保留原数据。

```text
上传 → SHA-256 同库查重 → MinIO → 唯一索引约束下创建 Document → ParseTask
→ MinerU / RAGFlow / Legacy fallback
→ ParsedDocument / ParsedBlock
→ PDF 只读页面事实画像；可选选页渲染与缺失 asset 补偿
→ 展示型富文本规范化（代码围栏除外）
→ Canonical Markdown + Asset Sidecar
→ normalize 后 provenance grounding + FigureLinker
→ CanonicalRelationGraph merger（caption/footnote/explicit/group/shared）
→ DCP 文档/页面画像与逐页 resolved chunk policy
→ Parent/Child 或 flat compatibility chunk
→ 同事务写 PostgreSQL chunks + document_canonical_artifacts
→ pgvector / 可选外部向量库 + Elasticsearch BM25
→ Hybrid Retrieval / KnowledgeContextService（Evidence Group）/ MCP
```

图片语义首期以 MinerU 主路径为可靠输入：MinerU adapter 将 caption、footnote、OCR、page
和 bbox 标准化到 `ParsedBlock`，随后 `vision_service.enhance_image_blocks()` 补充 VLM 描述，
Canonical 层再写入 Asset Sidecar 和 image child。RAGFlow 可作为显式辅助解析器或 MinerU
文档级 fallback，但不是 MinerU 图文绑定的运行依赖。统一关系层把 caption、footnote 和 explicit
引用合并为 versioned `CanonicalRelationGraph`；完整 graph 与 Canonical provenance 同 revision
持久化，Chunk 只保留检索所需的 compact summaries。

PDF 页面尺寸、原生文本字符数、嵌入图片数量/覆盖率和 parser 图片覆盖率始终以只读方式采集，
供 DCP-2 画像使用；该步骤不渲染页面、不注入 asset，失败时保持原解析结果。
页面主视觉保护由 `DOCUMENT_PAGE_VISUAL_PRESERVATION_ENABLED` 控制并默认关闭，同时尊重文档
上传选项 `enable_media_crop`。开启后，系统才会对存在高覆盖嵌入图像、且 parser 没有近整页
image block 的页面进行选页渲染；已有页面图按 page/bbox 覆盖率去重，普通论文小图页不补整页图。
补入的 block 标记为 `page_primary_visual`，保留权威 PDF page extent，并设置
`exclude_from_asset_group=true`，避免与普通 figure 组成错误图片组。画像或渲染失败时 fail-open
返回原 ParsedDocument，不触发 parser fallback，也不删除任何 OCR/正文。MinerU PDF 主路径随后
执行 block 来源标记，使用 PDF 原生文本层与 MinerU block 的规范化匹配证据区分
`native_text / mixed_or_ambiguous / mineru_ocr / parser_noise`。VLM 增强完成后、reflow 之前，
`mineru-ocr-admission-v1` 对 OCR 候选按文字质量、版式、页面视觉事实和书法语义证据评分；
`normal` 进入生产 ParsedDocument，`auxiliary/shadow` 仅保留在审计投影，不污染 Canonical
Markdown、text child、embedding 或 BM25。该行为由
`DOCUMENT_MINERU_OCR_BLOCK_ADMISSION_ENABLED` 控制，关闭时保持旧内容不变。
已有整页 parser 图片也会标记为 `page_primary_visual`。`block_reflow` 对这类视觉页使用页级
section scope：只接受本页顶部、短文本形态可靠的作品标题；页面中部的 parser 标题会降级为
普通文本，没有可靠标题时生成“第 N 页内容”边界。视觉页标题不会跨页传播，普通论文和连续正文
仍保留原有跨页章节继承。父子切分开启时，只有一个 `page_primary_visual` child 的页级 section
仍保留轻量 context-only parent，避免页面主视觉在树形接口中成为孤立块；普通单子块 section
继续沿用原有压平规则。若一个未分裂的 WeKnora source child 在 Canonical 物化后展开成两个
以上 table/image/text child，则按最终子块数量补建 source parent，避免续表等多块结构因
“先判父块、后展开”而整体落入孤立区。若单个未挂载 source child 与已保留的相邻父窗口
存在至少 25%（上限 64 字符）的明确区间重叠，则归入重叠度最高的父窗口；该规则只修复
父窗口边界遗漏，不为真正独立的单子块创建内容重复的父块。
跨图片、表格、caption 或页眉页脚的正文续接由 `_detect_floating_content_continuations()`
检测：扫描未以句末标点结尾的 text child，向前跨越浮动块（图片、表格、caption、页码噪声），
在满足同章节且页码距离 ≤6 的条件下匹配续写文本。检测成功后：
1) 恢复 child_b 的 `context_header`（修复如"续表 2"伪标题污染）；
2) 调用 `_adjust_continuation_boundary()` 将切分点对齐到 child_a 最后一个自然句末标点，
   尾部不完整文本移入 child_b 头部，同时剥离 MinerU 泄露的伪 caption 前缀（如"表格"）；
3) 将两个 child 归入共享 Parent（或 re-home 到现有 Parent）。无法安全归并的候选标记为
   `standalone_root_child`，不创建 1:1 合成父块。
当 `page_primary_visual` 至少覆盖 3 页且占已知页面 60% 以上时，文档进入视觉页主导切分：
先按 Canonical provenance 的权威页边界分段，再在页内生成检索子块，最终将同页 text/image
统一挂到唯一的 page-scoped context parent。该策略不跨页合并作品；纯图片页的父块使用图片
检索内容，避免只保存无信息的 `asset://` 标记。普通论文和低视觉覆盖文档仍使用通用滑窗。

Canonical 构建图片 asset 前会调用 `image_relation_resolver`：过滤与 OCR 重复的纯编号 caption，
将包含多个不同图号的单图 caption 标记为 ambiguous，并在 MinerU 原生字段缺失时使用归一化
同页 bbox、水平重叠、垂直距离和阅读顺序选择唯一 caption/footnote 候选。候选不唯一或坐标
不可靠时保持图片 Chunk 可用但禁止确定绑定；同页候选会先进行多图片所有权判定，caption 与
footnote 歧义分别记录，脚注冲突不会污染可靠 caption 的图号绑定资格。

`figure_linker` 在 Canonical normalize 后、父子切分前识别“如图 1 所示 / see Fig. 1”等
显式正文引用；中文范围同时支持“图 1～图 2”和期刊常见的“详见表 1、图 1～2”写法。
它只链接 P1 允许确定绑定的 asset。唯一图号直接绑定；重复图号按 page、section
依次要求唯一证据，否则保留 ambiguous 候选。规则分、页/章节匹配和距离等候选证据随
`document_canonical_artifacts` 持久化，明确不冒充概率 confidence。source node 保留 page、bbox、
source order、semantic role 和 normalize 前后字符范围；chunk 继承必要的 `source_node_ids`。
chunk 的 `canonical_parse_revision` 与当前 artifact 对齐；text child 精确范围和 ORM 双侧
metadata 属于 P3/P4。

显式关系的 Phase 1 边界已收敛：P4 occurrence 只写入完整覆盖 `[start,end)` 的最小正文 child，
排除 parent/image/table/OCR/equation，并在图片侧写有序 `image_bindings[]`；旧
`image_binding` 保留稳定的 v1 单关系投影。P5 对每个原始 hit、每种关系类型分别限制 3 张图和
5 个正文 child，按 metadata 声明顺序恢复结果，只允许同 KB 且同 document 的目标，数据库或
metadata 异常时 fail-open。物化与扩展分别受两个默认关闭的环境开关控制。

Phase 2 graph 由 `DOCUMENT_RELATION_GRAPH_ENABLED` 独立控制并默认关闭。开启 graph、关闭
materialization 时只 shadow 写 artifact，不改变 Chunk 数量、正文或检索；两者同时开启时才写
`relation_ids`、`relation_summaries`、有序出入边 Chunk ID 等轻量摘要。新 artifact 使用 schema v2
和 `relation_graph_json`，继续写 `figure_links_json` 兼容投影；旧 artifact 读取时可从兼容投影构造
仅含 explicit edge 的 graph。带 graph schema 标记的空图不会回退并复活旧关系。

Phase 3 由 `DOCUMENT_ASSET_GROUP_ENABLED` 独立控制并默认关闭，且只有总 graph 开启时才运行。
MinerU raw `page_width/page_height` 同时有效时，adapter 将其标为 authoritative extent；否则
page layout graph 只能使用 derived/missing extent。只有同页、同 section、权威 extent、唯一的
横向/纵向 partition，并且存在同 section 的显式 shared-context 节点时才生成 bound group；
缺少组级证据、缺 bbox、derived extent 或并列 partition 保持 ambiguous，跨 section rejected。
顶部公共说明只有覆盖整个 group 且候选唯一时才能 bound；
各图已有独立 caption 时 shared context rejected。Phase 4 的上下左右、以下 N 图和通用代词
仍未实现。

非 Markdown parser 输出进入 Canonical 前会移除 `<sub>/<sup>/<span>/<font>/<em>` 等展示型
HTML 标签并保留标签内文本；脚本、样式和 SVG 内容不进入检索正文。围栏代码及
`content_kind=code_block` 的 XML/配置标签保持原样。父子物化后，同一 `asset_id` 因 overlap
落入相邻 source child 时只生成一个 image child；正文仅对“来源范围重叠 + 精确后缀/前缀重叠”
产生的一句话短尾做定向合并，不启用通用短块合并。

数据库 `chunker_config` 决定父子切分是否启用，不能只通过 `.env` 判断部署实例的实际切分分支。

DCP-2 在 Canonical provenance 已定位后生成确定性的 `DocumentProfile / PageProfile`，再按页面
解析 versioned `ResolvedChunkPolicy`。证据来自 PDF 页面事实、page primary visual、source node
类型与 semantic role，不依赖文件名或知识库名称。结果写入 canonical artifact 的 `stats_json`，
包括文档主画像、逐页画像、策略详情和计数。

DCP-3 由默认关闭的 `DOCUMENT_ADAPTIVE_CHUNK_POLICY_ENABLED` 控制。开启后激活表格行组、
扫描正文、视觉作品页和海报四个低耦合策略。Canonical grounding 后，激活页面按权威页范围
独立调用现有 WeKnora；连续未激活页面仍走原全局 fallback，普通结构化文本边界不变。表格继续
复用结构保护与重复表头行组切分。规范化会改写的长表格/代码节点通过 Canonical 原始偏移映射
恢复页界，避免页面被并入相邻范围。所有 Parent/Child 都记录 resolved policy；
`chunk_policy_applied=true` 仅在 chunk 携带实际执行策略和页码证据时成立，
`shadow_fallback` 表示仅记录推荐策略。
关闭开关时全部策略退回 shadow，切分保持原 Canonical 全局路径。DCP-3 不改变 OCR/BM25/
embedding 准入，相关隔离仍属于 DCP-4。

DCP-3.1 在上述兼容策略之上引入可组合的 `BoundaryPolicy / ParentPolicy /
ContentAdmissionPolicy`，由 `DOCUMENT_ADAPTIVE_CHUNK_POLICY_V2_ENABLED` 控制。三类策略分别
决定切分边界、父块归属和来源准入，均随 chunk metadata 与 Canonical artifact 持久化。同页存在
多个已解析 `VisualItem` 时，以作品为 parent 边界；已绑定的图片与文本只进入对应作品。无法
唯一归属的整页 fallback、文本或图片不强挂到任一作品，而是进入同页
`page_overview_ambiguous` parent；作品级 singleton parent、页面概览 parent 和作品抽取后的
legacy remainder parent 都是明确可物化边界，不再被普通“少于两个子块”规则压平。禁止跨作品
或跨页强绑。关闭 v2 开关时继续使用原 DCP-3 策略。

DCP-4 初版的整 Chunk OCR 隔离已由 DCP-4R 替换。当前 `ContentAdmissionResult` 按
native/parser text、caption、VLM、image/table content 和 visual OCR 分通道合成内容；页面级
`chunk_policy_image_ocr_mode=shadow_auxiliary` 只隔离明确的 `visual_ocr` 字段，不影响同页可靠
文本、图片及描述。Canonical 图片 `content` 中的章节、脚注、上下文、描述和图片文字标签会先
拆成独立通道，避免在隔离 OCR 时丢失可靠的关联文本。embedding 三个入口与 Elasticsearch BM25
共用该合成器；当准入结果为空时会禁用旧 PostgreSQL embedding、删除外部向量点，外部向量命中
也必须存在启用的本地 embedding 才能进入结果。重新向量化会清除
历史 `skip_reason=visual_ocr_quarantine`，持久化 `metadata_json.content_admission` 诊断，并在
提交后刷新该文档关键词索引。旧 `DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED` 作为兼容触发器保留，
新部署使用 `DOCUMENT_FIELD_CONTENT_ADMISSION_ENABLED`；两者关闭时保持旧的全字段索引兼容行为。
详细门禁和真实回放结果以 `document_profile_parse_chunk_plan.md` 为准。

DCP-0.1 的确定性工程数据集现包含 42 个页面场景：11 个旧生产回归页和 31 个对抗页。生成器
同时输出本地 PDF/PNG、MinerU-shaped payload、layout/gold、六层 replay snapshot 以及
JSON/HTML 覆盖率；对抗页的分类和策略差异作为后续 DCP-1.1/DCP-2.1 输入，不参与真实准确率
声明。解析评测插件只读展示 case/gold/actual/diff，不写生产文档或评测表。

Canonical 主链路在 parent/child 计划生成后构建 `PageLayoutGraph`，再由
`document_visual_item_resolver.py` 输出归一化 `LayoutRegion / VisualItem /
VisualTextRelation`。局部图片优先形成作品单元；与其重复的整页 fallback 只保留
`duplicate_fallback` provenance；单张整页栅格有可靠分隔候选时可形成多个 candidate，
没有可靠分隔时只保留 `unsegmented_page_visual`。文本关系支持 caption、inscription、
shared_context、ambiguous 和 unbound，长正文及等距候选不会被强绑。

结果与 Canonical artifact 使用同一 parse revision，保存在
`stats_json.document_visual_layout`，并透传到 image/text child metadata。开关
`DOCUMENT_VISUAL_ITEM_RESOLUTION_ENABLED` 默认关闭；关闭时 resolver 仍输出 shadow
验收数据，但不改变内容准入、切分或检索。DCP-1.1 已完成结构闭环；书法 OCR 可信度和字段级
准入由 DCP-2.1 提供 shadow 证据，并由 DCP-3.1 的 block 级生产投影和解耦策略执行。

DCP-2.1 在 VisualItem 解析后生成确定性的 `document-profile-v2`。它不再用单一 page kind
直接决定内容去留，而是分别记录 `layout_kind / semantic_hints / text_origins / ocr_trust`，
并为 OCR 评分保存文本质量、连续性、页面面积、VisualItem、不可分割状态、歧义关系及相邻页
弱先验的 contribution evidence。原生 PDF 文本、caption 和 VLM 描述始终保留 normal shadow
建议；无原生文本但存在 VisualItem 的 parser text 视为视觉 OCR 候选，进入
`trusted / auxiliary / uncertain / shadow` 分档。完整画像保存在
Canonical artifact，页绑定 child 只保存紧凑 shadow metadata；现有 embedding、BM25、Chunk
内容和 parent 边界均不受影响；DCP-3.1 开启后，生产准入与 VisualItem parent 策略消费这些
证据，原始 shadow 画像仍完整保留用于解释和回放。

## 代码边界

- `api/`：鉴权后的 HTTP 路由和参数绑定。
- `services/document_parser/`：格式解析与统一 ParsedDocument。
- `services/chunker/`：Canonical 和低层切分算法。
- `services/chunking/`：草稿构建、metadata、关联和持久化辅助。
- `services/document_relation/`：无 ORM 依赖的 graph 模型、page layout graph、group/shared resolver、
  merger、legacy adapter 与纯物化逻辑。
- `document_relation_persistence_service.py`：Canonical artifact/graph 的 ORM 持久化与 dual-read 边界。
- `document_relation_expansion_service.py`：scoped retrieval expansion 只读查询边界。
- `services/embedding/`：索引文本构建；`embedding_service.py` 负责模型调用与写入。
- `retrieval_service.py`：向量、BM25、RRF、rerank、关系扩展开关和结果映射。
- `context/knowledge_context_service.py`：前端与 MCP 共用的上下文出口。

## 数据边界

原始文件、解析块、chunk、embedding 和索引默认保留在本地基础设施。远程模型供应商不是默认路径，
但管理员可以在模型配置中显式启用；启用后，发送给该模型的文本或图片将离开本地边界。
完整 embedding 向量不通过普通 chunk API 返回，只提供维度、范数和最多 16 维预览。

## MCP

项目提供独立的只读 MCP 服务，通过现有 HTTP API 调用统一 Context 服务。`mcp-placeholder`
路由仍是早期兼容占位接口，不代表实际 MCP 服务状态。

### 上下文返回模式与 Evidence Group

`KnowledgeContextService` 按 `return_mode` 分派：`legacy`（旧 prompt 构造）、
`chunks` / `chunks_with_selective_context`（cloud 扁平块）、`debug`、
`evidence_groups`（证据组）。MCP 的 `know_rag_search_context` 默认使用 `evidence_groups`：

```text
Hybrid Retrieval → Direct Hit Chunks → 按 Parent 聚合 → 局部 Context Window
→ Evidence Group（direct hit + neighbors + related images）
→ 图片按 asset 全局去重 → Group 确定性排序 → MCP response
```

关键约定：**Parent 只是结构边界，不等于必须整块返回**——每个 Evidence Group 只返回
「直接命中的 child + 相邻 neighbor + 关联图片」，不返回整个父块。图片关联优先级固定为
`direct_hit > figure_binding > same_parent`；同一物理图片资源（`metadata.asset_id`）跨
image / image_ocr_text / VLM 派生块全局去重，去重后证据合并（`relation_sources`）并保留
跨组归属（`related_group_nos`）。同 Parent 命中密度（`hit_ratio`）只用于 P3 same-parent
图片排序，不改变 `direct > figure > same_parent` 的等级关系。

图片 content 由 `compose_image_child_content` 以 `章节：/图片标题：/上下文：/图片描述：/
图片文字：` 字段标签组装，其中 `上下文：` 承载 nearby/OCR 正文，是图片的一等内容字段而非
包装标签。`_cloud_clean_content` 在清理时仅对非图片 chunk 剥除 `上下文：` 行，图片 chunk
保留该字段，避免其内容被清空后被 `_cloud_is_low_information_chunk` 误判为低信息、从
`media` 中过滤。

### MCP 接入认证链路

```text
外部 MCP Client
   X-Client-Id / X-Timestamp / X-Nonce / X-Signature
        ↓
MCP Auth Middleware（FastMCP Starlette app 之前的纯 ASGI 层）
   → 提取签名头 + body SHA256 + JSON-RPC tool name
        ↓ 携带 service token 调用
POST /api/v1/internal/mcp-access/verify（后端集中校验）
   → client 存在 / 启用 / 有效期 / timestamp ±300s / nonce 防重放 / HMAC 验签 / scope
        ↓ 放行或 401/403
现有 MCP Tools（know_rag_list_knowledge_bases / know_rag_search_context）
        ↓
Know_RAG Backend
```

Client Secret 只存在于后端（AES-256-GCM 加密入库）；MCP 容器不持有 client secret，也不直连
数据库。canonicalization 只有一份实现（`app/services/mcp_access/signature.py`），测试客户端
脚本复用同一实现。

标准 MCP 客户端（MCP Inspector / Claude Desktop 等）只能配固定 header，无法逐请求计算 HMAC，
因此接入时在它们与 MCP 之间放一个签名网关（`backend/scripts/mcp_signing_gateway.py`）：
客户端用静态 `Authorization: Bearer <GATEWAY_TOKEN>` 连网关 `:8011`，网关持有 client_id +
client_secret 注入四头签名后转发到 `:8010/mcp`。
