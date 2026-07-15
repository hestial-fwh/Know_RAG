export type MediaInfo = Record<string, unknown>

const PASSTHROUGH_SCHEMES = ['http://', 'https://', 'data:', 'blob:']

export function normalizeFileUrl(value?: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return ''
  let url = value.trim()
  if (PASSTHROUGH_SCHEMES.some((prefix) => url.startsWith(prefix))) return url
  if (url.startsWith('/files//files/')) url = url.replace('/files//files/', '/files/')
  if (url.startsWith('/files/')) return url
  if (url.startsWith('files/')) return `/${url}`
  if (url.startsWith('/data/files/')) return `/files/${url.replace('/data/files/', '')}`
  if (url.startsWith('minio://')) {
    const [, ...pathParts] = url.replace('minio://', '').split('/')
    return pathParts.length ? `/files/${pathParts.join('/')}` : ''
  }
  return `/files/${url.replace(/^\/+/, '')}`
}

function nestedInfo(value?: MediaInfo): MediaInfo | undefined {
  return value?.image_info && typeof value.image_info === 'object'
    ? value.image_info as MediaInfo
    : undefined
}

export function resolveImageUrl(payload?: MediaInfo): string {
  if (!payload) return ''
  const info = nestedInfo(payload) || payload
  const nested = nestedInfo(info)
  const candidates = [
    payload.image_url,
    payload.url,
    info.url,
    info.image_url,
    nested?.url,
    nested?.image_url,
    info.relative_path,
    nested?.relative_path,
    info.path,
    info.image_path,
    nested?.path,
    nested?.image_path,
  ]
  for (const candidate of candidates) {
    const url = normalizeFileUrl(candidate)
    if (url) return url
  }

  const base64 = payload.image_base64
    || payload.base64
    || info.image_base64
    || info.base64
    || nested?.image_base64
    || nested?.base64
  if (typeof base64 !== 'string' || !base64) return ''
  return base64.startsWith('data:') ? base64 : `data:image/png;base64,${base64}`
}
