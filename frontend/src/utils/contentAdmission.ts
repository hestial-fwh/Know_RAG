export interface ContentAdmissionChannel {
  status: string
  char_count: number
  preview: string
}

export function formatCountMap(value?: Record<string, number>) {
  if (!value) return '-'
  const entries = Object.entries(value)
    .filter(([, count]) => Number.isFinite(count))
    .sort(([left], [right]) => left.localeCompare(right))
  return entries.length
    ? entries.map(([key, count]) => `${key}: ${count}`).join('，')
    : '-'
}

export function admissionChannelEntries(value: unknown): Array<[string, ContentAdmissionChannel]> {
  if (!value || typeof value !== 'object') return []
  return Object.entries(value as Record<string, unknown>)
    .filter(([, detail]) => Boolean(detail) && typeof detail === 'object')
    .map(([source, detail]) => {
      const raw = detail as Record<string, unknown>
      return [
        source,
        {
          status: typeof raw.status === 'string' ? raw.status : 'unknown',
          char_count: typeof raw.char_count === 'number' ? raw.char_count : 0,
          preview: typeof raw.preview === 'string' ? raw.preview : ''
        }
      ] as [string, ContentAdmissionChannel]
    })
    .sort(([left], [right]) => left.localeCompare(right))
}

export function formatSourceList(value: unknown) {
  return Array.isArray(value) && value.length ? value.join('，') : '-'
}
