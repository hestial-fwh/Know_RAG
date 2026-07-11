<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  deleteKnowledgeBase,
  listKnowledgeBases,
  type KnowledgeBase
} from '../api/knowledgeBases'
import PageHeader from '../components/common/PageHeader.vue'

const router = useRouter()
const items = ref<KnowledgeBase[]>([])
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await listKnowledgeBases()
    items.value = result.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    loading.value = false
  }
}

async function remove(id: string) {
  if (!confirm('确认删除这个知识库？')) return
  try {
    await deleteKnowledgeBase(id)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page wide-page">
    <PageHeader title="知识库" description="创建和维护本地知识库">
      <template #actions>
        <button @click="router.push('/knowledge-bases/new')">新建知识库</button>
      </template>
    </PageHeader>

    <p v-if="error" class="text-error">{{ error }}</p>

    <div v-if="loading" class="card">
      <p class="text-muted">加载中...</p>
    </div>

    <div v-else-if="!items.length" class="empty-state">
      <div class="empty-icon">▤</div>
      <div>暂无知识库</div>
      <small>点击"新建知识库"创建你的第一个知识库</small>
    </div>

    <div v-else class="grid-3">
      <div v-for="kb in items" :key="kb.id" class="card kb-list-card">
        <div class="kb-card-header" @click="router.push(`/knowledge-bases/${kb.id}`)">
          <h3>{{ kb.name }}</h3>
          <p class="text-muted">{{ kb.description || '暂无描述' }}</p>
        </div>
        <div class="kb-card-metrics">
          <div class="kb-metric">
            <strong>{{ kb.document_count }}</strong>
            <span>文档</span>
          </div>
          <div class="kb-metric">
            <strong>{{ kb.chunk_count }}</strong>
            <span>切片</span>
          </div>
        </div>
        <small class="text-muted" style="display: block; margin: 8px 0 12px;">{{ kb.updated_at }}</small>
        <div class="actions">
          <button class="sm" @click="router.push(`/knowledge-bases/${kb.id}`)">查看</button>
          <button class="sm secondary" @click="router.push(`/knowledge-bases/${kb.id}/edit`)">编辑</button>
          <button class="sm danger" @click="remove(kb.id)">删除</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.kb-list-card {
  transition: transform 0.15s, box-shadow 0.15s;
}

.kb-list-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.kb-card-header {
  cursor: pointer;
  margin-bottom: 12px;
}

.kb-card-header h3 {
  margin-bottom: 4px;
}

.kb-card-metrics {
  display: flex;
  gap: 20px;
  padding: 10px 0;
  border-top: 1px solid var(--c-border-light);
  border-bottom: 1px solid var(--c-border-light);
}

.kb-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kb-metric strong {
  font-size: 20px;
  color: var(--t-primary);
}

.kb-metric span {
  font-size: 12px;
  color: var(--t-muted);
  font-weight: 600;
}
</style>
