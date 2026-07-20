import type { HybridSearchResult } from '../api/retrieval'

export type HitDocument = {
  id: string
  title: string
  fileName: string
  fileHash: string
}

export function collectUniqueDocuments(results: HybridSearchResult[]): HitDocument[] {
  const seen = new Map<string, HitDocument>()

  for (const item of results) {
    const documentId = String(item.document_id || '').trim()
    if (!documentId) continue

    const fileHash = String(item.file_hash || '').trim().toLocaleLowerCase()
    const key = fileHash ? `hash:${fileHash}` : `document:${documentId}`
    const title = (item.knowledge_title || item.title || '').trim()
    const fileName = (item.knowledge_filename || item.file_name || '').trim()
    const existing = seen.get(key)

    if (!existing) {
      seen.set(key, {
        id: documentId,
        title: title || fileName || '未命名文档',
        fileName,
        fileHash,
      })
      continue
    }

    if (existing.title === '未命名文档' && (title || fileName)) {
      existing.title = title || fileName
    }
    if (!existing.fileName && fileName) {
      existing.fileName = fileName
    }
  }

  return Array.from(seen.values())
}

export function showDocumentFileName(doc: HitDocument): boolean {
  if (!doc.fileName) return false

  const normalizedTitle = doc.title.toLocaleLowerCase()
  const normalizedFileName = doc.fileName.toLocaleLowerCase()
  const extensionIndex = normalizedFileName.lastIndexOf('.')
  const fileStem = extensionIndex > 0
    ? normalizedFileName.slice(0, extensionIndex)
    : normalizedFileName

  return normalizedTitle !== normalizedFileName && normalizedTitle !== fileStem
}
