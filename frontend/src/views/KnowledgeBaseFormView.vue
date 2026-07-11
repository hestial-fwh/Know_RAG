<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createKnowledgeBase, getKnowledgeBase, updateKnowledgeBase } from '../api/knowledgeBases'

const route = useRoute()
const router = useRouter()
const id = route.params.id as string | undefined
const isEdit = Boolean(id)
const form = ref({
  name: '',
  description: '',
  embedding_model_id: '',
  summary_model_id: ''
})
const error = ref('')
const loading = ref(false)

async function load() {
  if (!id) return
  try {
    const kb = await getKnowledgeBase(id)
    form.value = {
      name: kb.name,
      description: kb.description || '',
      embedding_model_id: kb.embedding_model_id || '',
      summary_model_id: kb.summary_model_id || ''
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
}

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const saved = isEdit && id
      ? await updateKnowledgeBase(id, form.value)
      : await createKnowledgeBase(form.value)
    router.push(`/knowledge-bases/${saved.id}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page narrow">
    <h1>{{ isEdit ? '编辑知识库' : '新建知识库' }}</h1>
    <form class="card form" @submit.prevent="submit">
      <label>
        名称
        <input v-model="form.name" required />
      </label>
      <label>
        描述
        <textarea v-model="form.description" rows="4" />
      </label>
      <label>
        Embedding 模型 ID
        <input v-model="form.embedding_model_id" placeholder="bge-m3" />
      </label>
      <label>
        摘要模型 ID
        <input v-model="form.summary_model_id" placeholder="qwen2.5:3b" />
      </label>
      <p v-if="error" class="text-error">{{ error }}</p>
      <div class="actions">
        <button :disabled="loading">{{ loading ? '保存中...' : '保存' }}</button>
        <button type="button" class="secondary" @click="router.back()">取消</button>
      </div>
    </form>
  </section>
</template>
