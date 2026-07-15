<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deleteChunk, disableChunk, enableChunk, getChunkTree, listChunks, type ChunkItem, type ChunkTreeParent } from '../api/chunks'

const route = useRoute()
const router = useRouter()
const documentId = ref((route.params.docId as string) || (route.query.doc_id as string) || '')
const items = ref<ChunkItem[]>([])
const treeItems = ref<ChunkTreeParent[]>([])
const orphanItems = ref<ChunkItem[]>([])
const treeStats = ref<{ total_chunks?: number; parent_count?: number; orphan_count?: number; chunk_type_counts?: Record<string, number> } | null>(null)
const error = ref('')
const loading = ref(false)
const treeLoading = ref(false)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const typeFilter = ref('all')
const indexFilter = ref('all')
const viewMode = ref<'list' | 'tree'>('list')
const expandedParents = ref<Record<string, boolean>>({})
const previewImage = ref<{ url: string; title: string; info?: Record<string, unknown> } | null>(null)

const statusText: Record<string, string> = {
  ready: '可用',
  disabled: '已禁用'
}

const pageCount = computed(() => Math.max(Math.ceil(total.value / pageSize.value), 1))
const filteredItems = computed(() => items.value)
const rangeText = computed(() => {
  if (!total.value) return '0 / 0'
  const start = (page.value - 1) * pageSize.value + 1
  const end = Math.min(page.value * pageSize.value, total.value)
  return `${start}-${end} / ${total.value}`
})

function preview(value?: string | null, limit = 180) {
  if (!value) return '-'
  return value.length > limit ? `${value.slice(0, limit)}...` : value
}

function skipReason(item: ChunkItem) {
  return item.embedding_index_info?.skip_reason || item.skip_reason || ''
}

function isEmbeddingSkipped(item: ChunkItem) {
  return Boolean(item.embedding_index_info?.skip_embedding_recommended || item.skip_embedding_recommended)
}

function indexedText(item: ChunkItem) {
  if (item.embedding_index_info?.is_indexed) return '已向量化'
  if (isEmbeddingSkipped(item)) return '规则跳过'
  return '未向量化'
}

function indexedClass(item: ChunkItem) {
  if (item.embedding_index_info?.is_indexed) return 'tag-ok'
  if (isEmbeddingSkipped(item)) return 'tag-muted'
  return 'tag-warn'
}

const skipReasonText: Record<string, string> = {
  placeholder_table: '表格占位，无有效文本',
  placeholder_image: '图片占位，无 OCR/VLM 描述',
  placeholder_equation: '公式占位，无有效文本',
  placeholder_media: '媒体占位',
  empty_content: '空内容',
  low_information_text: '低信息文本',
  orphan_short_heading: '孤立短标题',
  orphan_short_chunk: '孤立短块，已避免单独向量化',
  page_noise: '页眉/页脚/页码噪声',
  header_footer_noise: '页眉页脚噪声',
  pure_url_or_path: '纯 URL / 文件路径',
  embedding_content_empty: '无可用于向量化的语义文本'
}

function skipReasonLabel(item: ChunkItem) {
  const reason = skipReason(item)
  return reason ? (skipReasonText[reason] || reason) : ''
}

function normalizeFileUrl(value?: unknown) {
  if (typeof value !== 'string' || !value.trim()) return ''
  let url = value.trim()
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:') || url.startsWith('blob:')) return url
  if (url.startsWith('/files//files/')) url = url.replace('/files//files/', '/files/')
  if (url.startsWith('/files/')) return url
  if (url.startsWith('files/')) return `/${url}`
  if (url.startsWith('/data/files/')) return `/files/${url.replace('/data/files/', '')}`
  if (url.startsWith('minio://')) {
    const withoutScheme = url.replace('minio://', '')
    const parts = withoutScheme.split('/')
    parts.shift()
    return `/files/${parts.join('/')}`
  }
  return `/files/${url.replace(/^\/+/, '')}`
}

function imageUrl(info?: Record<string, unknown>) {
  if (!info) return ''
  const nested = typeof info.image_info === 'object' && info.image_info ? (info.image_info as Record<string, unknown>) : undefined
  const base64 = info.image_base64 || info.base64 || nested?.image_base64 || nested?.base64
  return normalizeFileUrl(info.url)
    || normalizeFileUrl(info.image_url)
    || normalizeFileUrl(nested?.url)
    || normalizeFileUrl(nested?.image_url)
    || normalizeFileUrl(info.relative_path)
    || normalizeFileUrl(nested?.relative_path)
    || normalizeFileUrl(info.path)
    || normalizeFileUrl(info.image_path)
    || normalizeFileUrl(nested?.path)
    || normalizeFileUrl(nested?.image_path)
    || (typeof base64 === 'string' && base64 ? (base64.startsWith('data:') ? base64 : `data:image/png;base64,${base64}`) : '')
}

function typeLabel(type?: string) {
  if (type === 'image') return '图片'
  if (type === 'table') return '表格'
  if (type === 'parent_text') return '父文本'
  return '文本'
}

function typeClass(type?: string) {
  return `tag type-${type || 'text'}`
}

function chunkSubtype(item: ChunkItem) {
  const meta = item.metadata_json || item.metadata || {}
  const subtype = meta.visual_subtype || meta.subtype || meta.raw_block_type || meta.type
  return typeof subtype === 'string' && subtype ? subtype : ''
}

function isEquation(item: ChunkItem) {
  const meta = item.metadata_json || item.metadata || {}
  return meta.is_equation === true || meta.subtype === 'equation'
}

function openImage(item: ChunkItem) {
  const url = imageUrl(item.image_info)
  if (!url) return
  previewImage.value = { url, title: `${typeLabel(item.chunk_type)} #${item.chunk_index}`, info: item.image_info }
}

function contentPreview(item: ChunkItem) {
  return item.content || item.content_preview || item.preview || '-'
}

async function load() {
  if (!documentId.value) {
    error.value = '请先上传文档'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await listChunks(documentId.value, page.value, pageSize.value, typeFilter.value, indexFilter.value)
    items.value = result.items
    total.value = result.total
    page.value = result.page
    pageSize.value = result.page_size
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    loading.value = false
  }
}

async function loadTree() {
  if (!documentId.value) return
  treeLoading.value = true
  error.value = ''
  try {
    const result = await getChunkTree(documentId.value)
    treeItems.value = result.items
    orphanItems.value = result.orphans || []
    treeStats.value = result.stats || null
    const nextExpanded: Record<string, boolean> = {}
    result.items.forEach((node, index) => {
      nextExpanded[node.parent.id] = index < 3
    })
    expandedParents.value = nextExpanded
  } catch (err) {
    error.value = err instanceof Error ? err.message : '父子树加载失败'
  } finally {
    treeLoading.value = false
  }
}

async function refreshCurrentView() {
  if (viewMode.value === 'tree') {
    await loadTree()
  } else {
    await load()
  }
}

async function toggle(item: ChunkItem) {
  try {
    if (item.is_enabled) {
      await disableChunk(item.id)
    } else {
      await enableChunk(item.id)
    }
    await refreshCurrentView()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
}

async function remove(id: string) {
  if (!confirm('确认删除这个切片？')) return
  try {
    await deleteChunk(id)
    await refreshCurrentView()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

function nextPage() {
  if (page.value < pageCount.value) {
    page.value += 1
    load()
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value -= 1
    load()
  }
}

function toggleParent(parentId: string) {
  expandedParents.value[parentId] = !expandedParents.value[parentId]
}

watch(pageSize, () => {
  page.value = 1
  if (viewMode.value === 'list') load()
})

watch([typeFilter, indexFilter], () => {
  page.value = 1
  if (viewMode.value === 'list') load()
})

watch(viewMode, (mode) => {
  if (mode === 'tree' && !treeItems.value.length) loadTree()
})

onMounted(load)
</script>

<template>
  <section class="page wide-page">
    <div class="page-header hero-header">
      <div>
        <p class="eyebrow">Chunks</p>
        <h1>切片管理</h1>
        <p class="text-muted">按页查看文档解析后的文本、表格、图片切片，检查索引内容、向量化状态和父子块关系。</p>
      </div>
      <div class="header-actions">
        <button class="secondary" @click="router.back()">返回</button>
        <button @click="refreshCurrentView">刷新</button>
      </div>
    </div>

    <div class="toolbar panel compact-panel">
      <div class="toolbar-group">
        <label>视图
          <select v-model="viewMode">
            <option value="list">列表视图</option>
            <option value="tree">父子树视图</option>
          </select>
        </label>
        <template v-if="viewMode === 'list'">
          <label>类型
            <select v-model="typeFilter">
              <option value="all">全部</option>
              <option value="text">文本</option>
              <option value="parent_text">父文本</option>
              <option value="table">表格</option>
              <option value="image">图片</option>
            </select>
          </label>
          <label>向量化
            <select v-model="indexFilter">
              <option value="all">全部</option>
              <option value="indexed">已向量化</option>
              <option value="missing">全部未向量化</option>
              <option value="pending">异常未向量化</option>
              <option value="skipped">规则跳过</option>
            </select>
          </label>
          <label>每页
            <select v-model.number="pageSize">
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </label>
        </template>
      </div>
      <div v-if="viewMode === 'list'" class="pager">
        <button class="secondary" :disabled="page <= 1 || loading" @click="prevPage">上一页</button>
        <span>{{ rangeText }}</span>
        <button class="secondary" :disabled="page >= pageCount || loading" @click="nextPage">下一页</button>
      </div>
      <div v-else class="pager">
        <span v-if="treeStats">父块 {{ treeStats.parent_count || 0 }} · 孤儿 {{ treeStats.orphan_count || 0 }}</span>
      </div>
    </div>

    <p v-if="error" class="text-error">{{ error }}</p>

    <template v-if="viewMode === 'tree'">
      <div v-if="treeLoading" class="card"><p class="text-muted">父子树加载中...</p></div>
      <div v-else-if="!treeItems.length && !orphanItems.length" class="empty-state">
        <div class="empty-icon">▦</div>
        <div>暂无父子块数据。</div>
      </div>
      <div v-else class="parent-tree-list">
        <article v-for="node in treeItems" :key="node.parent.id" class="card parent-node-card">
          <div class="parent-node-header" @click="toggleParent(node.parent.id)">
            <div>
              <span class="chunk-index">#{{ node.parent.chunk_index }}</span>
              <span :class="typeClass(node.parent.chunk_type)">{{ typeLabel(node.parent.chunk_type) }}</span>
              <strong>{{ node.parent.context_header || '父块上下文' }}</strong>
              <span class="tag tag-muted">{{ node.child_count }} 个子块</span>
              <span v-if="node.has_image_child" class="tag">含图片</span>
              <span v-if="node.has_table_child" class="tag">含表格</span>
              <span v-if="node.has_equation_child" class="tag">含公式</span>
            </div>
            <button class="secondary" type="button">{{ expandedParents[node.parent.id] ? '收起' : '展开' }}</button>
          </div>
          <pre class="chunk-full-content">{{ contentPreview(node.parent) }}</pre>
          <div v-if="expandedParents[node.parent.id]" class="child-node-list">
            <article v-for="child in node.children" :key="child.id" class="child-node-row">
              <div class="child-media">
                <img v-if="imageUrl(child.image_info)" :src="imageUrl(child.image_info)" class="chunk-thumb clickable" @click.stop="openImage(child)" />
                <div v-else class="media-placeholder small-placeholder">{{ typeLabel(child.chunk_type).slice(0, 1) }}</div>
              </div>
              <div class="child-main">
                <div class="chunk-title-row">
                  <div>
                    <span class="chunk-index">#{{ child.chunk_index }}</span>
                    <span :class="typeClass(child.chunk_type)">{{ isEquation(child) ? '公式' : typeLabel(child.chunk_type) }}</span>
                    <span v-if="chunkSubtype(child)" class="tag tag-muted">{{ chunkSubtype(child) }}</span>
                    <span :class="['tag', indexedClass(child)]">{{ indexedText(child) }}</span>
                  </div>
                  <div class="actions slim-actions">
                    <button @click.stop="router.push(`/chunks/${child.id}`)">查看</button>
                    <button class="secondary" @click.stop="toggle(child)">{{ child.is_enabled ? '禁用' : '启用' }}</button>
                  </div>
                </div>
                <p v-if="child.context_header" class="context-line">{{ preview(child.context_header, 120) }}</p>
                <p v-if="skipReasonLabel(child)" class="context-line skip-line">跳过原因：{{ skipReasonLabel(child) }}</p>
                <pre class="chunk-full-content">{{ contentPreview(child) }}</pre>
              </div>
            </article>
          </div>
        </article>

        <article v-if="orphanItems.length" class="card parent-node-card orphan-card">
          <div class="parent-node-header"><strong>未挂载父块的子块</strong><span class="tag tag-warn">{{ orphanItems.length }}</span></div>
          <div class="child-node-list">
            <article v-for="child in orphanItems" :key="child.id" class="child-node-row">
              <div class="child-main">
                <span class="chunk-index">#{{ child.chunk_index }}</span>
                <span :class="typeClass(child.chunk_type)">{{ typeLabel(child.chunk_type) }}</span>
                <pre class="chunk-full-content">{{ contentPreview(child) }}</pre>
              </div>
            </article>
          </div>
        </article>
      </div>
    </template>

    <template v-else>
      <div v-if="loading" class="card"><p class="text-muted">加载中...</p></div>
      <div v-else-if="!items.length" class="empty-state">
        <div class="empty-icon">▦</div>
        <div>暂无切片</div>
      </div>
      <div v-else class="chunk-list">
        <article v-for="item in filteredItems" :key="item.id" class="chunk-card">
          <div class="chunk-media">
            <img v-if="imageUrl(item.image_info)" :src="imageUrl(item.image_info)" class="chunk-thumb clickable" @click="openImage(item)" />
            <div v-else class="media-placeholder">{{ typeLabel(item.chunk_type).slice(0, 1) }}</div>
          </div>
          <div class="chunk-main">
            <div class="chunk-title-row">
              <div>
                <span class="chunk-index">#{{ item.chunk_index }}</span>
                <span :class="typeClass(item.chunk_type)">{{ isEquation(item) ? '公式' : typeLabel(item.chunk_type) }}</span>
                <span v-if="chunkSubtype(item)" class="tag tag-muted">{{ chunkSubtype(item) }}</span>
                <span :class="['tag', indexedClass(item)]">{{ indexedText(item) }}</span>
                <span class="tag tag-muted">{{ statusText[item.status] || item.status }}</span>
              </div>
              <div class="actions slim-actions">
                <button @click="router.push(`/chunks/${item.id}`)">查看</button>
                <button class="secondary" @click="toggle(item)">{{ item.is_enabled ? '禁用' : '启用' }}</button>
                <button class="danger" @click="remove(item.id)">删除</button>
              </div>
            </div>
            <p v-if="item.context_header" class="context-line">{{ preview(item.context_header, 120) }}</p>
            <p v-if="skipReasonLabel(item)" class="context-line skip-line">跳过原因：{{ skipReasonLabel(item) }}</p>
            <pre class="chunk-full-content">{{ contentPreview(item) }}</pre>
            <div class="chunk-meta-line">
              <span>长度 {{ item.token_count }}</span>
              <span>位置 {{ item.start_at ?? '-' }} - {{ item.end_at ?? '-' }}</span>
              <span v-if="item.parent_chunk_id">父块 {{ item.parent_chunk_id.slice(0, 8) }}</span>
              <span v-if="item.embedding_index_info?.dimension">维度 {{ item.embedding_index_info.dimension }}</span>
              <span v-if="item.embedding_index_info?.model_name">{{ item.embedding_index_info.model_name }}</span>
            </div>
          </div>
        </article>
        <div v-if="!filteredItems.length" class="empty-state"><div>当前筛选条件下没有切片。</div></div>
      </div>
    </template>

    <div v-if="previewImage" class="modal-backdrop" @click="previewImage = null">
      <div class="modal" @click.stop>
        <div class="page-header">
          <h2>{{ previewImage.title }}</h2>
          <button class="secondary" @click="previewImage = null">关闭</button>
        </div>
        <img :src="previewImage.url" class="preview-image" />
        <details class="card compact">
          <summary>图片元数据</summary>
          <pre>{{ JSON.stringify(previewImage.info || {}, null, 2) }}</pre>
        </details>
      </div>
    </div>
  </section>
</template>

<style scoped>
.parent-tree-list {
  display: grid;
  gap: 14px;
}
.parent-node-card {
  border-left: 4px solid var(--border-color, rgba(148, 163, 184, 0.35));
}
.parent-node-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
}
.parent-node-header > div {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.child-node-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
  padding-left: 18px;
  border-left: 1px dashed rgba(148, 163, 184, 0.45);
}
.child-node-row {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 12px;
  padding: 10px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 12px;
  background: rgba(148, 163, 184, 0.06);
}
.child-media {
  display: flex;
  align-items: flex-start;
  justify-content: center;
}
.small-placeholder {
  width: 48px;
  height: 48px;
  font-size: 14px;
}
.orphan-card {
  border-left-color: #f59e0b;
}
.skip-line {
  color: #92400e;
}
</style>
