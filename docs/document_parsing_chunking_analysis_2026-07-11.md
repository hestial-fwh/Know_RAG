# know_rag 文档解析与切分分析报告

分析日期：2026-07-11  
分析范围：`backend/app/services/parser_service.py`、`document_service.py`、`document_parser/`、`chunk_service.py`、`chunker/`、`chunking/`、相关配置、文档和测试。  
约束：仅分析，不修改现有实现。

## 1. 结论摘要

当前解析与切分并非整体不可用，代码已经具备多解析器、表格/图片 block、OCR/VLM 增强、Canonical Markdown、结构感知切分和父子切片等能力。但实际生产入口存在明显的状态一致性、数据保留和测试覆盖问题。

综合判断：**有问题，且至少包含 2 个高风险问题、3 个中风险问题。** 最优先应关注“空结果重建删除旧切片”和“解析失败被记录为成功”。它们分别可能导致已有知识内容消失，以及错误占位文本进入检索库。

| 编号 | 级别 | 问题 | 主要后果 |
|---|---|---|---|
| P1 | 高 | 活跃重建入口在确认新切片非空前删除旧切片 | 空解析/空计划可把已有切片清空，并仍将文档标为已解析 |
| P2 | 高 | 解析器异常被转换为可检索占位文档 | 失败被伪装成成功，错误原因和文件名可能进入检索结果 |
| P3 | 中 | Native Markdown 在 Canonical 之前已被重新拼装 | 空行、块间距等原始格式不再真正保真，边界与源码位置可能漂移 |
| P4 | 中 | 默认配置不走统一 Canonical 父子链路 | 默认行为与大量新代码/测试所验证的行为不同，跨 block 上下文较弱 |
| P5 | 中 | 新旧两套切分编排并存，关键测试主要覆盖非生产入口 | 测试通过也不能证明实际 `chunk_service.rebuild_document_chunks` 安全 |
| P6 | 低-中 | `start_at/end_at` 是规范化文本坐标，不稳定对应原文 | UI 定位、原文高亮、问题追踪可能不准确 |
| P7 | 低-中 | 关键词索引删除与数据库事务不是同一原子操作 | 重建失败时 DB 旧切片可回滚，但关键词索引可能已被删除 |

## 2. 当前实际链路

实际解析入口为：

1. `document_service.parse_document()` 调用 `parser_service.parse_local_document()`。
2. Markdown 固定走 `parse_markdown()`；其他格式根据配置尝试 MinerU 或 RAGFlow，异常时进入 fallback。
3. OCR/VLM 增强后统一调用 `reflow_parsed_document()`。
4. `chunk_service.rebuild_document_chunks()` 从数据库读取切分配置。
5. `enable_parent_child=true` 时走 `ParsedDocument -> CanonicalDocument -> WeKnora two-pass`；否则按每个 `ParsedBlock` 独立切分。
6. 切片写库后再写关键词索引；向量化与解析解耦。

需要注意：`backend/app/services/chunking/rebuild_service.py` 是另一套模块化编排，但生产入口没有导入或调用它；它主要被测试直接调用。

## 3. 详细问题与证据

### P1（高）：空切分结果会删除已有切片

活跃入口 `chunk_service.rebuild_document_chunks()` 一开始就删除当前文档的 `ChunkEmbedding` 和 `Chunk`，位置见：

- `backend/app/services/chunk_service.py:2206-2207`

之后才构建新切片。父子模式下，如果 Canonical Markdown 为空，`_rebuild_canonical_weknora_parent_child()` 直接返回 `([], 0)`：

- `backend/app/services/chunk_service.py:978-983`

主流程没有“新切片为空则保留旧切片/抛错”的保护，仍会 flush、更新知识库计数并返回 0。上层随后把文档设置为 `parse_status="parsed"`：

- `backend/app/services/document_service.py:788`

因此，重新解析一个产生空内容的文档时，可能出现“解析成功、切片数为 0、旧知识已消失”。非父子模式同样可能发生，因为删除也在新切片生成之前。

仓库中模块化的 `ChunkRebuildService` 已采用更安全的顺序：先 `build_drafts()`，为空时返回，再由 persistence 删除旧数据；但生产入口没有使用它。现有 `test_chunk_persistence_not_drop_all.py` 甚至明确断言空 drafts 返回 0，却没有验证旧切片是否保留，也没有覆盖活跃入口。

建议方向：先完整构建并验证新切片，再在同一 DB 事务中替换旧切片；对非空源文档生成 0 个切片应视为失败或至少是明确的阻断状态。

### P2（高）：解析失败会变成“成功解析的占位知识”

RAGFlow 解析异常在 `parser_service._parse_with_ragflow()` 中被捕获，并调用 `parse_binary_fallback()`：

- `backend/app/services/parser_service.py:161-181`
- `backend/app/services/document_parser/ragflow_format_adapter.py:906-908`

占位内容包含文件名、类型和失败原因，并作为普通 text block 返回。后续流程照常 reflow、切分、入库，最终文档被标记为 `parsed`。虽然 metadata 中有 `fallback_used` 和 `fallback_reason`，但上层没有以此决定失败/降级状态，也没有默认阻止该占位切片进入关键词或向量检索。

这会导致：

- 用户看到“解析成功”，但没有真实文档正文；
- 检索可能召回“附件文件/说明/解析异常”等占位信息；
- 质量统计中的 block_count、chunk_count 非零，掩盖真实失败率；
- 自动化任务无法仅依赖 `parse_status` 判断内容是否可用。

对于明确不支持的附件，生成可检索说明可以是产品选择；但对于“本应支持、运行时解析失败”的文件，不应与真实内容成功共用同一成功状态。

建议方向：区分 `parsed`、`parsed_with_fallback`、`failed` 或至少设置可靠的 `content_available=false`；解析器异常生成的占位块默认应跳过 embedding/关键词索引。

### P3（中）：Native Markdown 的“原文保真”承诺没有在端到端成立

`build_canonical_document()` 对 Markdown 声明 `parsed_document.content` 是 authoritative source，并原样使用：

- `backend/app/services/document_parser/canonical_document.py:461-477`

但该 `content` 已不是上传文件原文：

1. `parse_markdown()` 将源文件扫描成 block；
2. `_finalize()` 使用 `"\n\n".join(...)` 重新拼出 `ParsedDocument.content`（`ragflow_format_adapter.py:395-400`）；
3. OCR/VLM 后，`reflow_parsed_document()` 再次以 `"\n\n".join(...)` 重建 content（`block_reflow.py:317-325`）；
4. Canonical 层最终“原样保留”的其实是重建后的文本。

扫描器还会对段落使用 `strip()`，因此连续空行、块前后空白、部分段落内部的空行布局都可能变化。代码围栏和表格主体大多能保留，但不能称为完整的原始 Markdown 保真。

现有测试 `test_canonical_pipeline_convergence.py` 中“native markdown authoritative”是直接构造 `ParsedDocument(content=markdown)` 测 Canonical 函数，没有覆盖真实的 `parse_markdown -> reflow -> canonical` 端到端链路，因此无法发现此问题。

建议方向：把上传的 Markdown 原文作为独立字段贯穿后处理，block 仅用于预览/媒体增强；增加端到端逐字符或明确规范化规则后的等价性测试。

### P4（中）：默认运行模式没有使用统一 Canonical 父子切分

默认配置为：

- `ENABLE_PARENT_CHILD_CHUNKING=false`（`backend/app/core/config.py:47`）；
- 数据库配置创建时继承该默认值；前端更新 payload 默认也为 false。

`chunk_service.rebuild_document_chunks()` 只有在 `enable_parent_child` 为真时才调用 Canonical 路径（`chunk_service.py:2217-2223`）。默认模式是对每个 text block 分别调用 `split_document()`，表格和图片则直接独立落片。

影响是：

- 文档整体不会统一转换为 Canonical Markdown 后再切；
- 切分不能跨 parser block 合并连续正文；
- 标题上下文主要依赖 parser/reflow 写入的 `context_header`，不同解析器质量不一致；
- 父级上下文召回默认不可用；
- Canonical 噪声清理、asset sidecar 和父子完整性规则默认不生效。

这不是单纯的默认值偏好问题，而是两种模式具有明显不同的语义和质量特征。报告中不建议直接判断必须默认开启，因为这需要实际语料评测；但产品和运维必须明确当前数据库中的开关值，不能仅从新 Canonical 代码推断线上行为。

### P5（中）：两套实现并存导致测试与生产行为错位

仓库同时存在：

- 大型活跃实现：`backend/app/services/chunk_service.py`；
- 模块化实现：`backend/app/services/chunking/rebuild_service.py` 及其 builders/persistence。

搜索生产代码没有发现对 `ChunkRebuildService` 的调用，只有测试直接导入。以下测试验证的是非生产编排：

- `test_chunk_rebuild_fallback.py`
- `test_chunk_rebuild_not_empty.py`
- `test_chunking_rebuild_service_smoke.py`
- `test_chunk_persistence_not_drop_all.py`

这套模块化实现包含“先 build drafts、空结果不删除旧切片”的行为，但活跃入口没有该保护。因此测试名称看似覆盖了“rebuild not empty / persistence not drop all”，实际不能为生产入口提供对应保证。

此外，`chunk_service.py` 内仍保留大段旧结构化父子构建函数，但当前父子开关走的是 `_rebuild_canonical_weknora_parent_child()`。这种并存会增加修复落在错误实现上的概率。

建议方向：明确唯一编排入口；在未完成收敛前，所有关键回归测试必须通过公开生产函数或 API 入口验证，而不是只测候选模块。

### P6（低-中）：切片位置坐标不是可靠的原文坐标

父子模式的 `start_at/end_at` 来自对 normalized Canonical Markdown 的切分。Canonical 构建会：

- 删除页眉、页脚和页码噪声；
- 把 parser block 序列化为 Markdown；
- 把图片替换成 `asset://` marker；
- 预拆超长代码块和表格，并可能重复 fence/table header。

因此位置仅能解释为“规范化切分输入中的字符位置”，不能稳定映射到上传文件或原始 parser block。图片/表格从一个 source child 拆成多个 child 时，还会共享同一粗粒度 start/end。

如果当前字段只用于排序，影响较小；若 UI 用于原文高亮、页内定位、引用审计，则会出现偏移或多片同坐标。

建议方向：明确坐标空间，并额外保存 source block/order/page/bbox 或 Canonical-to-source 映射。

### P7（低-中）：关键词索引与数据库替换非原子

`rebuild_document_chunks()` 在 DB 删除和新切片构建之前先调用 `delete_document_from_index()`（`chunk_service.py:2198-2204`）。若后续异常，上层 DB rollback 可恢复旧 DB 切片，但 Elasticsearch 删除无法随数据库事务回滚。

结果可能是 DB 中旧切片仍存在，而关键词索引暂时缺失该文档；向量检索和关键词检索对同一文档表现不一致。

建议方向：新 DB 切片成功 flush/commit 后再切换关键词索引，或使用可重试的 outbox/重建状态保证最终一致性。

## 4. 做得较好的部分

以下能力设计合理，问题主要在端到端编排和状态语义，而不是所有底层算法：

- Markdown 表格按源顺序扫描，并保护 EOF 表格；
- Canonical 层把图片二进制/媒体信息放到 sidecar，不直接污染 Markdown；
- 图片 child 能合并 caption、附近文本、VLM 描述和 OCR；
- 超长 fenced code 和 Markdown table 在父子切分前有专门处理；
- 父级只有在至少存在两个 surviving children 时才落库；
- 对页眉页脚、页码噪声的清理较保守，并保护 native Markdown 和 code fence 内数字；
- 解析、OCR/VLM、切分、向量化有阶段化元数据和事件，具备进一步完善质量状态的基础。

## 5. 测试与验证情况

本次完成了静态调用链检查，并使用 `compileall` 验证 `backend/app` 可编译。聚焦 pytest 未能执行：当前工作区 Python 环境未提供 pytest，临时 `uv` 环境也没有安装项目测试依赖；Docker Compose 当前没有运行中的服务。为遵守“不修改现有实现”和本地优先约束，本次没有安装依赖或启动/改变服务。

测试覆盖审阅结论：底层 splitter、Canonical、表格顺序、噪声清理、父子完整性已有较多单元测试；不足主要是以下端到端场景：

1. `parse_markdown -> reflow -> canonical` 原文一致性；
2. 活跃 `chunk_service.rebuild_document_chunks` 在新结果为空时保留旧切片；
3. 支持格式解析器抛异常时，文档状态与检索入库行为；
4. 关键词索引删除成功、DB 重建失败后的恢复；
5. 父子开关开/关两种生产路径使用同一语料的质量对比；
6. `start_at/end_at` 到原始文档位置的可解释性。

## 6. 建议处理优先级

1. **先处理 P1**：禁止空结果替换已有切片，并为生产入口补回归测试。
2. **再处理 P2**：把真正的解析失败与“不支持格式的附件占位”分开建模，避免占位内容默认进入索引。
3. **收敛 P5**：选择唯一生产重建编排，消除“测试验证了一套、运行使用另一套”。
4. **修正 P3/P6**：定义 Markdown 保真边界和坐标空间，再补端到端测试。
5. **评测 P4**：用真实 PDF、DOCX、Markdown、表格、扫描件语料对比开关两种模式，再决定默认值。
6. **完善 P7**：为关键词索引建立提交后更新和失败重试机制。

## 7. 最终判断

当前系统的底层解析和切分能力已经较丰富，但生产链路的可靠性低于代码表面呈现的成熟度。最关键的问题不是“切分算法太简单”，而是：**失败和空结果没有被可靠地阻断，旧数据替换顺序不安全，新旧实现与测试入口没有完全收敛。** 在修复 P1、P2、P5 之前，不建议仅凭 `parse_status=parsed` 或现有模块单测判断文档已经形成可用知识。

## 8. 修复状态（2026-07-13）

本报告列出的 P1–P7 已在后续修复中处理：新切片先构建校验再事务替换；解析失败与非索引附件降级分离；Markdown 原文独立贯穿；所有格式统一使用 Canonical 生产切分入口；模块化 rebuild 持久化入口改为兼容委托；坐标空间写入 metadata；关键词索引改为 DB 提交后先写新数据再清理旧数据。对应生产入口回归测试见 `backend/tests/test_document_pipeline_safety.py`。
