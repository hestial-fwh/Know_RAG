<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getParseEvaluationReport,
  getParseEvaluationRun,
  listParseEvaluationItems,
  type ParseEvalItem,
  type ParseEvalRun
} from './api'
import { listChunks, type ChunkItem } from '../../api/chunks'

const route = useRoute()
const router = useRouter()
const run = ref<ParseEvalRun | null>(null)
const items = ref<ParseEvalItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const levelFilter = ref('all')
const targetFilter = ref('all')
const issueFilter = ref('all')
const selected = ref<ParseEvalItem | null>(null)
const report = ref('')
const error = ref('')
const loading = ref(false)
const activeTab = ref<'overview' | 'issues' | 'chunks' | 'report'>('overview')

// Chunks tab state
const chunks = ref<ChunkItem[]>([])
const chunksTotal = ref(0)
const chunksPage = ref(1)
const chunksPageSize = ref(50)
const chunksLoading = ref(false)
const chunkTypeFilter = ref('all')

// Collapsible state
const ocrLayersExpanded = ref(true)
const expandedSections = ref<Set<string>>(new Set())

const selectedIndex = computed(() => {
  if (!selected.value) return -1
  return items.value.findIndex((i: ParseEvalItem) => i.id === selected.value!.id)
})

const templateLabel: Record<string, string> = {
  generic_no_gt: '无 GT 通用规则诊断',
  scanned_contract_ocr: '扫描合同 OCR 模板评测'
}

const metricLabels: Record<string, string> = {
  document_id: '文档 ID',
  file_name: '文件名',
  file_type: '文件类型',
  chunk_count: '切片总数',
  chunk_type_counts: '切片类型统计',
  enabled_chunk_type_counts: '启用切片统计',
  page_count_observed: '识别到的页数',
  avg_chunk_length: '平均切片长度',
  short_chunks: '短切片比例',
  long_chunks: '长切片比例',
  noisy_chunks: '疑似乱码切片比例',
  space_glued_chunks: '空格粘连切片数',
  identifier_risk_chunks: '编号/字段风险切片比例',
  missing_page_chunks: '缺少页码切片数',
  media_without_url: '缺少图片地址比例',
  image_without_caption: '图片缺少标题/说明比例',
  image_without_vlm: '图片缺少 VLM 描述比例',
  weak_tables: '弱表格/疑似误判数',
  duplicate_groups: '重复文本组比例',
  duplicate_chunks: '重复切片数',
  issue_count: '问题总数',
  page_hit_rate: '页码识别率',
  line_hit_rate: '行号识别率',
  invoice_hit_rate: '发票号识别率',
  callout_hit_rate: 'Callout 识别率',
  keyword_hit_rate: '关键词保留率',
  expected_pages: '理论页数',
  expected_lines: '理论正文行数',
  expected_invoices: '理论发票号数',
  expected_callouts: '理论 Callout 数'
}

const metricTips: Record<string, string> = {
  short_chunks: '短切片过多会导致检索上下文不足。',
  long_chunks: '长切片过多可能混入多个主题，影响召回精度。',
  noisy_chunks: '疑似 OCR 噪声、乱码、不可读文本。',
  space_glued_chunks: '单词或字段被粘连，常见于 OCR/清洗问题。',
  identifier_risk_chunks: '编号、日期、发票号、代码等字段疑似被破坏。',
  missing_page_chunks: '缺少页码会影响溯源和页面级排查。',
  image_without_caption: '图片没有 caption 时，检索和问答可用性会下降。',
  image_without_vlm: '图片未生成语义描述，向量化后难以通过文本检索。',
  weak_tables: '表格内容过弱、结构不完整或疑似误判。',
  duplicate_chunks: '重复切片会污染检索结果。'
}

const issueLabels: Record<string, string> = {
  short_chunk: '切片过短',
  long_chunk: '切片过长',
  noisy_text: '疑似 OCR 噪声',
  space_gluing: '空格粘连',
  identifier_risk: '编号/字段风险',
  missing_page: '缺少页码信息',
  media_without_url: '媒体缺少地址',
  image_without_caption: '图片缺少说明',
  image_without_vlm: '图片缺少 VLM 描述',
  weak_table: '弱表格/疑似误判',
  duplicate_text: '重复文本',
  missing_expected_page: '缺失理论页码',
  missing_expected_line: '缺失理论行号',
  missing_invoice: '缺失发票号',
  missing_callout: '缺失 Callout',
  low_keyword_hit_rate: '关键词保留率低'
}

const targetLabels: Record<string, string> = {
  document: '文档',
  page: '页面',
  block: '解析块',
  chunk: '切片',
  image: '图片',
  table: '表格'
}

const levelLabels: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
  info: '提示'
}

const metrics = computed(() => {
  const scores = run.value?.scores || {}
  return (scores.metrics || run.value?.summary || {}) as Record<string, unknown>
})

const metricSections = computed(() => {
  const all = metrics.value
  const sectionDefs = [
    {
      title: '文档与切片概况',
      desc: '用于判断解析覆盖量、切片数量和文档基础状态。',
      keys: ['file_name', 'file_type', 'chunk_count', 'chunk_type_counts', 'enabled_chunk_type_counts', 'page_count_observed', 'avg_chunk_length']
    },
    {
      title: '文本 / OCR / 清洗风险',
      desc: '用于判断 OCR 文本是否可读、清洗是否造成字段粘连或编号破坏。',
      keys: ['short_chunks', 'long_chunks', 'noisy_chunks', 'space_glued_chunks', 'identifier_risk_chunks', 'missing_page_chunks']
    },
    {
      title: '图片 / 表格 / 多模态质量',
      desc: '用于判断图表裁剪、caption、VLM 描述和表格结构是否可用。',
      keys: ['media_without_url', 'image_without_caption', 'image_without_vlm', 'weak_tables']
    },
    {
      title: '规则模板命中情况',
      desc: '有规则模板时，用于衡量页码、行号、发票号、关键词等固定字段命中率。',
      keys: ['page_hit_rate', 'line_hit_rate', 'invoice_hit_rate', 'callout_hit_rate', 'keyword_hit_rate', 'expected_pages', 'expected_lines', 'expected_invoices', 'expected_callouts']
    },
    {
      title: '重复与问题统计',
      desc: '用于判断重复污染和问题总体规模。',
      keys: ['duplicate_groups', 'duplicate_chunks', 'issue_count']
    }
  ]
  return sectionDefs
    .map(section => ({
      ...section,
      items: section.keys.filter(key => Object.prototype.hasOwnProperty.call(all, key)).map(key => ({ key, value: all[key] }))
    }))
    .filter(section => section.items.length > 0)
})

const issueTypes = computed(() => {
  const set = new Set(items.value.map(item => item.issue_type).filter(Boolean))
  return Array.from(set)
})

function riskText(level?: string) {
  const map: Record<string, string> = { low: '低风险', medium: '中风险', high: '高风险', critical: '严重风险', unknown: '未知' }
  return map[level || 'unknown'] || level || '-'
}

function riskClass(level?: string) {
  if (level === 'critical') return 'pe-risk-critical'
  if (level === 'high') return 'pe-risk-high'
  if (level === 'medium') return 'pe-risk-medium'
  if (level === 'low') return 'pe-risk-low'
  return ''
}

function levelText(level: string) {
  return levelLabels[level] || level || '-'
}

function issueText(type: string) {
  return issueLabels[type] || type || '-'
}

function targetText(type: string) {
  return targetLabels[type] || type || '-'
}

function metricLabel(key: string) {
  return metricLabels[key] || key
}

function metricValue(value: unknown) {
  if (typeof value === 'number') {
    if (value >= 0 && value <= 1) return `${(value * 100).toFixed(1)}%`
    return Number.isInteger(value) ? String(value) : value.toFixed(2)
  }
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return '-'
  try { return JSON.stringify(value) } catch { return String(value) }
}

function safeJson(value: unknown) {
  if (value === null || value === undefined) return '-'
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function score10() {
  if (!run.value) return '0.0'
  return ((run.value.score_10 ?? run.value.score / 10) || 0).toFixed(1)
}

function fileUrl(value: unknown) {
  if (!value || typeof value !== 'string') return ''
  if (value.startsWith('/files/')) return value
  if (value.startsWith('files/')) return `/${value}`
  if (value.startsWith('/data/files/')) return `/files/${value.replace('/data/files/', '')}`
  if (value.startsWith('http')) return value
  if (value.includes('/')) return `/files/${value.replace(/^\/+/, '')}`
  return ''
}

function itemImageUrl(item: ParseEvalItem | null) {
  const chunk = item?.chunk
  const info = chunk?.image_info as Record<string, unknown> | undefined
  if (!info) return ''
  const nested = info.image_info as Record<string, unknown> | undefined
  return fileUrl(info.url) || fileUrl(info.relative_path) || fileUrl(info.path) || fileUrl(nested?.url) || fileUrl(nested?.relative_path) || ''
}

function chunkOcrMeta(item: ParseEvalItem) {
  const chunk = item?.chunk as Record<string, unknown> | undefined
  const meta = (chunk?.metadata_json || chunk?.metadata || {}) as Record<string, unknown>
  return meta
}

function ocrLayerValue(item: ParseEvalItem, key: string) {
  const val = chunkOcrMeta(item)[key]
  return typeof val === 'string' && val ? val : ''
}

function hasOcrLayers(item: ParseEvalItem) {
  return !!(
    item.chunk?.content ||
    ocrLayerValue(item, 'ocr_raw_text') ||
    ocrLayerValue(item, 'ocr_cleaned_text') ||
    ocrLayerValue(item, 'ocr_normalized_text') ||
    ocrLayerValue(item, 'vlm_description')
  )
}

function fallbackId(item: ParseEvalItem) {
  const chunkIndex = item.chunk_index
  if (chunkIndex !== null && chunkIndex !== undefined) return String(chunkIndex)
  return item.target_id || '-'
}

function pageText(pageNo?: number | null) {
  return pageNo === null || pageNo === undefined ? '-' : String(pageNo)
}

async function loadRun() {
  const runId = route.params.runId as string
  run.value = await getParseEvaluationRun(runId)
  const res = await getParseEvaluationReport(runId)
  report.value = res.markdown || ''
}

async function loadItems() {
  const runId = route.params.runId as string
  loading.value = true
  try {
    const res = await listParseEvaluationItems(runId, {
      page: page.value,
      page_size: pageSize.value,
      level: levelFilter.value,
      target_type: targetFilter.value,
      issue_type: issueFilter.value
    })
    items.value = res.items
    total.value = res.total
    if (!selected.value || !res.items.some(item => item.id === selected.value?.id)) {
      selected.value = res.items[0] || null
    }
  } finally {
    loading.value = false
  }
}

async function reload(reset = false) {
  if (reset) page.value = 1
  error.value = ''
  try {
    await loadRun()
    await loadItems()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载评测结果失败'
  }
}

// ── Chunks tab ──

async function loadChunks() {
  if (!run.value) return
  chunksLoading.value = true
  try {
    const result = await listChunks(
      run.value.document_id,
      chunksPage.value,
      chunksPageSize.value,
      chunkTypeFilter.value
    )
    chunks.value = result.items
    chunksTotal.value = result.total
  } catch (err) {
    console.error('Failed to load chunks:', err)
  } finally {
    chunksLoading.value = false
  }
}

function chunkImageUrl(item: ChunkItem): string {
  const info = item.image_info as Record<string, unknown> | undefined
  if (!info) return ''
  const nested = info.image_info as Record<string, unknown> | undefined
  return fileUrl(info.url) || fileUrl(nested?.url) || fileUrl(info.relative_path) || fileUrl(nested?.relative_path) || ''
}

function chunkTypeLabel(type?: string): string {
  const map: Record<string, string> = { text: '文本', parent_text: '父文本', table: '表格', image: '图片' }
  return map[type || ''] || type || '文本'
}

function chunkTypeClass(type?: string): string {
  return 'tag type-' + (type || 'text')
}

function chunkPreview(item: ChunkItem): string {
  return item.content_preview || item.preview || (item.content ? item.content.slice(0, 200) : '-')
}

// ── Detail navigation ──

function selectPrev() {
  if (selectedIndex.value > 0) {
    selected.value = items.value[selectedIndex.value - 1]
  }
}

function selectNext() {
  if (selectedIndex.value >= 0 && selectedIndex.value < items.value.length - 1) {
    selected.value = items.value[selectedIndex.value + 1]
  }
}

// ── Collapsible sections ──

function toggleSection(title: string) {
  const s = new Set(expandedSections.value)
  if (s.has(title)) {
    s.delete(title)
  } else {
    s.add(title)
  }
  expandedSections.value = s
}

watch([levelFilter, targetFilter, issueFilter, pageSize], () => reload(true))

watch([chunkTypeFilter, chunksPageSize], () => {
  chunksPage.value = 1
  loadChunks()
})

watch(activeTab, (tab: string) => {
  if (tab === 'chunks' && chunks.value.length === 0 && run.value) {
    loadChunks()
  }
})

onMounted(() => reload())
</script>

<template>
  <section class="page pe-page pe-run-page">
    <p v-if="error" class="pe-error">{{ error }}</p>

    <div v-if="loading && !run" class="pe-loading">
      <div class="loader-dot"></div>
      <span>加载评测数据中...</span>
    </div>

    <div v-if="run" class="pe-hero">
      <div>
        <p class="pe-eyebrow">Parse Evaluation Result</p>
        <h1>解析质量评测结果</h1>
        <p class="muted">
          {{ templateLabel[run.template_id] || run.template_id }} · {{ run.mode }} · {{ run.created_at }}
        </p>
      </div>
      <div class="pe-actions">
        <button class="secondary" @click="router.push(`/parse-eval/documents/${run.document_id}`)">返回文档评测</button>
        <button class="secondary" @click="router.push(`/documents/${run.document_id}`)">返回文档</button>
      </div>
    </div>

    <div v-if="run" class="pe-score-strip">
      <div class="pe-score-card">
        <span>综合评分</span>
        <strong>{{ score10() }}</strong>
        <em>/ 10</em>
      </div>
      <div class="pe-score-card" :class="riskClass(run.risk_level)">
        <span>风险等级</span>
        <strong>{{ riskText(run.risk_level) }}</strong>
        <em>{{ run.risk_level }}</em>
      </div>
      <div class="pe-score-card wide">
        <span>入库建议</span>
        <p>{{ run.recommendation }}</p>
      </div>
      <div class="pe-score-card">
        <span>问题数量</span>
        <strong>{{ run.item_count ?? total }}</strong>
        <em>条</em>
      </div>
    </div>

    <div v-if="run" class="pe-tabs">
      <button class="pe-tab" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'">指标总览</button>
      <button class="pe-tab" :class="{ active: activeTab === 'issues' }" @click="activeTab = 'issues'">问题诊断</button>
      <button class="pe-tab" :class="{ active: activeTab === 'chunks' }" @click="activeTab = 'chunks'">切片浏览</button>
      <button class="pe-tab" :class="{ active: activeTab === 'report' }" @click="activeTab = 'report'">评测报告</button>
    </div>

    <template v-if="run && activeTab === 'overview'">
      <div v-for="section in metricSections" :key="section.title" class="pe-card pe-metric-section">
        <div class="pe-section-title pe-collapsible-header" @click="toggleSection(section.title)">
          <div>
            <h2>{{ section.title }}</h2>
            <p class="muted">{{ section.desc }}</p>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <span class="pe-chip gray">{{ section.items.length }} 项</span>
            <span class="pe-collapse-icon">{{ expandedSections.has(section.title) ? '▾' : '▸' }}</span>
          </div>
        </div>
        <div v-if="expandedSections.has(section.title)" class="pe-metric-grid">
          <div v-for="item in section.items" :key="item.key" class="pe-metric">
            <span>{{ metricLabel(item.key) }}</span>
            <strong>{{ metricValue(item.value) }}</strong>
            <small v-if="metricTips[item.key]">{{ metricTips[item.key] }}</small>
            <small v-else>{{ item.key }}</small>
          </div>
        </div>
      </div>
    </template>

    <template v-if="run && activeTab === 'issues'">
      <div class="pe-card">
        <div class="pe-toolbar">
          <label>
            问题级别
            <select v-model="levelFilter">
              <option value="all">全部级别</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
              <option value="info">提示</option>
            </select>
          </label>
          <label>
            对象类型
            <select v-model="targetFilter">
              <option value="all">全部对象</option>
              <option value="document">文档</option>
              <option value="page">页面</option>
              <option value="chunk">切片</option>
              <option value="image">图片</option>
              <option value="table">表格</option>
            </select>
          </label>
          <label>
            问题类型
            <select v-model="issueFilter">
              <option value="all">全部问题</option>
              <option v-for="type in issueTypes" :key="type" :value="type">{{ issueText(type) }}</option>
            </select>
          </label>
          <label>
            每页数量
            <select v-model.number="pageSize">
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
          </label>
        </div>

        <div class="pe-issue-layout">
          <div>
            <div class="pe-section-title">
              <div>
                <h2>问题列表</h2>
                <p class="muted">{{ total ? `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)} / ${total}` : '0 / 0' }}</p>
              </div>
              <div class="pe-actions">
                <button class="secondary" :disabled="page <= 1 || loading" @click="page--; loadItems()">上一页</button>
                <button class="secondary" :disabled="page * pageSize >= total || loading" @click="page++; loadItems()">下一页</button>
              </div>
            </div>

            <div class="pe-issue-list">
              <button
                v-for="item in items"
                :key="item.id"
                class="pe-issue-card"
                :class="[`level-${item.issue_level}`, { active: selected?.id === item.id }]"
                @click="selected = item"
              >
                <div class="pe-issue-head">
                  <div class="pe-issue-title">
                    <span class="pe-chip" :class="item.issue_level === 'high' || item.issue_level === 'critical' ? 'red' : item.issue_level === 'medium' ? 'orange' : 'gray'">
                      {{ levelText(item.issue_level) }}
                    </span>
                    <strong>{{ issueText(item.issue_type) }}</strong>
                  </div>
                  <small>{{ targetText(item.target_type) }}</small>
                </div>
                <p>{{ item.message }}</p>
                <small>{{ targetText(item.target_type) }} · #{{ fallbackId(item) }} · 页码 {{ pageText(item.page_no) }}</small>
              </button>
              <div v-if="!items.length" class="pe-empty">
                <div>当前筛选条件下没有问题。<br />可以切换级别、对象或问题类型重新查看。</div>
              </div>
            </div>
          </div>

          <div class="pe-card pe-detail">
            <template v-if="selected">
              <div class="pe-detail-nav">
                <button class="secondary" :disabled="selectedIndex <= 0" @click="selectPrev">← 上一个</button>
                <span class="muted">{{ selectedIndex + 1 }} / {{ items.length }}</span>
                <button class="secondary" :disabled="selectedIndex >= items.length - 1" @click="selectNext">下一个 →</button>
              </div>
              <p class="pe-eyebrow">Issue Detail</p>
              <h2>{{ issueText(selected.issue_type) }}</h2>
              <p>{{ selected.message }}</p>

              <div class="pe-detail-grid">
                <span>问题级别</span><strong>{{ levelText(selected.issue_level) }}</strong>
                <span>对象类型</span><strong>{{ targetText(selected.target_type) }}</strong>
                <span>页码</span><strong>{{ pageText(selected.page_no) }}</strong>
                <span>切片编号</span><strong>{{ fallbackId(selected) }}</strong>
                <span>目标 ID</span><strong>{{ selected.target_id || '-' }}</strong>
              </div>

              <img v-if="itemImageUrl(selected)" :src="itemImageUrl(selected)" class="pe-preview-image" />

              <h3>修复建议</h3>
              <p class="muted">{{ selected.suggestion || '暂无建议' }}</p>

              <h3>内容预览</h3>
              <pre class="pe-pre light">{{ selected.chunk?.content_preview || selected.chunk?.preview || selected.evidence?.preview || '-' }}</pre>

              <template v-if="hasOcrLayers(selected)">
                <div class="pe-collapsible-header" @click="ocrLayersExpanded = !ocrLayersExpanded">
                  <h3>解析分层文本</h3>
                  <span class="pe-collapse-icon">{{ ocrLayersExpanded ? '▾' : '▸' }}</span>
                </div>
                <div v-if="ocrLayersExpanded" class="pe-ocr-layers">
                  <div v-if="selected.chunk?.content" class="pe-ocr-layer">
                    <small class="pe-ocr-label">最终入库文本 <code>chunk.content</code></small>
                    <pre class="pe-pre light">{{ selected.chunk.content }}</pre>
                  </div>
                  <div v-if="ocrLayerValue(selected, 'ocr_raw_text')" class="pe-ocr-layer">
                    <small class="pe-ocr-label">OCR 原始文本 <code>ocr_raw_text</code></small>
                    <pre class="pe-pre">{{ ocrLayerValue(selected, 'ocr_raw_text') }}</pre>
                  </div>
                  <div v-if="ocrLayerValue(selected, 'ocr_cleaned_text')" class="pe-ocr-layer">
                    <small class="pe-ocr-label">OCR 清洗文本 <code>ocr_cleaned_text</code></small>
                    <pre class="pe-pre">{{ ocrLayerValue(selected, 'ocr_cleaned_text') }}</pre>
                  </div>
                  <div v-if="ocrLayerValue(selected, 'ocr_normalized_text')" class="pe-ocr-layer">
                    <small class="pe-ocr-label">OCR 归一化文本 <code>ocr_normalized_text</code></small>
                    <pre class="pe-pre">{{ ocrLayerValue(selected, 'ocr_normalized_text') }}</pre>
                  </div>
                  <div v-if="ocrLayerValue(selected, 'vlm_description')" class="pe-ocr-layer">
                    <small class="pe-ocr-label">VLM 图片描述 <code>vlm_description</code></small>
                    <pre class="pe-pre">{{ ocrLayerValue(selected, 'vlm_description') }}</pre>
                  </div>
                </div>
              </template>

              <h3>证据数据</h3>
              <pre class="pe-pre">{{ safeJson(selected.evidence) }}</pre>
            </template>
            <div v-else class="pe-empty">
              <div>点击左侧问题查看详情。</div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template v-if="run && activeTab === 'chunks'">
      <div class="pe-card">
        <div class="pe-section-title">
          <div>
            <h2>文档切片浏览</h2>
            <p class="muted">按顺序查看文档解析后的所有切片内容，包括文本、表格和图片。</p>
          </div>
          <div class="pe-actions">
            <select v-model="chunkTypeFilter" class="pe-chunk-filter">
              <option value="all">全部类型</option>
              <option value="text">文本</option>
              <option value="parent_text">父文本</option>
              <option value="table">表格</option>
              <option value="image">图片</option>
            </select>
            <select v-model.number="chunksPageSize" class="pe-chunk-filter">
              <option :value="20">20条/页</option>
              <option :value="50">50条/页</option>
              <option :value="100">100条/页</option>
            </select>
          </div>
        </div>

        <div v-if="chunksLoading" class="pe-loading">
          <div class="loader-dot"></div>
          <span>加载切片中...</span>
        </div>

        <div v-else-if="!chunks.length" class="pe-empty">
          <div>暂无切片数据。</div>
        </div>

        <template v-else>
          <div class="pe-chunk-list">
            <article v-for="item in chunks" :key="item.id" class="pe-chunk-card">
              <div class="pe-chunk-media">
                <img v-if="chunkImageUrl(item)" :src="chunkImageUrl(item)" class="pe-chunk-thumb" />
                <div v-else class="pe-chunk-placeholder">{{ chunkTypeLabel(item.chunk_type).charAt(0) }}</div>
              </div>
              <div class="pe-chunk-main">
                <div class="pe-chunk-title-row">
                  <div>
                    <span class="pe-chunk-index">#{{ item.chunk_index }}</span>
                    <span :class="chunkTypeClass(item.chunk_type)">{{ chunkTypeLabel(item.chunk_type) }}</span>
                    <span class="tag tag-muted">{{ item.is_enabled ? '可用' : '已禁用' }}</span>
                  </div>
                </div>
                <p class="pe-chunk-preview">{{ chunkPreview(item) }}</p>
                <div class="pe-chunk-meta">
                  <span>长度 {{ item.token_count }}</span>
                  <span>位置 {{ item.start_at ?? '-' }} - {{ item.end_at ?? '-' }}</span>
                </div>
              </div>
            </article>
          </div>

          <div class="pe-section-title" style="margin-top: 16px;">
            <p class="muted">{{ chunksTotal ? `${(chunksPage - 1) * chunksPageSize + 1}-${Math.min(chunksPage * chunksPageSize, chunksTotal)} / ${chunksTotal}` : '0 / 0' }}</p>
            <div class="pe-actions">
              <button class="secondary" :disabled="chunksPage <= 1 || chunksLoading" @click="chunksPage--; loadChunks()">上一页</button>
              <button class="secondary" :disabled="chunksPage * chunksPageSize >= chunksTotal || chunksLoading" @click="chunksPage++; loadChunks()">下一页</button>
            </div>
          </div>
        </template>
      </div>
    </template>

    <template v-if="run && activeTab === 'report'">
      <div class="pe-report">
        <div class="pe-card">
          <h2>Markdown 评测报告</h2>
          <pre class="pe-pre light">{{ report || '暂无报告内容。' }}</pre>
        </div>
        <div class="pe-card pe-report-aside">
          <h2>报告说明</h2>
          <p class="muted">
            这份报告由评测插件基于文档、切片和解析元数据生成。无 GT 模式下是风险诊断，不代表真实 OCR 准确率；模板模式下会基于规则标准答案计算字段命中情况。
          </p>
          <div class="pe-kv">
            <span>评测模板</span><strong>{{ templateLabel[run.template_id] || run.template_id }}</strong>
            <span>风险等级</span><strong>{{ riskText(run.risk_level) }}</strong>
            <span>问题数量</span><strong>{{ run.item_count ?? total }}</strong>
            <span>创建时间</span><strong>{{ run.created_at }}</strong>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>
