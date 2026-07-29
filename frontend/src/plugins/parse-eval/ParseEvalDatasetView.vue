<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  getDocumentProfileDatasetReport,
  type DocumentProfileDatasetCase,
  type DocumentProfileDatasetResponse
} from './api'

const router = useRouter()
const data = ref<DocumentProfileDatasetResponse | null>(null)
const error = ref('')
const query = ref('')
const capability = ref('all')
const selected = ref<DocumentProfileDatasetCase | null>(null)

const cases = computed(() => data.value?.report?.adversarial_dataset.cases || [])
const capabilities = computed(() => data.value?.report?.adversarial_dataset.capabilities || [])
const filteredCases = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return cases.value.filter((item) => {
    const capabilityMatch = capability.value === 'all' || item.capabilities.includes(capability.value)
    const textMatch = !needle || [
      item.case_id,
      item.document_id,
      item.confusion_group,
      item.variant,
      ...item.diffs
    ].join(' ').toLowerCase().includes(needle)
    return capabilityMatch && textMatch
  })
})

onMounted(async () => {
  try {
    data.value = await getDocumentProfileDatasetReport()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取 DCP-0.1 报告失败'
  }
})
</script>

<template>
  <section class="page pe-page">
    <div class="pe-hero">
      <div>
        <p class="pe-eyebrow">DCP-0.1 Dataset Acceptance</p>
        <h1>对抗数据集验收</h1>
        <p class="muted">展示合成 case、gold、当前 replay actual 和差异。该页面只证明工程覆盖，不声明真实 MinerU 准确率。</p>
      </div>
      <div class="pe-actions">
        <button class="secondary" @click="router.push('/parse-eval')">返回解析评测</button>
      </div>
    </div>

    <p v-if="error" class="pe-error">{{ error }}</p>
    <div v-if="data && !data.available" class="pe-card">
      <h2>尚未生成数据集</h2>
      <p class="muted">在 backend 目录执行：</p>
      <pre class="content-block">{{ data.generate_command }}</pre>
    </div>

    <template v-else-if="data?.report">
      <div class="pe-score-strip">
        <div class="pe-score-card">
          <span>总页面场景</span>
          <strong>{{ data.manifest?.page_count || 0 }}</strong>
        </div>
        <div class="pe-score-card">
          <span>对抗场景</span>
          <strong>{{ data.report.adversarial_dataset.case_count }}</strong>
        </div>
        <div class="pe-score-card">
          <span>混淆组</span>
          <strong>{{ data.report.adversarial_dataset.confusion_group_count }}</strong>
        </div>
        <div class="pe-score-card" :class="data.report.dcp01_dataset_gate_passed ? 'pe-risk-low' : 'pe-risk-high'">
          <span>DCP-0.1 数据门禁</span>
          <strong>{{ data.report.dcp01_dataset_gate_passed ? 'PASS' : 'FAIL' }}</strong>
        </div>
      </div>

      <div class="pe-card">
        <div class="pe-inline-form">
          <input v-model="query" placeholder="搜索 case、混淆组或差异" />
          <select v-model="capability">
            <option value="all">全部能力</option>
            <option v-for="item in capabilities" :key="item" :value="item">{{ item }}</option>
          </select>
        </div>
        <p class="muted">
          {{ filteredCases.length }} 个结果 · accuracy_claim_allowed={{ data.report.accuracy_claim_allowed }}
        </p>
        <div class="pe-dataset-table-wrap">
          <table class="pe-dataset-table">
            <thead>
              <tr><th>Case</th><th>混淆组 / 变体</th><th>能力</th><th>Actual</th><th>差异</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredCases" :key="`${item.document_id}-${item.page_no}`" @click="selected = item">
                <td><strong>{{ item.case_id }}</strong><br><small>{{ item.document_id }} · P{{ item.page_no }}</small></td>
                <td>{{ item.confusion_group || '-' }}<br><small>{{ item.variant || '-' }}</small></td>
                <td><span v-for="tag in item.capabilities" :key="tag" class="pe-chip gray">{{ tag }}</span></td>
                <td>{{ item.actual.page_kind || '-' }}</td>
                <td>{{ item.diffs.join('；') || '无差异' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="selected" class="pe-card">
        <div class="pe-section-title">
          <h2>{{ selected.case_id }} · Gold / Actual</h2>
          <button class="secondary" @click="selected = null">关闭</button>
        </div>
        <div class="pe-grid-2">
          <div><h3>Gold</h3><pre class="content-block">{{ JSON.stringify(selected.gold, null, 2) }}</pre></div>
          <div><h3>Actual</h3><pre class="content-block">{{ JSON.stringify(selected.actual, null, 2) }}</pre></div>
        </div>
        <h3>复现与产物</h3>
        <pre class="content-block">{{ JSON.stringify(selected.artifacts, null, 2) }}</pre>
      </div>
    </template>
  </section>
</template>
