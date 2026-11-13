import { describe, expect, it } from 'vitest'
import { layoutGraph, graphEdgePath } from './knowledgeGraphLayout'

describe('知识图谱布局', () => {
  it('相同输入产生稳定且有界的位置', () => {
    const ids = Array.from({ length: 150 }, (_, i) => String(i))
    const first = layoutGraph(ids, [{ source: '0', target: '1' }])
    expect(first).toEqual(layoutGraph(ids, [{ source: '0', target: '1' }]))
    for (const point of first.values()) {
      expect(point.x).toBeGreaterThanOrEqual(85)
      expect(point.x).toBeLessThanOrEqual(915)
      expect(Number.isFinite(point.y)).toBe(true)
    }
  })
  it('空图、孤立节点和缺失端点都能正常布局', () => {
    expect(layoutGraph([], []).size).toBe(0)
    expect(layoutGraph(['a'], [{ source: 'a', target: 'missing' }]).size).toBe(1)
  })
  it('连线接到两个节点圆心且自环闭合', () => {
    expect(graphEdgePath({ x: 0, y: 0 }, { x: 100, y: 0 })).toBe('M 0 0 L 100 0')
    expect(graphEdgePath({ x: 20, y: 35 }, { x: -10, y: 90 })).toBe('M 20 35 L -10 90')
    expect(graphEdgePath({ x: 50, y: 50 }, { x: 50, y: 50 })).toContain(' C ')
    expect(graphEdgePath({ x: 50, y: 50 }, { x: 50, y: 50 })).toMatch(/^M 50 50 C .*50 50$/)
  })
})
