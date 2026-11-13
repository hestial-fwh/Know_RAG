<script setup lang="ts">
import type { RetrievalDiagnostics } from '../api/retrieval'

defineProps<{ paths: NonNullable<RetrievalDiagnostics['association']>['paths'] }>()
defineEmits<{ openChunk: [chunkId: string] }>()

function pathLabel(steps: Array<Record<string, unknown>>) {
  return steps.map(step => step.kind === 'shared_entity' ? '共享实体线索'
    : step.kind === 'event_seed' ? '事件种子' : `原文关系 ${String(step.predicate || '')}`).join(' → ')
}
</script>

<template>
  <div class="query-graph" aria-label="查询证据子图">
    <p>节点表示支持原文。关系路径与共享实体线索使用不同文字标签。</p>
    <p v-if="!paths.length" class="text-muted">当前查询未返回完整关联路径。</p>
    <article v-for="(path, index) in paths" :key="index" class="path-card">
      <p>{{ path.hop }} 跳 · {{ pathLabel(path.steps) }}</p>
      <div class="path-nodes">
        <template v-for="(chunkId, nodeIndex) in path.support_chunk_ids" :key="chunkId">
          <span v-if="nodeIndex" class="path-link" aria-hidden="true">──</span>
          <button type="button" class="secondary" @click="$emit('openChunk', chunkId)">查看原文 {{ nodeIndex + 1 }}</button>
        </template>
      </div>
    </article>
  </div>
</template>

<style scoped>
.query-graph { overflow-wrap: anywhere; }
.path-card { border-top: 1px solid var(--border-color, #d9e2ec); padding: 12px 0; }
.path-nodes { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.path-nodes button { min-height: 44px; }
.path-link { color: var(--text-muted, #526479); }
</style>
