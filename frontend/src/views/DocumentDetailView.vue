<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { normalizeFileUrl } from '../utils/media'
import { formatCountMap } from '../utils/contentAdmission'
import {
  embedDocument,
  getDocument,
  getDocumentEmbeddingStatus,
  getDocumentProcessingProfile,
  parseDocument,
  reparseDocument,
  type DocumentPageProfileV2,
  type DocumentProcessingProfileResponse,
  type DocumentItem,
  type EmbeddingStatus
} from '../api/documents'
import { getLatestParseTask, type ParseTask } from '../api/parseTasks'

const route = useRoute()
const router = useRouter()
const doc = ref<DocumentItem | null>(null)
const embeddingStatus = ref<EmbeddingStatus | null>(null)
const error = ref('')
const message = ref('')
const loading = ref(false)
const embeddingLoading = ref(false)
const statusLoading = ref(false)
const latestTask = ref<ParseTask | null>(null)
const taskLoading = ref(false)
const processingProfile = ref<DocumentProcessingProfileResponse | null>(null)
const profileError = ref('')
const selectedProfilePageNo = ref(0)
const profilePageLoading = ref(false)

const statusText: Record<string, string> = {
  pending: '待解析',
  parsing: '解析中',
  parsed: '已解析',
  failed: '解析失败'
}

statusText.parsed_with_fallback = '已保留为附件（无可检索正文）'
statusText.chunk_failed = '解析完成，切片失败'

const stats = computed(() => doc.value?.parse_statistics || {})
const chunkTypeCounts = computed(() => stats.value.chunk_type_counts || {})
const admissionSummary = computed(() => embeddingStatus.value?.content_admission_summary)
const actualParser = computed(() => stats.value.parser_actual || stats.value.parser || '-')
const parserNotice = computed(() => {
  if (stats.value.fallback_used) {
    const reason = stats.value.fallback_reason || stats.value.parser_fallback_reason || '未知原因'
    return `⚠ 解析器回退：请求 ${stats.value.parser_requested || stats.value.parser_attempted || '-'}，实际使用 ${actualParser.value}（原因：${reason}）`
  }
  if (stats.value.parser_attempted && stats.value.parser_fallback_reason) {
    return `已尝试 ${stats.value.parser_attempted}，实际回退到 ${actualParser.value}`
  }
  if (stats.value.mineru_pdf_enabled) return '当前主解析器：MinerU'
  if (stats.value.document_parser_engine === 'mineru') return '当前文档解析引擎：MinerU'
  if (stats.value.pdf_engine) return `当前 PDF 主解析器：${stats.value.pdf_engine}`
  return '当前文档解析器以详情区显示为准'
})
const selectedProfilePage = computed<DocumentPageProfileV2 | undefined>(() =>
  processingProfile.value?.pages?.find(
    (page) => page.page_no === selectedProfilePageNo.value
  ) || processingProfile.value?.pages?.[0]
)
const profilePageIndex = computed(() => processingProfile.value?.available_pages || [])
const selectedProfileIndex = computed(() =>
  profilePageIndex.value.findIndex(
    (page) => page.page_no === selectedProfilePageNo.value
  )
)

function numberValue(value: unknown) {
  return typeof value === 'number' ? value : 0
}

function formatStringMap(value?: Record<string, string>) {
  if (!value) return '-'
  const entries = Object.entries(value).sort(([left], [right]) =>
    left.localeCompare(right)
  )
  return entries.length
    ? entries.map(([key, status]) => `${key}: ${status}`).join('，')
    : '-'
}

async function load() {
  doc.value = await getDocument(route.params.id as string)
}

async function loadLatestTask() {
  if (!doc.value) return
  try {
    latestTask.value = await getLatestParseTask(doc.value.id)
  } catch {
    latestTask.value = null
  }
}

function openWorkbench() {
  if (latestTask.value?.id) router.push(`/parse-tasks/${latestTask.value.id}`)
}

async function loadEmbeddingStatus() {
  if (!doc.value) return
  statusLoading.value = true
  error.value = ''
  message.value = ''
  try {
    embeddingStatus.value = await getDocumentEmbeddingStatus(doc.value.id)
    message.value = '向量化状态已刷新'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取向量化状态失败'
  } finally {
    statusLoading.value = false
  }
}

async function loadProcessingProfile() {
  if (!doc.value) return
  profileError.value = ''
  try {
    processingProfile.value = await getDocumentProcessingProfile(doc.value.id)
    const firstPage = processingProfile.value.available_pages?.[0]
    if (firstPage) await selectProcessingProfilePage(firstPage.page_no)
  } catch (err) {
    profileError.value = err instanceof Error ? err.message : '获取页面画像失败'
  }
}

async function selectProcessingProfilePage(pageNo: number) {
  if (!doc.value || profilePageLoading.value) return
  profilePageLoading.value = true
  profileError.value = ''
  try {
    const detail = await getDocumentProcessingProfile(doc.value.id, pageNo)
    if (processingProfile.value) {
      processingProfile.value = {
        ...processingProfile.value,
        pages: detail.pages,
        legacy_pages: detail.legacy_pages,
        ocr_admission: detail.ocr_admission,
        block_origin: detail.block_origin,
        policy_v2_enabled: detail.policy_v2_enabled,
        parent_policy: detail.parent_policy,
        resolved_policies: detail.resolved_policies,
        requested_page_no: pageNo
      }
    } else {
      processingProfile.value = detail
    }
    selectedProfilePageNo.value = pageNo
  } catch (err) {
    profileError.value = err instanceof Error ? err.message : '获取页面画像失败'
  } finally {
    profilePageLoading.value = false
  }
}

async function moveProcessingProfilePage(offset: number) {
  const targetIndex = selectedProfileIndex.value + offset
  const target = profilePageIndex.value[targetIndex]
  if (target) await selectProcessingProfilePage(target.page_no)
}

async function runParse(reparse = false) {
  if (!doc.value) return
  loading.value = true
  taskLoading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = reparse ? await reparseDocument(doc.value.id) : await parseDocument(doc.value.id)
    doc.value = result
    const taskId = result.parse_task?.id || result.parse_task?.task_id
    if (taskId) {
      message.value = reparse ? '重新解析任务已启动' : '解析任务已启动'
      router.push(`/parse-tasks/${taskId}`)
      return
    }
    message.value = '解析完成'
    await loadLatestTask()
    await loadEmbeddingStatus()
    await loadProcessingProfile()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '解析失败'
    await load()
  } finally {
    taskLoading.value = false
    loading.value = false
  }
}

async function runEmbedding() {
  if (!doc.value) return
  embeddingLoading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await embedDocument(doc.value.id)
    embeddingStatus.value = result
    message.value = '文档向量生成完成'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '向量生成失败'
  } finally {
    embeddingLoading.value = false
  }
}

onMounted(async () => {
  try {
    await load()
    await loadLatestTask()
    await loadEmbeddingStatus()
    await loadProcessingProfile()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  }
})
</script>

<template>
  <section class="page">
    <p v-if="error" class="text-error">{{ error }}</p>
    <p v-if="message" class="text-success">{{ message }}</p>
    <div v-if="doc" class="card">
      <div class="page-header">
        <div>
          <h1>{{ doc.title }}</h1>
          <p class="text-muted">{{ doc.file_name }}</p>
        </div>
        <div class="actions">
          <button :disabled="loading" @click="runParse(false)">解析</button>
          <button class="secondary" :disabled="loading" @click="runParse(true)">重新解析</button>
          <button type="button" :disabled="embeddingLoading" @click="runEmbedding">
            {{ embeddingLoading ? '正在生成向量...' : (embeddingStatus?.embedded_chunks ? '重新生成向量' : '生成向量') }}
          </button>
          <button type="button" class="secondary" :disabled="statusLoading" @click="loadEmbeddingStatus">
            {{ statusLoading ? '正在获取状态...' : '查看向量化状态' }}
          </button>
          <button type="button" @click="router.push(`/documents/${doc.id}/content`)">查看原文档</button>
          <a v-if="doc.storage_path" class="secondary" :href="normalizeFileUrl(doc.storage_path)" :download="doc.file_name">下载文件</a>
          <button type="button" class="secondary" @click="router.push(`/documents/${doc.id}/chunks`)">查看切片</button>
          <button v-if="latestTask" type="button" class="secondary" :disabled="taskLoading" @click="openWorkbench">解析工作台</button>
          <button type="button" class="secondary" @click="router.push(`/parse-eval/documents/${doc.id}`)">解析评测</button>
        </div>
      </div>

      <h2>解析统计</h2>
      <div class="card subtle-card">
        <div class="parser-line">
          <strong>实际解析器：{{ actualParser }}</strong>
          <span>{{ parserNotice }}</span>
        </div>
        <p v-if="stats.parser_fallback_reason" class="text-muted">回退原因：{{ stats.parser_fallback_reason }}</p>
      </div>
      <div class="grid">
        <div class="stat-card"><span class="stat-label">文本块</span><span class="stat-value">{{ numberValue(stats.text_block_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">表格块</span><span class="stat-value">{{ numberValue(stats.table_block_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">图片块</span><span class="stat-value">{{ numberValue(stats.image_block_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">OCR 增强</span><span class="stat-value">{{ numberValue(stats.ocr_enhanced_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">图片描述增强</span><span class="stat-value">{{ numberValue(stats.enhanced_image_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">PDF 图/表裁剪</span><span class="stat-value">图 {{ numberValue(stats.figure_crop_count) }} / 表 {{ numberValue(stats.table_crop_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">Caption 绑定</span><span class="stat-value">{{ numberValue(stats.caption_bound_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">OCR/VLM 失败</span><span class="stat-value">OCR {{ numberValue(stats.ocr_failed_count) }} / VLM {{ numberValue(stats.vlm_failed_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">MinerU content_list</span><span class="stat-value">{{ numberValue(stats.mineru_content_list_count) }}</span></div>
        <div class="stat-card"><span class="stat-label">后处理合并</span><span class="stat-value">{{ numberValue(stats.reflow_merged_blocks) }}</span></div>
      </div>

      <div v-if="processingProfile?.available" class="card subtle-card">
        <div class="page-header">
          <div>
            <h2>多轴页面画像（DCP-2.1）</h2>
            <p class="text-muted">
              {{ processingProfile.profile_version }} ·
              {{ processingProfile.shadow_only ? 'Shadow，只读建议' : 'Active' }} ·
              revision {{ processingProfile.parse_revision }}
            </p>
          </div>
          <span :class="['tag', processingProfile.summary?.uncertain_page_count ? 'tag-warn' : 'tag-ok']">
            不确定页 {{ processingProfile.summary?.uncertain_page_count || 0 }}
          </span>
        </div>
        <p v-if="profileError" class="text-error">{{ profileError }}</p>
        <div class="grid">
          <div class="stat-card">
            <span class="stat-label">布局分布</span>
            <span class="stat-value">{{ formatCountMap(processingProfile.summary?.layout_distribution) }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">语义提示</span>
            <span class="stat-value">{{ processingProfile.summary?.semantic_hints?.join('、') || '无' }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">文本来源</span>
            <span class="stat-value">{{ formatCountMap(processingProfile.summary?.text_origin_distribution) }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">OCR 可信度</span>
            <span class="stat-value">{{ formatCountMap(processingProfile.summary?.ocr_trust_distribution) }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">DCP-3.1 生产投影</span>
            <span class="stat-value">
              {{ processingProfile.ocr_admission?.production_projection_applied ? '已执行' : '未执行' }}
              · 隔离 {{ processingProfile.ocr_admission?.excluded_block_count || 0 }} block
            </span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Block 准入分布</span>
            <span class="stat-value">{{ formatCountMap(processingProfile.ocr_admission?.admission_counts) }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">ParentPolicy</span>
            <span class="stat-value">
              {{ processingProfile.parent_policy?.applied ? 'VisualItem 已应用' : '兼容模式' }}
              · 作品 parent {{ processingProfile.parent_policy?.visual_item_parent_count || 0 }}
              · 页面概览 parent {{ processingProfile.parent_policy?.page_overview_parent_count || 0 }}
            </span>
          </div>
        </div>
        <div class="actions">
          <button
            type="button"
            class="secondary"
            :disabled="profilePageLoading || selectedProfileIndex <= 0"
            @click="moveProcessingProfilePage(-1)"
          >
            上一页
          </button>
          <span v-if="selectedProfilePage" class="text-muted">
            第 {{ selectedProfileIndex + 1 }} / {{ profilePageIndex.length }} 页 ·
            P{{ selectedProfilePage.page_no }} · {{ selectedProfilePage.layout_kind }} ·
            {{ selectedProfilePage.ocr_trust_band }}
          </span>
          <button
            type="button"
            class="secondary"
            :disabled="
              profilePageLoading
              || selectedProfileIndex < 0
              || selectedProfileIndex >= profilePageIndex.length - 1
            "
            @click="moveProcessingProfilePage(1)"
          >
            下一页
          </button>
        </div>
        <div v-if="selectedProfilePage" class="card">
          <h3>第 {{ selectedProfilePage.page_no }} 页画像</h3>
          <dl class="details">
            <dt>布局</dt><dd>{{ selectedProfilePage.layout_kind }}</dd>
            <dt>语义提示</dt><dd>{{ selectedProfilePage.semantic_hints.join('、') || '无' }}</dd>
            <dt>文本来源</dt><dd>{{ selectedProfilePage.text_origins.join('、') || '无' }}</dd>
            <dt>Shadow 准入建议</dt><dd>{{ formatStringMap(selectedProfilePage.origin_admission) }}</dd>
            <dt>OCR 评分</dt>
            <dd>{{ selectedProfilePage.ocr_trust_score ?? '不适用' }} · {{ selectedProfilePage.ocr_trust_band }}</dd>
            <dt>视觉作品</dt>
            <dd>{{ selectedProfilePage.visual_item_count }}，歧义文本 {{ selectedProfilePage.ambiguous_text_count }}</dd>
            <dt>原生/Canonical 字符</dt>
            <dd>{{ selectedProfilePage.native_text_chars }} / {{ selectedProfilePage.canonical_text_chars }}</dd>
            <dt>不确定原因</dt>
            <dd>{{ selectedProfilePage.uncertainty_reasons.join('、') || '无' }}</dd>
          </dl>
          <details>
            <summary>评分证据与贡献</summary>
            <pre class="content-block">{{ JSON.stringify(selectedProfilePage.evidence, null, 2) }}</pre>
          </details>
          <details open>
            <summary>
              DCP-3.1 Block 准入（本页
              {{ processingProfile.ocr_admission?.blocks?.length || 0 }} 个候选）
            </summary>
            <p
              v-if="!processingProfile.ocr_admission?.production_projection_applied"
              class="text-muted"
            >
              当前 revision 未启用 reflow 前生产投影；以下无 active 判定。
            </p>
            <div
              v-for="block in processingProfile.ocr_admission?.blocks || []"
              :key="`${block.page_no}-${block.order_index}`"
              class="admission-block"
            >
              <div class="actions">
                <span
                  :class="[
                    'tag',
                    block.ocr_admission === 'normal'
                      ? 'tag-ok'
                      : block.ocr_admission === 'auxiliary'
                        ? 'tag-warn'
                        : ''
                  ]"
                >
                  {{ block.ocr_admission || 'unknown' }}
                </span>
                <span class="text-muted">
                  来源 {{ block.text_origin || '-' }} · 评分
                  {{ block.ocr_quality_score ?? '-' }} · bbox
                  {{ block.bbox?.join(', ') || '缺失' }}
                </span>
              </div>
              <p class="text-muted">
                {{ block.ocr_admission_reasons?.join('、') || '无判定原因' }}
              </p>
              <pre class="content-block">{{ block.text || '' }}</pre>
            </div>
            <p
              v-if="!(processingProfile.ocr_admission?.blocks?.length)"
              class="text-muted"
            >
              本页没有 MinerU OCR 准入候选，或该 revision 尚未重新解析。
            </p>
          </details>
        </div>
      </div>
      <p v-else-if="profileError" class="text-muted">页面画像暂不可用：{{ profileError }}</p>
      <p v-else-if="processingProfile && !processingProfile.available" class="text-muted">
        页面画像暂不可用：{{ processingProfile.reason || '尚未生成 DCP-2.1 画像，请重新解析文档' }}
      </p>

      <dl class="details">
        <dt>ID</dt><dd>{{ doc.id }}</dd>
        <dt>知识 ID</dt><dd>{{ doc.knowledge_id || doc.id }}</dd>
        <dt>知识库 ID</dt><dd>{{ doc.knowledge_base_id }}</dd>
        <dt>来源</dt><dd>{{ doc.source || 'file' }}</dd>
        <dt>渠道</dt><dd>{{ doc.channel || 'local' }}</dd>
        <dt>启用状态</dt><dd>{{ doc.enable_status || 'enabled' }}</dd>
        <dt>处理时间</dt><dd>{{ doc.processed_at || '-' }}</dd>
        <dt>文件类型</dt><dd>{{ doc.file_type }}</dd>
        <dt>文件大小</dt><dd>{{ doc.file_size }}</dd>
        <dt>文件 Hash</dt><dd>{{ doc.file_hash }}</dd>
        <dt>本地路径</dt><dd>{{ doc.storage_path }}</dd>
        <dt>解析状态</dt><dd>{{ statusText[doc.parse_status] || doc.parse_status }}</dd>
        <dt>最新解析任务</dt><dd>{{ latestTask ? `${latestTask.current_stage_label || latestTask.current_stage} · ${Math.round(latestTask.progress || 0)}%` : '-' }}</dd>
        <dt>提示信息</dt><dd>{{ doc.error_message || '-' }}</dd>
        <dt>切片总数</dt><dd>{{ doc.chunk_count }}</dd>
        <dt>切片类型统计</dt>
        <dd>text: {{ chunkTypeCounts.text || 0 }}，table: {{ chunkTypeCounts.table || 0 }}，image: {{ chunkTypeCounts.image || 0 }}</dd>
        <dt>实际解析器</dt><dd>{{ actualParser }}</dd>
        <dt>解析器原始字段</dt><dd>{{ stats.parser || '-' }}</dd>
        <dt>尝试解析器</dt><dd>{{ stats.parser_attempted || '-' }}</dd>
        <dt>解析偏好</dt><dd>{{ stats.parser_preference || '-' }}</dd>
        <dt>文档解析引擎</dt><dd>{{ stats.document_parser_engine || '-' }}</dd>
        <dt>请求解析器</dt><dd>{{ stats.parser_requested || '-' }}</dd>
        <dt>是否回退</dt><dd>{{ stats.fallback_used ? '是' : '否' }}</dd>
        <dt>PDF 引擎</dt><dd>{{ stats.pdf_engine || '-' }}</dd>
        <dt>MinerU 主解析</dt><dd>{{ stats.mineru_pdf_enabled ? '已启用' : '未启用 / 已回退' }}</dd>
        <dt>MinerU 配置来源</dt><dd>{{ stats.mineru_config_source || '-' }}</dd>
        <dt>MinerU content_list 数</dt><dd>{{ stats.mineru_content_list_count ?? '-' }}</dd>
        <dt>MinerU Endpoint</dt><dd>{{ stats.mineru_endpoint || '-' }}</dd>
        <dt>回退原因</dt><dd>{{ stats.parser_fallback_reason || '-' }}</dd>
        <dt>LayoutRecognizer</dt><dd>{{ stats.layout_recognizer || '-' }}</dd>
        <dt>TableStructureRecognizer</dt><dd>{{ stats.table_structure_recognizer || '-' }}</dd>
        <dt>多栏阅读顺序</dt><dd>{{ stats.multi_column_ordering || '-' }}</dd>
        <dt>纵向合并引擎</dt><dd>{{ stats.vertical_concat_engine || '-' }}</dd>
        <dt>后处理引擎</dt><dd>{{ stats.block_reflow_engine || '-' }}</dd>
        <dt>后处理输入/输出</dt><dd>{{ stats.reflow_input_blocks ?? '-' }} / {{ stats.reflow_output_blocks ?? '-' }}</dd>
        <dt>合并 block 数</dt><dd>{{ stats.reflow_merged_blocks ?? '-' }}</dd>
        <dt>OCR 修复</dt><dd>{{ stats.ocr_repair_enabled ? `已启用，修复 ${stats.ocr_repaired_blocks || 0} 个 block` : '-' }}</dd>
        <dt>图文绑定</dt><dd>{{ stats.figure_caption_binding || '-' }}</dd>
        <dt>扫描页图片数</dt><dd>{{ stats.scanned_page_image_count ?? '-' }}</dd>
        <dt>已向量化</dt>
        <dd>{{ embeddingStatus ? `${embeddingStatus.embedded_chunks}/${embeddingStatus.total_chunks}，待处理 ${embeddingStatus.pending_chunks ?? embeddingStatus.real_missing_chunks ?? 0}，规则跳过 ${embeddingStatus.skipped_chunks ?? 0}` : '-' }}</dd>
        <dt>索引文本数量</dt><dd>{{ embeddingStatus?.indexed_content_count ?? '-' }}</dd>
        <dt>带上下文标题切片数</dt><dd>{{ embeddingStatus?.has_context_header_count ?? '-' }}</dd>
        <dt>规则跳过</dt><dd>{{ embeddingStatus?.skipped_chunks ?? '-' }}</dd>
        <dt>异常未向量化</dt><dd>{{ embeddingStatus?.pending_chunks ?? embeddingStatus?.real_missing_chunks ?? '-' }}</dd>
        <dt>向量供应商</dt><dd>{{ embeddingStatus?.provider || '-' }}</dd>
        <dt>向量模型</dt><dd>{{ embeddingStatus?.model || '-' }}</dd>
        <dt>向量维度</dt><dd>{{ embeddingStatus?.dimension || '-' }}</dd>
        <dt>创建时间</dt><dd>{{ doc.created_at }}</dd>
        <dt>更新时间</dt><dd>{{ doc.updated_at }}</dd>
      </dl>
      <div v-if="admissionSummary" class="card subtle-card">
        <h3>索引准入（DCP-4R）</h3>
        <dl class="details">
          <dt>已有准入决策</dt>
          <dd>{{ admissionSummary.chunks_with_decision }}/{{ embeddingStatus?.total_chunks || 0 }}</dd>
          <dt>内容来源</dt>
          <dd>{{ formatCountMap(admissionSummary.source_counts) }}</dd>
          <dt>通道状态</dt>
          <dd>{{ formatCountMap(admissionSummary.channel_status_counts) }}</dd>
          <dt>已恢复旧隔离标记</dt>
          <dd>{{ admissionSummary.recovered_legacy_skip_chunks || 0 }}</dd>
          <dt>仍残留旧隔离标记</dt>
          <dd>
            <span :class="['tag', admissionSummary.stale_legacy_skip_chunks ? 'tag-warn' : 'tag-ok']">
              {{ admissionSummary.stale_legacy_skip_chunks || 0 }}
            </span>
          </dd>
          <dt>当前跳过原因</dt>
          <dd>{{ formatCountMap(admissionSummary.skip_reason_counts) }}</dd>
        </dl>
      </div>
      <p class="text-muted">重新解析后将使用新的文档处理链路；解析工作台默认不自动向量化，确认切片无误后可手动点击"生成向量"。如果切片入库失败，也可以进入解析工作台查看已识别的解析块。</p>
    </div>
  </section>
</template>
