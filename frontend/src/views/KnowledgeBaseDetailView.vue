<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  embedKnowledgeBase,
  getCommunityReports,
  getKnowledgeBase,
  getKnowledgeBaseEmbeddingStatus,
  getKnowledgeIndexStatus,
  buildKnowledgeIndex,
  buildCommunityIndex,
  type CommunityReportItem,
  type KnowledgeIndexStatus,
  type KnowledgeBase,
  type KnowledgeBaseEmbeddingStatus
} from '../api/knowledgeBases'

type TabId = 'overview' | 'index' | 'graph' | 'summaries' | 'settings'

const route = useRoute()
const router = useRouter()
const kb = ref<KnowledgeBase | null>(null)
const embeddingStatus = ref<KnowledgeBaseEmbeddingStatus | null>(null)
const error = ref('')
const message = ref('')
const loading = ref(false)
const statusLoading = ref(false)
const entityStatus = ref<KnowledgeIndexStatus | null>(null)
const entityLoading = ref(false)
const activeTab = ref<TabId>('overview')
const communityReports = ref<CommunityReportItem[]>([])
const communityStatus = ref<Record<string, unknown> | null>(null)
const communityLoading = ref(false)

// 历史库没有 family_scope，按 A 展示，避免详情页出现空白；保存时仍要求显式选择。
const familyScopeRaw = computed(() => kb.value?.retrieval_profile?.family_scope)
const familyScope = computed(() => familyScopeRaw.value === 'b' ? 'b' : 'a')
const familyTitle = computed(() => familyScopeRaw.value === 'b'
  ? '事件型（B）'
  : familyScopeRaw.value === 'a'
    ? '关系型（A）'
    : '历史库（未设置家族）')
const BASE_TABS: Array<{ id: TabId; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'index', label: '索引' },
  { id: 'graph', label: '图谱' },
  { id: 'summaries', label: '主题摘要' },
  { id: 'settings', label: '检索设置' },
]
// B 家族不构建社区，因此隐藏只对社区报告有意义的“主题摘要”页签。
const tabs = computed(() => familyScope.value === 'b'
  ? BASE_TABS.filter((tab) => tab.id !== 'summaries')
  : BASE_TABS)
const buildIndexes = computed(() => kb.value?.retrieval_profile?.build_indexes || [])
const hasIndexes = computed(() => buildIndexes.value.length > 0)
const hasCommunities = computed(() => buildIndexes.value.includes('communities'))
const counts = computed(() => entityStatus.value?.counts)
const strategy = computed(() => kb.value?.retrieval_profile?.strategy)

const STRATEGY_NAMES: Record<string, string> = {
  semantic: '仅语义检索',
  hybrid: '标准混合检索',
  auto: '自动选择',
  fusion: '关系 + 主题背景',
  local: 'Local 关系增强',
  global: 'Global 主题综合',
  drift: 'DRIFT 有界探索',
  event_graph: '事件关联',
}

const strategyTitle = computed(() => {
  const value = strategy.value
  if (!value) return '沿用全局配置'
  if (value.family === 'event_graph') return STRATEGY_NAMES.event_graph
  if (value.tier === 'graph') return STRATEGY_NAMES[value.graph_method || 'local'] || '图增强'
  return STRATEGY_NAMES[value.tier] || value.tier
})

const strategyEntries = computed(() => {
  const value = strategy.value
  if (!value) return []
  const rows: Array<{ label: string; value: string }> = [{ label: '知识库家族', value: familyTitle.value }]
  if (value.family === 'event_graph') {
    rows.push({ label: '策略家族', value: 'event_graph' })
  } else {
    rows.push({ label: '策略级别', value: value.tier })
    if (value.graph_method) rows.push({ label: '图方法', value: value.graph_method })
    if (value.tier === 'fusion' && value.fusion) {
      rows.push({ label: '排序模式', value: value.fusion.rank_mode || 'rrf' })
      rows.push({ label: 'Global 背景', value: value.fusion.include_global ? '开启' : '关闭' })
    }
  }
  rows.push({ label: '回退策略', value: value.fallback_policy || 'hybrid' })
  return rows
})

const budgetEntries = computed(() => Object.entries(strategy.value?.budget || {}))

const coverageText = computed(() => {
  const status = entityStatus.value
  if (!status) return ''
  const docs = status.documents || {}
  return `完整覆盖 ${status.covered_documents}/${status.total_documents} 篇；排队 ${docs.pending || 0}，处理中 ${docs.running || 0}，部分完成 ${docs.partial || 0}，失败 ${docs.failed || 0}，过期 ${docs.stale || 0}，未构建 ${docs.missing || 0}。`
})

async function loadCommunityReports() {
  if (!kb.value) return
  communityLoading.value = true
  try {
    const result = await getCommunityReports(kb.value.id)
    communityReports.value = result.items || []
    communityStatus.value = result.status || null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取主题摘要失败'
  } finally {
    communityLoading.value = false
  }
}

async function retryCommunities() {
  if (!kb.value) return
  communityLoading.value = true
  try {
    const result = await buildCommunityIndex(kb.value.id)
    message.value = `社区任务状态：${result.state}，等待 worker 处理`
    await Promise.all([loadEntityStatus(), loadCommunityReports()])
  } catch (err) {
    error.value = err instanceof Error ? err.message : '社区任务提交失败'
  } finally {
    communityLoading.value = false
  }
}

async function loadEntityStatus(build = false, retry = false) {
  if (!kb.value) return
  entityLoading.value = true
  try {
    if (build) {
      const result = await buildKnowledgeIndex(kb.value.id, retry)
      message.value = `已新增 ${result.created_jobs} 个索引任务，重试 ${result.retried_jobs} 个未完成任务，等待 worker 处理`
    }
    entityStatus.value = await getKnowledgeIndexStatus(kb.value.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '实体索引请求失败'
  } finally {
    entityLoading.value = false
  }
}

async function loadEmbeddingStatus() {
  if (!kb.value) return
  statusLoading.value = true
  error.value = ''
  message.value = ''
  try {
    embeddingStatus.value = await getKnowledgeBaseEmbeddingStatus(kb.value.id)
    message.value = '向量化状态已刷新'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取向量化状态失败'
  } finally {
    statusLoading.value = false
  }
}

async function runEmbedding() {
  if (!kb.value) return
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await embedKnowledgeBase(kb.value.id)
    embeddingStatus.value = {
      knowledge_base_id: result.knowledge_base_id,
      total_chunks: result.total_chunks,
      embedded_chunks: result.embedded_chunks,
      missing_chunks: result.missing_chunks,
      model: result.model,
      dimension: result.dimension
    }
    message.value = '知识库向量生成完成'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '向量生成失败'
  } finally {
    loading.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'summaries' && hasCommunities.value && !communityReports.value.length) {
    void loadCommunityReports()
  }
})

onMounted(async () => {
  try {
    kb.value = await getKnowledgeBase(route.params.id as string)
    await loadEmbeddingStatus()
    if (hasIndexes.value) await loadEntityStatus()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page">
    <p v-if="error" class="text-error">{{ error }}</p>
    <p v-if="message" class="text-success">{{ message }}</p>
    <div v-if="kb" class="card">
      <div class="page-header">
        <div>
          <h1>{{ kb.name }}</h1>
          <p class="text-muted">{{ kb.description || '暂无描述' }}</p>
          <p class="text-muted">当前检索：{{ strategyTitle }}</p>
        </div>
        <div class="actions">
          <button type="button" @click="router.push(`/documents/upload?kb_id=${kb.id}`)">上传文档</button>
          <button type="button" class="secondary" @click="router.push(`/documents?kb_id=${kb.id}`)">查看文档</button>
          <button type="button" class="secondary" @click="router.push(`/knowledge-bases/${kb.id}/edit`)">编辑</button>
        </div>
      </div>

      <div class="kb-tabs" role="tablist" aria-label="知识库视图">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.id"
          :class="{ active: activeTab === tab.id }"
          @click="activeTab = tab.id"
        >{{ tab.label }}</button>
      </div>

      <section v-if="activeTab === 'overview'" role="tabpanel" class="kb-panel">
        <div class="stat-grid">
          <div class="stat-card"><span class="stat-label">文档</span><span class="stat-value">{{ kb.document_count }}</span></div>
          <div class="stat-card"><span class="stat-label">切片</span><span class="stat-value">{{ kb.chunk_count }}</span></div>
          <div class="stat-card"><span class="stat-label">已向量化</span><span class="stat-value">{{ embeddingStatus ? `${embeddingStatus.embedded_chunks}/${embeddingStatus.total_chunks}` : '-' }}</span></div>
          <div class="stat-card"><span class="stat-label">实体</span><span class="stat-value">{{ counts?.entities ?? '-' }}</span></div>
          <template v-if="familyScope === 'a'">
            <div class="stat-card"><span class="stat-label">关系</span><span class="stat-value">{{ counts?.relations ?? '-' }}</span></div>
            <div class="stat-card"><span class="stat-label">社区</span><span class="stat-value">{{ counts?.communities ?? '-' }}</span></div>
            <div class="stat-card"><span class="stat-label">可用报告</span><span class="stat-value">{{ counts?.reports ?? '-' }}</span></div>
          </template>
          <div v-else class="stat-card"><span class="stat-label">事件</span><span class="stat-value">{{ counts?.events ?? '-' }}</span></div>
        </div>
        <dl class="details">
          <dt>检索家族</dt><dd>{{ familyTitle }}</dd>
          <dt>ID</dt><dd>{{ kb.id }}</dd>
          <dt>规则跳过</dt><dd>{{ embeddingStatus?.skipped_chunks ?? '-' }}</dd>
          <dt>异常未向量化</dt><dd>{{ embeddingStatus?.pending_chunks ?? embeddingStatus?.real_missing_chunks ?? '-' }}</dd>
          <dt>向量模型</dt><dd>{{ embeddingStatus?.model || '-' }}</dd>
          <dt>向量维度</dt><dd>{{ embeddingStatus?.dimension || '-' }}</dd>
          <dt>Embedding 模型</dt><dd>{{ kb.embedding_model_id || '-' }}</dd>
          <dt>摘要模型</dt><dd>{{ kb.summary_model_id || '-' }}</dd>
          <dt>创建时间</dt><dd>{{ kb.created_at }}</dd>
          <dt>更新时间</dt><dd>{{ kb.updated_at }}</dd>
        </dl>
        <div class="actions">
          <button type="button" :disabled="loading" @click="runEmbedding">
            {{ loading ? '正在生成向量...' : '批量生成向量' }}
          </button>
          <button type="button" class="secondary" :disabled="statusLoading" @click="loadEmbeddingStatus">
            {{ statusLoading ? '正在获取状态...' : '刷新向量化状态' }}
          </button>
        </div>
      </section>

      <section v-else-if="activeTab === 'index'" role="tabpanel" class="kb-panel">
        <template v-if="hasIndexes">
          <h2>派生检索索引 · {{ familyTitle }}</h2>
          <div class="stat-grid compact">
            <div class="stat-card"><span class="stat-label">实体</span><span class="stat-value">{{ counts?.entities ?? 0 }}</span></div>
            <template v-if="familyScope === 'a'">
              <div class="stat-card"><span class="stat-label">关系</span><span class="stat-value">{{ counts?.relations ?? 0 }}</span></div>
              <div class="stat-card"><span class="stat-label">社区</span><span class="stat-value">{{ counts?.communities ?? 0 }}</span></div>
              <div class="stat-card"><span class="stat-label">可用报告</span><span class="stat-value">{{ counts?.reports ?? 0 }}</span></div>
            </template>
            <div v-else class="stat-card"><span class="stat-label">事件</span><span class="stat-value">{{ counts?.events ?? 0 }}</span></div>
          </div>
          <p v-if="entityStatus" class="text-muted">{{ coverageText }}</p>
          <p v-if="entityStatus && familyScope === 'a'">Local 关系增强：{{ entityStatus.graph_retrieval_ready ? '可用' : '未就绪' }}。</p>
          <p v-if="entityStatus && familyScope === 'b'">事件关联：{{ entityStatus.event_retrieval_ready ? '可用' : '未就绪' }}。</p>
          <p v-if="hasCommunities && entityStatus">Global：{{ entityStatus.global_retrieval_ready ? '可用' : '未就绪' }} · 社区报告状态：{{ entityStatus.community_status?.state || '等待构建' }}。</p>
          <div class="actions">
            <button type="button" :disabled="entityLoading" @click="loadEntityStatus(true)">补建缺失索引</button>
            <button v-if="entityStatus && (entityStatus.documents.partial || entityStatus.documents.failed)" type="button" :disabled="entityLoading" @click="loadEntityStatus(true, true)">重试未完成索引</button>
            <button v-if="hasCommunities" type="button" :disabled="entityLoading || !entityStatus?.graph_retrieval_ready" @click="retryCommunities">重试/补建社区报告</button>
            <button type="button" class="secondary" :disabled="entityLoading" @click="loadEntityStatus()">{{ entityLoading ? '处理中...' : '刷新索引状态' }}</button>
          </div>
        </template>
        <p v-else class="text-muted">该知识库尚未构建派生索引。编辑并保存后会按所选家族自动补建：关系型构建实体、关系和社区报告，事件型构建实体和事件。</p>
        <div v-if="!hasIndexes" class="actions">
          <button type="button" @click="router.push(`/knowledge-bases/${kb.id}/edit`)">编辑知识库</button>
        </div>
      </section>

      <section v-else-if="activeTab === 'graph'" role="tabpanel" class="kb-panel">
        <h2>实体—关系—事件子图</h2>
        <p class="text-muted">图谱展示当前有效、带原文证据的有界子图：实体为节点，抽取关系和事件参与为边。单次最多 80 个节点，超出会标记截断；它不是完整图数据库导出。</p>
        <dl class="details">
          <dt>实体</dt><dd>{{ counts?.entities ?? '-' }}</dd>
          <dt>关系</dt><dd>{{ counts?.relations ?? '-' }}</dd>
          <dt>事件</dt><dd>{{ counts?.events ?? '-' }}</dd>
        </dl>
        <div class="actions">
          <button type="button" :disabled="!hasIndexes" @click="router.push(`/knowledge-bases/${kb.id}/graph`)">打开知识图谱</button>
        </div>
        <p v-if="entityStatus && !entityStatus.graph_retrieval_ready" class="text-muted">关系或事件索引尚未就绪，图谱可能只显示实体节点。</p>
      </section>

      <section v-else-if="activeTab === 'summaries'" role="tabpanel" class="kb-panel">
        <template v-if="hasCommunities">
          <h2>社区主题摘要</h2>
          <p class="text-muted">报告由社区成员资料 map-reduce 生成，属于派生内容，不可作为原文引用；Global 背景和主题综合使用它。</p>
          <p v-if="communityStatus" class="text-muted">状态：{{ communityStatus.state || '未知' }}<span v-if="communityStatus.graph_revision"> · revision {{ communityStatus.graph_revision }}</span></p>
          <p v-if="communityLoading" class="text-muted">正在加载主题摘要...</p>
          <ul v-else-if="communityReports.length" class="summary-list">
            <li v-for="report in communityReports" :key="report.report_id">
              <strong>{{ report.title }}</strong>
              <span class="text-muted"> · {{ report.points?.length || 0 }} 条要点</span>
              <span class="text-muted"> · {{ report.graph_revision }}</span>
            </li>
          </ul>
          <p v-else class="text-muted">当前没有可用报告，可能还在等待关系索引或 worker 处理。</p>
          <div class="actions">
            <button type="button" :disabled="communityLoading" @click="loadCommunityReports">刷新主题摘要</button>
            <button type="button" class="secondary" :disabled="communityLoading || !entityStatus?.graph_retrieval_ready" @click="retryCommunities">补建/重试社区报告</button>
          </div>
        </template>
        <p v-else class="text-muted">当前知识库未开启社区索引。开启社区（需同时开启关系）后才能生成主题摘要。</p>
      </section>

      <section v-else role="tabpanel" class="kb-panel">
        <h2>检索设置</h2>
        <p class="text-muted">当前策略：{{ strategyTitle }}</p>
        <dl class="details">
          <template v-for="entry in strategyEntries" :key="entry.label">
            <dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd>
          </template>
        </dl>
        <template v-if="budgetEntries.length">
          <h2>预算</h2>
          <dl class="details">
            <template v-for="[key, value] in budgetEntries" :key="key">
              <dt>{{ key }}</dt><dd>{{ value }}</dd>
            </template>
          </dl>
        </template>
        <p class="text-muted">检索家族创建后不可更改；需要更换时请删除知识库并按新家族重建。索引依赖由家族自动推导，不需要手动勾选。</p>
        <div class="actions">
          <button type="button" @click="router.push(`/knowledge-bases/${kb.id}/edit`)">编辑检索设置</button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.kb-tabs {
  display: flex;
  gap: 4px;
  margin: 16px 0 18px;
  border-bottom: 1px solid var(--c-border);
  overflow-x: auto;
}
.kb-tabs button {
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  color: var(--t-secondary);
  padding: 9px 14px;
  white-space: nowrap;
}
.kb-tabs button:hover:not(:disabled) {
  background: var(--c-surface-alt);
  transform: none;
  box-shadow: none;
}
.kb-tabs button.active {
  color: var(--t-primary);
  border-bottom-color: var(--c-primary);
  font-weight: 600;
}
.kb-panel {
  padding-top: 4px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin: 14px 0 18px;
}
.stat-grid.compact {
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  margin: 10px 0 14px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--c-border);
  border-radius: 9px;
  background: var(--c-surface-alt);
}
.stat-label {
  color: var(--t-secondary);
  font-size: 12px;
}
.stat-value {
  color: var(--t-primary);
  font-size: 20px;
  font-weight: 600;
}
.summary-list {
  display: grid;
  gap: 8px;
  margin: 12px 0;
  padding: 0;
  list-style: none;
}
.summary-list li {
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-surface-alt);
}
</style>