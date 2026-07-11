import { api } from './client'

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  embedding_model_id: string
  summary_model_id: string
  document_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseList {
  items: KnowledgeBase[]
  page: number
  page_size: number
  total: number
}

export interface KnowledgeBasePayload {
  name: string
  description?: string
  embedding_model_id?: string
  summary_model_id?: string
}

export function listKnowledgeBases() {
  return api.get<KnowledgeBaseList>('/api/v1/knowledge-bases')
}

export function getKnowledgeBase(id: string) {
  return api.get<KnowledgeBase>(`/api/v1/knowledge-bases/${id}`)
}

export function createKnowledgeBase(payload: KnowledgeBasePayload) {
  return api.post<KnowledgeBase>('/api/v1/knowledge-bases', payload)
}

export function updateKnowledgeBase(id: string, payload: KnowledgeBasePayload) {
  return api.put<KnowledgeBase>(`/api/v1/knowledge-bases/${id}`, payload)
}

export function deleteKnowledgeBase(id: string) {
  return api.delete<{ id: string; deleted: boolean }>(`/api/v1/knowledge-bases/${id}`)
}

export interface KnowledgeBaseEmbeddingStatus {
  knowledge_base_id: string
  total_chunks: number
  embedded_chunks: number
  missing_chunks: number
  skipped_chunks?: number
  pending_chunks?: number
  real_missing_chunks?: number
  failed_chunks?: number
  model: string
  dimension: number
}

export interface KnowledgeBaseEmbeddingResult {
  knowledge_base_id: string
  total_chunks: number
  embedded_chunks: number
  missing_chunks: number
  skipped_chunks?: number
  pending_chunks?: number
  real_missing_chunks?: number
  failed_chunks?: number
  model: string
  dimension: number
}

export function embedKnowledgeBase(id: string) {
  return api.post<KnowledgeBaseEmbeddingResult>(`/api/v1/knowledge-bases/${id}/embedding`, {})
}

export function getKnowledgeBaseEmbeddingStatus(id: string) {
  return api.get<KnowledgeBaseEmbeddingStatus>(`/api/v1/knowledge-bases/${id}/embedding/status`)
}
