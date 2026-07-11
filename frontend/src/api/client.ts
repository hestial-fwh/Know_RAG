const API_BASE = import.meta.env.VITE_API_BASE || ''

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

export interface ApiResponse<T> {
  success: boolean
  data: T
  message: string
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('know_rag_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const isFormData = body instanceof FormData
  const headers: Record<string, string> = authHeaders()
  if (!isFormData) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body)
  })
  const payload = (await response.json()) as ApiResponse<T>
  if (!response.ok) {
    throw new Error(payload.message || `API request failed: ${response.status}`)
  }
  if (!payload.success) {
    throw new Error(payload.message || 'API request failed')
  }
  return payload.data
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path)
}
