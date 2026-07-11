<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listParseEvalTemplates, type ParseEvalTemplate } from './api'

const router = useRouter()
const docId = ref('')
const templates = ref<ParseEvalTemplate[]>([])
const error = ref('')

const templateLabel: Record<string, string> = {
  generic_no_gt: '无 GT 通用规则诊断',
  scanned_contract_ocr: '扫描合同 OCR 模板评测'
}

const templateTag: Record<string, string> = {
  generic_no_gt: '无标准答案',
  scanned_contract_ocr: '规则模板'
}

onMounted(async () => {
  try {
    templates.value = (await listParseEvalTemplates()).items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取评测模板失败'
  }
})

function openDoc() {
  const id = docId.value.trim()
  if (id) router.push(`/parse-eval/documents/${id}`)
}
</script>

<template>
  <section class="page pe-page">
    <div class="pe-hero">
      <div>
        <p class="pe-eyebrow">Parse Evaluation</p>
        <h1>解析质量评测</h1>
        <p class="muted">
          独立插件式评测模块。它只读取文档、切片和解析事件，用来判断 OCR、清洗、图表裁剪、chunk 入库质量是否可靠，不接管主解析流程。
        </p>
      </div>
      <div class="pe-actions">
        <button class="secondary" @click="router.push('/documents')">返回文档列表</button>
      </div>
    </div>

    <p v-if="error" class="pe-error">{{ error }}</p>

    <div class="pe-grid-2">
      <div class="pe-card">
        <h2>打开文档评测</h2>
        <p class="muted">
          推荐从"文档详情页"点击"解析评测"进入；也可以在这里粘贴文档 ID。
        </p>
        <div class="pe-inline-form">
          <input v-model="docId" placeholder="输入 document_id，例如 31bb36b8-e403-..." @keyup.enter="openDoc" />
          <button @click="openDoc">打开评测页</button>
        </div>
      </div>

      <div class="pe-card">
        <h2>评测页能解决什么</h2>
        <ol class="pe-list">
          <li>没有 GT 时，做质量风险诊断，不给虚假的准确率。</li>
          <li>有规则模板时，自动生成标准字段并计算命中率。</li>
          <li>定位问题来自 OCR、清洗、图表裁剪、caption、chunk 还是 VLM。</li>
          <li>给出是否建议向量化入库的工程判断。</li>
        </ol>
      </div>
    </div>

    <div class="pe-card">
      <div class="pe-section-title">
        <h2>当前支持的评测模板</h2>
        <span class="pe-chip gray">Phase AA</span>
      </div>
      <div class="pe-help-grid">
        <div v-for="tpl in templates" :key="tpl.id" class="pe-help-card">
          <h3>
            {{ templateLabel[tpl.id] || tpl.name }}
            <span class="pe-chip" :class="tpl.needs_gt ? 'orange' : 'blue'">{{ templateTag[tpl.id] || (tpl.needs_gt ? '有模板/GT' : '无GT诊断') }}</span>
          </h3>
          <p class="muted">{{ tpl.description }}</p>
          <p class="muted">适用模式：{{ tpl.mode }}</p>
        </div>
      </div>
    </div>

    <div class="pe-card">
      <div class="pe-section-title">
        <h2>建议使用流程</h2>
      </div>
      <div class="pe-help-grid">
        <div class="pe-help-card">
          <h3>1. 先解析</h3>
          <p class="muted">上传文档并完成解析，确认已经产生文本、图片、表格或 chunk。</p>
        </div>
        <div class="pe-help-card">
          <h3>2. 再评测</h3>
          <p class="muted">运行"无 GT 诊断"或"扫描合同模板"，查看风险和证据。</p>
        </div>
        <div class="pe-help-card">
          <h3>3. 决定入库</h3>
          <p class="muted">评测通过后再手动生成向量；高风险文档先修复 OCR / 清洗 / chunk。</p>
        </div>
      </div>
    </div>
  </section>
</template>
