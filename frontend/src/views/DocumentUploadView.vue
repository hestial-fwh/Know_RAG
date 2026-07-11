<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBases'
import { getParseTask, getLatestParseTask, type ParseTask } from '../api/parseTasks'
import {
  uploadDocument,
  embedDocument,
  startBatchVectorize,
  getBatchVectorizeStatus,
  type BatchVectorizeStatus,
} from '../api/documents'

// ── form state ───────────────────────────────────────────────────────────────

const route = useRoute()
const router = useRouter()
const knowledgeBases = ref<KnowledgeBase[]>([])
const knowledgeBaseId = ref((route.query.kb_id as string) || '')
const title = ref('')
const parseImmediately = ref(true)
const error = ref('')
const message = ref('')
const parseMode = ref('mineru')
const mineruProfile = ref('cpu')
const enableOcr = ref('auto')
const enableVlm = ref(true)
const enableMediaCrop = ref(true)

// ── batch item type ──────────────────────────────────────────────────────────

type ItemStatus =
  | 'pending'
  | 'uploading'
  | 'uploaded'
  | 'parsing'
  | 'parsed'
  | 'vectorizing'
  | 'vectorized'
  | 'failed'

interface UploadFileItem {
  id: number
  file: File
  name: string
  size: string
  type: string
  status: ItemStatus
  documentId: string | null
  parseTaskId: string | null
  error: string
  uploadProgress: number
  parseProgress: number
  vectorProgress: number
  _pollTimer: ReturnType<typeof setInterval> | null
}

const fileItems = ref<UploadFileItem[]>([])
let _nextFid = 0

function _newItem(f: File): UploadFileItem {
  return {
    id: ++_nextFid,
    file: f,
    name: f.name,
    size: fmtSize(f.size),
    type: (f.name.split('.').pop() || 'FILE').toUpperCase(),
    status: 'pending',
    documentId: null,
    parseTaskId: null,
    error: '',
    uploadProgress: 0,
    parseProgress: 0,
    vectorProgress: 0,
    _pollTimer: null,
  }
}

// ── batch vectorize state ────────────────────────────────────────────────────

const batchVectorizing = ref(false)
const batchStatus = ref<BatchVectorizeStatus | null>(null)
const batchError = ref('')
let _batchPollTimer: ReturnType<typeof setInterval> | null = null

// ── computed helpers ─────────────────────────────────────────────────────────

const isMultiFile = computed(() => fileItems.value.length > 1)

const batchSummary = computed(() => {
  const items = fileItems.value
  const total = items.length
  const uploaded = items.filter(i => i.status !== 'pending' && i.status !== 'uploading').length
  const parsed = items.filter(i => i.status === 'parsed' || i.status === 'vectorizing' || i.status === 'vectorized').length
  const vectorized = items.filter(i => i.status === 'vectorized').length
  const failed = items.filter(i => i.status === 'failed').length
  const overallPct = total > 0 ? Math.round((uploaded / total) * 100) : 0
  return { total, uploaded, parsed, vectorized, failed, overallPct }
})

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function statusLabel(s: ItemStatus): string {
  const map: Record<ItemStatus, string> = {
    pending: '待上传', uploading: '上传中', uploaded: '已上传',
    parsing: '解析中', parsed: '已解析', vectorizing: '向量化中',
    vectorized: '已向量化', failed: '失败',
  }
  return map[s] || s
}

function statusTagClass(s: ItemStatus): string {
  if (s === 'failed') return 'tag error'
  if (s === 'uploaded' || s === 'parsed' || s === 'vectorized') return 'tag success'
  if (s === 'uploading' || s === 'parsing' || s === 'vectorizing') return 'tag info'
  return 'tag muted'
}

// ── file selection ───────────────────────────────────────────────────────────

function onFilesChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (!files || files.length === 0) return
  for (let i = 0; i < files.length; i++) {
    fileItems.value.push(_newItem(files[i]))
  }
  input.value = ''
}

function removeFileItem(fid: number) {
  fileItems.value = fileItems.value.filter((item) => {
    if (item.id === fid) {
      if (item._pollTimer) { clearInterval(item._pollTimer); item._pollTimer = null }
      return item.status === 'uploading' || item.status === 'parsing' || item.status === 'vectorizing'
    }
    return true
  })
}

// ── parse polling ────────────────────────────────────────────────────────────

function startParsePolling(item: UploadFileItem) {
  if (item._pollTimer) clearInterval(item._pollTimer)
  item._pollTimer = setInterval(async () => {
    try {
      let task: ParseTask | null = null
      if (item.parseTaskId) {
        task = await getParseTask(item.parseTaskId)
      } else if (item.documentId) {
        task = await getLatestParseTask(item.documentId)
      }
      if (!task) return
      const st = task.status
      if (st === 'completed') {
        item.status = 'parsed'
        item.parseProgress = 100
        if (item._pollTimer) { clearInterval(item._pollTimer); item._pollTimer = null }
      } else if (st === 'failed') {
        item.status = 'failed'
        item.error = task.error_message || '解析失败'
        item.parseProgress = 0
        if (item._pollTimer) { clearInterval(item._pollTimer); item._pollTimer = null }
      } else {
        item.status = 'parsing'
        item.parseProgress = task.progress > 0 ? task.progress : 30
      }
    } catch {
      // silently retry
    }
  }, 1500)
}

onBeforeUnmount(() => {
  for (const item of fileItems.value) {
    if (item._pollTimer) clearInterval(item._pollTimer)
  }
  if (_batchPollTimer) clearInterval(_batchPollTimer)
})

// ── upload logic ─────────────────────────────────────────────────────────────

async function uploadOne(item: UploadFileItem): Promise<void> {
  item.status = 'uploading'
  item.error = ''
  item.uploadProgress = 0
  try {
    const saved = await uploadDocument(
      {
        knowledge_base_id: knowledgeBaseId.value,
        file: item.file,
        title: title.value ? `${title.value} — ${item.name}` : undefined,
        parse_immediately: parseImmediately.value,
        parser_engine: parseMode.value,
        mineru_profile: mineruProfile.value,
        enable_ocr: enableOcr.value,
        enable_vlm: enableVlm.value,
        enable_media_crop: enableMediaCrop.value,
      },
      (pct) => { item.uploadProgress = pct },
    )
    item.uploadProgress = 100
    item.documentId = saved.id
    item.parseTaskId = saved.parse_task?.id || saved.parse_task?.task_id || null
    if (parseImmediately.value) {
      item.status = 'parsing'
      item.parseProgress = 0
      startParsePolling(item)
    } else {
      item.status = 'uploaded'
    }
  } catch (err) {
    item.status = 'failed'
    item.error = err instanceof Error ? err.message : '上传失败'
    throw err
  }
}

async function submitBatch() {
  error.value = ''
  message.value = ''
  if (!knowledgeBaseId.value) { error.value = '请先创建知识库'; return }
  const pending = fileItems.value.filter(i => i.status === 'pending' || i.status === 'failed')
  if (pending.length === 0) { error.value = '没有待上传的文件'; return }

  for (const item of pending) { item.status = 'pending'; item.error = '' }

  const queue = fileItems.value.filter(i => i.status === 'pending')
  const concurrency = 2
  let idx = 0

  async function worker() {
    while (idx < queue.length) {
      const item = queue[idx++]
      try { await uploadOne(item) } catch { /* error on item */ }
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, () => worker()))

  const succeeded = fileItems.value.filter(i => i.status !== 'failed' && i.status !== 'pending')
  const failed = fileItems.value.filter(i => i.status === 'failed')

  // Single file: navigate
  if (succeeded.length === 1 && fileItems.value.length === 1) {
    const item = succeeded[0]
    if (parseImmediately.value && item.parseTaskId) { router.push(`/parse-tasks/${item.parseTaskId}`); return }
    if (item.documentId) { router.push(`/documents/${item.documentId}`); return }
  }

  message.value = `上传完成：${succeeded.length} 成功`
  if (failed.length > 0) error.value = `${failed.length} 个文件上传失败`
}

// ── per-document vectorize ───────────────────────────────────────────────────

async function vectorizeOne(item: UploadFileItem) {
  if (!item.documentId) return
  item.status = 'vectorizing'; item.error = ''
  try {
    await embedDocument(item.documentId)
    item.status = 'vectorized'; item.vectorProgress = 100
  } catch (err) {
    item.status = 'parsed'; item.error = err instanceof Error ? err.message : '向量化失败'
  }
}

// ── batch vectorize ──────────────────────────────────────────────────────────

function stopBatchPolling() {
  if (_batchPollTimer !== null) { clearInterval(_batchPollTimer); _batchPollTimer = null }
}

async function runBatchVectorize(mode: 'batch' | 'all') {
  batchError.value = ''; batchVectorizing.value = true
  try {
    let payload: { document_ids?: string[]; only_unvectorized?: boolean } | undefined
    if (mode === 'batch') {
      const ids = fileItems.value.filter(i => i.documentId).map(i => i.documentId!)
      if (ids.length === 0) { batchError.value = '没有已上传的文档'; batchVectorizing.value = false; return }
      payload = { document_ids: ids, only_unvectorized: false }
    } else {
      payload = { only_unvectorized: true }
    }
    const result = await startBatchVectorize(knowledgeBaseId.value, payload)
    stopBatchPolling()
    _batchPollTimer = setInterval(async () => {
      try {
        const status = await getBatchVectorizeStatus(result.batch_id)
        batchStatus.value = status
        if (status.items) {
          for (const bi of status.items) {
            const match = fileItems.value.find(fi => fi.documentId === bi.document_id)
            if (match) {
              if (bi.status === 'completed') match.status = 'vectorized'
              else if (bi.status === 'failed') { match.status = 'failed'; match.error = bi.error }
              else if (bi.status === 'running') match.status = 'vectorizing'
              else if (bi.status === 'skipped') match.status = 'vectorized'
              match.vectorProgress = bi.percent
            }
          }
        }
        if (['completed', 'partial_failed', 'failed', 'cancelled'].includes(status.status)) {
          stopBatchPolling(); batchVectorizing.value = false
          if (status.status === 'completed') message.value = '批量向量化全部完成'
          else if (status.status === 'partial_failed') message.value = `完成（${status.failed_documents} 个失败）`
          else if (status.status === 'failed') batchError.value = '批量向量化失败'
        }
      } catch { /* retry */ }
    }, 1500)
  } catch (err) {
    batchError.value = err instanceof Error ? err.message : '启动失败'
    batchVectorizing.value = false
  }
}

// ── lifecycle ────────────────────────────────────────────────────────────────

async function loadKnowledgeBases() {
  try {
    const result = await listKnowledgeBases()
    knowledgeBases.value = result.items
    if (!knowledgeBaseId.value && result.items.length) knowledgeBaseId.value = result.items[0].id
  } catch (err) { error.value = err instanceof Error ? err.message : '请求失败' }
}

onMounted(loadKnowledgeBases)
</script>

<template>
  <section class="page upload-workbench-page">
    <div class="page-header hero-header upload-hero">
      <div>
        <p class="eyebrow">Document Intake</p>
        <h1>文档上传</h1>
        <p class="text-muted">支持多文件批量上传，每个文件独立跟踪上传/解析/向量化进度。</p>
      </div>
      <div class="upload-hero-card">
        <strong>支持格式</strong>
        <span>PDF / DOCX / PPT / Excel / HTML / JSON / EPUB / 图片 / ZIP</span>
      </div>
    </div>

    <!-- batch summary bar -->
    <div v-if="fileItems.length > 1" class="batch-summary-card card">
      <div class="batch-summary-row">
        <div class="summary-item"><span class="summary-num">{{ batchSummary.total }}</span><span class="summary-label">总文件</span></div>
        <div class="summary-item"><span class="summary-num">{{ batchSummary.uploaded }}</span><span class="summary-label">已上传</span></div>
        <div class="summary-item"><span class="summary-num">{{ batchSummary.parsed }}</span><span class="summary-label">已解析</span></div>
        <div class="summary-item"><span class="summary-num">{{ batchSummary.vectorized }}</span><span class="summary-label">已向量化</span></div>
        <div v-if="batchSummary.failed > 0" class="summary-item err"><span class="summary-num">{{ batchSummary.failed }}</span><span class="summary-label">失败</span></div>
      </div>
      <div class="batch-summary-bar-wrap">
        <div class="batch-summary-bar" :style="{ width: batchSummary.overallPct + '%' }"></div>
      </div>
      <span class="text-muted" style="font-size:12px;">总体进度 {{ batchSummary.overallPct }}%</span>
    </div>

    <form @submit.prevent="submitBatch">
      <div class="upload-grid">
        <!-- basic info card -->
        <div class="card upload-panel">
          <h2>基础信息</h2>
          <label>知识库
            <select v-model="knowledgeBaseId">
              <option value="">请选择知识库</option>
              <option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
            </select>
          </label>
          <label>标题前缀
            <input v-model="title" placeholder="多文件时作为文件名前缀（可选）" />
          </label>
          <label>文件 <span class="text-muted">（可多选）</span>
            <input type="file" multiple
              accept=".txt,.md,.markdown,.pdf,.doc,.docx,.rtf,.csv,.tsv,.xls,.xlsx,.ppt,.pptx,.html,.htm,.json,.jsonl,.xml,.yaml,.yml,.epub,.zip,image/*"
              @change="onFilesChange" />
          </label>
        </div>

        <!-- parse options card -->
        <div class="card upload-panel">
          <h2>解析选项</h2>
          <label>解析模式
            <select v-model="parseMode">
              <option value="mineru">MinerU 文档解析</option>
              <option value="auto">自动选择</option>
              <option value="ragflow">RAGFlow / DeepDoc</option>
            </select>
          </label>
          <label v-if="parseMode === 'mineru' || parseMode === 'auto'">MinerU 运行版本
            <select v-model="mineruProfile">
              <option value="cpu">CPU pipeline（稳定）</option>
              <option value="gpu">GPU VLM（需要 CUDA）</option>
            </select>
          </label>
          <label>OCR 策略
            <select v-model="enableOcr">
              <option value="auto">自动 OCR</option><option value="force">强制 OCR</option><option value="off">关闭 OCR</option>
            </select>
          </label>
          <label class="check pretty-check"><input v-model="enableMediaCrop" type="checkbox" /><span>启用 PDF 图/表裁剪</span></label>
          <label class="check pretty-check"><input v-model="enableVlm" type="checkbox" /><span>启用图片理解 / VLM 描述</span></label>
          <label class="check pretty-check"><input v-model="parseImmediately" type="checkbox" /><span>上传后立即解析</span></label>
        </div>

        <!-- file list -->
        <div v-if="fileItems.length > 0" class="card upload-panel" style="grid-column:1/-1;">
          <h2>文件列表 <span class="text-muted">（{{ fileItems.length }} 个）</span></h2>
          <div class="file-table">
            <div v-for="item in fileItems" :key="item.id" class="file-row" :class="'row-' + item.status">
              <!-- name + size -->
              <div class="file-cell name-cell">
                <span class="file-type-badge small">{{ item.type }}</span>
                <div class="file-name-meta">
                  <strong>{{ item.name }}</strong>
                  <span class="text-muted">{{ item.size }}</span>
                </div>
              </div>
              <!-- status tag -->
              <div class="file-cell status-cell">
                <span :class="statusTagClass(item.status)">{{ statusLabel(item.status) }}</span>
              </div>
              <!-- progress bars -->
              <div class="file-cell progress-cell">
                <div class="mini-bars">
                  <div v-if="item.status === 'uploading'" class="mini-bar-row">
                    <span class="mini-bar-label">上传</span>
                    <div class="mini-bar-wrap"><div class="mini-bar" :style="{width:item.uploadProgress+'%'}"></div></div>
                    <span class="mini-bar-pct">{{ item.uploadProgress }}%</span>
                  </div>
                  <div v-if="item.status === 'parsing'" class="mini-bar-row">
                    <span class="mini-bar-label">解析</span>
                    <div class="mini-bar-wrap"><div class="mini-bar" :style="{width:item.parseProgress+'%'}"></div></div>
                    <span class="mini-bar-pct">{{ item.parseProgress }}%</span>
                  </div>
                  <div v-if="item.status === 'vectorizing'" class="mini-bar-row">
                    <span class="mini-bar-label">向量化</span>
                    <div class="mini-bar-wrap"><div class="mini-bar" :style="{width:item.vectorProgress+'%'}"></div></div>
                    <span class="mini-bar-pct">{{ item.vectorProgress }}%</span>
                  </div>
                </div>
              </div>
              <!-- actions -->
              <div class="file-cell actions-cell">
                <button v-if="item.documentId" type="button" class="btn-sm secondary" @click="router.push(`/documents/${item.documentId}`)">查看</button>
                <button v-if="item.parseTaskId" type="button" class="btn-sm secondary" @click="router.push(`/parse-tasks/${item.parseTaskId}`)">工作台</button>
                <button v-if="item.documentId && item.status !== 'uploading' && item.status !== 'parsing' && item.status !== 'vectorizing' && item.status !== 'vectorized' && item.status !== 'failed'"
                  type="button" class="btn-sm" @click="vectorizeOne(item)">向量化</button>
                <button v-if="item.status === 'failed'" type="button" class="btn-sm" @click="item.status='pending';item.error=''">重试</button>
                <button v-if="item.status === 'pending' || item.status === 'failed'" type="button" class="btn-sm danger" @click="removeFileItem(item.id)">移除</button>
              </div>
              <div v-if="item.error" class="file-cell error-cell text-error" style="grid-column:1/-1;">{{ item.error }}</div>
            </div>
          </div>
        </div>

        <!-- batch vectorize card -->
        <div v-if="fileItems.some(fi => fi.documentId)" class="card upload-panel" style="grid-column:1/-1;">
          <h2>批量向量化</h2>
          <div class="vec-btn-row">
            <button type="button" :disabled="batchVectorizing" @click="runBatchVectorize('batch')">
              {{ batchVectorizing ? '进行中...' : '向量化当前批次文档' }}
            </button>
            <button type="button" class="secondary" :disabled="batchVectorizing" @click="runBatchVectorize('all')">
              向量化知识库全部未完成文档
            </button>
          </div>
          <p v-if="batchError" class="text-error" style="margin-top:8px;">{{ batchError }}</p>

          <!-- batch progress panel -->
          <div v-if="batchStatus && (batchStatus.status === 'running' || batchStatus.status === 'completed' || batchStatus.status === 'partial_failed')" class="batch-progress-panel">
            <div class="batch-progress-bar-wrap">
              <div class="batch-progress-bar" :style="{ width: batchStatus.percent + '%' }"></div>
            </div>
            <div class="batch-progress-grid">
              <div><span class="text-muted">文档进度</span><strong>{{ batchStatus.completed_documents }} / {{ batchStatus.total_documents }}</strong></div>
              <div><span class="text-muted">Chunk 进度</span><strong>{{ batchStatus.embedded_chunks }} / {{ batchStatus.total_chunks }}</strong></div>
              <div v-if="batchStatus.current_document_title"><span class="text-muted">当前处理</span><strong>{{ batchStatus.current_document_title }}</strong></div>
              <div><span class="text-muted">总体</span><strong>{{ batchStatus.percent }}%</strong></div>
            </div>
            <div v-if="batchStatus.failed_documents > 0" class="text-warning" style="margin-top:6px;">
              {{ batchStatus.failed_documents }} 个文档向量化失败
            </div>
            <!-- per-doc detail -->
            <details v-if="batchStatus.items.length > 0" style="margin-top:10px;">
              <summary class="text-muted" style="cursor:pointer;font-size:13px;">文档详情</summary>
              <div v-for="bi in batchStatus.items" :key="bi.document_id" class="batch-doc-detail">
                <span>{{ bi.title }}</span>
                <span :class="statusTagClass(bi.status as ItemStatus)">{{ bi.status }}</span>
                <span class="text-muted">{{ bi.embedded_chunks }}/{{ bi.total_chunks }} chunks</span>
                <span v-if="bi.error" class="text-error">{{ bi.error }}</span>
              </div>
            </details>
          </div>
        </div>

        <!-- actions -->
        <div class="upload-actions-panel" style="grid-column:1/-1;">
          <p v-if="message" class="text-success">{{ message }}</p>
          <p v-if="error" class="text-error">{{ error }}</p>
          <div class="actions">
            <button :disabled="fileItems.filter(i => i.status === 'pending' || i.status === 'failed').length === 0 || fileItems.some(i => i.status === 'uploading')">
              {{ fileItems.some(i => i.status === 'uploading') ? '上传中...'
                : isMultiFile ? '批量上传并解析'
                : parseImmediately ? '上传并进入解析工作台' : '仅上传' }}
            </button>
            <button type="button" class="secondary" @click="router.back()">取消</button>
          </div>
        </div>
      </div>
    </form>
  </section>
</template>

<style scoped>
/* ── batch summary card ───────────────────────────────────────────────── */
.batch-summary-card { padding: 16px 20px; margin-bottom: 16px; }
.batch-summary-row { display: flex; gap: 32px; margin-bottom: 10px; }
.summary-item { display: flex; flex-direction: column; align-items: center; }
.summary-num { font-size: 22px; font-weight: 800; color: var(--c-primary); }
.summary-label { font-size: 12px; color: var(--t-muted); margin-top: 2px; }
.summary-item.err .summary-num { color: var(--s-error); }
.batch-summary-bar-wrap { height: 8px; background: var(--c-border); border-radius: 4px; overflow: hidden; margin-bottom: 4px; }
.batch-summary-bar { height: 100%; background: var(--c-primary); border-radius: 4px; transition: width 0.3s ease; }

/* ── file table ────────────────────────────────────────────────────────── */
.file-table { display: grid; gap: 6px; max-height: 500px; overflow-y: auto; }
.file-row { display: grid; grid-template-columns: 2fr 100px 1.5fr 180px; gap: 10px; align-items: center; padding: 10px 12px; border: 1px solid var(--c-border); border-radius: var(--r-sm); background: var(--c-surface-alt); }
.file-row.row-failed { border-color: var(--s-error-border); background: var(--s-error-bg); }
.file-cell { min-width: 0; }
.name-cell { display: flex; align-items: center; gap: 8px; }
.file-name-meta { display: flex; flex-direction: column; min-width: 0; }
.file-name-meta strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.file-name-meta span { font-size: 12px; }
.status-cell { text-align: center; }
.actions-cell { display: flex; gap: 4px; justify-content: flex-end; }
.error-cell { font-size: 12px; padding-left: 30px; }

/* ── mini progress bars ────────────────────────────────────────────────── */
.mini-bars { display: flex; flex-direction: column; gap: 3px; }
.mini-bar-row { display: flex; align-items: center; gap: 6px; }
.mini-bar-label { font-size: 10px; color: var(--t-muted); width: 32px; flex-shrink: 0; }
.mini-bar-wrap { flex: 1; height: 6px; background: var(--c-border); border-radius: 3px; overflow: hidden; }
.mini-bar { height: 100%; background: var(--c-primary); border-radius: 3px; transition: width 0.2s ease; }
.mini-bar-pct { font-size: 10px; color: var(--t-muted); width: 30px; text-align: right; flex-shrink: 0; }

/* ── batch progress panel ──────────────────────────────────────────────── */
.batch-progress-panel { margin-top: 12px; padding: 14px; border: 1px solid var(--c-border); border-radius: var(--r-sm); background: var(--c-surface-alt); }
.batch-progress-bar-wrap { height: 10px; background: var(--c-border); border-radius: 5px; overflow: hidden; margin-bottom: 10px; }
.batch-progress-bar { height: 100%; background: var(--c-primary); border-radius: 5px; transition: width 0.3s ease; }
.batch-progress-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 20px; font-size: 13px; }
.batch-progress-grid div { display: flex; justify-content: space-between; }
.batch-doc-detail { display: grid; grid-template-columns: 2fr 80px 1fr 2fr; gap: 8px; align-items: center; padding: 4px 0; font-size: 13px; border-bottom: 1px solid var(--c-border); }

/* ── buttons and tags ──────────────────────────────────────────────────── */
.vec-btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
.btn-sm { padding: 4px 10px; font-size: 12px; border-radius: var(--r-sm); border: 1px solid var(--c-primary); background: var(--c-primary); color: #fff; cursor: pointer; white-space: nowrap; }
.btn-sm.secondary { background: transparent; color: var(--c-primary); }
.btn-sm.danger { background: transparent; color: var(--s-error); border-color: var(--s-error); }
.btn-sm:hover { opacity: 0.85; }
.file-type-badge.small { padding: 2px 6px; font-size: 10px; border-radius: 3px; background: var(--c-primary); color: #fff; font-weight: 700; flex-shrink: 0; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.tag.muted { background: var(--c-surface-alt); color: var(--t-muted); }
.tag.info { background: #dbeafe; color: #1e40af; }
.tag.success { background: #d1fae5; color: #065f46; }
.tag.error { background: #fee2e2; color: #991b1b; }
.tag.warning { background: #fef3c7; color: #92400e; }

.text-error { color: var(--s-error); font-size: 13px; }
.text-success { color: var(--s-success, #065f46); font-size: 13px; }
.text-warning { color: var(--s-warning, #92400e); font-size: 13px; }
.text-muted { color: var(--t-muted); }
</style>
