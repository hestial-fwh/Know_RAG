<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getRetrievalConfig, updateRetrievalConfig, type RetrievalConfig } from '../api/config'

const form = ref<RetrievalConfig>({
  embedding_top_k: 5,
  score_threshold: 0.3,
  use_vector: true,
  use_keyword: true,
  rerank_model_id: '',
  fusion_strategy: 'rrf',
  rrf_k: 60,
  rrf_vector_weight: 1,
  rrf_keyword_weight: 1,
  include_context: true
})
const message = ref('')
const error = ref('')

async function load() {
  form.value = await getRetrievalConfig()
}

async function save() {
  error.value = ''
  message.value = ''
  try {
    form.value = await updateRetrievalConfig(form.value)
    message.value = '保存成功'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page narrow">
    <h1>检索配置</h1>
    <form class="card form" @submit.prevent="save">
      <label>Embedding Top K <input v-model.number="form.embedding_top_k" type="number" /></label>
      <label>分数阈值 <input v-model.number="form.score_threshold" type="number" step="0.01" /></label>
      <label>
        融合策略
        <select v-model="form.fusion_strategy">
          <option value="rrf">RRF</option>
          <option value="weighted">加权分数</option>
        </select>
      </label>
      <div class="grid">
        <label>RRF K <input v-model.number="form.rrf_k" type="number" min="1" /></label>
        <label>向量权重 <input v-model.number="form.rrf_vector_weight" type="number" min="0" step="0.1" /></label>
        <label>关键词权重 <input v-model.number="form.rrf_keyword_weight" type="number" min="0" step="0.1" /></label>
      </div>
      <label class="check"><input v-model="form.use_vector" type="checkbox" /> 启用向量检索</label>
      <label class="check"><input v-model="form.use_keyword" type="checkbox" /> 启用关键词检索</label>
      <label class="check"><input v-model="form.include_context" type="checkbox" /> 默认返回上下文预览</label>
      <label>Rerank 模型 ID <input v-model="form.rerank_model_id" /></label>
      <p v-if="message" class="text-success">{{ message }}</p>
      <p v-if="error" class="text-error">{{ error }}</p>
      <button>保存</button>
    </form>
  </section>
</template>
