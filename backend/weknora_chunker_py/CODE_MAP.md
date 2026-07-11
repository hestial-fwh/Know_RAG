# WeKnora 切分代码映射表

## 1. 文档处理入口

WeKnora Go 源码：

```text
internal/application/service/knowledge_process.go
```

关键函数：

```text
processDocument
buildSplitterConfig
buildParentChildConfigs
processChunks
resolveDocReader
```

Python 参考：

```text
SplitterConfig
build_parent_child_configs
split
split_parent_child
```

注意：WeKnora 的 `buildParentChildConfigs` 在当前源码中只传了 size / overlap / separators，没有透传 strategy / tokenLimit / languages。Python 参考实现里已经按 Know_RAG 推荐方式透传。

---

## 2. 切分配置结构

WeKnora Go 源码：

```text
internal/types/knowledgebase.go
ChunkingConfig
```

核心字段：

```text
ChunkSize
ChunkOverlap
Separators
EnableParentChild
ParentChunkSize
ChildChunkSize
Strategy
TokenLimit
Languages
```

Python 参考：

```python
@dataclass
class SplitterConfig:
    chunk_size: int = 512
    chunk_overlap: int = 80
    separators: List[str] = ["\n\n", "\n", "。"]
    strategy: str = "auto"
    token_limit: int = 0
    languages: List[str] = []
```

---

## 3. 策略入口

WeKnora Go 源码：

```text
internal/infrastructure/chunker/strategy.go
Split
SplitParentChild
resolveChainWithProfile
runTier
ensureDefaults
```

Python 参考：

```python
split
split_parent_child
resolve_chain_with_profile
run_tier
```

策略逻辑：

```text
strategy=heading    -> heading -> legacy
strategy=heuristic  -> heuristic -> legacy
strategy=legacy     -> legacy
strategy=recursive  -> legacy
strategy=auto       -> profile -> heading/heuristic/legacy
```

---

## 4. 文档画像 / auto 策略

WeKnora Go 源码：

```text
internal/infrastructure/chunker/profiler.go
ProfileDocument
SelectStrategy
DominantHeadingLevel
HeuristicMarkerTotal
```

Python 参考：

```python
DocProfile
profile_document
select_strategy
```

核心规则：

```text
Markdown 标题足够多 -> heading-aware
章节号、分页符、Chapter、第一章等明显 -> heuristic
否则 -> legacy
```

---

## 5. Heading-aware 切分

WeKnora Go 源码：

```text
internal/infrastructure/chunker/heading_splitter.go
splitByHeadingsImpl
findHeadingBoundaries
observeSubHeadings
coalesceTinyChunks
```

Python 参考：

```python
split_by_headings
find_heading_boundaries
observe_subheadings
coalesce_tiny_chunks
HeadingHierarchy
```

核心价值：

```text
按 Markdown 标题边界切分，并生成 context_header。
向量化时使用 document_title + context_header + chunk.content。
```

---

## 6. Heuristic 切分

WeKnora Go 源码：

```text
internal/infrastructure/chunker/heuristic_splitter.go
splitByHeuristicsImpl
findHeuristicBoundaries
applyOverlapAligned
appendOversizeBlock
```

Python 参考：

```python
split_by_heuristics
find_heuristic_boundaries
apply_overlap_aligned
append_oversize_block
```

结构边界包括：

```text
1. Introduction
2.3 Methods
第一章
第3节
Chapter 1
Section 2
分页符 \f
--- / === / ***
连续空行
全大写短标题
```

---

## 7. 结构正则

WeKnora Go 源码：

```text
internal/infrastructure/chunker/patterns.go
```

Python 参考：

```python
MARKDOWN_HEADING_PATTERN
NUMBERED_SECTION_PATTERN
ALL_CAPS_HEADING_PATTERN
VISUAL_SEPARATOR_PATTERN
PAGE_FOOTER_PATTERN
GERMAN_CHAPTER_PATTERN
ENGLISH_CHAPTER_PATTERN
CHINESE_CHAPTER_PATTERN
```

---

## 8. Legacy recursive splitter

WeKnora Go 源码：

```text
internal/infrastructure/chunker/splitter.go
SplitText
splitBySeparators
buildUnitsWithProtection
mergeUnits
computeOverlap
```

Python 参考：

```python
split_text_legacy
split_by_separators_with_offsets
build_units_with_protection
merge_units
compute_overlap
```

核心保护：

```text
LaTeX block math
Markdown images
Markdown links
Markdown tables
Fenced code blocks
```

---

## 9. 表格表头追踪

WeKnora Go 源码：

```text
internal/infrastructure/chunker/header_tracker.go
headerTracker.update
headerTracker.getHeaders
```

Python 参考：

```python
HeaderTracker.update
HeaderTracker.get_headers
```

核心价值：

```text
表格跨 chunk 时，后续 chunk 尽量补表头，避免向量和上下文缺少列名。
```

---

## 10. 校验与 fallback

WeKnora Go 源码：

```text
internal/infrastructure/chunker/validator.go
ValidateChunks
```

Python 参考：

```python
validate_chunks
```

fallback 触发场景：

```text
没切出 chunk
大文档只切出一个 chunk
tiny chunk 太多
所有 chunk 都远小于目标大小
chunk 超过 2x chunk_size
```

---

## 11. chunk 入库与父子关系

WeKnora Go 源码：

```text
internal/application/service/knowledge_process.go
processChunks
```

关键点：

```text
parent chunk:
  ChunkType = parent_text
  只入库，不入向量

child chunk:
  ChunkType = text
  ParentChunkID = parent.ID
  入向量 / 关键词索引
```

Python 参考：

```python
ParentChildResult.parents
ParentChildResult.children
ChildChunk.parent_index
```

---

## 12. embedding 文本增强

WeKnora Go 源码：

```text
internal/types/chunk.go
EmbeddingContent

internal/application/service/knowledge_process.go
indexContent := titlePrefix + chunk.EmbeddingContent()
```

Python 参考：

```python
Chunk.embedding_content(document_title)
```

推荐 Know_RAG 使用：

```text
embedding_text = document_title + heading_path/context_header + chunk.content
```

---

## 13. 检索后 parent 回填

WeKnora Go 源码：

```text
internal/application/service/chat_pipeline/merge.go
resolveParentChunks
```

Python 参考：

```python
resolve_parent_content(child, parents)
```

核心逻辑：

```text
向量命中 child
  -> 根据 ParentChunkID 找 parent_text
  -> 返回 parent.content 给 LLM
  -> 保留 child 作为 source 定位依据
```
