<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getChunk, type ChunkItem } from '../api/chunks'
import { getDocument, type DocumentItem } from '../api/documents'
import { eventChunk, getParseTaskEvents, type ParseTask, type ParseTaskEvent } from '../api/parseTasks'

const route = useRoute()
const router = useRouter()
const taskId = ref(route.params.taskId as string)
const task = ref<ParseTask | null>(null)
const document = ref<DocumentItem | null>(null)
const events = ref<ParseTaskEvent[]>([])
const chunks = ref<ChunkItem[]>([])
const selected = ref<ParseTaskEvent | ChunkItem | null>(null)
const error = ref('')
const loading = ref(false)
const lastSeq = ref(0)
const eventFilter = ref('all')
let timer: number | undefined

const stages = [
  { key: 'queued', label: '排队' },
  { key: 'prepare', label: '文件接收' },
  { key: 'parse', label: '结构解析' },
  { key: 'ocr', label: 'OCR' },
  { key: 'vlm', label: '图片理解' },
  { key: 'chunk', label: '切片入库' },
  { key: 'done', label: '完成' }
]

const terminal = computed(() => ['completed', 'failed'].includes(task.value?.status || ''))
const selectedChunk = computed(() => selected.value && !('payload' in selected.value) ? selected.value as ChunkItem : null)
const statusLabel = computed(() => {
  if (!task.value) return '加载中'
  if (task.value.status === 'queued') return '排队中'
  if (task.value.status === 'running') return '解析中'
  if (task.value.status === 'completed') return '已完成'
  if (task.value.status === 'failed') return '失败'
  return task.value.status
})

const stats = computed(() => task.value?.stats || {})
const chunkStats = computed(() => (stats.value.chunk_type_counts || {}) as Record<string, number>)
const filteredEvents = computed(() => {
  if (eventFilter.value === 'all') return events.value
  if (eventFilter.value === 'blocks') return events.value.filter(e => ['parsed_block', 'chunk_created'].includes(e.event_type))
  if (eventFilter.value === 'errors') return events.value.filter(e => e.event_type.includes('failed') || e.stage === 'failed')
  return events.value.filter(e => e.stage === eventFilter.value || e.event_type === eventFilter.value)
})

function isActiveStage(key: string) {
  if (!task.value) return false
  if (key === 'done' && task.value.status === 'completed') return true
  return task.value.current_stage === key
}

function isDoneStage(key: string) {
  if (!task.value) return false
  const order = stages.map(item => item.key)
  const current = task.value.status === 'completed' ? order.length - 1 : order.indexOf(task.value.current_stage)
  const idx = order.indexOf(key)
  return idx >= 0 && current >= 0 && idx < current
}

function typeLabel(type?: string) {
  if (type === 'image') return '图片'
  if (type === 'table') return '表格'
  if (type === 'parent_text') return '父文本'
  if (type === 'text') return '文本'
  return type || '事件'
}

function eventTitle(event: ParseTaskEvent) {
  if (event.event_type === 'chunk_created') return `切片 #${event.payload?.chunk_index ?? event.seq_no} 已生成`
  if (event.event_type === 'parsed_block') return `解析块 #${event.payload?.block_index ?? event.seq_no}`
  return event.message || event.event_type
}

function eventType(event: ParseTaskEvent) {
  const payloadType = String(event.payload?.chunk_type || event.payload?.block_type || '')
  if (payloadType) return payloadType
  return event.stage
}

function previewText(value?: unknown, limit = 180) {
  const text = typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value)
  if (!text) return '-'
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}

function normalizeFileUrl(value?: unknown) {
  if (typeof value !== 'string' || !value.trim()) return ''
  let url = value.trim()
  if (url.startsWith('/files//files/')) url = url.replace('/files//files/', '/files/')
  if (url.startsWith('/files/')) return url
  if (url.startsWith('files/')) return `/${url}`
  if (url.startsWith('/data/files/')) return `/files/${url.replace('/data/files/', '')}`
  return `/files/${url.replace(/^\/+/, '')}`
}

function imageUrl(payload?: Record<string, unknown>) {
  if (!payload) return ''
  const info = (payload.image_info && typeof payload.image_info === 'object') ? payload.image_info as Record<string, unknown> : payload
  const nested = (info.image_info && typeof info.image_info === 'object') ? info.image_info as Record<string, unknown> : undefined
  return normalizeFileUrl(payload.image_url)
    || normalizeFileUrl(info.url)
    || normalizeFileUrl(nested?.url)
    || normalizeFileUrl(info.relative_path)
    || normalizeFileUrl(nested?.relative_path)
    || normalizeFileUrl(info.path)
    || normalizeFileUrl(nested?.path)
}

function payloadPreview(event: ParseTaskEvent) {
  return previewText(event.payload?.preview_text || event.payload?.content_preview || event.payload?.preview || event.payload?.message || event.message, 220)
}

async function openEvent(event: ParseTaskEvent) {
  selected.value = event
  const chunkId = event.payload?.id || event.payload?.chunk_id
  if (event.event_type === 'chunk_created' && typeof chunkId === 'string') {
    try {
      selected.value = await getChunk(chunkId)
    } catch (_) {
      selected.value = event
    }
  }
}

function openChunk(chunk: ChunkItem) {
  selected.value = chunk
}

function selectedPayload() {
  if (!selected.value) return {}
  if ('payload' in selected.value) return selected.value.payload || {}
  return selected.value as unknown as Record<string, unknown>
}

function selectedImageUrl() {
  const current = selected.value
  if (!current) return ''
  if ('payload' in current) return imageUrl(current.payload)
  return imageUrl(current as unknown as Record<string, unknown>) || imageUrl((current as ChunkItem).image_info)
}

function selectedTitle() {
  const current = selected.value
  if (!current) return '请选择一个块'
  if ('payload' in current) return eventTitle(current)
  return `${typeLabel(current.chunk_type)} #${current.chunk_index}`
}

async function pullEvents() {
  if (!taskId.value) return
  loading.value = !task.value
  try {
    const result = await getParseTaskEvents(taskId.value, lastSeq.value, 300)
    task.value = result.task
    if (result.task?.document_id && !document.value) {
      try { document.value = await getDocument(result.task.document_id) } catch (_) {}
    }
    for (const event of result.items) {
      events.value.push(event)
      lastSeq.value = Math.max(lastSeq.value, event.seq_no)
      const chunk = event.event_type === 'chunk_created' ? eventChunk(event.payload) : null
      if (chunk && !chunks.value.some(item => item.id === chunk.id)) chunks.value.push(chunk)
    }
    if (!selected.value && result.items.length) selected.value = result.items[result.items.length - 1]
    if (terminal.value && timer) {
      window.clearInterval(timer)
      timer = undefined
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '解析任务状态获取失败'
  } finally {
    loading.value = false
  }
}

function startPolling() {
  pullEvents()
  timer = window.setInterval(pullEvents, 1200)
}

onMounted(startPolling)
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <section class="page parse-workbench-page">
    <div class="parse-topbar">
      <div>
        <p class="eyebrow">Parse Workbench</p>
        <h1>文档解析工作台</h1>
        <p class="text-muted">上传后实时观察解析阶段、block 生成、图片/表格切片和 OCR/VLM 状态。本工作台默认不执行向量化，先用于检查解析结果。</p>
      </div>
      <div class="header-actions">
        <button class="secondary" @click="router.push(`/documents/${task?.document_id || document?.id || ''}`)">文档详情</button>
        <button class="secondary" @click="router.push(`/documents/${task?.document_id || document?.id || ''}/chunks`)">查看切片</button>
        <button @click="pullEvents">刷新</button>
      </div>
    </div>

    <p v-if="error" class="text-error">{{ error }}</p>
    <div v-if="loading && !task" class="card"><p class="text-muted">正在加载解析任务...</p></div>

    <div v-if="task" class="parse-layout">
      <aside class="parse-sidebar-panel">
        <div class="file-card">
          <div class="file-icon">{{ (document?.file_type || stats.file_type || 'doc').toString().slice(0, 3).toUpperCase() }}</div>
          <div>
            <h2>{{ document?.title || stats.file_name || '解析任务' }}</h2>
            <p class="text-muted">{{ document?.file_name || stats.file_name }}</p>
          </div>
        </div>

        <div class="overall-progress-card">
          <div class="status-row">
            <span :class="['status-pill', `status-${task.status}`]">{{ statusLabel }}</span>
            <strong>{{ Math.round(task.progress || 0) }}%</strong>
          </div>
          <div class="progress-track"><div class="progress-bar" :style="{ width: `${task.progress || 0}%` }"></div></div>
          <p class="muted small">当前阶段：{{ task.current_stage_label || task.current_stage }}</p>
          <p class="muted small">默认：只解析和生成切片，不自动向量化。</p>
        </div>

        <div class="stage-list">
          <div v-for="stage in stages" :key="stage.key" :class="['stage-step', { active: isActiveStage(stage.key), done: isDoneStage(stage.key) }]">
            <span class="stage-dot"></span>
            <span>{{ stage.label }}</span>
          </div>
        </div>

        <div class="stats-grid-mini">
          <div><strong>{{ stats.parsed_block_count || 0 }}</strong><span>解析块</span></div>
          <div><strong>{{ stats.text_block_count || chunkStats.text || 0 }}</strong><span>文本块</span></div>
          <div><strong>{{ stats.table_block_count || chunkStats.table || 0 }}</strong><span>表格块</span></div>
          <div><strong>{{ stats.image_block_count || chunkStats.image || 0 }}</strong><span>图片块</span></div>
        </div>
      </aside>

      <main class="parse-stream-panel">
        <div class="stream-toolbar">
          <div>
            <h2>实时解析流</h2>
            <p class="text-muted">解析块会先出现；切片入库失败时，仍可在这里查看已识别的文本、表格和图片结果。</p>
          </div>
          <select v-model="eventFilter">
            <option value="all">全部事件</option>
            <option value="blocks">只看解析结果/切片</option>
            <option value="parse">结构解析</option>
            <option value="ocr">OCR</option>
            <option value="vlm">图片理解</option>
            <option value="chunk">切片入库</option>
            <option value="errors">错误</option>
          </select>
        </div>

        <div v-if="!filteredEvents.length" class="empty-stream">
          <div class="loader-dot"></div>
          <p>等待解析事件...</p>
        </div>
        <div v-else class="event-feed">
          <article v-for="event in filteredEvents" :key="event.id" :class="['event-card', `event-${eventType(event)}`]" @click="openEvent(event)">
            <div class="event-icon">{{ typeLabel(eventType(event)).slice(0, 1) }}</div>
            <div class="event-main">
              <div class="event-title-row">
                <strong>{{ eventTitle(event) }}</strong>
                <span class="tag">{{ event.stage_label || event.stage }}</span>
              </div>
              <p>{{ payloadPreview(event) }}</p>
              <div class="event-meta">
                <span>#{{ event.seq_no }}</span>
                <span>{{ event.event_type }}</span>
                <span v-if="event.payload?.page_no">P{{ event.payload.page_no }}</span>
              </div>
            </div>
            <img v-if="imageUrl(event.payload)" :src="imageUrl(event.payload)" class="event-thumb" />
          </article>
        </div>
      </main>

      <aside class="parse-detail-panel">
        <div class="detail-sticky">
          <h2>{{ selectedTitle() }}</h2>
          <p class="text-muted">点击左侧事件或切片可以查看完整内容、裁剪图和元数据。</p>
          <img v-if="selectedImageUrl()" :src="selectedImageUrl()" class="workbench-preview-image" />
          <div v-if="selectedChunk" class="detail-actions-row">
            <button @click="router.push(`/chunks/${selectedChunk.id}`)">打开切片详情</button>
          </div>
          <div class="detail-section">
            <h3>内容预览</h3>
            <pre>{{ previewText((selectedPayload().content as string) || (selectedPayload().content_preview as string) || (selectedPayload().preview_text as string) || (selectedPayload().preview as string) || (selected && 'message' in selected ? selected.message : ''), 2000) }}</pre>
          </div>
          <details class="detail-section" open>
            <summary>元数据</summary>
            <pre>{{ JSON.stringify(selectedPayload(), null, 2) }}</pre>
          </details>
        </div>
      </aside>
    </div>
  </section>
</template>
