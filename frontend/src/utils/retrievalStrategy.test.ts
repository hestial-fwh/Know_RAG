import { describe, expect, it } from 'vitest'
import { strategyFromSelection } from './retrievalStrategy'

describe('检索策略选择', () => {
  it('将Global和DRIFT映射到graph并保留预算', () => {
    for (const method of ['global', 'drift']) {
      expect(strategyFromSelection(method, 'error', { max_searches: 2 })).toEqual({
        family: 'tiered', tier: 'graph', graph_method: method, fallback_policy: 'error', budget: { max_searches: 2 }
      })
    }
  })
  it('自动选择留在tiered家族', () => {
    expect(strategyFromSelection('auto')).toMatchObject({ family: 'tiered', tier: 'auto' })
    expect(strategyFromSelection('event_graph')).toMatchObject({ family: 'event_graph' })
  })
  it('拒绝将继承或未知选项直接写入策略', () => {
    expect(() => strategyFromSelection('inherit')).toThrow()
  })
})

describe('融合检索策略', () => {
  it('fusion 默认使用两级 RRF 与事件独立计分', () => {
    expect(strategyFromSelection('fusion')).toMatchObject({
      family: 'tiered', tier: 'fusion',
      fusion: { rank_mode: 'rrf', event_policy: 'independent' },
    })
  })
  it('fusion 可显式开启背景、事件线与特征模式', () => {
    expect(strategyFromSelection('fusion', 'error', { relation_quota: 30 }, {
      rank_mode: 'feature', include_global: true, include_event_timeline: true,
    })).toEqual({
      family: 'tiered', tier: 'fusion',
      fusion: {
        rank_mode: 'feature', event_policy: 'independent',
        include_global: true, include_event_timeline: true,
      },
      fallback_policy: 'error', budget: { relation_quota: 30 },
    })
  })
})
