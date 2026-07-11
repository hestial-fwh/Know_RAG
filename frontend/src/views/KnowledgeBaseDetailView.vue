<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  embedKnowledgeBase,
  getKnowledgeBase,
  getKnowledgeBaseEmbeddingStatus,
  type KnowledgeBase,
  type KnowledgeBaseEmbeddingStatus
} from '../api/knowledgeBases'

const route = useRoute()
const router = useRouter()
const kb = ref<KnowledgeBase | null>(null)
const embeddingStatus = ref<KnowledgeBaseEmbeddingStatus | null>(null)
const error = ref('')
const message = ref('')
const loading = ref(false)
const statusLoading = ref(false)

async function loadEmbeddingStatus() {
  if (!kb.value) return
  statusLoading.value = true
  error.value = ''
  message.value = ''
  try {
    embeddingStatus.value = await getKnowledgeBaseEmbeddingStatus(kb.value.id)
    message.value = '向量化状态已刷新'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取向量化状态失败'
  } finally {
    statusLoading.value = false
  }
}

async function runEmbedding() {
  if (!kb.value) return
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await embedKnowledgeBase(kb.value.id)
    embeddingStatus.value = {
      knowledge_base_id: result.knowledge_base_id,
      total_chunks: result.total_chunks,
      embedded_chunks: result.embedded_chunks,
      missing_chunks: result.missing_chunks,
      model: result.model,
      dimension: result.dimension
    }
    message.value = '知识库向量生成完成'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '向量生成失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    kb.value = await getKnowledgeBase(route.params.id as string)
    await loadEmbeddingStatus()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page">
    <p v-if="error" class="text-error">{{ error }}</p>
    <p v-if="message" class="text-success">{{ message }}</p>
    <div v-if="kb" class="card">
      <div class="page-header">
        <div>
          <h1>{{ kb.name }}</h1>
          <p class="text-muted">{{ kb.description || '暂无描述' }}</p>
        </div>
        <div class="actions">
          <button type="button" @click="router.push(`/documents/upload?kb_id=${kb.id}`)">上传文档</button>
          <button type="button" class="secondary" @click="router.push(`/documents?kb_id=${kb.id}`)">查看文档</button>
          <button type="button" :disabled="loading" @click="runEmbedding">
            {{ loading ? '正在生成向量...' : '批量生成向量' }}
          </button>
          <button type="button" class="secondary" :disabled="statusLoading" @click="loadEmbeddingStatus">
            {{ statusLoading ? '正在获取状态...' : '查看向量化状态' }}
          </button>
          <button type="button" class="secondary" @click="router.push(`/knowledge-bases/${kb.id}/edit`)">编辑</button>
        </div>
      </div>
      <dl class="details">
        <dt>ID</dt><dd>{{ kb.id }}</dd>
        <dt>文档数</dt><dd>{{ kb.document_count }}</dd>
        <dt>切片数</dt><dd>{{ kb.chunk_count }}</dd>
        <dt>已向量化</dt>
        <dd>
          {{ embeddingStatus ? `${embeddingStatus.embedded_chunks}/${embeddingStatus.total_chunks}，待处理 ${embeddingStatus.pending_chunks ?? embeddingStatus.real_missing_chunks ?? 0}，规则跳过 ${embeddingStatus.skipped_chunks ?? 0}` : '-' }}
        </dd>
        <dt>规则跳过</dt><dd>{{ embeddingStatus?.skipped_chunks ?? '-' }}</dd>
        <dt>异常未向量化</dt><dd>{{ embeddingStatus?.pending_chunks ?? embeddingStatus?.real_missing_chunks ?? '-' }}</dd>
        <dt>向量模型</dt><dd>{{ embeddingStatus?.model || '-' }}</dd>
        <dt>向量维度</dt><dd>{{ embeddingStatus?.dimension || '-' }}</dd>
        <dt>Embedding 模型</dt><dd>{{ kb.embedding_model_id || '-' }}</dd>
        <dt>摘要模型</dt><dd>{{ kb.summary_model_id || '-' }}</dd>
        <dt>创建时间</dt><dd>{{ kb.created_at }}</dd>
        <dt>更新时间</dt><dd>{{ kb.updated_at }}</dd>
      </dl>
    </div>
  </section>
</template>
