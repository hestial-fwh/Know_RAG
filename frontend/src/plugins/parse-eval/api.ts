import { api } from '../../api/client'
import type { ChunkItem } from '../../api/chunks'

export interface ParseEvalTemplate {
  id: string
  name: string
  mode: string
  description: string
  file_types?: string[]
  needs_gt?: boolean
  defaults?: Record<string, unknown>
}

export interface ParseEvalRun {
  id: string
  evaluation_id?: string
  document_id: string
  knowledge_base_id: string
  mode: string
  template_id: string
  status: string
  score: number
  score_10?: number
  risk_level: string
  recommendation: string
  summary: Record<string, unknown>
  scores: Record<string, unknown>
  options: Record<string, unknown>
  report_markdown: string
  item_count?: number
  created_at: string
  updated_at: string
  finished_at?: string | null
}

export interface ParseEvalItem {
  id: string
  evaluation_id: string
  document_id: string
  target_type: string
  target_id: string
  page_no?: number | null
  chunk_index?: number | null
  score?: number | null
  issue_level: string
  issue_type: string
  message: string
  evidence: Record<string, unknown>
  suggestion: string
  chunk?: ChunkItem
  created_at: string
}

export interface ParseEvalItemsResponse {
  evaluation_id: string
  page: number
  page_size: number
  total: number
  items: ParseEvalItem[]
}

export interface ParseEvalReport {
  evaluation_id: string
  markdown: string
}

export function listParseEvalTemplates() {
  return api.get<{ items: ParseEvalTemplate[] }>('/api/v1/parse-eval/templates')
}

export function runParseEvaluation(
  documentId: string,
  payload: { mode: string; template_id: string; options?: Record<string, unknown> }
) {
  return api.post<ParseEvalRun>(`/api/v1/parse-eval/documents/${documentId}/run`, {
    mode: payload.mode,
    template_id: payload.template_id,
    options: payload.options || {}
  })
}

export function getLatestParseEvaluation(documentId: string) {
  return api.get<ParseEvalRun | null>(`/api/v1/parse-eval/documents/${documentId}/latest`)
}

export function getParseEvaluationRun(runId: string) {
  return api.get<ParseEvalRun>(`/api/v1/parse-eval/runs/${runId}`)
}

export function listParseEvaluationItems(
  runId: string,
  params: { page?: number; page_size?: number; level?: string; issue_type?: string; target_type?: string } = {}
) {
  const query = new URLSearchParams({
    page: String(params.page || 1),
    page_size: String(params.page_size || 50),
    level: params.level || 'all',
    issue_type: params.issue_type || 'all',
    target_type: params.target_type || 'all'
  })
  return api.get<ParseEvalItemsResponse>(`/api/v1/parse-eval/runs/${runId}/items?${query.toString()}`)
}

export function getParseEvaluationReport(runId: string) {
  return api.get<ParseEvalReport>(`/api/v1/parse-eval/runs/${runId}/report`)
}
