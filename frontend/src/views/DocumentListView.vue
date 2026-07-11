<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBases'
import { deleteDocument, listDocuments, type DocumentItem } from '../api/documents'

const route = useRoute()
const router = useRouter()
const knowledgeBases = ref<KnowledgeBase[]>([])
const knowledgeBaseId = ref((route.query.kb_id as string) || '')
const items = ref<DocumentItem[]>([])
const error = ref('')
const loading = ref(false)

const statusText: Record<string, string> = {
  pending: '待解析',
  parsing: '解析中',
  parsed: '已解析',
  failed: '解析失败'
}

function fileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function loadKnowledgeBases() {
  const result = await listKnowledgeBases()
  knowledgeBases.value = result.items
  if (!knowledgeBaseId.value && result.items.length) {
    knowledgeBaseId.value = result.items[0].id
  }
}

async function loadDocuments() {
  if (!knowledgeBaseId.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await listDocuments(knowledgeBaseId.value)
    items.value = result.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    loading.value = false
  }
}

async function remove(id: string) {
  if (!confirm('确认删除这个文档？')) return
  try {
    await deleteDocument(id)
    await loadDocuments()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

watch(knowledgeBaseId, loadDocuments)

onMounted(async () => {
  try {
    await loadKnowledgeBases()
    await loadDocuments()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1>文档</h1>
        <p class="text-muted">查看知识库下已上传并落盘的本地文档。</p>
      </div>
      <button @click="router.push(`/documents/upload?kb_id=${knowledgeBaseId}`)">上传</button>
    </div>
    <div class="card form inline-form">
      <label>
        知识库
        <select v-model="knowledgeBaseId">
          <option value="">请选择知识库</option>
          <option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
        </select>
      </label>
    </div>
    <p v-if="error" class="text-error">{{ error }}</p>
    <div v-if="!knowledgeBases.length" class="empty-state">
      <div class="empty-icon">▤</div>
      <div>请先创建知识库</div>
    </div>
    <div v-else-if="loading" class="card"><p class="text-muted">加载中...</p></div>
    <div v-else-if="!items.length" class="empty-state">
      <div class="empty-icon">▢</div>
      <div>暂无文档</div>
    </div>
    <table v-else class="data-table">
      <thead>
        <tr>
          <th>标题</th>
          <th>文件名</th>
          <th>类型</th>
          <th>大小</th>
          <th>状态</th>
          <th>切片数</th>
          <th>更新时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="doc in items" :key="doc.id">
          <td>{{ doc.title }}</td>
          <td>{{ doc.file_name }}</td>
          <td>{{ doc.file_type }}</td>
          <td>{{ fileSize(doc.file_size) }}</td>
          <td>{{ statusText[doc.parse_status] || doc.parse_status }}</td>
          <td>{{ doc.chunk_count }}</td>
          <td>{{ doc.updated_at }}</td>
          <td class="actions">
            <button @click="router.push(`/documents/${doc.id}`)">查看</button>
            <button class="secondary" @click="router.push(`/documents/${doc.id}/chunks`)">切片</button>
            <button class="danger" @click="remove(doc.id)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
