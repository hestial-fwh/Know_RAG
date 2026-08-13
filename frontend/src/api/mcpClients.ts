import { api } from './client'

export type McpClientStatus = 'enabled' | 'disabled' | 'expired'

export interface McpClient {
  id: string
  name: string
  client_id: string
  is_enabled: boolean
  expires_at: string | null
  scopes: string[]
  description: string
  last_used_at: string | null
  created_at: string | null
  updated_at: string | null
  status: McpClientStatus
  is_expired: boolean
}

export interface McpClientWithSecret extends McpClient {
  client_secret: string
}

export interface McpClientListResult {
  items: McpClient[]
  total: number
  page: number
  page_size: number
}

export interface McpClientCreatePayload {
  name: string
  description?: string
  scopes: string[]
  expires_in_days: number | null
}

export interface McpClientUpdatePayload {
  name?: string
  description?: string
  scopes?: string[]
  expires_in_days?: number | null
}

export const MCP_SCOPE_OPTIONS = [
  { value: 'know_rag_list_knowledge_bases', label: 'list_knowledge_bases', hint: '列出知识库及其文档/切片数量' },
  { value: 'know_rag_search_context', label: 'search_context', hint: '在知识库中检索并返回 LLM 上下文' }
] as const

export const listMcpClients = () => api.get<McpClientListResult>('/api/v1/admin/mcp-clients')
export const getMcpClient = (id: string) => api.get<McpClient>(`/api/v1/admin/mcp-clients/${id}`)
export const createMcpClient = (payload: McpClientCreatePayload) =>
  api.post<McpClientWithSecret>('/api/v1/admin/mcp-clients', payload)
export const updateMcpClient = (id: string, payload: McpClientUpdatePayload) =>
  api.patch<McpClient>(`/api/v1/admin/mcp-clients/${id}`, payload)
export const resetMcpClientSecret = (id: string) =>
  api.post<McpClientWithSecret>(`/api/v1/admin/mcp-clients/${id}/reset-secret`)
export const enableMcpClient = (id: string) =>
  api.post<McpClient>(`/api/v1/admin/mcp-clients/${id}/enable`)
export const disableMcpClient = (id: string) =>
  api.post<McpClient>(`/api/v1/admin/mcp-clients/${id}/disable`)
export const deleteMcpClient = (id: string) =>
  api.delete<{ id: string }>(`/api/v1/admin/mcp-clients/${id}`)
