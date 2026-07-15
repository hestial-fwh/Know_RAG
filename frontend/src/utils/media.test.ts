import { describe, expect, it } from 'vitest'

import { normalizeFileUrl, resolveImageUrl } from './media'

describe('media URL compatibility', () => {
  it('normalizes local and MinIO paths', () => {
    expect(normalizeFileUrl('/files//files/kb/doc/chart.png')).toBe('/files/kb/doc/chart.png')
    expect(normalizeFileUrl('/data/files/kb/doc/chart.png')).toBe('/files/kb/doc/chart.png')
    expect(normalizeFileUrl('minio://know-rag/kb/doc/chart.png')).toBe('/files/kb/doc/chart.png')
  })

  it('supports canonical and legacy image fields', () => {
    expect(resolveImageUrl({ image_url: '/files/kb/new.png' })).toBe('/files/kb/new.png')
    expect(resolveImageUrl({ url: '/files/kb/legacy.png' })).toBe('/files/kb/legacy.png')
    expect(resolveImageUrl({ image_info: { image_path: 'kb/nested.png' } })).toBe('/files/kb/nested.png')
  })

  it('uses base64 only when no file URL exists', () => {
    expect(resolveImageUrl({ image_base64: 'aGVsbG8=' })).toBe('data:image/png;base64,aGVsbG8=')
    expect(resolveImageUrl({ base64: 'data:image/webp;base64,eA==' })).toBe('data:image/webp;base64,eA==')
  })
})
