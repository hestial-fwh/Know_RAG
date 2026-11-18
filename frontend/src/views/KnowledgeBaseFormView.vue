<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createKnowledgeBase, getKnowledgeBase, updateKnowledgeBase } from '../api/knowledgeBases'
import type { RetrievalProfile } from '../api/retrieval'
import { strategyFromSelection } from '../utils/retrievalStrategy'

const route = useRoute()
const router = useRouter()
const id = route.params.id as string | undefined
const isEdit = Boolean(id)
type FamilyScope = 'a' | 'b'
const INDEXES_BY_FAMILY: Record<FamilyScope, NonNullable<RetrievalProfile['build_indexes']>> = {
  a: ['entities', 'relations', 'communities'],
  b: ['entities', 'events'],
}
const familyScope = ref<FamilyScope>('a')
const retrievalSelection = ref('auto')
const communityProvider = ref<'native' | 'microsoft_graphrag_2_7_0'>('native')
const fusionRankMode = ref<'rrf' | 'feature'>('rrf')
const fusionIncludeGlobal = ref(false)
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
const derivedIndexes = computed(() => INDEXES_BY_FAMILY[familyScope.value])
// 已有 v2 库的家族是只读的：切换家族会让已有索引失效，必须删库重建。
const familyLocked = computed(() => isEdit && Boolean(loadedRetrievalProfile.value?.family_scope))
const indexSummary = computed(() => familyScope.value === 'a'
  ? '实体、关系、社区报告'
  : '实体、事件')

// 切换家族后，把不属于该家族的检索方式回退到 auto，避免提交非法组合。
watch(familyScope, (scope) => {
  const allowed = scope === 'a'
    ? ['auto', 'hybrid', 'semantic', 'graph', 'fusion', 'global', 'drift']
    : ['auto', 'hybrid', 'semantic', 'event_graph']
  if (!allowed.includes(retrievalSelection.value)) retrievalSelection.value = 'auto'
})

async function load() {
  if (!id) return
  try {
    const kb = await getKnowledgeBase(id)
    const profile = kb.retrieval_profile
    familyScope.value = profile?.family_scope === 'b' ? 'b' : 'a'
    retrievalSelection.value = profile?.version === 'new_profile_v2'
      ? (profile.strategy?.family === 'tiered' ? profile.strategy.tier : 'event_graph')
      : 'legacy'
    loadedRetrievalProfile.value = profile || { version: 'legacy_v1' }
    if (profile?.strategy?.family === 'tiered' && profile.strategy.tier === 'graph'
      && profile.strategy.graph_method && profile.strategy.graph_method !== 'local') {
      retrievalSelection.value = profile.strategy.graph_method
    }
    communityProvider.value = profile?.community?.provider || 'native'
    const fusion = profile?.strategy?.family === 'tiered'
      ? profile.strategy.fusion
      : undefined
    if (fusion) {
      fusionRankMode.value = fusion.rank_mode || 'rrf'
      fusionIncludeGlobal.value = Boolean(fusion.include_global)
    }
    indexModelName.value = profile?.index_model?.model_name || ''
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
    // 未改家族、未改检索方式时保留原始 profile，避免编辑名称也触发一次策略迁移。
    const reuseLoaded = loadedRetrievalProfile.value
      && retrievalSelection.value === loadedRetrievalSelection.value
      && familyScope.value === (loadedRetrievalProfile.value.family_scope || 'a')
    // A 家族没有事件索引，融合固定关闭事件通道，只保留关系与 Global 背景。
    const fusionSettings = familyScope.value === 'a' && retrievalSelection.value === 'fusion'
      ? {
          rank_mode: fusionRankMode.value,
          event_policy: 'off' as const,
          include_global: fusionIncludeGlobal.value,
          include_event_timeline: false,
        }
      : undefined
    const retrievalProfile: RetrievalProfile = reuseLoaded
      ? loadedRetrievalProfile.value as RetrievalProfile
      : retrievalSelection.value === 'legacy'
        ? { version: 'legacy_v1' }
        : {
            version: 'new_profile_v2',
            family_scope: familyScope.value,
            build_indexes: [...derivedIndexes.value],
            strategy: strategyFromSelection(
              retrievalSelection.value,
              'hybrid',
              undefined,
              fusionSettings,
            ),
          }
    const payload = { ...form.value, retrieval_profile: {
      ...retrievalProfile,
      family_scope: familyScope.value,
      build_indexes: [...derivedIndexes.value],
      index_model: indexModelName.value.trim() ? {
        ...(indexModelName.value.trim() === loadedRetrievalProfile.value?.index_model?.model_name ? loadedRetrievalProfile.value.index_model : {}),
        provider: 'ollama' as const, model_name: indexModelName.value.trim(),
        base_url: loadedRetrievalProfile.value?.index_model?.base_url || 'http://host.docker.internal:11434'
      } : null,
      community: familyScope.value === 'a' ? {
        ...loadedRetrievalProfile.value?.community,
        provider: communityProvider.value,
        level: communityProvider.value === 'native' ? 0 : loadedRetrievalProfile.value?.community?.level || 0,
      } : {
        provider: 'native' as const,
        level: 0,
      },
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
      <fieldset class="family-choice">
        <legend>检索家族</legend>
        <label class="family-card">
          <input v-model="familyScope" type="radio" value="a" :disabled="familyLocked" />
          <span>
            <strong>关系型（A）</strong>
            <small>适合事实、定义、关系和主题概览；构建实体、关系、社区报告。</small>
          </span>
        </label>
        <label class="family-card">
          <input v-model="familyScope" type="radio" value="b" :disabled="familyLocked" />
          <span>
            <strong>事件型（B）</strong>
            <small>适合事件、时间线、参与者；只构建实体和事件，不建关系与社区。</small>
          </span>
        </label>
        <p class="text-muted">
          一个知识库只能选择一种家族；创建后不可更改，需要更换时请删除后按新家族重建。
          历史库未设置家族时，需要在首次保存前选择一次。
        </p>
      </fieldset>
      <label>
        检索方式
        <select v-model="retrievalSelection">
          <option v-if="loadedRetrievalSelection === 'legacy'" value="legacy">沿用全局配置（仅旧库）</option>
          <option value="auto">自动选择（推荐，按意图路由）</option>
          <option value="hybrid">标准检索（固定混合，不路由）</option>
          <option value="semantic">仅语义检索（只走向量）</option>
          <template v-if="familyScope === 'a'">
            <option value="graph">Local 关系增强（只走关系邻域）</option>
            <option value="fusion">关系 + 主题背景（固定组合）</option>
            <option value="global">Global 主题综合（社区报告替代主结果）</option>
            <option value="drift">DRIFT 有界探索（多轮子问题）</option>
          </template>
          <option v-else value="event_graph">事件关联（基础 + 事件通道）</option>
        </select>
      </label>
      <p class="text-muted">
        自动选择时：A 家族可在语义、混合、Local、Global、DRIFT 之间路由；B 家族在混合和事件关联之间路由，关系与概览问题降级为混合。
      </p>
      <template v-if="familyScope === 'a' && retrievalSelection === 'fusion'">
        <label>
          结构排序方式
          <select v-model="fusionRankMode">
            <option value="rrf">两级 RRF（推荐）</option>
            <option value="feature">图特征重排（保守）</option>
          </select>
        </label>
        <label class="check"><input v-model="fusionIncludeGlobal" type="checkbox" /> 附带 Global 背景摘要（不可引用）</label>
        <p class="text-muted">A 家族没有事件通道，融合只组合关系与主检索结果；两类都不可用时回退混合检索。</p>
      </template>
      <p class="text-muted">
        将构建：{{ indexSummary }}。索引由本机 Ollama 和独立 worker 生成，保存后可在知识库详情页查看进度。
      </p>
      <label>
        本知识库抽取模型（本机 Ollama）
        <input v-model="indexModelName" placeholder="qwen2.5:3b，留空使用全局生成模型" />
      </label>
      <template v-if="familyScope === 'a'">
        <label>
          社区构建方式
          <select v-model="communityProvider">
            <option value="native">本地确定性分区</option>
            <option value="microsoft_graphrag_2_7_0">官方 GraphRAG 2.7.0 Leiden（独立运行环境）</option>
          </select>
        </label>
        <p class="text-muted">先构建关系，再由 worker 生成带原文支持的派生报告；删除或修改来源后旧报告立即失效。</p>
      </template>
      <p v-if="error" class="text-error">{{ error }}</p>
      <div class="actions">
        <button :disabled="loading">{{ loading ? '保存中...' : '保存' }}</button>
        <button type="button" class="secondary" @click="router.back()">取消</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.family-choice {
  margin: 8px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-surface-alt);
}
.family-choice legend {
  padding: 0 4px;
  color: var(--t-secondary);
  font-size: 13px;
}
.family-card {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: 8px 0;
  cursor: pointer;
}
.family-card input {
  margin-top: 4px;
}
.family-card span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.family-card small {
  color: var(--t-secondary);
}
</style>