<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBases'
import StatCard from '../components/common/StatCard.vue'

const router = useRouter()
const knowledgeBases = ref<KnowledgeBase[]>([])
const loading = ref(false)

const totalDocs = computed(() => knowledgeBases.value.reduce((sum, kb) => sum + (kb.document_count || 0), 0))
const totalChunks = computed(() => knowledgeBases.value.reduce((sum, kb) => sum + (kb.chunk_count || 0), 0))
const kbCount = computed(() => knowledgeBases.value.length)

const quickActions = [
  { to: '/knowledge-bases/new', icon: '＋', label: '新建知识库', desc: '创建新的知识库来组织文档' },
  { to: '/documents/upload', icon: '⬆', label: '上传文档', icon_bg: 'upload' },
  { to: '/debug/search', icon: '◎', label: '检索调试', desc: '测试本地混合检索效果' },
  { to: '/parse-eval', icon: '◈', label: '解析评测', desc: '检查文档解析质量' }
]

onMounted(async () => {
  loading.value = true
  try {
    const result = await listKnowledgeBases()
    knowledgeBases.value = result.items
  } catch {
    // silent
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="page wide-page">
    <div class="hero-header">
      <p class="eyebrow">Dashboard</p>
      <h1>know_rag 知识库管理</h1>
      <p class="text-muted">本地知识库建立维护底座 · 数据留在本地</p>
    </div>

    <div class="grid-4" style="margin-bottom: 28px;">
      <StatCard label="知识库" :value="kbCount" icon="▤" />
      <StatCard label="文档总数" :value="totalDocs" icon="▢" />
      <StatCard label="切片总数" :value="totalChunks" icon="▦" />
      <StatCard label="解析状态" value="就绪" icon="◈" description="DeepDoc 引擎" />
    </div>

    <h2 style="margin-bottom: 16px;">快速操作</h2>
    <div class="grid-4" style="margin-bottom: 28px;">
      <div
        v-for="action in quickActions"
        :key="action.to"
        class="quick-action card compact"
        @click="router.push(action.to)"
      >
        <span class="quick-action-icon">{{ action.icon }}</span>
        <strong>{{ action.label }}</strong>
        <small v-if="action.desc" class="text-muted">{{ action.desc }}</small>
      </div>
    </div>

    <h2 style="margin-bottom: 16px;">知识库概览</h2>
    <div v-if="loading" class="card">
      <p class="text-muted">加载中...</p>
    </div>
    <div v-else-if="!knowledgeBases.length" class="empty-state">
      <div class="empty-icon">▤</div>
      <div>暂无知识库</div>
      <small>点击右上角"新建知识库"开始创建</small>
    </div>
    <div v-else class="grid-3">
      <div
        v-for="kb in knowledgeBases"
        :key="kb.id"
        class="card kb-card"
        @click="router.push(`/knowledge-bases/${kb.id}`)"
      >
        <h3>{{ kb.name }}</h3>
        <p class="text-muted" style="margin: 4px 0 12px;">{{ kb.description || '暂无描述' }}</p>
        <div class="kb-card-stats">
          <span><strong>{{ kb.document_count }}</strong> 文档</span>
          <span><strong>{{ kb.chunk_count }}</strong> 切片</span>
        </div>
        <small class="text-muted" style="margin-top: 8px; display: block;">{{ kb.updated_at }}</small>
      </div>
    </div>
  </section>
</template>

<style scoped>
.quick-action {
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quick-action:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.quick-action-icon {
  font-size: 24px;
  color: var(--c-primary);
  margin-bottom: 4px;
}

.kb-card {
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}

.kb-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.kb-card-stats {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: var(--t-secondary);
}

.kb-card-stats strong {
  color: var(--t-primary);
  font-size: 16px;
}
</style>
