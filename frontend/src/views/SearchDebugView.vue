<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBases'
import { buildLlmContext, hybridSearch, type HybridSearchResult, type LLMContextResponse, type ParentChildSummary, type ParentGroup } from '../api/retrieval'
import { collectUniqueDocuments, showDocumentFileName } from '../utils/retrievalDocuments'
import type { HybridSearchPayload, RetrievalDiagnostics, RetrievalStrategy } from '../api/retrieval'
import { strategyFromSelection } from '../utils/retrievalStrategy'
import RetrievalGraph from '../components/RetrievalGraph.vue'

const router = useRouter()
const knowledgeBases = ref<KnowledgeBase[]>([])
const knowledgeBaseId = ref('')
const query = ref('')
const topK = ref(5)
const vectorThreshold = ref(0.3)
const keywordThreshold = ref(0.3)
const useVector = ref(true)
const useKeyword = ref(true)
const includeContext = ref(true)
const includeParentContext = ref(true)
const parentContextMode = ref('standard')
const parentContextMaxChars = ref(2000)
const neighborContextMaxChars = ref(300)
const parentChildrenLimit = ref(10)
const maxParentContexts = ref(5)
const mergeHitsByParent = ref(true)
const llmMaxContextChars = ref(8000)
const llmMaxSources = ref(8)
const llmIncludeImages = ref(true)
const llmIncludeImageUrls = ref(false)
const llmContext = ref<LLMContextResponse | null>(null)
const llmLoading = ref(false)
const llmError = ref('')
const fusionStrategy = ref('rrf')
const rrfK = ref(60)
const vectorWeight = ref(1)
const keywordWeight = ref(1)
const results = ref<HybridSearchResult[]>([])
const parentGroups = ref<ParentGroup[]>([])
const error = ref('')
const message = ref('')
const loading = ref(false)
const retrievalSelection = ref('inherit')
const fallbackPolicy = ref<'hybrid' | 'error'>('hybrid')
const retrievalDiagnostics = ref<RetrievalDiagnostics | null>(null)
const graphVisible = ref(false)
const explorationBudget = ref({ max_rounds: 2, max_searches: 4, max_candidates: 50, max_model_calls: 5, timeout_seconds: 120 })
const comparisonSelection = ref('hybrid')
const comparison = ref<{ diagnostics: RetrievalDiagnostics | null; chunks: HybridSearchResult[] } | null>(null)
const comparing = ref(false)
const comparisonError = ref('')
const lastSearch = ref<{ kbId: string; payload: HybridSearchPayload } | null>(null)
const usesLegacyControls = computed(() => retrievalSelection.value === 'inherit'
  && knowledgeBases.value.find(kb => kb.id === knowledgeBaseId.value)?.retrieval_profile?.version !== 'new_profile_v2')

const uniqueDocuments = computed(() => collectUniqueDocuments(results.value))

type ImageLikeItem = Partial<HybridSearchResult & ParentChildSummary> & {
  image_url?: string
  image_info?: Record<string, unknown>
  metadata?: Record<string, unknown>
  metadata_json?: Record<string, unknown>
}

type ContextLike = {
  content?: string
  content_preview?: string
  display_content?: string
  display_preview?: string
  content_length?: number
  returned_chars?: number
  preview_chars?: number
  is_truncated?: boolean
} | null | undefined

async function loadKnowledgeBases() {
  const data = await listKnowledgeBases()
  knowledgeBases.value = data.items
  if (data.items.length) knowledgeBaseId.value = data.items[0].id
}

function formatScore(score?: number) {
  return Number(score || 0).toFixed(4)
}

function metadataPreview(item: HybridSearchResult) {
  return JSON.stringify(item.metadata_json || item.metadata || {})
}

function displayText(value: any) {
  return String(value?.display_content || value?.display_preview || value?.matched_content || value?.preview || value?.content || value?.content_preview || '-')
}

function parentContextText(ctx: ContextLike) {
  return ctx?.display_content || ctx?.display_preview || ctx?.content || ctx?.content_preview || '-'
}

function contextLengthText(ctx: ContextLike) {
  if (!ctx) return '未返回父块上下文'
  const total = Number(ctx.content_length || 0)
  const returned = Number(ctx.returned_chars || ctx.content?.length || ctx.content_preview?.length || 0)
  const requested = Number(ctx.preview_chars || 0)
  const parts: string[] = []
  if (returned || total) parts.push(`返回 ${returned || '-'} / 总 ${total || '-'} 字`)
  if (requested) parts.push(`请求上限 ${requested} 字`)
  if (ctx.is_truncated) parts.push('已截断')
  return parts.join(' · ') || '-'
}

function getNestedImageInfo(item: ImageLikeItem): Record<string, unknown> {
  const direct = item.image_info || {}
  if (Object.keys(direct).length) return direct
  const meta = item.metadata_json || item.metadata || {}
  const nested = meta.image_info
  return nested && typeof nested === 'object' ? nested as Record<string, unknown> : {}
}

function imageUrl(item: ImageLikeItem) {
  const info = getNestedImageInfo(item)
  const meta = item.metadata_json || item.metadata || {}
  const candidates = [
    item.image_url,
    info.url,
    info.image_url,
    info.public_url,
    info.relative_path,
    info.path,
    meta.image_url,
    meta.url,
    meta.relative_path,
    meta.path
  ]
  for (const candidate of candidates) {
    const raw = String(candidate || '').trim()
    if (!raw) continue
    if (raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('/files/') || raw.startsWith('data:')) return raw
    if (raw.startsWith('kb/')) return `/files/${raw}`
  }
  return ''
}

function isImageLike(item: ImageLikeItem) {
  return String(item.chunk_type || '').toLowerCase() === 'image' || Boolean(imageUrl(item))
}


function buildRetrievalPayload(): HybridSearchPayload {
  const strategy: RetrievalStrategy | undefined = retrievalSelection.value === 'inherit' ? undefined
    : strategyFromSelection(retrievalSelection.value, fallbackPolicy.value, explorationBudget.value)
  return {
    query_text: query.value,
    match_count: topK.value,
    vector_threshold: vectorThreshold.value,
    keyword_threshold: keywordThreshold.value,
    // 新策略自行确定通道；仅兼容模式继续传递旧开关，避免形成互相矛盾的请求。
    ...(usesLegacyControls.value ? {
      disable_vector_match: !useVector.value,
      disable_keywords_match: !useKeyword.value
    } : {}),
    ...(retrievalSelection.value !== 'inherit' ? {
      strategy
    } : {}),
    include_content: false,
    include_context: includeContext.value,
    include_parent_context: includeParentContext.value,
    parent_context_mode: parentContextMode.value,
    parent_context_max_chars: parentContextMaxChars.value,
    neighbor_context_max_chars: neighborContextMaxChars.value,
    parent_children_limit: parentChildrenLimit.value,
    max_parent_contexts: maxParentContexts.value,
    merge_hits_by_parent: mergeHitsByParent.value,
    fusion_strategy: fusionStrategy.value,
    rrf_k: rrfK.value,
    rrf_vector_weight: vectorWeight.value,
    rrf_keyword_weight: keywordWeight.value
  }
}

async function refreshLlmContext() {
  if (!knowledgeBaseId.value || !query.value.trim()) return
  llmLoading.value = true
  llmError.value = ''
  try {
    await search()
  } catch (err) {
    llmError.value = err instanceof Error ? err.message : '大模型上下文构造失败'
  } finally {
    llmLoading.value = false
  }
}

async function copyLlmContext() {
  if (!llmContext.value?.context_text) return
  try {
    await navigator.clipboard.writeText(llmContext.value.context_text)
    message.value = '已复制大模型上下文'
  } catch {
    llmError.value = '复制失败，请手动选择文本复制'
  }
}

async function search() {
  if (loading.value || comparing.value) return
  retrievalDiagnostics.value = null
  error.value = ''
  message.value = ''
  results.value = []
  parentGroups.value = []
  llmContext.value = null
  llmError.value = ''
  comparison.value = null
  comparisonError.value = ''
  lastSearch.value = null
  if (!knowledgeBaseId.value) {
    error.value = '请先选择知识库'
    return
  }
  if (!query.value.trim()) {
    error.value = '请先输入查询内容'
    return
  }
  loading.value = true
  try {
    const payload: HybridSearchPayload = JSON.parse(JSON.stringify({
      ...buildRetrievalPayload(), return_mode: 'debug',
      max_context_chars: llmMaxContextChars.value, max_sources: llmMaxSources.value,
      include_images: llmIncludeImages.value, include_image_urls: llmIncludeImageUrls.value,
      include_tables: true, include_equations: true
    }))
    const context = await buildLlmContext(knowledgeBaseId.value, payload)
    const data = context.retrieval_results
    if (!data) throw new Error('调试接口未返回召回结果，请更新后端')
    llmContext.value = context
    // 对比使用完成检索时的参数快照，后续编辑表单不会悄悄改变问题或证据预算。
    lastSearch.value = { kbId: knowledgeBaseId.value, payload: {
      ...payload, strategy: data.meta.retrieval?.requested_strategy as RetrievalStrategy | undefined
    } }
    retrievalDiagnostics.value = data.meta.retrieval || null
    results.value = data.results
    parentGroups.value = data.parent_groups || data.grouped_results || []
    message.value = data.results.length ? `检索完成，共 ${data.results.length} 条子块结果，${parentGroups.value.length} 个父块上下文` : '暂无检索结果'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '检索失败'
  } finally {
    loading.value = false
  }
}

async function compareStrategy() {
  if (!lastSearch.value || loading.value || comparing.value) return
  comparing.value = true
  comparisonError.value = ''
  try {
    const snapshot = lastSearch.value
    const common = { ...snapshot.payload }
    delete common.disable_vector_match
    delete common.disable_keywords_match
    const strategy = strategyFromSelection(comparisonSelection.value, 'error', common.strategy?.budget)
    const data = await hybridSearch(snapshot.kbId, { ...common, strategy })
    comparison.value = { diagnostics: data.meta.retrieval || null, chunks: data.results }
  } catch (err) {
    comparisonError.value = err instanceof Error ? err.message : '对比失败'
  } finally {
    comparing.value = false
  }
}

onMounted(async () => {
  try {
    await loadKnowledgeBases()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page">
    <h1>本地检索调试</h1>
    <div v-if="retrievalDiagnostics" class="card" aria-live="polite">
      <p>实际召回通道：{{ retrievalDiagnostics.channels.join(' + ') || '无可用通道' }} · {{ retrievalDiagnostics.elapsed_ms }} ms</p>
      <p v-if="retrievalDiagnostics.degraded" class="text-error">已降级：{{ retrievalDiagnostics.degradation_reason }}</p>
      <p v-if="retrievalDiagnostics.routing">自动选级 · {{ retrievalDiagnostics.routing.reason }}</p>
      <div v-if="retrievalDiagnostics.drift">
        <p>DRIFT 探索 {{ retrievalDiagnostics.drift.rounds }} 轮 · 模型调用 {{ retrievalDiagnostics.drift.model_calls }} 次 · 检索调用 {{ retrievalDiagnostics.drift.searches }} 次 · 消耗候选 {{ retrievalDiagnostics.drift.candidates_consumed }} 个</p>
        <p>终止原因 {{ retrievalDiagnostics.drift.stop_reason }} <span v-if="retrievalDiagnostics.drift.coverage_incomplete">· 当前证据覆盖不完整</span></p>
        <details><summary>查看子问题与实际通道</summary>
          <p v-for="(entry, index) in retrievalDiagnostics.drift.trace" :key="index">第 {{ entry.round }} 轮 · {{ entry.derived_query ? '派生子问题' : '原始问题' }} · {{ entry.query }} · {{ entry.channels.join(' + ') }}</p>
        </details>
      </div>
      <div v-if="retrievalDiagnostics.global">
        <p>Global 模型调用 {{ retrievalDiagnostics.global.model_calls }} 次 · 派生摘要（附原文支持）</p>
        <p v-if="retrievalDiagnostics.global.coverage_incomplete" class="text-muted">当前报告或证据覆盖不完整，不支持全库精确枚举、计数。</p>
        <div v-for="(summary, index) in retrievalDiagnostics.global.derived_summaries" :key="index">
          <p>{{ summary.text }}</p>
          <details><summary>查看支持引文</summary>
            <blockquote v-for="(cite, citeIndex) in summary.evidence" :key="citeIndex">
              {{ cite.quote }} <button type="button" class="secondary" @click="router.push(`/chunks/${cite.chunk_id}`)">查看原文</button>
            </blockquote>
          </details>
        </div>
      </div>
      <p v-if="retrievalDiagnostics.association?.coverage_incomplete" class="text-muted">当前证据覆盖不完整，不能用于全库枚举或精确统计。</p>
      <details v-if="retrievalDiagnostics.association?.paths?.length">
        <summary>为什么召回：关联证据路径</summary>
        <div v-for="(path, pathIndex) in retrievalDiagnostics.association.paths" :key="pathIndex">
          <p>{{ path.hop }} 跳 · {{ path.steps.map(step => step.kind === 'shared_entity' ? '共享实体线索' : step.kind === 'event_seed' ? '事件种子' : `关系：${step.predicate}`).join(' → ') }}</p>
          <button v-for="chunkId in path.support_chunk_ids" :key="chunkId" type="button" class="secondary" @click="router.push(`/chunks/${chunkId}`)">查看原文切片</button>
        </div>
      </details>
      <button type="button" class="secondary" :aria-expanded="graphVisible" aria-controls="query-evidence-graph" @click="graphVisible = !graphVisible">{{ graphVisible ? '收起查询子图' : '查看查询子图' }}</button>
      <RetrievalGraph v-if="graphVisible" id="query-evidence-graph" :paths="retrievalDiagnostics.association?.paths || []" @open-chunk="chunkId => router.push(`/chunks/${chunkId}`)" />
      <details><summary>策略执行详情</summary><pre>{{ JSON.stringify(retrievalDiagnostics, null, 2) }}</pre></details>
    </div>
    <form class="card form" @submit.prevent="search">
      <label>
        知识库
        <select v-model="knowledgeBaseId">
          <option value="">请选择知识库</option>
          <option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
        </select>
      </label>
      <label>
        查询内容
        <textarea v-model="query" rows="3" placeholder="请输入要检索的问题或关键词" />
      </label>
      <label>
        检索策略
        <select v-model="retrievalSelection">
          <option value="inherit">使用知识库默认策略</option>
          <option value="semantic">分级融合：语义检索</option>
          <option value="hybrid">分级融合：混合检索</option>
          <option value="auto">分级融合：自动选级</option>
          <option value="graph">分级融合：Local 图增强</option>
          <option value="event_graph">事件关联检索</option>
          <option value="global">分级融合：Global 主题综合</option>
          <option value="drift">分级融合：DRIFT 有界探索</option>
        </select>
      </label>
      <label v-if="['graph', 'event_graph', 'global', 'drift', 'auto'].includes(retrievalSelection)">
        能力不可用时
        <select v-model="fallbackPolicy"><option value="hybrid">回退混合检索并说明原因</option><option value="error">返回错误（严格对比）</option></select>
      </label>
      <fieldset v-if="['drift', 'auto'].includes(retrievalSelection)" class="grid">
        <legend>探索总预算</legend>
        <label>最多轮数 <input v-model.number="explorationBudget.max_rounds" type="number" min="1" max="3" /></label>
        <label>最多局部检索 <input v-model.number="explorationBudget.max_searches" type="number" min="1" max="8" /></label>
        <label>总候选额度 <input v-model.number="explorationBudget.max_candidates" type="number" min="3" max="100" /></label>
        <label>最多模型调用 <input v-model.number="explorationBudget.max_model_calls" type="number" min="2" max="13" /></label>
        <label>总时间上限（秒） <input v-model.number="explorationBudget.timeout_seconds" type="number" min="1" max="180" /></label>
      </fieldset>
      <div class="grid">
        <label>返回数量 <input v-model.number="topK" type="number" min="1" max="100" /></label>
        <label>向量阈值 <input v-model.number="vectorThreshold" type="number" min="0" max="1" step="0.01" /></label>
        <label>关键词阈值 <input v-model.number="keywordThreshold" type="number" min="0" max="1" step="0.01" /></label>
      </div>
      <label>
        融合策略
        <select v-model="fusionStrategy">
          <option value="rrf">RRF</option>
          <option value="weighted">加权分数</option>
        </select>
      </label>
      <div class="grid">
        <label>RRF 参数 <input v-model.number="rrfK" type="number" min="1" /></label>
        <label>向量权重 <input v-model.number="vectorWeight" type="number" min="0" step="0.1" /></label>
        <label>关键词权重 <input v-model.number="keywordWeight" type="number" min="0" step="0.1" /></label>
      </div>
      <label v-if="usesLegacyControls" class="check"><input v-model="useVector" type="checkbox" /> 启用向量检索</label>
      <label v-if="usesLegacyControls" class="check"><input v-model="useKeyword" type="checkbox" /> 启用关键词检索</label>
      <label class="check"><input v-model="includeContext" type="checkbox" /> 返回上下文预览</label>
      <div class="card" style="margin-top: 12px;">
        <h3>父子块上下文设置</h3>
        <label class="check"><input v-model="includeParentContext" type="checkbox" /> 返回父块上下文</label>
        <label class="check"><input v-model="mergeHitsByParent" type="checkbox" /> 按父块聚合命中子块</label>
        <label>父块返回模式
          <select v-model="parentContextMode">
            <option value="compact">精简模式</option>
            <option value="standard">标准模式</option>
            <option value="full">完整模式</option>
          </select>
        </label>
        <div class="grid">
          <label>父块上下文长度 <input v-model.number="parentContextMaxChars" type="number" min="0" max="50000" /></label>
          <label>前后邻居长度 <input v-model.number="neighborContextMaxChars" type="number" min="0" max="20000" /></label>
          <label>每个父块相关子块数 <input v-model.number="parentChildrenLimit" type="number" min="0" max="500" /></label>
          <label>最多返回父块数 <input v-model.number="maxParentContexts" type="number" min="0" max="100" /></label>
        </div>
        <p class="text-muted">检索仍先召回子块；开启聚合后，后端会按 parent_chunk_id 分组，并按父块分数返回父块上下文。</p>
      </div>
      <div class="card" style="margin-top: 12px;">
        <h3>大模型上下文构造层</h3>
        <p class="text-muted">这里显示的是清洗、筛选、限长后准备发送给大模型或 MCP 的内容，不包含分数、chunk id、调试标签等检索内部信息。</p>
        <div class="grid">
          <label>上下文最大长度 <input v-model.number="llmMaxContextChars" type="number" min="500" max="100000" /></label>
          <label>最多资料数 <input v-model.number="llmMaxSources" type="number" min="1" max="100" /></label>
        </div>
        <label class="check"><input v-model="llmIncludeImages" type="checkbox" /> 包含图片 / chart 的语义描述</label>
        <label class="check"><input v-model="llmIncludeImageUrls" type="checkbox" /> 包含图片 URL（仅在允许发给多模态云端模型时开启）</label>
      </div>

      <p v-if="error" class="text-error">{{ error }}</p>
      <p v-if="message" class="text-success">{{ message }}</p>
      <button :disabled="loading || comparing">{{ loading ? '检索中...' : '开始检索' }}</button>
    </form>

    <section v-if="lastSearch && retrievalDiagnostics?.contract === 'new_profile_v2'" class="card">
      <h2>同题召回对比</h2>
      <p class="text-muted">沿用上次完成检索的问题、知识库、阈值和证据预算。点击后追加一次严格检索，索引覆盖与实际路线见详情。</p>
      <label>对比策略
        <select v-model="comparisonSelection" :disabled="comparing || loading">
          <option value="semantic">语义</option><option value="hybrid">混合</option>
          <option value="graph">Local</option><option value="event_graph">事件关联</option>
          <option value="global">Global</option><option value="drift">DRIFT</option><option value="auto">自动选级</option>
        </select>
      </label>
      <button type="button" class="secondary" :disabled="comparing || loading" @click="compareStrategy">{{ comparing ? '对比中...' : '运行一次对比' }}</button>
      <p v-if="comparisonError" class="text-error" role="alert">{{ comparisonError }}</p>
      <div v-if="comparison" aria-live="polite">
        <p>原策略返回 {{ results.length }} 条，对比策略返回 {{ comparison.chunks.length }} 条。</p>
        <p v-for="item in comparison.chunks" :key="item.chunk_id">
          {{ item.title }} <button type="button" class="secondary" @click="router.push(`/chunks/${item.chunk_id}`)">查看对比原文</button>
        </p>
        <details><summary>查看对比实际路线、覆盖和耗时</summary><pre>{{ JSON.stringify(comparison.diagnostics, null, 2) }}</pre></details>
      </div>
    </section>

    <section v-if="llmContext || llmLoading || llmError" class="card llm-context-card">
      <div class="page-header">
        <div>
          <h2>大模型上下文构造结果</h2>
          <p class="text-muted" v-if="llmContext">
            {{ llmContext.meta.context_chars }} / {{ llmContext.meta.max_context_chars }} 字 ·
            资料 {{ llmContext.meta.source_count }} 条 ·
            图片证据 {{ llmContext.meta.image_count }} 条
            <span v-if="llmContext.meta.is_truncated"> · 已截断</span>
          </p>
          <p class="text-muted" v-else>检索后自动生成清洗后的 LLM/MCP 上下文。</p>
        </div>
        <div class="actions-row">
          <button type="button" class="secondary" :disabled="llmLoading || loading || !query.trim()" @click="refreshLlmContext">{{ llmLoading ? '检索中...' : '重新检索与构造上下文' }}</button>
          <button type="button" :disabled="!llmContext?.context_text" @click="copyLlmContext">复制上下文</button>
        </div>
      </div>
      <p v-if="llmError" class="text-error">{{ llmError }}</p>
      <textarea v-if="llmContext" class="llm-context-textarea" readonly :value="llmContext.context_text" />
      <div v-if="llmContext?.sources?.length" class="llm-source-list">
        <strong>资料来源摘要</strong>
        <ul>
          <li v-for="source in llmContext.sources" :key="String(source.source_id)">
            {{ source.summary_text || `${source.source_id} · ${source.document_title || '未知文档'}` }}
            <!-- {{ source.source_id }} · {{ source.document_title || '未知文档' }}
            <span v-if="source.matched_child_count"> · 命中 {{ source.matched_child_count }} 个子块</span>
            <span v-if="source.evidence_count"> · 证据 {{ source.evidence_count }} 条</span> -->
          </li>
        </ul>
      </div>
    </section>

    <div v-if="uniqueDocuments.length" class="card hit-documents-card">
      <div class="page-header">
        <h2>命中文档 ({{ uniqueDocuments.length }})</h2>
      </div>
      <ul class="doc-link-list">
        <li v-for="doc in uniqueDocuments" :key="doc.id">
          <router-link :to="`/documents/${doc.id}/content`">{{ doc.title }}</router-link>
          <span v-if="showDocumentFileName(doc)" class="text-muted"> · {{ doc.fileName }}</span>
        </li>
      </ul>
    </div>

    <div v-if="!results.length" class="empty-state">
      <div class="empty-icon">◎</div>
      <div>暂无检索结果</div>
      <small>输入查询内容后点击&quot;开始检索&quot;</small>
    </div>

    <div v-else-if="parentGroups.length" class="result-list">
      <article v-for="(group, index) in parentGroups" :key="group.parent_key" class="card parent-result-card">
        <div class="page-header">
          <div>
            <h2>父块 #{{ index + 1 }} · 命中 {{ group.matched_child_count }} 个子块</h2>
            <p class="text-muted">父块分数 {{ formatScore(group.score) }} · 最高子块分数 {{ formatScore(group.max_child_score) }}</p>
          </div>
          <button v-if="group.matched_children?.[0]?.document_id" type="button" @click="router.push(`/documents/${group.matched_children[0].document_id}/content`)">查看文档</button>
          <button v-if="group.parent_chunk_id" type="button" @click="router.push(`/chunks/${group.parent_chunk_id}`)">查看父块</button>
        </div>
        <dl class="details">
          <dt>父块上下文</dt>
          <dd>
            <div class="context-meta">{{ contextLengthText(group.parent_context) }}</div>
            <div class="preview parent-context-preview">{{ parentContextText(group.parent_context) }}</div>
          </dd>
          <dt>命中子块</dt>
          <dd>
            <div v-for="child in group.matched_children" :key="child.chunk_id" class="preview child-hit-card">
              <div class="child-title"><strong>{{ child.chunk_type }} #{{ child.chunk_index }}</strong> · 分数 {{ formatScore(child.score) }}</div>
              <img v-if="isImageLike(child) && imageUrl(child)" class="retrieval-thumb" :src="imageUrl(child)" alt="命中图片" />
              <div>{{ displayText(child) }}</div>
            </div>
          </dd>
          <dt>同父块相关子块</dt>
          <dd>
            <div v-for="child in group.parent_children" :key="child.chunk_id" class="preview child-hit-card">
              <div class="child-title"><strong>{{ child.relation || 'related' }} · {{ child.chunk_type }} #{{ child.chunk_index }}</strong></div>
              <img v-if="isImageLike(child) && imageUrl(child)" class="retrieval-thumb" :src="imageUrl(child)" alt="相关图片" />
              <div>{{ child.display_preview || child.content_preview || '-' }}</div>
            </div>
          </dd>
        </dl>
      </article>
    </div>

    <div v-else class="result-list">
      <article v-for="(item, index) in results" :key="item.chunk_id" class="card">
        <div class="page-header">
          <div>
            <h2>#{{ index + 1 }} {{ item.knowledge_title || item.title || '未命名文档' }}</h2>
            <p class="text-muted">{{ item.knowledge_filename || item.file_name }} · 切片 {{ item.chunk_index }}</p>
          </div>
          <button v-if="item.document_id || item.knowledge_id" type="button" @click="router.push(`/documents/${(item.document_id || item.knowledge_id)}/content`)">查看文档</button>
          <button type="button" @click="router.push(`/chunks/${item.chunk_id}`)">查看切片</button>
        </div>
        <dl class="details">
          <dt>文档标题</dt><dd>{{ item.knowledge_title || item.title || '-' }}</dd>
          <dt>文件名</dt><dd>{{ item.knowledge_filename || item.file_name || '-' }}</dd>
          <dt>知识 ID</dt><dd>{{ item.knowledge_id || item.document_id }}</dd>
          <dt>起始位置</dt><dd>{{ item.start_at ?? '-' }}</dd>
          <dt>结束位置</dt><dd>{{ item.end_at ?? '-' }}</dd>
          <dt>切片类型</dt><dd>{{ item.chunk_type || 'text' }}</dd>
          <dt>上下文标题</dt><dd>{{ item.context_header || '-' }}</dd>
          <dt>RRF 分数</dt><dd>{{ formatScore(item.rrf_score) }}</dd>
          <dt>总分</dt><dd>{{ formatScore(item.score) }}</dd>
          <dt>向量分数</dt><dd>{{ formatScore(item.vector_score) }}</dd>
          <dt>关键词分数</dt><dd>{{ formatScore(item.keyword_score) }}</dd>
          <dt>向量排名</dt><dd>{{ item.vector_rank ?? '-' }}</dd>
          <dt>关键词排名</dt><dd>{{ item.keyword_rank ?? '-' }}</dd>
          <dt>匹配类型</dt><dd>{{ item.match_type }}</dd>
          <dt>命中图片</dt>
          <dd>
            <img v-if="isImageLike(item) && imageUrl(item)" class="retrieval-thumb" :src="imageUrl(item)" alt="命中图片" />
            <span v-else>-</span>
          </dd>
          <dt>命中文本</dt><dd class="preview">{{ displayText(item) }}</dd>
          <dt>父切片</dt>
          <dd>
            <div class="context-meta">{{ contextLengthText(item.context?.parent) }}</div>
            <div class="preview">{{ parentContextText(item.context?.parent) }}</div>
          </dd>
          <dt>前置切片</dt><dd class="preview">{{ item.context?.previous?.display_preview || item.context?.previous?.content_preview || '-' }}</dd>
          <dt>后置切片</dt><dd class="preview">{{ item.context?.next?.display_preview || item.context?.next?.content_preview || '-' }}</dd>
          <dt>元数据</dt><dd><pre>{{ metadataPreview(item) }}</pre></dd>
        </dl>
      </article>
    </div>
  </section>
</template>

<style scoped>
.context-meta {
  color: #64748b;
  font-size: 12px;
  margin-bottom: 8px;
}

.parent-context-preview {
  white-space: pre-wrap;
  line-height: 1.7;
}

.child-hit-card + .child-hit-card {
  margin-top: 12px;
}

.child-title {
  margin-bottom: 6px;
}

.retrieval-thumb {
  display: block;
  max-width: 260px;
  max-height: 180px;
  object-fit: contain;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  background: #f8fafc;
  margin: 8px 0;
}

.llm-context-card {
  margin-top: 16px;
}

.llm-context-textarea {
  width: 100%;
  min-height: 360px;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  line-height: 1.6;
  white-space: pre-wrap;
}

.llm-source-list {
  margin-top: 12px;
  color: #334155;
  font-size: 13px;
}

.actions-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

</style>
