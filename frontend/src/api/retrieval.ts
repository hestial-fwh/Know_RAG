import { api } from './client'

export interface FusionSettings {
  rank_mode?: 'rrf' | 'feature'
  event_policy?: 'independent' | 'fused' | 'off'
  include_global?: boolean
  include_event_timeline?: boolean
  event_reserved_slots?: number
  relation_reserved_slots?: number
  event_relevance_threshold?: number
  graph_feature_weight?: number
  event_feature_weight?: number
}

export type RetrievalStrategy =
  | { family: 'tiered'; tier: 'semantic' | 'hybrid' | 'graph' | 'fusion' | 'auto'; graph_method?: 'local' | 'global' | 'drift'; fusion?: FusionSettings; fallback_policy?: 'hybrid' | 'error'; budget?: Record<string, number> }
  | { family: 'event_graph'; fallback_policy?: 'hybrid' | 'error'; budget?: Record<string, number> }

export interface RetrievalProfile {
  index_model?: { provider: 'ollama'; model_name: string; base_url: string; think?: boolean | null; reference_mode?: 'indices' | 'names' } | null
  family_scope?: 'a' | 'b'
  build_indexes?: Array<'entities' | 'relations' | 'events' | 'communities'>
  community?: {
    provider: 'native' | 'microsoft_graphrag_2_7_0'
    max_communities?: number
    max_members?: number
    max_source_chars?: number
    level?: number
  }
  version: 'legacy_v1' | 'new_profile_v2'
  strategy?: RetrievalStrategy
}

export interface RetrievalDiagnostics {
  contract: string
  requested_strategy: Record<string, unknown>
  executed_strategy: Record<string, unknown>
  degraded: boolean
  degradation_reason: string | null
  channels: string[]
  elapsed_ms: number
  result_count: number
  routing?: {
    provider: string
    family_scope?: 'a' | 'b'
    intent: string
    reason: string
    chosen_strategy: RetrievalStrategy
    coverage_incomplete: boolean
  }
  drift?: {
    provider: string
    graph_revision: string
    model_calls: number
    searches: number
    rounds: number
    candidates_consumed: number
    stop_reason: string
    coverage_incomplete: boolean
    trace: Array<{ round: number; query: string; derived_query: boolean; channels: string[] }>
  }
  candidate_budget?: {
    basis: 'per_channel'
    candidate_k: number
    final_top_k: number
    raw_counts: { vector: number; keyword: number }
    unique_counts: { vector: number; keyword: number }
    candidate_counts: { vector: number; keyword: number }
    base_fused_count: number
    merged_count_before_rerank: number
    selected_count: number
  }
  association?: {
    provider: string
    coverage_incomplete?: boolean
    budget_truncated?: boolean
    paths: Array<{ support_chunk_ids: string[]; hop: number; steps: Array<Record<string, unknown>> }>
  }
  fusion?: {
    mode: { rank_mode: 'rrf' | 'feature'; event_policy: 'independent' | 'fused' | 'off' }
    executed: string[]
    channel_status: Record<string, string>
    candidate_counts: Record<string, number>
    structure_paths?: Record<string, number>
    weights?: Record<string, number>
    quota?: Record<string, number>
    elapsed_ms?: Record<string, number>
    coverage_incomplete: boolean
    event?: {
      policy: string
      seeds: number
      events_scored: number
      direct_overlap_count: number
      top_event_relevance: number
      reserved_slots_used: number
    }
  }
  global_background?: {
    executed: boolean
    status: string
    graph_revision?: string | null
    model_calls?: number
    coverage_incomplete: boolean
  }
  global?: {
    provider: string
    clustering_provider: string
    graph_revision: string
    model_calls: number
    coverage_incomplete: boolean
    withheld_summaries?: number
    derived_summaries: Array<{
      text: string
      derived_summary: true
      context_complete?: boolean
      evidence: Array<{ chunk_id: string; quote: string }>
    }>
  }
}

export interface HybridSearchPayload {
  return_mode?: string
  strategy?: RetrievalStrategy
  retrieval_mode?: string
  rerank_enabled?: boolean
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

export interface SourceLocation {
  page_no?: number | null
  page_range?: string
  source_pages?: number[]
  source_spans?: Array<{
    source_node_id: string
    page_no: number | null
    page_label?: string
    start: number
    end: number
    coordinate_space: string
    bbox?: number[] | null
    bbox_precision?: string | null
    bbox_coordinate_space?: string | null
  }>
  page_location_status?: 'resolved' | 'partial' | 'unavailable' | 'legacy_page'
}

export interface HybridSearchResult extends SourceLocation {
  id?: string
  chunk_id: string
  /** 同一精确来源的原始切片 ID，关联路径归一化后仍可追溯。 */
  source_chunk_ids?: string[]
  document_id: string
  knowledge_id?: string
  knowledge_base_id: string
  knowledge_title?: string
  title: string
  file_name: string
  file_hash?: string
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

export interface ParentContext extends SourceLocation {
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

export interface ParentChildSummary extends SourceLocation {
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
    retrieval?: RetrievalDiagnostics
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

export interface LLMContextImage extends SourceLocation {
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
  retrieval_results?: HybridSearchResponse
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
    retrieval?: RetrievalDiagnostics
  }
  retrieval_meta?: Record<string, unknown>
}

export function hybridSearch(knowledgeBaseId: string, payload: HybridSearchPayload) {
  return api.post<HybridSearchResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/hybrid-search`, payload)
}


export function buildLlmContext(knowledgeBaseId: string, payload: HybridSearchPayload) {
  return api.post<LLMContextResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/llm-context`, payload)
}
