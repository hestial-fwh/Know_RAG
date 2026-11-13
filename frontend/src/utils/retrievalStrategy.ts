import type { FusionSettings, RetrievalStrategy } from '../api/retrieval'

/** 设置页、调试页与同题对比共用映射，避免Global/DRIFT被序列化成非法tier。 */
export function strategyFromSelection(
  selection: string,
  fallback: 'hybrid' | 'error' = 'hybrid',
  budget?: Record<string, number>,
  fusion?: FusionSettings,
): RetrievalStrategy {
  if (selection === 'event_graph') return { family: 'event_graph', fallback_policy: fallback, ...(budget ? { budget } : {}) }
  if (selection === 'fusion') {
    return {
      family: 'tiered',
      tier: 'fusion',
      fusion: {
        rank_mode: 'rrf',
        event_policy: 'independent',
        include_global: false,
        include_event_timeline: false,
        ...(fusion || {}),
      },
      fallback_policy: fallback,
      ...(budget ? { budget } : {}),
    }
  }
  if (['graph', 'global', 'drift'].includes(selection)) {
    const graph_method = selection === 'graph' ? 'local' : selection as 'global' | 'drift'
    return { family: 'tiered', tier: 'graph', graph_method, fallback_policy: fallback, ...(budget ? { budget } : {}) }
  }
  if (selection === 'auto' || selection === 'semantic' || selection === 'hybrid') {
    return { family: 'tiered', tier: selection, fallback_policy: fallback, ...(budget ? { budget } : {}) }
  }
  throw new Error('请选择有效的检索策略')
}