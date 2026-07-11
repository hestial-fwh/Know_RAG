<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  getDeepDocStatus,
  getModelConfig,
  getProviderModels,
  getProviderPresets,
  testAllModelApis,
  testEmbedding,
  testModelApi,
  updateModelConfig,
  type ApiTestResult,
  type DeepDocStatus,
  type ModelConfig,
  type ProviderModelOption,
  type ProviderPreset
} from '../api/config'

type ModelField = 'llm' | 'embedding' | 'image2text' | 'ocr'

const LOCAL_PROVIDER_PRESETS: ProviderPreset[] = [
  { value: 'deepdoc-builtin', label: 'DeepDoc 内置 OCR（默认，无需 API）', default_base_url: '', api_style: 'builtin_ocr', model_examples: { ocr: 'deepdoc-local-ocr' } },
  { value: 'ollama', label: 'Ollama / 本地模型', default_base_url: 'http://host.docker.internal:11434', api_style: 'ollama', model_examples: { llm: 'qwen2.5:3b', embedding: 'bge-m3', image2text: 'qwen2.5vl:7b', ocr: 'qwen2.5vl:7b' } },
  { value: 'openai-compatible', label: 'OpenAI-Compatible', default_base_url: 'https://api.example.com/v1', api_style: 'openai', model_examples: { llm: 'gpt-4o-mini', embedding: 'text-embedding-3-small', image2text: 'gpt-4o-mini', ocr: 'gpt-4o-mini' } },
  { value: 'openai', label: 'OpenAI', default_base_url: 'https://api.openai.com/v1', api_style: 'openai', model_examples: { llm: 'gpt-4o-mini', embedding: 'text-embedding-3-small', image2text: 'gpt-4o-mini', ocr: 'gpt-4o-mini' } },
  { value: 'deepseek', label: 'DeepSeek', default_base_url: 'https://api.deepseek.com/v1', api_style: 'openai', model_examples: { llm: 'deepseek-chat' } },
  { value: 'dashscope', label: '阿里云百炼 / DashScope', default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', api_style: 'openai', model_examples: { llm: 'qwen-plus', embedding: 'text-embedding-v4', image2text: 'qwen-vl-plus', ocr: 'qwen-vl-plus' } },
  { value: 'siliconflow', label: 'SiliconFlow', default_base_url: 'https://api.siliconflow.cn/v1', api_style: 'openai', model_examples: { llm: 'Qwen/Qwen2.5-72B-Instruct', embedding: 'BAAI/bge-m3', image2text: 'Qwen/Qwen2.5-VL-72B-Instruct', ocr: 'Qwen/Qwen2.5-VL-72B-Instruct' } },
  { value: 'zhipu', label: '智谱 AI', default_base_url: 'https://open.bigmodel.cn/api/paas/v4', api_style: 'openai', model_examples: { llm: 'glm-4-flash', embedding: 'embedding-3', image2text: 'glm-4v-flash', ocr: 'glm-4v-flash' } },
  { value: 'hunyuan', label: '腾讯混元', default_base_url: 'https://api.hunyuan.cloud.tencent.com/v1', api_style: 'openai', model_examples: { llm: 'hunyuan-lite', image2text: 'hunyuan-vision', ocr: 'hunyuan-vision' } },
  { value: 'volcengine', label: '火山引擎 Ark', default_base_url: 'https://ark.cn-beijing.volces.com/api/v3', api_style: 'openai', model_examples: { llm: 'doubao-1-5-pro-32k', embedding: 'doubao-embedding-text', image2text: 'doubao-vision-pro', ocr: 'doubao-vision-pro' } },
  { value: 'paddleocr', label: 'PaddleOCR / 自定义 OCR 服务', default_base_url: 'http://host.docker.internal:8868/ocr', api_style: 'custom_ocr', model_examples: { ocr: 'paddleocr' } },
  { value: 'mineru', label: 'MinerU 文档解析引擎', default_base_url: 'http://mineru-cpu:8000', api_style: 'document_parser', model_examples: { ocr: 'mineru' } },
  { value: 'custom', label: '自定义 API', default_base_url: '', api_style: 'custom', model_examples: {} }
]

const presets = ref<ProviderPreset[]>([])
const form = ref<ModelConfig>({
  provider: 'ollama',
  llm_model: 'qwen2.5:3b',
  ollama_base_url: 'http://host.docker.internal:11434',
  api_key: '',
  embedding_provider: 'ollama',
  embedding_model: 'bge-m3',
  embedding_base_url: 'http://host.docker.internal:11434',
  embedding_api_key: '',
  embedding_dimension: 1024,
  image2text_provider: '',
  image2text_model: '',
  image2text_base_url: '',
  image2text_api_key: '',
  ocr_provider: 'deepdoc-builtin',
  ocr_model: 'deepdoc-local-ocr',
  ocr_base_url: '',
  ocr_api_key: ''
})
const message = ref('')
const error = ref('')
const dirty = ref(false)
const apiTests = ref<Record<string, ApiTestResult>>({})
const testing = ref<Record<string, boolean>>({})
const deepdoc = ref<DeepDocStatus | null>(null)
const modelOptions = ref<Record<ModelField, ProviderModelOption[]>>({ llm: [], embedding: [], image2text: [], ocr: [] })
const modelLoadMessage = ref<Record<ModelField, string>>({ llm: '', embedding: '', image2text: '', ocr: '' })
const loadingModels = ref<Record<ModelField, boolean>>({ llm: false, embedding: false, image2text: false, ocr: false })
const testText = ref('这是正文')
const testTitle = ref('测试文档')
const testContextHeader = ref('第一章 > 第二节')
const testResult = ref<{
  provider?: string
  model: string
  dimension: number
  configured_dimension?: number | null
  sample_size: number
  input_length: number
  input_preview: string
} | null>(null)

const providerOptions = computed(() => {
  const fromBackend = presets.value.length ? presets.value : (form.value.provider_presets || [])
  return fromBackend.length ? fromBackend : LOCAL_PROVIDER_PRESETS
})

function providerOptionsFor(field: ModelField) {
  const items = providerOptions.value.filter(item => {
    const examples = item.model_examples || {}
    if (field === 'ocr' && item.value === 'deepdoc-builtin') return true
    return Boolean(examples[field]) || item.value === 'custom' || item.value === 'openai-compatible' || item.value === 'ollama'
  })
  if (field === 'ocr' && !items.some(item => item.value === 'deepdoc-builtin')) {
    items.unshift(LOCAL_PROVIDER_PRESETS[0])
  }
  return items
}

function presetOf(value?: string) {
  return providerOptions.value.find(item => item.value === value)
}

function getProvider(field: ModelField) {
  if (field === 'llm') return form.value.provider
  if (field === 'embedding') return form.value.embedding_provider
  if (field === 'image2text') return form.value.image2text_provider
  return form.value.ocr_provider
}

function getCurrentModel(field: ModelField) {
  if (field === 'llm') return form.value.llm_model
  if (field === 'embedding') return form.value.embedding_model
  if (field === 'image2text') return form.value.image2text_model || ''
  return form.value.ocr_model || ''
}

function setCurrentModel(field: ModelField, value: string) {
  if (field === 'llm') form.value.llm_model = value
  else if (field === 'embedding') form.value.embedding_model = value
  else if (field === 'image2text') form.value.image2text_model = value
  else form.value.ocr_model = value
  dirty.value = true
}

function markDirty() {
  dirty.value = true
}

function eventValue(event: Event) {
  return (event.target as HTMLInputElement).value
}

function applyPreset(field: ModelField) {
  const provider = String(getProvider(field) || '')
  modelOptions.value[field] = []
  modelLoadMessage.value[field] = ''
  dirty.value = true
  if (!provider) {
    if (field === 'image2text') {
      form.value.image2text_base_url = ''
      form.value.image2text_model = ''
      form.value.image2text_api_key = ''
    }
    if (field === 'ocr') {
      form.value.ocr_base_url = ''
      form.value.ocr_model = ''
      form.value.ocr_api_key = ''
    }
    return
  }
  const preset = presetOf(provider)
  if (!preset) return
  const defaultUrl = preset.default_base_url || ''
  const examples = preset.model_examples || {}

  if (field === 'llm') {
    form.value.ollama_base_url = defaultUrl
    form.value.llm_model = examples.llm || ''
  } else if (field === 'embedding') {
    form.value.embedding_base_url = defaultUrl
    form.value.embedding_model = examples.embedding || ''
  } else if (field === 'image2text') {
    form.value.image2text_base_url = defaultUrl
    form.value.image2text_model = examples.image2text || ''
  } else if (field === 'ocr') {
    form.value.ocr_base_url = defaultUrl
    form.value.ocr_model = examples.ocr || ''
    if (provider === 'deepdoc-builtin') {
      form.value.ocr_base_url = ''
      form.value.ocr_api_key = ''
      form.value.ocr_model = examples.ocr || 'deepdoc-local-ocr'
    }
  }

  void loadProviderModels(field, true)
}

async function loadProviderModels(field: ModelField, silent = false) {
  const provider = getProvider(field)
  modelLoadMessage.value[field] = ''
  if (!provider) {
    modelOptions.value[field] = []
    return
  }
  loadingModels.value[field] = true
  try {
    const result = await getProviderModels(field, form.value)
    const models = result.models || []
    modelOptions.value[field] = models
    if (models.length) {
  const current = getCurrentModel(field)
  if (!current) {
    setCurrentModel(field, models[0].id)
  }
  modelLoadMessage.value[field] = `已获取 ${models.length} 个可用模型；也可手动输入模型名`
} else {
      modelLoadMessage.value[field] = result.message || '该供应商未返回模型列表，可手动填写模型名'
    }
  } catch (err) {
    modelOptions.value[field] = []
    const msg = err instanceof Error ? err.message : '获取模型列表失败'
    modelLoadMessage.value[field] = msg
    if (!silent) error.value = msg
  } finally {
    loadingModels.value[field] = false
  }
}

function statusLabel(ok?: boolean, skipped?: boolean) {
  if (skipped) return '未配置'
  return ok ? '可用' : '失败'
}

async function load() {
  const [cfg, providerData, deepdocStatus] = await Promise.all([
    getModelConfig(),
    getProviderPresets().catch(() => ({ providers: [] })),
    getDeepDocStatus().catch(() => null)
  ])
  form.value = cfg
  presets.value = providerData.providers || cfg.provider_presets || []
  if (!form.value.ocr_provider && (!form.value.ocr_model || form.value.ocr_model === 'deepdoc-local-ocr')) {
    form.value.ocr_provider = 'deepdoc-builtin'
    form.value.ocr_model = 'deepdoc-local-ocr'
    form.value.ocr_base_url = ''
    form.value.ocr_api_key = ''
  }
  if (form.value.ocr_provider === 'deepdoc-builtin') {
    form.value.ocr_model = form.value.ocr_model || 'deepdoc-local-ocr'
    form.value.ocr_base_url = ''
    form.value.ocr_api_key = ''
  }
  deepdoc.value = deepdocStatus
  dirty.value = false
  await Promise.allSettled([
    loadProviderModels('llm', true),
    loadProviderModels('embedding', true),
    form.value.image2text_provider ? loadProviderModels('image2text', true) : Promise.resolve(),
    form.value.ocr_provider ? loadProviderModels('ocr', true) : Promise.resolve()
  ])
  dirty.value = false
}

async function save() {
  message.value = ''
  error.value = ''
  try {
    form.value = await updateModelConfig(form.value)
    dirty.value = false
    message.value = '保存成功，刷新后会保留当前配置'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败'
  }
}

async function runApiTest(type: ModelField) {
  message.value = ''
  error.value = ''
  testing.value[type] = true
  try {
    const result = await testModelApi(type, form.value)
    apiTests.value[type] = result
    const validated = result.validated ? '，模型已通过列表校验' : (result.list_available === false ? '，该供应商未提供模型列表，已执行真实调用测试' : '')
    message.value = `${type} API 测试成功${validated}`
  } catch (err) {
    const msg = err instanceof Error ? err.message : `${type} API 测试失败`
    apiTests.value[type] = { available: false, response_preview: msg }
    error.value = msg
  } finally {
    testing.value[type] = false
  }
}

async function runAllApiTests() {
  message.value = ''
  error.value = ''
  testing.value.all = true
  try {
    const result = await testAllModelApis(form.value)
    apiTests.value = result.results || {}
    message.value = 'API 批量测试完成'
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'API 批量测试失败'
  } finally {
    testing.value.all = false
  }
}

async function refreshDeepDoc() {
  deepdoc.value = await getDeepDocStatus(true)
}

async function runEmbeddingTest() {
  message.value = ''
  error.value = ''
  testResult.value = null
  try {
    testResult.value = await testEmbedding({
      text: testText.value,
      title: testTitle.value,
      context_header: testContextHeader.value
    })
    message.value = 'Embedding 测试成功'
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Embedding 测试失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="page narrow">
    <h1>模型、API 与 DeepDoc 配置</h1>
    <div class="card subtle sticky-actions">
      <span v-if="dirty" class="text-warning">有未保存修改，刷新前请先保存。</span>
      <span v-else class="text-muted">当前配置已保存。</span>
      <button type="button" @click="save">保存配置</button>
    </div>

    <form class="card form" @submit.prevent="save">
      <h2>LLM API</h2>
      <p class="text-muted">参考 WeKnora 的 provider/base_url/api_key/model_name 风格；切换供应商会同步更新默认 Base URL 和推荐模型名。</p>
      <label>LLM 供应商
        <select v-model="form.provider" @change="applyPreset('llm')">
          <option v-for="item in providerOptionsFor('llm')" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label>LLM Base URL <input v-model="form.ollama_base_url" @input="markDirty" placeholder="http://host.docker.internal:11434 或 https://.../v1" /></label>
      <label>LLM API Key（可选） <input v-model="form.api_key" @input="markDirty" type="password" autocomplete="off" /></label>
      <label>LLM 模型
        <input
          :value="form.llm_model"
          list="llm-model-options"
          placeholder="可选择或手动输入模型名，例如 qwen-plus"
          @input="setCurrentModel('llm', eventValue($event))"
        />
        <datalist id="llm-model-options">
          <option v-for="item in modelOptions.llm" :key="item.id" :value="item.id">
            {{ item.name || item.id }}
          </option>
        </datalist>
        <small class="field-help">可从列表选择，也可手动输入供应商支持的模型名。</small>
      </label>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="loadProviderModels('llm')" :disabled="loadingModels.llm">{{ loadingModels.llm ? '获取中...' : '刷新 LLM 模型列表' }}</button>
        <span class="text-muted">{{ modelLoadMessage.llm }}</span>
      </div>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="runApiTest('llm')" :disabled="testing.llm">{{ testing.llm ? '测试中...' : '测试 LLM API' }}</button>
        <span v-if="apiTests.llm" :class="apiTests.llm.available ? 'ok' : 'error'">{{ statusLabel(apiTests.llm.available, apiTests.llm.skipped) }}：{{ apiTests.llm.response_preview || apiTests.llm.dimension || '' }}</span>
      </div>

      <h2>Embedding API</h2>
      <label>Embedding 供应商
        <select v-model="form.embedding_provider" @change="applyPreset('embedding')">
          <option v-for="item in providerOptionsFor('embedding')" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label>Embedding Base URL <input v-model="form.embedding_base_url" @input="markDirty" /></label>
      <label>Embedding API Key <input v-model="form.embedding_api_key" @input="markDirty" type="password" autocomplete="off" /></label>
      <label>Embedding 模型
        <input
          :value="form.embedding_model"
          list="embedding-model-options"
          placeholder="可选择或手动输入模型名，例如 qwen-plus"
          @input="setCurrentModel('embedding', eventValue($event))"
        />
        <datalist id="embedding-model-options">
          <option v-for="item in modelOptions.embedding" :key="item.id" :value="item.id">
            {{ item.name || item.id }}
          </option>
        </datalist>
        <small class="field-help">可从列表选择，也可手动输入供应商支持的模型名。</small>
      </label>
      <label>Embedding 维度 <input v-model.number="form.embedding_dimension" @input="markDirty" type="number" /></label>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="loadProviderModels('embedding')" :disabled="loadingModels.embedding">{{ loadingModels.embedding ? '获取中...' : '刷新 Embedding 模型列表' }}</button>
        <span class="text-muted">{{ modelLoadMessage.embedding }}</span>
      </div>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="runApiTest('embedding')" :disabled="testing.embedding">{{ testing.embedding ? '测试中...' : '测试 Embedding API' }}</button>
        <span v-if="apiTests.embedding" :class="apiTests.embedding.available ? 'ok' : 'error'">{{ statusLabel(apiTests.embedding.available, apiTests.embedding.skipped) }}：维度 {{ apiTests.embedding.dimension || '-' }}</span>
      </div>

      <h2>图片理解 API（可选）</h2>
      <p class="text-muted">用于 DOCX/PDF 图片 chunk 的 VLM 描述。留空则只保存图片，不生成图片语义描述。</p>
      <label>Image2Text 供应商
        <select v-model="form.image2text_provider" @change="applyPreset('image2text')">
          <option value="">关闭</option>
          <option v-for="item in providerOptionsFor('image2text')" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label>Image2Text Base URL <input v-model="form.image2text_base_url" @input="markDirty" /></label>
      <label>Image2Text API Key <input v-model="form.image2text_api_key" @input="markDirty" type="password" autocomplete="off" /></label>
      <label>Image2Text 模型
        <input
          :value="form.image2text_model"
          list="image2text-model-options"
          placeholder="可选择或手动输入模型名，例如 qwen-plus"
          @input="setCurrentModel('image2text', eventValue($event))"
        />
        <datalist id="image2text-model-options">
          <option v-for="item in modelOptions.image2text" :key="item.id" :value="item.id">
            {{ item.name || item.id }}
          </option>
        </datalist>
        <small class="field-help">可从列表选择，也可手动输入供应商支持的模型名。</small>
      </label>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="loadProviderModels('image2text')" :disabled="loadingModels.image2text || !form.image2text_provider">{{ loadingModels.image2text ? '获取中...' : '刷新 Image2Text 模型列表' }}</button>
        <span class="text-muted">{{ modelLoadMessage.image2text }}</span>
      </div>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="runApiTest('image2text')" :disabled="testing.image2text || !form.image2text_provider">{{ testing.image2text ? '测试中...' : '测试 Image2Text API' }}</button>
        <span v-if="apiTests.image2text" :class="apiTests.image2text.available ? 'ok' : 'error'">{{ statusLabel(apiTests.image2text.available, apiTests.image2text.skipped) }}：{{ apiTests.image2text.response_preview || '' }}</span>
      </div>

      <h2>OCR / DeepDoc 内置 OCR / MinerU 文档解析</h2>
      <p class="text-muted">对齐 RAGFlow：DeepDoc OCR 默认启用，不需要单独配置 OCR API。MinerU 作为文档解析引擎使用（非图片 OCR），当文档上传后优先调用 MinerU /file_parse 进行全文档解析。MinerU 不支持或失败时 fallback 到 DeepDoc/RAGFlow。</p>
      <p v-if="form.ocr_provider === 'mineru'" class="text-warning">
        ⚠ MinerU 已纳入 docker-compose。CPU 解析服务地址为 http://mineru-cpu:8000，GPU 解析服务地址为 http://mineru-gpu:8000。<br/>
        上传页的“MinerU 运行版本”会真正写入 parse_options，并决定本次解析请求走 CPU pipeline 还是 GPU VLM 后端。<br/>
        不建议把 OCR 供应商设为 MinerU；MinerU 主要作为文档主解析器使用。
      </p>
      <label>OCR 供应商
        <select v-model="form.ocr_provider" @change="applyPreset('ocr')">
          <option v-for="item in providerOptionsFor('ocr')" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label>OCR Base URL <input v-model="form.ocr_base_url" @input="markDirty" :disabled="form.ocr_provider === 'deepdoc-builtin'" placeholder="DeepDoc 内置 OCR 不需要 Base URL" /></label>
      <label>OCR API Key <input v-model="form.ocr_api_key" @input="markDirty" :disabled="form.ocr_provider === 'deepdoc-builtin'" type="password" autocomplete="off" /></label>
      <label>OCR 模型
        <input
          :value="form.ocr_model"
          list="ocr-model-options"
          placeholder="可选择或手动输入模型名，例如 qwen-plus"
          @input="setCurrentModel('ocr', eventValue($event))"
        />
        <datalist id="ocr-model-options">
          <option v-for="item in modelOptions.ocr" :key="item.id" :value="item.id">
            {{ item.name || item.id }}
          </option>
        </datalist>
        <small class="field-help">可从列表选择，也可手动输入供应商支持的模型名。</small>
      </label>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="loadProviderModels('ocr')" :disabled="loadingModels.ocr || !form.ocr_provider">{{ loadingModels.ocr ? '获取中...' : '刷新 OCR 模型列表' }}</button>
        <span class="text-muted">{{ modelLoadMessage.ocr }}</span>
      </div>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="runApiTest('ocr')" :disabled="testing.ocr || !form.ocr_provider">{{ testing.ocr ? '测试中...' : '测试 OCR API' }}</button>
        <span v-if="apiTests.ocr" :class="apiTests.ocr.available ? 'ok' : 'error'">{{ statusLabel(apiTests.ocr.available, apiTests.ocr.skipped) }}：{{ apiTests.ocr.response_preview || '' }}</span>
      </div>

      <h2>DeepDoc 生产模型状态</h2>
      <p class="text-muted">对齐 RAGFlow：Docker 启动后默认使用 DeepDoc 解析链路，并自动准备 DeepDoc 模型到 <code>/data/models/deepdoc</code>。能下载/加载模型时走 ONNX LayoutRecognizer、TSR 和 XGBoost；模型暂不可用时不会阻塞服务，会自动使用启发式兜底。</p>
      <div v-if="deepdoc" class="card subtle">
        <dl class="details">
          <dt>启用状态</dt><dd>{{ deepdoc.enabled ? '默认启用' : '未启用' }}</dd>
          <dt>运行模式</dt><dd>{{ deepdoc.mode || 'builtin-fallback' }}</dd>
          <dt>模型就绪</dt><dd>{{ deepdoc.model_ready ? '生产模型已就绪' : (deepdoc.auto_download ? '自动准备中 / 使用兜底解析' : '未准备 / 使用兜底解析') }}</dd>
          <dt>自动下载</dt><dd>{{ deepdoc.auto_download ? '已开启' : '已关闭' }}{{ deepdoc.download_attempted ? ' · 已尝试准备模型' : '' }}</dd>
          <dt>内置 OCR</dt><dd>{{ deepdoc.ocr_available ? '可用' : '默认启用，当前环境未检测到本地 OCR 引擎' }} · {{ deepdoc.ocr_engine || 'auto' }}</dd>
          <dt>模型目录</dt><dd>{{ deepdoc.model_dir || '-' }}</dd>
          <dt>Layout ONNX</dt><dd>{{ deepdoc.layout_loaded ? '已加载' : '未加载' }} · {{ deepdoc.layout_model_path || '-' }}</dd>
          <dt>TSR ONNX</dt><dd>{{ deepdoc.tsr_loaded ? '已加载' : '未加载' }} · {{ deepdoc.tsr_model_path || '-' }}</dd>
          <dt>XGBoost</dt><dd>{{ deepdoc.xgb_loaded ? '已加载' : '未加载' }} · {{ deepdoc.xgb_model_path || '-' }}</dd>
          <dt>DeepDoc 服务</dt><dd>{{ deepdoc.service_url || '-' }}</dd>
          <dt>下载信息</dt><dd>{{ deepdoc.download_messages?.length ? deepdoc.download_messages.join('；') : '-' }}</dd>
          <dt>错误信息</dt><dd>{{ deepdoc.errors?.length ? deepdoc.errors.join('；') : '-' }}</dd>
        </dl>
      </div>
      <div class="actions inline-actions">
        <button type="button" class="secondary" @click="refreshDeepDoc">刷新 DeepDoc 状态</button>
        <button type="button" class="secondary" @click="runAllApiTests" :disabled="testing.all">{{ testing.all ? '测试中...' : '批量测试全部 API' }}</button>
      </div>

      <h2>Embedding 专项测试</h2>
      <label>文档标题（可选） <input v-model="testTitle" /></label>
      <label>上下文标题（可选） <input v-model="testContextHeader" /></label>
      <label>测试正文 <textarea v-model="testText" rows="4" /></label>

      <div v-if="testResult" class="card subtle">
        <dl class="details">
          <dt>供应商</dt><dd>{{ testResult.provider || '-' }}</dd>
          <dt>模型</dt><dd>{{ testResult.model }}</dd>
          <dt>维度</dt><dd>{{ testResult.dimension }}</dd>
          <dt>配置维度</dt><dd>{{ testResult.configured_dimension ?? '-' }}</dd>
          <dt>样本长度</dt><dd>{{ testResult.sample_size }}</dd>
          <dt>输入长度</dt><dd>{{ testResult.input_length }}</dd>
          <dt>输入预览</dt><dd>{{ testResult.input_preview }}</dd>
        </dl>
      </div>

      <p v-if="message" class="text-success">{{ message }}</p>
      <p v-if="error" class="text-error">{{ error }}</p>
      <div class="actions">
        <button>保存配置</button>
        <button type="button" class="secondary" @click="runEmbeddingTest">测试 Embedding</button>
      </div>
    </form>
  </section>
</template>
