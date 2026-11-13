import { api } from './client'
import type { RetrievalProfile } from './retrieval'

export interface KnowledgeBase {
  retrieval_profile?: RetrievalProfile
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
  retrieval_profile?: RetrievalProfile
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

export interface KnowledgeIndexStatus {
  global_retrieval_ready?: boolean
  community_status?: { state: string; graph_revision?: string }
  graph_retrieval_ready: boolean
  event_retrieval_ready: boolean
  enabled: boolean
  state: string
  total_documents: number
  covered_documents: number
  documents: Record<string, number>
  counts?: {
    entities: number
    relations: number
    events: number
    communities: number
    reports: number
  }
}

export function getKnowledgeIndexStatus(id: string) {
  return api.get<KnowledgeIndexStatus>(`/api/v1/knowledge-bases/${id}/knowledge-index/status`)
}

export interface GraphEvidence {
  chunk_id: string
  document_id: string
  document_title: string
  parse_revision: string
  quote: string
  quote_truncated: boolean
  page_no?: number | null
}
export interface KnowledgeGraphNode {
  id: string
  kind: 'entity' | 'event'
  label: string
  entity_type?: string
  scope?: string
  qualifiers?: Record<string, unknown>
  evidence: GraphEvidence[]
}
export interface KnowledgeGraphEdge {
  id: string
  source: string
  target: string
  kind: 'relation' | 'participation'
  label: string
  qualifiers?: Record<string, unknown>
  evidence: GraphEvidence[]
}
export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  meta: { state: string; enabled: boolean; truncated: boolean; limit: number; build_indexes: string[] }
}
export function getKnowledgeGraph(id: string, options: { q?: string; focus?: string; limit?: number }) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(options)) if (value !== undefined && value !== '') params.set(key, String(value))
  return api.get<KnowledgeGraph>(`/api/v1/knowledge-bases/${id}/knowledge-index/graph?${params}`)
}

export function buildKnowledgeIndex(id: string, retry = false) {
  return api.post<{ created_jobs: number; retried_jobs: number }>(
    `/api/v1/knowledge-bases/${id}/knowledge-index/build`, { retry })
}

export function buildCommunityIndex(id: string) {
  return api.post<{ graph_job_id: string; created: boolean; state: string }>(
    `/api/v1/knowledge-bases/${id}/knowledge-index/communities/build`, { retry: true })
}

export interface CommunityReportItem {
  report_id: string
  community_id: string
  graph_revision: string
  title: string
  points: Array<{ text: string; evidence?: Array<Record<string, unknown>> }>
  derived_summary: boolean
  provider?: Record<string, unknown>
}

export interface CommunityReportsResponse {
  items: CommunityReportItem[]
  status: Record<string, unknown>
}

export function getCommunityReports(id: string) {
  return api.get<CommunityReportsResponse>(`/api/v1/knowledge-bases/${id}/knowledge-index/communities`)
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
