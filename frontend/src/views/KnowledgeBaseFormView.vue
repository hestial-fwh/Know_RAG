<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createKnowledgeBase, getKnowledgeBase, updateKnowledgeBase } from '../api/knowledgeBases'
import type { RetrievalProfile } from '../api/retrieval'
import { strategyFromSelection } from '../utils/retrievalStrategy'

const route = useRoute()
const router = useRouter()
const id = route.params.id as string | undefined
const isEdit = Boolean(id)
const retrievalSelection = ref('auto')
const ADVANCED_SELECTIONS = ['semantic', 'graph', 'global', 'drift', 'event_graph'] as const
const isAdvancedSelection = (value: string) => (ADVANCED_SELECTIONS as readonly string[]).includes(value)
const ADVANCED_LABELS: Record<string, string> = {
  semantic: '仅语义检索',
  graph: 'Local 图增强',
  global: 'Global 主题综合',
  drift: 'DRIFT 有界探索',
  event_graph: '旧事件关联路径',
}
const advancedLabel = (value: string) => ADVANCED_LABELS[value] || value
const ALL_INDEXES: NonNullable<RetrievalProfile['build_indexes']> = [
  'entities', 'relations', 'events', 'communities',
]
const communityProvider = ref<'native' | 'microsoft_graphrag_2_7_0'>('native')
const fusionRankMode = ref<'rrf' | 'feature'>('rrf')
const fusionEventPolicy = ref<'independent' | 'fused' | 'off'>('independent')
const fusionIncludeGlobal = ref(false)
const fusionIncludeEventTimeline = ref(false)
const indexModelName = ref('')
const loadedRetrievalProfile = ref<RetrievalProfile | null>(null)
const loadedRetrievalSelection = ref('')
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
    retrievalSelection.value = kb.retrieval_profile?.version === 'new_profile_v2'
      ? (kb.retrieval_profile.strategy?.family === 'tiered' ? kb.retrieval_profile.strategy.tier : 'event_graph')
      : 'legacy'
    loadedRetrievalProfile.value = kb.retrieval_profile || { version: 'legacy_v1' }
    if (kb.retrieval_profile?.strategy?.family === 'tiered' && kb.retrieval_profile.strategy.tier === 'graph'
      && kb.retrieval_profile.strategy.graph_method && kb.retrieval_profile.strategy.graph_method !== 'local') {
      retrievalSelection.value = kb.retrieval_profile.strategy.graph_method
    }
    communityProvider.value = kb.retrieval_profile?.community?.provider || 'native'
    const fusion = kb.retrieval_profile?.strategy?.family === 'tiered'
      ? kb.retrieval_profile.strategy.fusion
      : undefined
    if (fusion) {
      fusionRankMode.value = fusion.rank_mode || 'rrf'
      fusionEventPolicy.value = fusion.event_policy || 'independent'
      fusionIncludeGlobal.value = Boolean(fusion.include_global)
      fusionIncludeEventTimeline.value = Boolean(fusion.include_event_timeline)
    }
    indexModelName.value = kb.retrieval_profile?.index_model?.model_name || ''
    loadedRetrievalSelection.value = retrievalSelection.value
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
    // 只在用户保存时迁移旧库；载入编辑页不会改变原策略。
    const retrievalProfile: RetrievalProfile = loadedRetrievalProfile.value && retrievalSelection.value === loadedRetrievalSelection.value
      ? loadedRetrievalProfile.value
      : retrievalSelection.value === 'legacy'
        ? { version: 'legacy_v1' }
        : { version: 'new_profile_v2', strategy: strategyFromSelection(
          retrievalSelection.value,
          'hybrid',
          undefined,
          retrievalSelection.value === 'fusion' ? {
            rank_mode: fusionRankMode.value,
            event_policy: fusionEventPolicy.value,
            include_global: fusionIncludeGlobal.value,
            include_event_timeline: fusionIncludeEventTimeline.value,
          } : undefined,
        ) }
    // 派生索引固定全量构建；legacy 旧库保持原有索引开关，避免迁移时额外调用模型。
    const indexes: NonNullable<RetrievalProfile['build_indexes']> = retrievalSelection.value === 'legacy'
      ? (loadedRetrievalProfile.value?.build_indexes || [])
      : [...ALL_INDEXES]
    const payload = { ...form.value, retrieval_profile: {
      ...retrievalProfile, build_indexes: indexes,
      index_model: indexModelName.value.trim() ? {
        ...(indexModelName.value.trim() === loadedRetrievalProfile.value?.index_model?.model_name ? loadedRetrievalProfile.value.index_model : {}),
        provider: 'ollama' as const, model_name: indexModelName.value.trim(),
        base_url: loadedRetrievalProfile.value?.index_model?.base_url || 'http://host.docker.internal:11434'
      } : null,
      community: { ...loadedRetrievalProfile.value?.community, provider: communityProvider.value,
        level: communityProvider.value === 'native' ? 0 : loadedRetrievalProfile.value?.community?.level || 0 }
    } }
    const saved = isEdit && id
      ? await updateKnowledgeBase(id, payload)
      : await createKnowledgeBase(payload)
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
      <label>
        检索预设
        <select v-model="retrievalSelection" aria-describedby="retrieval-help">
          <option v-if="loadedRetrievalSelection === 'legacy'" value="legacy">沿用全局配置（仅旧库）</option>
          <option v-if="isAdvancedSelection(retrievalSelection)" :value="retrievalSelection">{{ advancedLabel(retrievalSelection) }}（高级）</option>
          <option value="hybrid">标准检索（固定混合，不路由）</option>
          <option value="auto">自动选择（推荐，按意图路由）</option>
          <option value="fusion">融合检索（固定组合）</option>
        </select>
      </label>
      <p id="retrieval-help" class="text-muted">默认使用自动选择：检索时按意图决定走混合、关系、事件或融合。新建知识库会构建实体、关系、事件和社区四类索引，供路由和背景使用。</p>
      <details class="advanced-strategy" :open="isAdvancedSelection(retrievalSelection)">
        <summary>高级 / 兼容路径</summary>
        <label>
          高级策略
          <select v-model="retrievalSelection">
            <option value="semantic">仅语义检索（只走向量）</option>
            <option value="graph">Local 图增强（只走关系邻域）</option>
            <option value="global">Global 主题综合（社区报告替代主结果）</option>
            <option value="drift">DRIFT 有界探索（多轮子问题）</option>
            <option value="event_graph">旧事件关联路径（基础 + 事件等权）</option>
          </select>
        </label>
        <p class="text-muted">保留给旧知识库、对比测试和特定场景；选择后会覆盖上面的预设。</p>
      </details>
      <template v-if="retrievalSelection === 'fusion'">
        <label>
          结构排序方式
          <select v-model="fusionRankMode">
            <option value="rrf">两级 RRF（推荐）</option>
            <option value="feature">图特征重排（保守）</option>
          </select>
        </label>
        <label>
          事件通道政策
          <select v-model="fusionEventPolicy">
            <option value="independent">独立计分 + 保留位（推荐）</option>
            <option value="fused">并入结构排名</option>
            <option value="off">关闭事件通道</option>
          </select>
        </label>
        <label class="check"><input v-model="fusionIncludeGlobal" type="checkbox" /> 附带 Global 背景摘要（不可引用）</label>
        <label class="check"><input v-model="fusionIncludeEventTimeline" type="checkbox" /> 输出事件线（派生上下文）</label>
        <p class="text-muted">融合检索至少需要关系或事件索引；两类都未就绪时会按回退策略降级为混合检索。</p>
      </template>
      <p class="text-muted">保存后自动构建全部派生索引：实体、关系、事件、社区（社区依赖关系）。可在知识库详情页查看构建进度。</p>
      <label>
        本知识库抽取模型（本机 Ollama）
        <input v-model="indexModelName" placeholder="qwen2.5:3b，留空使用全局生成模型" />
      </label>
      <label>
        社区构建方式
        <select v-model="communityProvider">
          <option value="native">本地确定性分区</option>
          <option value="microsoft_graphrag_2_7_0">官方 GraphRAG 2.7.0 Leiden（独立运行环境）</option>
        </select>
      </label>
      <p id="community-help" class="text-muted">先构建关系，再由 worker 生成带原文支持的派生报告。官方选项使用官方社区算法；报告和 Global 证据校验由本项目负责。删除或修改来源后旧报告立即失效。</p>
      <p id="entity-index-help" class="text-muted">索引由本机 Ollama 和独立 worker 构建，未启动 worker 时任务会一直排队；保存后无需手动勾选或补建。</p>
      <div class="actions">
        <button :disabled="loading">{{ loading ? '保存中...' : '保存' }}</button>
        <button type="button" class="secondary" @click="router.back()">取消</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.advanced-strategy {
  margin: 8px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-surface-alt);
}
.advanced-strategy summary {
  cursor: pointer;
  color: var(--t-secondary);
  font-size: 13px;
}
.advanced-strategy select {
  margin-top: 8px;
}
</style>
