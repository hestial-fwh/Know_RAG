import { describe, expect, it } from 'vitest'
import {
  admissionChannelEntries,
  formatCountMap,
  formatSourceList
} from './contentAdmission'

describe('content admission presentation', () => {
  it('renders deterministic source and status counts', () => {
    expect(formatCountMap({ visual_ocr: 2, caption: 3 })).toBe(
      'caption: 3，visual_ocr: 2'
    )
    expect(formatCountMap({})).toBe('-')
  })

  it('normalizes channel diagnostics for the chunk detail view', () => {
    expect(
      admissionChannelEntries({
        visual_ocr: {
          status: 'shadow_auxiliary',
          char_count: 8,
          preview: '坏 OCR'
        },
        vlm_description: {
          status: 'normal',
          char_count: 12,
          preview: '一幅行书作品'
        }
      })
    ).toEqual([
      [
        'visual_ocr',
        {
          status: 'shadow_auxiliary',
          char_count: 8,
          preview: '坏 OCR'
        }
      ],
      [
        'vlm_description',
        {
          status: 'normal',
          char_count: 12,
          preview: '一幅行书作品'
        }
      ]
    ])
  })

  it('formats admitted and shadow source lists', () => {
    expect(formatSourceList(['caption', 'vlm_description'])).toBe(
      'caption，vlm_description'
    )
    expect(formatSourceList([])).toBe('-')
  })
})
