import { describe, expect, it } from 'vitest'
import type { HybridSearchResult } from '../api/retrieval'
import {
  collectUniqueDocuments,
  showDocumentFileName,
  type HitDocument,
} from './retrievalDocuments'

function result(overrides: Partial<HybridSearchResult>): HybridSearchResult {
  return {
    chunk_id: 'chunk-1',
    document_id: 'document-1',
    knowledge_base_id: 'kb-1',
    title: 'Document',
    file_name: 'Document.pdf',
    chunk_index: 0,
    score: 1,
    vector_score: 1,
    keyword_score: 0,
    match_type: 'vector',
    ...overrides,
  }
}

describe('collectUniqueDocuments', () => {
  it('collapses different document ids with the same content hash', () => {
    const documents = collectUniqueDocuments([
      result({ document_id: 'document-vector', file_hash: 'ABC123' }),
      result({ document_id: 'document-keyword', chunk_id: 'chunk-2', file_hash: 'abc123' }),
    ])

    expect(documents).toHaveLength(1)
    expect(documents[0].id).toBe('document-vector')
  })

  it('falls back to document id when a hash is unavailable', () => {
    const documents = collectUniqueDocuments([
      result({ document_id: 'document-1', file_hash: undefined }),
      result({ document_id: 'document-1', chunk_id: 'chunk-2', file_hash: undefined }),
    ])

    expect(documents).toHaveLength(1)
  })
})

describe('showDocumentFileName', () => {
  it('hides a filename when its stem is already the title', () => {
    const document: HitDocument = {
      id: 'document-1',
      title: 'db278_20260428104424_nrqnd095db',
      fileName: 'db278_20260428104424_nrqnd095db.pdf',
      fileHash: 'abc123',
    }

    expect(showDocumentFileName(document)).toBe(false)
  })
})
