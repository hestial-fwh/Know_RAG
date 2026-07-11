<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getChunkerConfig, updateChunkerConfig, type ChunkerConfig } from '../api/config'

const form = ref<ChunkerConfig>({
  strategy: 'auto',
  chunk_size: 512,
  chunk_overlap: 80,
  separators: ['\n\n', '\n', '。', '.', '；', ';', '，', ',', ' '],
  enable_parent_child: false,
  parent_chunk_size: 4096,
  child_chunk_size: 384,
  child_chunk_overlap: 64
})
const separatorText = ref('')
const error = ref('')
const message = ref('')
const loading = ref(false)

function syncSeparatorText() {
  separatorText.value = (form.value.separators || []).map((item) => item.replace(/\n/g, '\\n')).join('|')
}

function syncSeparatorsFromText() {
  form.value.separators = separatorText.value
    .split('|')
    .map((item) => item.replace(/\\n/g, '\n'))
    .filter((item) => item.length > 0)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    form.value = await getChunkerConfig()
    syncSeparatorText()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取切分配置失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  message.value = ''
  syncSeparatorsFromText()
  try {
    form.value = await updateChunkerConfig(form.value)
    syncSeparatorText()
    message.value = '切分配置已保存，下一次上传或重新解析文档时生效'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存切分配置失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page narrow">
    <h1>切分配置</h1>
    <div v-if="loading" class="card"><p class="text-muted">加载中...</p></div>
    <form v-else class="card form" @submit.prevent="save">
      <label>
        切分策略
        <select v-model="form.strategy">
          <option value="auto">自动</option>
          <option value="heading">标题感知</option>
          <option value="recursive">递归分隔符</option>
          <option value="legacy">传统字符窗口</option>
        </select>
      </label>
      <div class="grid">
        <label>切片大小 <input v-model.number="form.chunk_size" type="number" min="100" max="10000" /></label>
        <label>重叠长度 <input v-model.number="form.chunk_overlap" type="number" min="0" max="5000" /></label>
      </div>
      <label>分隔符（使用 | 分隔，换行写作 \n） <input v-model="separatorText" /></label>
      <label class="check">
        <input v-model="form.enable_parent_child" type="checkbox" />
        启用父子分块
      </label>
      <div class="grid">
        <label>父切片大小 <input v-model.number="form.parent_chunk_size" type="number" min="512" max="50000" /></label>
        <label>子切片大小 <input v-model.number="form.child_chunk_size" type="number" min="100" max="10000" /></label>
        <label>子切片重叠 <input v-model.number="form.child_chunk_overlap" type="number" min="0" max="5000" /></label>
      </div>
      <p class="text-muted">配置保存后不会自动重切已有文档。请在文档详情页点击"重新解析"以应用新切分策略，旧向量会被清除。</p>
      <p v-if="message" class="text-success">{{ message }}</p>
      <p v-if="error" class="text-error">{{ error }}</p>
      <div class="actions">
        <button>保存</button>
        <button type="button" class="secondary" @click="load">刷新</button>
      </div>
    </form>
  </section>
</template>
