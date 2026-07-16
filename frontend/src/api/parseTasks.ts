import { api } from './client'

export interface ParseTask {
  id: string
  task_id?: string
  document_id: string
  knowledge_base_id: string
  task_type: string
  status: 'queued' | 'running' | 'completed' | 'failed' | string
  current_stage: string
  current_stage_label?: string
  progress: number
  total_steps: number
  completed_steps: number
  stats: Record<string, unknown>
  error_message: string
  started_at?: string | null
  finished_at?: string | null
  created_at: string
  updated_at: string
  event_count?: number
}

export interface ParseTaskEvent {
  id: string
  task_id: string
  document_id: string
  seq_no: number
  event_type: string
  stage: string
  stage_label?: string
  message: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ParseTaskEventsResponse {
  task: ParseTask
  items: ParseTaskEvent[]
}

export function getParseTask(taskId: string) {
  return api.get<ParseTask>(`/api/v1/parse-tasks/${taskId}`)
}

export function getParseTaskEvents(taskId: string, afterSeq = 0, limit = 200) {
  const params = new URLSearchParams({ after_seq: String(afterSeq), limit: String(limit) })
  return api.get<ParseTaskEventsResponse>(`/api/v1/parse-tasks/${taskId}/events?${params.toString()}`)
}

export function getLatestParseTask(documentId: string) {
  return api.get<ParseTask | null>(`/api/v1/parse-tasks/documents/${documentId}/latest`)
}
