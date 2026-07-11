import { api, type ApiResponse } from './client'

// ── Types ────────────────────────────────────────────────────────────────────

export interface DocumentItem {
  id: string
  document_id?: string
  knowledge_id?: string
  knowledge_base_id: string
  title: string
  file_name: string
  file_type: string
  source?: string
  channel?: string
  metadata?: Record<string, unknown>
  metadata_json?: Record<string, unknown>
  enable_status?: string
  file_size: number
  file_hash: string
  storage_path: string
  parse_status: string
  error_message: string
  processed_at?: string | null
  chunk_count: number
  parse_statistics?: {
    parsed_block_count?: number
    text_block_count?: number
    table_block_count?: number
    image_block_count?: number
    enhanced_image_count?: number
    ocr_enhanced_count?: number
    chunk_type_counts?: Record<string, number>
    parser?: string
    parser_actual?: string
    parser_requested?: string
    parser_attempted?: string
    parser_preference?: string
    parser_fallback_reason?: string
    fallback_used?: boolean
    fallback_reason?: string
    document_parser_engine?: string
    pdf_engine?: string
    mineru_pdf_enabled?: boolean
    mineru_content_list_count?: number
    mineru_config_source?: string
    mineru_endpoint?: string
    mineru_base_url?: string
    block_reflow_engine?: string
    block_reflow_enabled?: boolean
    reflow_input_blocks?: number
    reflow_output_blocks?: number
    reflow_merged_blocks?: number
    ocr_repair_enabled?: boolean
    ocr_repaired_blocks?: number
    table_crop_count?: number
    figure_crop_count?: number
    scanned_page_image_count?: number
    ocr_failed_count?: number
    vlm_failed_count?: number
    layout_recognizer?: string
    table_structure_recognizer?: string
    vertical_concat_engine?: string
    figure_caption_binding?: string
    multi_column_ordering?: string
    caption_bound_count?: number
  }
  created_at: string
  updated_at: string
  parse_task?: { id: string; task_id?: string; status: string; progress: number; current_stage: string }
}

export interface DocumentList {
  knowledge_base_id: string
  items: DocumentItem[]
  page: number
  page_size: number
  total: number
}

// ── Upload with progress ─────────────────────────────────────────────────────

const API_BASE = import.meta.env.VITE_API_BASE || ''

export function uploadDocument(
  payload: {
    knowledge_base_id: string
    file: File
    title?: string
    parse_immediately: boolean
    parser_engine?: string
    mineru_profile?: string
    enable_ocr?: string
    enable_vlm?: boolean
    enable_media_crop?: boolean
  },
  onProgress?: (pct: number) => void,
): Promise<DocumentItem> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('knowledge_base_id', payload.knowledge_base_id)
    form.append('file', payload.file)
    form.append('parse_immediately', String(payload.parse_immediately))
    if (payload.parser_engine) form.append('parser_engine', payload.parser_engine)
    if (payload.mineru_profile) form.append('mineru_profile', payload.mineru_profile)
    if (payload.enable_ocr) form.append('enable_ocr', payload.enable_ocr)
    if (typeof payload.enable_vlm === 'boolean') form.append('enable_vlm', String(payload.enable_vlm))
    if (typeof payload.enable_media_crop === 'boolean') form.append('enable_media_crop', String(payload.enable_media_crop))
    if (payload.title) form.append('title', payload.title)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/api/v1/documents/upload`)

    const token = localStorage.getItem('know_rag_token')
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    })

    xhr.addEventListener('load', () => {
      try {
        const resp = JSON.parse(xhr.responseText) as ApiResponse<DocumentItem>
        if (xhr.status >= 200 && xhr.status < 300 && resp.success) {
          resolve(resp.data)
        } else {
          reject(new Error(resp.message || `upload failed: ${xhr.status}`))
        }
      } catch {
        reject(new Error('Invalid response from server'))
      }
    })

    xhr.addEventListener('error', () => reject(new Error('网络错误，上传失败')))
    xhr.addEventListener('abort', () => reject(new Error('上传已取消')))

    xhr.send(form)
  })
}

// ── Document CRUD ────────────────────────────────────────────────────────────

export function listDocuments(knowledgeBaseId: string) {
  return api.get<DocumentList>(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`)
}

export function getDocument(id: string) {
  return api.get<DocumentItem>(`/api/v1/documents/${id}`)
}

export function deleteDocument(id: string) {
  return api.delete<{ id: string; deleted: boolean }>(`/api/v1/documents/${id}`)
}

export function parseDocument(id: string) {
  return api.post<DocumentItem>(`/api/v1/documents/${id}/parse`)
}

export function reparseDocument(id: string) {
  return api.post<DocumentItem>(`/api/v1/documents/${id}/reparse`)
}

// ── Embedding ────────────────────────────────────────────────────────────────

export interface EmbeddingStatus {
  document_id?: string
  knowledge_base_id?: string
  total_chunks: number
  embedded_chunks: number
  missing_chunks: number
  skipped_chunks?: number
  pending_chunks?: number
  real_missing_chunks?: number
  missing_chunks_before?: number
  embedded_this_run?: number
  skipped_this_run?: number
  failed_chunks?: number
  has_context_header_count?: number
  indexed_content_count?: number
  model: string
  dimension: number
  provider?: string
}

export interface EmbeddingResult {
  document_id?: string
  knowledge_base_id?: string
  total_chunks: number
  embedded_chunks: number
  missing_chunks: number
  skipped_chunks?: number
  pending_chunks?: number
  real_missing_chunks?: number
  missing_chunks_before?: number
  embedded_this_run?: number
  skipped_this_run?: number
  failed_chunks?: number
  has_context_header_count?: number
  indexed_content_count?: number
  model: string
  dimension: number
  provider?: string
}

export function embedDocument(id: string) {
  return api.post<EmbeddingResult>(`/api/v1/documents/${id}/embedding`, {})
}

export function getDocumentEmbeddingStatus(id: string) {
  return api.get<EmbeddingStatus>(`/api/v1/documents/${id}/embedding/status`)
}

// ── Batch vectorize ──────────────────────────────────────────────────────────

export interface BatchVectorizeItem {
  document_id: string
  title: string
  status: string
  total_chunks: number
  embedded_chunks: number
  skipped_chunks: number
  failed_chunks: number
  percent: number
  error: string
}

export interface BatchVectorizeStatus {
  batch_id: string
  knowledge_base_id: string
  status: string
  total_documents: number
  completed_documents: number
  failed_documents: number
  skipped_documents: number
  total_chunks: number
  embedded_chunks: number
  current_document_id: string
  current_document_title: string
  percent: number
  error: string
  started_at: string | null
  finished_at: string | null
  items: BatchVectorizeItem[]
}

export interface StartBatchPayload {
  document_ids?: string[]
  only_unvectorized?: boolean
  force?: boolean
}

export function startBatchVectorize(kbId: string, payload?: StartBatchPayload) {
  return api.post<{ batch_id: string; status: string; total_documents: number }>(
    `/api/v1/knowledge-bases/${kbId}/vectorize-batch`,
    payload || {}
  )
}

export function getBatchVectorizeStatus(batchId: string) {
  return api.get<BatchVectorizeStatus>(`/api/v1/vectorize-batch/${batchId}`)
}
