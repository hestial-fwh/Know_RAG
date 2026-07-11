import { api } from './client'

export interface RetrievalConfig {
  embedding_top_k: number
  score_threshold: number
  use_vector: boolean
  use_keyword: boolean
  rerank_model_id: string
  fusion_strategy: string
  rrf_k: number
  rrf_vector_weight: number
  rrf_keyword_weight: number
  include_context: boolean
}

export interface ProviderPreset {
  value: string
  label: string
  default_base_url: string
  api_style: string
  model_examples?: Record<string, string>
}

export interface ApiTestResult {
  available?: boolean
  model_type?: string
  provider?: string
  model?: string
  dimension?: number
  sample?: number[]
  response_preview?: string
  skipped?: boolean
  reason?: string
  validated?: boolean
  list_available?: boolean
  model_count?: number
}

export interface ProviderModelOption {
  id: string
  name?: string
  size?: number
  modified_at?: string
}

export interface ProviderModelsResult {
  provider?: string
  models: ProviderModelOption[]
  list_available?: boolean
  message?: string
}

export interface DeepDocStatus {
  enabled: boolean
  service_url: string
  mode?: string
  ocr_available?: boolean
  ocr_engine?: string
  model_dir: string
  layout_model_path: string
  layout_loaded: boolean
  tsr_model_path: string
  tsr_loaded: boolean
  xgb_model_path: string
  xgb_loaded: boolean
  errors: string[]
  auto_download?: boolean
  download_attempted?: boolean
  download_messages?: string[]
  model_ready?: boolean
}

export interface ModelConfig {
  provider: string
  llm_model: string
  ollama_base_url: string
  api_key?: string
  embedding_provider: string
  embedding_model: string
  embedding_base_url?: string
  embedding_api_key?: string
  embedding_dimension?: number | null
  image2text_provider?: string
  image2text_model?: string
  image2text_base_url?: string
  image2text_api_key?: string
  ocr_provider?: string
  ocr_model?: string
  ocr_base_url?: string
  ocr_api_key?: string
  provider_presets?: ProviderPreset[]
}

export interface VectorStoreConfig {
  provider: string
  config: Record<string, unknown>
  is_active: boolean
  providers?: Array<{ value: string; label: string; default_config: Record<string, unknown> }>
}

export interface ChunkerConfig {
  id?: string
  strategy: string
  chunk_size: number
  chunk_overlap: number
  separators: string[]
  enable_parent_child: boolean
  parent_chunk_size: number
  child_chunk_size: number
  child_chunk_overlap: number
}

export const getRetrievalConfig = () => api.get<RetrievalConfig>('/api/v1/retrieval-config')
export const updateRetrievalConfig = (payload: RetrievalConfig) =>
  api.put<RetrievalConfig>('/api/v1/retrieval-config', payload)

export const getModelConfig = () => api.get<ModelConfig>('/api/v1/model-config')
export const getProviderPresets = () => api.get<{ providers: ProviderPreset[] }>('/api/v1/model-config/providers')
export const updateModelConfig = (payload: ModelConfig) =>
  api.put<ModelConfig>('/api/v1/model-config', payload)
export const testModelApi = (modelType: 'llm' | 'embedding' | 'image2text' | 'ocr', payload: ModelConfig) =>
  api.post<ApiTestResult>(`/api/v1/model-config/test/${modelType}`, payload)
export const getProviderModels = (modelType: 'llm' | 'embedding' | 'image2text' | 'ocr', payload: ModelConfig) => {
  const cfg: Record<string, unknown> = { model_type: modelType }
  if (modelType === 'llm') {
    cfg.provider = payload.provider
    cfg.base_url = payload.ollama_base_url
    cfg.api_key = payload.api_key || ''
    cfg.model_name = payload.llm_model
  } else if (modelType === 'embedding') {
    cfg.provider = payload.embedding_provider
    cfg.base_url = payload.embedding_base_url || ''
    cfg.api_key = payload.embedding_api_key || payload.api_key || ''
    cfg.model_name = payload.embedding_model
  } else if (modelType === 'image2text') {
    cfg.provider = payload.image2text_provider || ''
    cfg.base_url = payload.image2text_base_url || ''
    cfg.api_key = payload.image2text_api_key || payload.api_key || ''
    cfg.model_name = payload.image2text_model || ''
  } else {
    cfg.provider = payload.ocr_provider || ''
    cfg.base_url = payload.ocr_base_url || ''
    cfg.api_key = payload.ocr_api_key || payload.api_key || ''
    cfg.model_name = payload.ocr_model || ''
  }
  return api.post<ProviderModelsResult>('/api/v1/model-config/models', cfg)
}
export const testAllModelApis = (payload: ModelConfig) =>
  api.post<{ available: boolean; results: Record<string, ApiTestResult> }>('/api/v1/model-config/test', payload)
export const getDeepDocStatus = (reload = false) => api.get<DeepDocStatus>(`/api/v1/model-config/deepdoc/status${reload ? '?reload=true' : ''}`)

export const getVectorStoreConfig = () =>
  api.get<VectorStoreConfig>('/api/v1/vector-store/config')
export const getVectorStoreProviders = () =>
  api.get<{ providers: Array<{ value: string; label: string; default_config: Record<string, unknown> }> }>('/api/v1/vector-store/providers')
export const updateVectorStoreConfig = (payload: VectorStoreConfig) =>
  api.put<VectorStoreConfig>('/api/v1/vector-store/config', payload)
export const testVectorStore = (payload: VectorStoreConfig) =>
  api.post<{ available: boolean; provider: string; url?: string; uri?: string }>('/api/v1/vector-store/test', payload)

export const getChunkerConfig = () => api.get<ChunkerConfig>('/api/v1/chunker-config')
export const updateChunkerConfig = (payload: ChunkerConfig) =>
  api.put<ChunkerConfig>('/api/v1/chunker-config', payload)

export function testEmbedding(payload: { text: string; title?: string; context_header?: string }) {
  return api.post<{
    provider?: string
    model: string
    dimension: number
    configured_dimension?: number | null
    sample_size: number
    input_length: number
    input_preview: string
  }>(
    '/api/v1/initialization/embedding/test',
    payload
  )
}
