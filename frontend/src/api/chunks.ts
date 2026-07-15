import { api } from './client'

export interface ChunkItem {
  id: string
  chunk_id?: string
  document_id: string
  knowledge_id?: string
  knowledge_base_id: string
  content?: string
  content_preview?: string
  preview?: string
  chunk_index: number
  seq?: number
  token_count: number
  is_enabled: boolean
  status: string
  start_at?: number | null
  end_at?: number | null
  pre_chunk_id?: string | null
  next_chunk_id?: string | null
  chunk_type?: string
  parent_chunk_id?: string | null
  content_hash?: string
  image_info?: Record<string, unknown>
  relation_chunks?: unknown[]
  indirect_relation_chunks?: unknown[]
  context_header?: string
  flags?: Record<string, unknown>
  metadata: Record<string, unknown>
  metadata_json?: Record<string, unknown>
  skip_embedding_recommended?: boolean
  skip_reason?: string
  embedding_status?: string
  embedding_index_info?: {
    is_indexed: boolean
    model_name?: string
    dimension?: number | null
    source_type?: string
    is_enabled?: boolean
    skip_embedding_recommended?: boolean
    skip_reason?: string
    status?: string
    indexed_content_preview?: string
    vector_preview?: number[]
    vector_size?: number
    vector_norm?: number | null
  }
  created_at: string
  updated_at: string
}

export interface ChunkList {
  document_id: string
  items: ChunkItem[]
  page: number
  page_size: number
  total: number
}

export function listChunks(documentId: string, page = 1, pageSize = 50, type = 'all', indexStatus = 'all') {
  const normalizedType = type || 'all'
  const normalizedIndexStatus = indexStatus || 'all'
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    // 兼容两版后端：旧版使用 type alias，新版使用 chunk_type。
    type: normalizedType,
    chunk_type: normalizedType,
    index_status: normalizedIndexStatus
  })
  return api.get<ChunkList>(`/api/v1/documents/${documentId}/chunks?${params.toString()}`)
}

export function getChunk(id: string) {
  return api.get<ChunkItem>(`/api/v1/chunks/by-id/${id}`)
}

export function enableChunk(id: string) {
  return api.put<ChunkItem>(`/api/v1/chunks/${id}/enable`)
}

export function disableChunk(id: string) {
  return api.put<ChunkItem>(`/api/v1/chunks/${id}/disable`)
}

export function deleteChunk(id: string) {
  return api.delete<{ id: string; deleted: boolean }>(`/api/v1/chunks/${id}`)
}

export interface ChunkTreeParent {
  parent: ChunkItem
  children: ChunkItem[]
  child_count: number
  child_type_counts: Record<string, number>
  has_image_child?: boolean
  has_table_child?: boolean
  has_equation_child?: boolean
}

export interface ChunkTreeResponse {
  document_id: string
  items: ChunkTreeParent[]
  orphans: ChunkItem[]
  stats: {
    total_chunks: number
    parent_count: number
    orphan_count: number
    chunk_type_counts: Record<string, number>
  }
}

export function getChunkTree(documentId: string) {
  return api.get<ChunkTreeResponse>(`/api/v1/documents/${documentId}/chunk-tree`)
}
