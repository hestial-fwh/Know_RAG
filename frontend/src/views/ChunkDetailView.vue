<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getChunk, type ChunkItem } from '../api/chunks'
import { resolveImageUrl } from '../utils/media'
import {
  admissionChannelEntries,
  formatSourceList
} from '../utils/contentAdmission'

const route = useRoute()
const router = useRouter()
const chunk = ref<ChunkItem | null>(null)
const error = ref('')
const showPreview = ref(false)

const statusText: Record<string, string> = {
  ready: '可用',
  disabled: '已禁用'
}

const vectorPreview = computed(() => chunk.value?.embedding_index_info?.vector_preview || [])
const vectorText = computed(() => vectorPreview.value.map((v) => Number(v).toFixed(6)).join(', '))
const contentAdmission = computed(() => {
  const metadata = chunk.value?.metadata_json || chunk.value?.metadata || {}
  const value = metadata.content_admission
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
})
const admissionChannels = computed(() => {
  const channels = contentAdmission.value?.channels
  return admissionChannelEntries(channels)
})

function imageUrl(info?: Record<string, unknown>) {
  return resolveImageUrl(info)
}

function typeLabel(type?: string) {
  if (type === 'image') return '图片'
  if (type === 'table') return '表格'
  if (type === 'parent_text') return '父文本'
  return '文本'
}

function jsonText(value?: Record<string, unknown>) {
  return JSON.stringify(value || {}, null, 2)
}

function tableHtml(info?: Record<string, unknown>) {
  const html = info?.table_html
  return typeof html === 'string' ? html : ''
}

onMounted(async () => {
  try {
    chunk.value = await getChunk(route.params.id as string)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page wide-page">
    <p v-if="error" class="text-error">{{ error }}</p>
    <div v-if="chunk" class="chunk-detail-layout">
      <div class="page-header hero-header">
        <div>
          <p class="eyebrow">Chunk Detail</p>
          <h1>切片 #{{ chunk.chunk_index }}</h1>
          <p class="text-muted">{{ typeLabel(chunk.chunk_type) }} · {{ statusText[chunk.status] || chunk.status }} · {{ chunk.id }}</p>
        </div>
        <div class="header-actions">
          <button v-if="chunk.document_id || chunk.knowledge_id" type="button" @click="router.push(`/documents/${(chunk.document_id || chunk.knowledge_id)}/content`)">查看文档</button>
          <button class="secondary" @click="router.back()">返回</button>
        </div>
      </div>

      <div class="detail-grid">
        <article class="card detail-card main-detail-card">
          <h2>切片内容</h2>
          <div v-if="imageUrl(chunk.image_info)" class="media-preview-block">
            <img :src="imageUrl(chunk.image_info)" class="detail-image clickable" @click="showPreview = true" />
          </div>
          <div v-if="tableHtml(chunk.image_info)" class="table-html-preview" v-html="tableHtml(chunk.image_info)"></div>
          <pre class="content-block readable-content">{{ chunk.content }}</pre>
        </article>

        <aside class="card detail-card side-detail-card">
          <h2>基础信息</h2>
          <dl class="details compact-details">
            <dt>类型</dt><dd><span class="tag">{{ typeLabel(chunk.chunk_type) }}</span></dd>
            <dt>状态</dt><dd>{{ statusText[chunk.status] || chunk.status }}</dd>
            <dt>启用</dt><dd>{{ chunk.is_enabled ? '是' : '否' }}</dd>
            <dt>长度</dt><dd>{{ chunk.token_count }}</dd>
            <dt>起始位置</dt><dd>{{ chunk.start_at ?? '-' }}</dd>
            <dt>结束位置</dt><dd>{{ chunk.end_at ?? '-' }}</dd>
            <dt>前置切片</dt><dd class="mono">{{ chunk.pre_chunk_id || '-' }}</dd>
            <dt>后置切片</dt><dd class="mono">{{ chunk.next_chunk_id || '-' }}</dd>
            <dt>父切片</dt><dd class="mono">{{ chunk.parent_chunk_id || '-' }}</dd>
          </dl>
        </aside>
      </div>

      <div class="detail-grid">
        <article class="card detail-card">
          <h2>向量索引</h2>
          <dl class="details compact-details">
            <dt>是否已索引</dt><dd><span :class="['tag', chunk.embedding_index_info?.is_indexed ? 'tag-ok' : 'tag-warn']">{{ chunk.embedding_index_info?.is_indexed ? '是' : '否' }}</span></dd>
            <dt>索引启用</dt><dd>{{ chunk.embedding_index_info?.is_enabled ? '是' : '否' }}</dd>
            <dt>来源类型</dt><dd>{{ chunk.embedding_index_info?.source_type || '-' }}</dd>
            <dt>索引模型</dt><dd>{{ chunk.embedding_index_info?.model_name || '-' }}</dd>
            <dt>维度</dt><dd>{{ chunk.embedding_index_info?.dimension ?? '-' }}</dd>
            <dt>向量长度</dt><dd>{{ chunk.embedding_index_info?.vector_size || 0 }}</dd>
            <dt>向量范数</dt><dd>{{ chunk.embedding_index_info?.vector_norm ? Number(chunk.embedding_index_info.vector_norm).toFixed(4) : '-' }}</dd>
          </dl>
          <h3>索引文本</h3>
          <pre class="content-block">{{ chunk.embedding_index_info?.indexed_content_preview || '-' }}</pre>
          <div v-if="contentAdmission" class="vector-panel">
            <h3>字段级索引准入</h3>
            <dl class="details compact-details">
              <dt>决策版本</dt><dd>{{ contentAdmission.version || '-' }}</dd>
              <dt>最终决策</dt><dd>{{ contentAdmission.decision === 'index' ? '参与索引' : '不参与索引' }}</dd>
              <dt>索引来源</dt><dd>{{ formatSourceList(contentAdmission.index_sources) }}</dd>
              <dt>辅助保留来源</dt><dd>{{ formatSourceList(contentAdmission.shadow_sources) }}</dd>
              <dt>恢复旧隔离标记</dt><dd>{{ contentAdmission.legacy_visual_ocr_skip_recovered ? '是' : '否' }}</dd>
            </dl>
            <div v-for="[source, detail] in admissionChannels" :key="source" class="card subtle-card">
              <strong>{{ source }} · {{ detail.status || 'unknown' }}</strong>
              <p class="text-muted">{{ detail.char_count || 0 }} 字符</p>
              <pre class="content-block">{{ detail.preview || '-' }}</pre>
            </div>
          </div>
          <div class="vector-panel">
            <div class="page-header small-header">
              <h3>向量预览</h3>
              <span class="text-muted">仅展示前 16 维，完整向量不通过 API 返回</span>
            </div>
            <pre class="content-block vector-block">{{ vectorText || '-' }}</pre>
          </div>
        </article>

        <article class="card detail-card">
          <h2>上下文与元数据</h2>
          <dl class="details compact-details">
            <dt>文档 ID</dt><dd class="mono">{{ chunk.document_id }}</dd>
            <dt>知识 ID</dt><dd class="mono">{{ chunk.knowledge_id || chunk.document_id }}</dd>
            <dt>知识库 ID</dt><dd class="mono">{{ chunk.knowledge_base_id }}</dd>
            <dt>内容 Hash</dt><dd class="mono">{{ chunk.content_hash || '-' }}</dd>
            <dt>上下文标题</dt><dd>{{ chunk.context_header || '-' }}</dd>
          </dl>
          <details open>
            <summary>image_info</summary>
            <pre>{{ jsonText(chunk.image_info) }}</pre>
          </details>
          <details>
            <summary>metadata_json</summary>
            <pre>{{ jsonText(chunk.metadata_json || chunk.metadata) }}</pre>
          </details>
        </article>
      </div>

      <div v-if="showPreview && imageUrl(chunk.image_info)" class="modal-backdrop" @click="showPreview = false">
        <div class="modal" @click.stop>
          <div class="page-header"><h2>图片预览</h2><button class="secondary" @click="showPreview = false">关闭</button></div>
          <img :src="imageUrl(chunk.image_info)" class="preview-image" />
        </div>
      </div>
    </div>
  </section>
</template>
