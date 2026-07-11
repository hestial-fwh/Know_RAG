import { api } from './client'

export interface HybridSearchPayload {
  query?: string
  query_text?: string
  top_k?: number
  match_count?: number
  score_threshold?: number
  vector_threshold?: number
  keyword_threshold?: number
  use_vector?: boolean
  use_keyword?: boolean
  disable_vector_match?: boolean
  disable_keywords_match?: boolean
  include_content: boolean
  include_context?: boolean
  include_parent_context?: boolean
  parent_context_mode?: string
  parent_context_max_chars?: number
  neighbor_context_max_chars?: number
  parent_children_limit?: number
  max_parent_contexts?: number
  merge_hits_by_parent?: boolean
  fusion_strategy?: string
  rrf_k?: number
  rrf_vector_weight?: number
  rrf_keyword_weight?: number
  max_context_chars?: number
  max_sources?: number
  include_images?: boolean
  include_image_urls?: boolean
  include_tables?: boolean
  include_equations?: boolean
}

export interface HybridSearchResult {
  id?: string
  chunk_id: string
  document_id: string
  knowledge_id?: string
  knowledge_base_id: string
  knowledge_title?: string
  title: string
  file_name: string
  knowledge_filename?: string
  knowledge_source?: string
  knowledge_channel?: string
  content?: string
  preview?: string
  matched_content?: string
  display_content?: string
  display_preview?: string
  chunk_index: number
  seq?: number
  start_at?: number | null
  end_at?: number | null
  score: number
  rrf_score?: number
  vector_score: number
  keyword_score: number
  vector_rank?: number | null
  keyword_rank?: number | null
  match_type: string
  chunk_type?: string
  parent_chunk_id?: string | null
  context_header?: string
  metadata?: Record<string, unknown>
  metadata_json?: Record<string, unknown>
  chunk_metadata?: Record<string, unknown>
  image_info?: Record<string, unknown>
  image_url?: string
  context?: {
    parent?: ParentContext | null
    previous?: ParentContext | null
    next?: ParentContext | null
    parent_children?: ParentChildSummary[]
  } | null
  parent_context?: ParentContext | null
}

export interface ParentContext {
  chunk_id: string
  content?: string
  content_preview?: string
  display_content?: string
  display_preview?: string
  chunk_type?: string
  is_truncated?: boolean
  content_length?: number
  returned_chars?: number
  preview_chars?: number
  image_info?: Record<string, unknown>
  image_url?: string
}

export interface ParentChildSummary {
  chunk_id: string
  chunk_type: string
  chunk_index: number
  context_header?: string
  content_preview?: string
  display_preview?: string
  relation?: string
  is_matched?: boolean
  metadata?: Record<string, unknown>
  metadata_json?: Record<string, unknown>
  image_info?: Record<string, unknown>
  image_url?: string
}

export interface ParentGroup {
  parent_key: string
  parent_chunk_id?: string | null
  parent_context?: ParentContext | null
  score: number
  max_child_score: number
  matched_child_count: number
  matched_children: HybridSearchResult[]
  parent_children: ParentChildSummary[]
  is_orphan?: boolean
}

export interface HybridSearchResponse {
  query: string
  results: HybridSearchResult[]
  parent_groups?: ParentGroup[]
  grouped_results?: ParentGroup[]
  meta: {
    top_k: number
    score_threshold: number
    use_vector: boolean
    use_keyword: boolean
    fusion_strategy?: string
    rrf_k?: number
    vector_weight?: number
    keyword_weight?: number
    include_context?: boolean
    include_parent_context?: boolean
    parent_context_mode?: string
    parent_context_max_chars?: number
    neighbor_context_max_chars?: number
    parent_children_limit?: number
    max_parent_contexts?: number
    merge_hits_by_parent?: boolean
    parent_group_count?: number
    embedding_model: string
  }
}


export interface LLMContextSource {
  source_id: string
  document_title?: string
  file_name?: string
  context_header?: string
  parent_returned_chars?: number
  parent_total_chars?: number
  matched_child_count?: number
  evidence_count?: number
  evidence?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export interface LLMContextImage {
  source_id?: string
  document_title?: string
  file_name?: string
  page_no?: number | null
  context_header?: string
  content?: string
  image_url?: string
  [key: string]: unknown
}

export interface LLMContextResponse {
  query: string
  context_text: string
  sources: LLMContextSource[]
  images: LLMContextImage[]
  meta: {
    context_chars: number
    max_context_chars: number
    is_truncated: boolean
    source_count: number
    image_count: number
    include_images: boolean
    include_image_urls: boolean
    builder: string
  }
  retrieval_meta?: Record<string, unknown>
}

export function hybridSearch(knowledgeBaseId: string, payload: HybridSearchPayload) {
  return api.post<HybridSearchResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/hybrid-search`, payload)
}


export function buildLlmContext(knowledgeBaseId: string, payload: HybridSearchPayload) {
  return api.post<LLMContextResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/llm-context`, payload)
}
