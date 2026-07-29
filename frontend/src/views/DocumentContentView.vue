<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getDocument,
  getDocumentVisualLayout,
  type DocumentItem,
  type DocumentVisualLayoutResponse,
  type VisualItemLayout
} from '../api/documents'
import { normalizeFileUrl } from '../utils/media'

const route = useRoute()
const router = useRouter()
const doc = ref<DocumentItem | null>(null)
const textContent = ref('')
const error = ref('')
const textError = ref('')
const layoutError = ref('')
const loading = ref(false)
const textLoading = ref(false)
const layoutLoading = ref(false)
const layoutData = ref<DocumentVisualLayoutResponse | null>(null)
const layoutMode = ref(false)
const selectedLayoutPage = ref(1)
const selectedVisualItemId = ref('')
const showVisualRegions = ref(true)
const showTextRegions = ref(true)

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
const layoutPages = computed(() => layoutData.value?.layout?.pages || [])
const layoutPageIndex = computed(() => layoutData.value?.available_pages || [])
const currentLayoutPage = computed(() =>
  layoutPages.value.find((page) => page.page_no === selectedLayoutPage.value)
    || layoutPages.value[0]
)
const selectedVisualItem = computed(() =>
  currentLayoutPage.value?.visual_items.find(
    (item) => item.visual_item_id === selectedVisualItemId.value
  )
)
function typeLabel() {
  if (!doc.value) return ''
  return fileType.value.toUpperCase()
}

function bboxStyle(bbox?: number[] | null) {
  if (!bbox || bbox.length < 4) return { display: 'none' }
  return {
    left: `${bbox[0] * 100}%`,
    top: `${bbox[1] * 100}%`,
    width: `${Math.max(0, bbox[2] - bbox[0]) * 100}%`,
    height: `${Math.max(0, bbox[3] - bbox[1]) * 100}%`
  }
}

function selectVisualItem(item: VisualItemLayout) {
  selectedVisualItemId.value = item.visual_item_id
}

async function loadLayoutPage(pageNo: number) {
  if (!doc.value) return
  layoutLoading.value = true
  layoutError.value = ''
  try {
    layoutData.value = await getDocumentVisualLayout(doc.value.id, pageNo)
    selectedLayoutPage.value = pageNo
    selectedVisualItemId.value = ''
  } catch (layoutRequestError) {
    layoutError.value = layoutRequestError instanceof Error
      ? layoutRequestError.message
      : '布局验收数据加载失败'
  } finally {
    layoutLoading.value = false
  }
}

async function toggleLayoutMode() {
  if (layoutMode.value) {
    layoutMode.value = false
    return
  }
  if (!doc.value) return
  if (layoutData.value?.layout?.pages.length) {
    layoutMode.value = true
    return
  }
  layoutLoading.value = true
  layoutError.value = ''
  try {
    const index = await getDocumentVisualLayout(doc.value.id)
    if (!index.available) {
      layoutError.value = index.reason || '布局验收数据尚未生成'
      return
    }
    const firstPage = index.available_pages?.[0]
    if (!firstPage) {
      layoutError.value = '布局验收数据没有可用页面'
      return
    }
    layoutData.value = await getDocumentVisualLayout(
      doc.value.id,
      firstPage.page_no
    )
    selectedLayoutPage.value = firstPage.page_no
    layoutMode.value = true
  } catch (layoutRequestError) {
    layoutError.value = layoutRequestError instanceof Error
      ? layoutRequestError.message
      : '布局验收数据加载失败'
  } finally {
    layoutLoading.value = false
  }
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
          <p v-if="layoutError" class="text-muted">布局验收暂不可用：{{ layoutError }}</p>
        </div>
        <div class="header-actions">
          <button
            v-if="isPdf"
            type="button"
            :disabled="layoutLoading"
            :class="{ secondary: !layoutMode }"
            @click="toggleLayoutMode"
          >
            {{ layoutLoading ? '加载布局…' : layoutMode ? '原文预览' : '布局验收' }}
          </button>
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
      <div v-else-if="isPdf && !layoutMode" class="card pdf-viewer-card">
        <iframe :src="fileUrl" class="doc-iframe" title="PDF 查看器" />
      </div>

      <div v-else-if="isPdf && layoutMode" class="layout-acceptance">
        <div class="card layout-toolbar">
          <div>
            <strong>DCP-1.1 布局验收</strong>
            <span class="text-muted">
              · {{ layoutData?.algorithm_version }} ·
              {{ layoutData?.shadow_only ? 'Shadow' : 'Active' }}
            </span>
          </div>
          <div class="layout-page-buttons">
            <button
              v-for="page in layoutPageIndex"
              :key="page.page_no"
              class="secondary"
              :class="{ active: page.page_no === currentLayoutPage?.page_no }"
              @click="loadLayoutPage(page.page_no)"
            >
              P{{ page.page_no }}
            </button>
          </div>
          <label><input v-model="showVisualRegions" type="checkbox">视觉区域</label>
          <label><input v-model="showTextRegions" type="checkbox">文本区域</label>
        </div>

        <div class="layout-grid">
          <div class="card layout-canvas-card">
            <div class="layout-canvas">
              <div
                v-for="region in currentLayoutPage?.regions || []"
                v-show="region.region_kind === 'text' ? showTextRegions : showVisualRegions"
                :key="region.region_id"
                class="layout-region"
                :class="[`kind-${region.region_kind}`, `status-${region.status}`]"
                :style="bboxStyle(region.normalized_bbox)"
                :title="`${region.region_kind} · ${region.status} · ${region.evidence.join(', ')}`"
              />
              <button
                v-for="(item, index) in currentLayoutPage?.visual_items || []"
                :key="item.visual_item_id"
                class="visual-item-box"
                :class="{ selected: item.visual_item_id === selectedVisualItemId }"
                :style="bboxStyle(item.normalized_bbox)"
                @click="selectVisualItem(item)"
              >
                VI-{{ index + 1 }}
              </button>
            </div>
          </div>

          <aside class="card layout-sidebar">
            <h3>第 {{ currentLayoutPage?.page_no }} 页</h3>
            <dl class="details">
              <dt>区域数</dt><dd>{{ currentLayoutPage?.summary.region_count || 0 }}</dd>
              <dt>作品单元</dt><dd>{{ currentLayoutPage?.summary.visual_item_count || 0 }}</dd>
              <dt>歧义文本</dt><dd>{{ currentLayoutPage?.summary.ambiguous_text_count || 0 }}</dd>
              <dt>未绑定文本</dt><dd>{{ currentLayoutPage?.summary.unbound_text_count || 0 }}</dd>
              <dt>重复 fallback</dt><dd>{{ currentLayoutPage?.summary.duplicate_fallback_count || 0 }}</dd>
            </dl>
            <p v-if="currentLayoutPage?.fallback_reason" class="tag tag-warn">
              {{ currentLayoutPage.fallback_reason }}
            </p>
            <template v-if="selectedVisualItem">
              <h3>选中作品</h3>
              <pre class="content-block">{{ JSON.stringify(selectedVisualItem, null, 2) }}</pre>
            </template>
            <h3>文本关系</h3>
            <div
              v-for="relation in currentLayoutPage?.text_relations || []"
              :key="relation.relation_id"
              class="relation-row"
              :class="`relation-${relation.status}`"
            >
              <strong>{{ relation.relation_type }}</strong>
              <span>{{ relation.status }} · {{ relation.score.toFixed(2) }}</span>
              <small>{{ relation.visual_item_ids.join(', ') || '未绑定' }}</small>
            </div>
          </aside>
        </div>
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

.layout-acceptance {
  display: grid;
  gap: 16px;
}

.layout-toolbar,
.layout-page-buttons {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.layout-page-buttons {
  flex: 1;
}

.layout-page-buttons button.active {
  outline: 2px solid #2563eb;
}

.layout-grid {
  display: grid;
  grid-template-columns: minmax(520px, 1fr) minmax(300px, 380px);
  gap: 16px;
}

.layout-canvas-card {
  display: flex;
  justify-content: center;
  background: #eef2f7;
}

.layout-canvas {
  position: relative;
  width: min(100%, 680px);
  aspect-ratio: 612 / 792;
  overflow: hidden;
  background:
    linear-gradient(#fff, #fbfbfc),
    repeating-linear-gradient(0deg, transparent, transparent 31px, #f1f5f9 32px);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
}

.layout-region,
.visual-item-box {
  position: absolute;
  box-sizing: border-box;
}

.layout-region {
  pointer-events: none;
  border: 1px dashed #64748b;
  background: rgba(100, 116, 139, 0.08);
}

.layout-region.kind-visual,
.layout-region.kind-visual_candidate {
  border-color: #2563eb;
  background: rgba(37, 99, 235, 0.1);
}

.layout-region.kind-text {
  border-color: #16a34a;
  background: rgba(22, 163, 74, 0.08);
}

.layout-region.status-duplicate_fallback {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.08);
}

.visual-item-box {
  z-index: 2;
  padding: 4px;
  border: 3px solid #7c3aed;
  background: transparent;
  color: #6d28d9;
  text-align: left;
  font-weight: 800;
}

.visual-item-box.selected {
  border-color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
}

.layout-sidebar {
  max-height: calc(100vh - 220px);
  overflow: auto;
}

.relation-row {
  display: grid;
  gap: 3px;
  margin-bottom: 8px;
  padding: 9px;
  border-left: 4px solid #16a34a;
  background: #f8fafc;
}

.relation-row.relation-ambiguous {
  border-color: #f59e0b;
}

.relation-row.relation-unbound {
  border-color: #94a3b8;
}

@media (max-width: 980px) {
  .layout-grid {
    grid-template-columns: 1fr;
  }
}
</style>
