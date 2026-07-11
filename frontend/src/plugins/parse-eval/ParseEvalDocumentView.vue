<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getDocument, type DocumentItem } from '../../api/documents'
import {
  getLatestParseEvaluation,
  listParseEvalTemplates,
  runParseEvaluation,
  type ParseEvalRun,
  type ParseEvalTemplate
} from './api'

const route = useRoute()
const router = useRouter()
const doc = ref<DocumentItem | null>(null)
const templates = ref<ParseEvalTemplate[]>([])
const latest = ref<ParseEvalRun | null>(null)
const selectedTemplate = ref('generic_no_gt')
const pageCount = ref<number | null>(null)
const linesPerPage = ref(33)
const calloutsPerPage = ref(5)
const loading = ref(false)
const error = ref('')
const message = ref('')

const activeTemplate = computed(() => templates.value.find(item => item.id === selectedTemplate.value))

const templateLabel: Record<string, string> = {
  generic_no_gt: '无 GT 通用规则诊断',
  scanned_contract_ocr: '扫描合同 OCR 模板评测'
}

const templateTag: Record<string, string> = {
  generic_no_gt: '无标准答案',
  scanned_contract_ocr: '规则模板'
}

function riskText(level?: string) {
  const map: Record<string, string> = { low: '低风险', medium: '中风险', high: '高风险', critical: '严重风险', unknown: '未知' }
  return map[level || 'unknown'] || level || '-'
}

function formatSize(size?: number) {
  if (!size && size !== 0) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function score10(run?: ParseEvalRun | null) {
  if (!run) return '-'
  return ((run.score_10 ?? run.score / 10) || 0).toFixed(1)
}

async function load() {
  const docId = route.params.docId as string
  doc.value = await getDocument(docId)
  const res = await listParseEvalTemplates()
  templates.value = res.items
  latest.value = await getLatestParseEvaluation(docId)
}

async function runEval() {
  if (!doc.value) return
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const tpl = activeTemplate.value
    const options: Record<string, unknown> = {}
    if (selectedTemplate.value === 'scanned_contract_ocr') {
      if (pageCount.value) options.page_count = pageCount.value
      options.lines_per_page = linesPerPage.value
      options.callouts_per_page = calloutsPerPage.value
    }
    const run = await runParseEvaluation(doc.value.id, {
      mode: tpl?.mode || 'no_gt',
      template_id: selectedTemplate.value,
      options
    })
    latest.value = run
    message.value = '评测完成，正在打开结果页。'
    router.push(`/parse-eval/runs/${run.id}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '运行评测失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载评测页失败'
  }
})
</script>

<template>
  <section class="page pe-page">
    <p v-if="error" class="pe-error">{{ error }}</p>
    <p v-if="message" class="pe-message">{{ message }}</p>

    <div v-if="doc" class="pe-hero">
      <div>
        <p class="pe-eyebrow">Parse Evaluation</p>
        <h1>{{ doc.title }}</h1>
        <p class="muted">{{ doc.file_name }} · {{ doc.file_type }} · {{ doc.parse_status }}</p>
      </div>
      <div class="pe-actions">
        <button class="secondary" @click="router.push('/parse-eval')">评测首页</button>
        <button class="secondary" @click="router.push(`/documents/${doc.id}`)">返回文档</button>
        <button v-if="latest" @click="router.push(`/parse-eval/runs/${latest.id}`)">查看最近评测</button>
      </div>
    </div>

    <div v-if="doc" class="pe-doc-overview">
      <div class="pe-stat">
        <span>文档 ID</span>
        <strong>{{ doc.id }}</strong>
        <small>评测插件只读取文档结果，不修改解析链路。</small>
      </div>
      <div class="pe-stat"><span>文件类型</span><strong>{{ doc.file_type }}</strong><small>{{ formatSize(doc.file_size) }}</small></div>
      <div class="pe-stat"><span>文本块</span><strong>{{ doc.parse_statistics?.text_block_count || 0 }}</strong><small>解析输出</small></div>
      <div class="pe-stat"><span>图片块</span><strong>{{ doc.parse_statistics?.image_block_count || 0 }}</strong><small>含图表裁剪</small></div>
      <div class="pe-stat"><span>切片数</span><strong>{{ doc.chunk_count }}</strong><small>最终入库 chunk</small></div>
    </div>

    <div v-if="doc" class="pe-grid-2">
      <div class="pe-card">
        <div class="pe-section-title">
          <h2>选择评测模板</h2>
          <span class="pe-chip gray">{{ templates.length }} 个模板</span>
        </div>
        <div class="pe-template-list">
          <button
            v-for="tpl in templates"
            :key="tpl.id"
            class="pe-template-card"
            :class="{ active: selectedTemplate === tpl.id }"
            @click="selectedTemplate = tpl.id"
          >
            <h3>
              {{ templateLabel[tpl.id] || tpl.name }}
              <span class="pe-chip" :class="tpl.needs_gt ? 'orange' : 'blue'">{{ templateTag[tpl.id] || (tpl.needs_gt ? '有模板/GT' : '无GT诊断') }}</span>
            </h3>
            <p class="muted">{{ tpl.description }}</p>
          </button>
        </div>

        <div v-if="selectedTemplate === 'scanned_contract_ocr'" class="pe-card" style="margin-top: 16px; margin-bottom: 0;">
          <h3>扫描合同模板参数</h3>
          <div class="pe-options">
            <label class="pe-field"><span>页数（可选）</span><input v-model.number="pageCount" type="number" placeholder="默认识别不到为 25" /></label>
            <label class="pe-field"><span>每页普通 OCR 行数</span><input v-model.number="linesPerPage" type="number" /></label>
            <label class="pe-field"><span>每页 Callout 数</span><input v-model.number="calloutsPerPage" type="number" /></label>
          </div>
        </div>

        <div class="pe-actions" style="margin-top: 18px;">
          <button class="pe-run-button" :disabled="loading" @click="runEval">{{ loading ? '评测中...' : '运行评测' }}</button>
        </div>
      </div>

      <div class="pe-card">
        <div class="pe-section-title">
          <h2>最近评测</h2>
          <span v-if="latest" class="pe-chip" :class="latest.risk_level === 'high' ? 'red' : latest.risk_level === 'medium' ? 'orange' : 'blue'">
            {{ riskText(latest.risk_level) }}
          </span>
        </div>
        <div v-if="latest" class="pe-latest">
          <div class="pe-score-circle">{{ score10(latest) }}</div>
          <div>
            <h3>{{ riskText(latest.risk_level) }}</h3>
            <p class="muted">{{ latest.recommendation }}</p>
            <div class="pe-kv">
              <span>评测模板</span><strong>{{ templateLabel[latest.template_id] || latest.template_id }}</strong>
              <span>问题数量</span><strong>{{ latest.item_count ?? '-' }}</strong>
              <span>创建时间</span><strong>{{ latest.created_at }}</strong>
            </div>
            <div class="pe-actions" style="margin-top: 14px;">
              <button @click="router.push(`/parse-eval/runs/${latest.id}`)">打开结果</button>
            </div>
          </div>
        </div>
        <div v-else class="pe-empty">
          <div>
            <strong>暂无评测记录</strong><br />请选择模板并点击"运行评测"。
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
