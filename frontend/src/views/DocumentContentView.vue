<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getDocument, type DocumentItem } from '../api/documents'
import { normalizeFileUrl } from '../utils/media'

const route = useRoute()
const router = useRouter()
const doc = ref<DocumentItem | null>(null)
const textContent = ref('')
const error = ref('')
const textError = ref('')
const loading = ref(false)
const textLoading = ref(false)

const INLINE_IMAGE_TYPES = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'])
const INLINE_TEXT_TYPES = new Set(['txt', 'md', 'markdown', 'csv', 'json', 'xml', 'yaml', 'yml', 'log', 'py', 'js', 'ts', 'html', 'css', 'sh', 'bat', 'ini', 'cfg', 'conf', 'toml'])

const fileUrl = computed(() => {
  if (!doc.value?.storage_path) return ''
  return normalizeFileUrl(doc.value.storage_path)
})

const fileType = computed(() => (doc.value?.file_type || '').toLowerCase().replace(/^\./, ''))

const isPdf = computed(() => fileType.value === 'pdf')
const isImage = computed(() => INLINE_IMAGE_TYPES.has(fileType.value))
const isText = computed(() => INLINE_TEXT_TYPES.has(fileType.value))
function typeLabel() {
  if (!doc.value) return ''
  return fileType.value.toUpperCase()
}

async function loadTextContent() {
  if (!fileUrl.value || !isText.value) return
  textLoading.value = true
  try {
    const res = await fetch(fileUrl.value)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    textContent.value = await res.text()
  } catch (err) {
    textError.value = err instanceof Error ? err.message : '加载文件内容失败'
  } finally {
    textLoading.value = false
  }
}

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    doc.value = await getDocument(route.params.id as string)
    if (isText.value) {
      await loadTextContent()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载文档失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="page wide-page">
    <p v-if="error" class="text-error">{{ error }}</p>

    <div v-if="loading" class="card">
      <p class="text-muted">加载中...</p>
    </div>

    <template v-else-if="doc">
      <div class="page-header hero-header">
        <div>
          <p class="eyebrow">文档查看</p>
          <h1>{{ doc.title }}</h1>
          <p class="text-muted">{{ doc.file_name }} · {{ typeLabel() }}</p>
        </div>
        <div class="header-actions">
          <button type="button" @click="router.push(`/documents/${doc.id}`)">元数据详情</button>
          <a v-if="fileUrl" class="btn secondary" :href="fileUrl" :download="doc.file_name">下载文件</a>
          <button class="secondary" @click="router.back()">返回</button>
        </div>
      </div>

      <!-- 无文件 -->
      <div v-if="!doc.storage_path" class="card">
        <div class="empty-state">
          <div class="empty-icon">📄</div>
          <div>未找到原始文件</div>
          <small class="text-muted">该文档可能尚未上传文件或存储路径缺失。</small>
        </div>
      </div>

      <!-- PDF -->
      <div v-else-if="isPdf" class="card pdf-viewer-card">
        <iframe :src="fileUrl" class="doc-iframe" title="PDF 查看器" />
      </div>

      <!-- 图片 -->
      <div v-else-if="isImage" class="card image-viewer-card">
        <img :src="fileUrl" :alt="doc.file_name" class="doc-image" />
      </div>

      <!-- 纯文本 -->
      <div v-else-if="isText" class="card text-viewer-card">
        <p v-if="textLoading" class="text-muted">加载文本内容...</p>
        <p v-else-if="textError" class="text-error">{{ textError }}</p>
        <pre v-else class="doc-text">{{ textContent }}</pre>
      </div>

      <!-- 不支持内联渲染 -->
      <div v-else class="card">
        <div class="empty-state">
          <div class="empty-icon">📁</div>
          <div>不支持在线预览</div>
          <small class="text-muted">{{ typeLabel() }} 文件无法在浏览器中直接查看，请下载后使用本地应用打开。</small>
          <a v-if="fileUrl" class="btn secondary" :href="fileUrl" :download="doc.file_name" style="margin-top: 16px">下载文件</a>
        </div>
      </div>
    </template>

    <div v-else class="card">
      <p class="text-muted">文档不存在或已删除。</p>
    </div>
  </section>
</template>

<style scoped>
.pdf-viewer-card {
  padding: 0;
  overflow: hidden;
  height: calc(100vh - 200px);
  min-height: 600px;
}

.doc-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

.image-viewer-card {
  display: flex;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.03);
}

.doc-image {
  max-width: 100%;
  max-height: calc(100vh - 240px);
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
}

.text-viewer-card {
  padding: 24px;
}

.doc-text {
  max-height: calc(100vh - 240px);
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 14px;
  line-height: 1.6;
  background: rgba(148, 163, 184, 0.06);
  padding: 20px;
  border-radius: 8px;
  margin: 0;
}
</style>
