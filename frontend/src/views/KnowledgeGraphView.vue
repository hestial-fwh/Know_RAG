<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getKnowledgeBase, getKnowledgeGraph, type KnowledgeGraph, type KnowledgeGraphNode, type KnowledgeGraphEdge } from '../api/knowledgeBases'
import { layoutGraph, graphEdgePath } from '../utils/knowledgeGraphLayout'

type GraphItem = KnowledgeGraphNode | KnowledgeGraphEdge
const route = useRoute()
const router = useRouter()
const kbId = computed(() => String(route.params.id))
const name = ref('')
const query = ref('')
const focus = ref<string>()
const limit = ref(80)
const graph = ref<KnowledgeGraph | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const zoom = ref(1)
const center = ref({ x: 500, y: 340 })
const view = ref<'graph' | 'list'>('graph')
const filterOpen = ref(false)
const entityType = ref('all')
const sourceId = ref('')
const showAllLabels = ref(false)
const selected = ref<GraphItem | null>(null)
const showEvidence = ref(false)
const hovered = ref<string | null>(null)
const canvas = ref<HTMLElement | null>(null)
const fullscreen = ref(false)
let requestVersion = 0
let drag: { x: number; y: number; center: { x: number; y: number }; width: number; height: number } | null = null
let moved = false
const icons = {
  search: 'm21 21-4.5-4.5M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z',
  filter: 'M3 4h18l-7 8v7l-4 2V12Z',
  refresh: 'M20 7v5h-5M4 17v-5h5M5.2 7a8 8 0 0 1 13-2L20 7M4 17l1.8 2a8 8 0 0 0 13-2',
  fullscreen: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
  fit: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M9 9h6v6H9Z',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  network: 'M12 5v5m0 0H5v7m7-7h7v7M9 2h6v4H9ZM2 17h6v4H2Zm14 0h6v4h-6Z',
  close: 'm6 6 12 12M6 18 18 6',
  arrow: 'M7 17 17 7M7 7h10v10'
}
const sources = computed(() => {
  const result = new Map<string, string>()
  for (const item of [...(graph.value?.nodes || []), ...(graph.value?.edges || [])]) {
    for (const proof of item.evidence) result.set(proof.document_id, proof.document_title)
  }
  return Array.from(result, ([id, title]) => ({ id, title }))
})
// 来源筛选只作用于已加载子图，切换后同步清除选择，防止详情与当前可见范围不一致。
const nodes = computed(() => (graph.value?.nodes || []).filter(node =>
  (entityType.value === 'all' || node.kind === entityType.value)
  && (!sourceId.value || node.evidence.some(proof => proof.document_id === sourceId.value))))
const edges = computed(() => {
  const ids = new Set(nodes.value.map(node => node.id))
  return (graph.value?.edges || []).filter(edge => ids.has(edge.source) && ids.has(edge.target)
    && (!sourceId.value || edge.evidence.some(proof => proof.document_id === sourceId.value)))
})
const positions = computed(() => layoutGraph(nodes.value.map(node => node.id), edges.value))
const viewBox = computed(() => `${center.value.x - 500 / zoom.value} ${center.value.y - 340 / zoom.value} ${1000 / zoom.value} ${680 / zoom.value}`)
const selectedNode = computed(() => selected.value && (selected.value.kind === 'entity' || selected.value.kind === 'event') ? selected.value as KnowledgeGraphNode : null)
const adjacent = computed(() => {
  const ids = new Set<string>()
  if (!selected.value) return ids
  if (selectedNode.value) {
    ids.add(selectedNode.value.id)
    for (const edge of edges.value) if (edge.source === selectedNode.value.id || edge.target === selectedNode.value.id) {
      ids.add(edge.source)
      ids.add(edge.target)
    }
  } else {
    const edge = selected.value as KnowledgeGraphEdge
    ids.add(edge.source)
    ids.add(edge.target)
  }
  return ids
})
const importantLabels = computed(() => {
  const degree = new Map(nodes.value.map(node => [node.id, 0]))
  for (const edge of edges.value) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1)
  }
  const ranked = [...nodes.value].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || a.id.localeCompare(b.id))
  return new Set(ranked.slice(0, 12).map(node => node.id))
})
// 高连接节点也只展示少量邻接标签，完整名称仍可通过悬停、列表和详情查看。
const neighborLabels = computed(() => new Set([...adjacent.value].slice(0, 10)))
const relationLabels = computed(() => new Set(edges.value.filter(highlighted).slice(0, 6).map(edge => edge.id)))
const stateText = computed(() => ({ disabled: '尚未构建知识图谱', not_ready: '图谱索引尚未就绪', empty: '没有匹配的图谱数据', ready: '当前子图' }[graph.value?.meta.state || ''] || '正在加载图谱'))
const selectedSources = computed(() => new Set(selected.value?.evidence.map(proof => proof.document_id)).size)
const selectedKind = computed(() => selected.value
  ? { entity: '实体', event: '事件', relation: '抽取关系', participation: '事件参与' }[selected.value.kind]
  : '')
const activeFilter = computed(() => entityType.value !== 'all' || showAllLabels.value)

function highlighted(edge: KnowledgeGraphEdge) {
  return selectedNode.value ? edge.source === selectedNode.value.id || edge.target === selectedNode.value.id : selected.value?.id === edge.id
}
function labelVisible(node: KnowledgeGraphNode) {
  return showAllLabels.value || hovered.value === node.id || selected.value?.id === node.id
    || (selected.value ? neighborLabels.value.has(node.id) : importantLabels.value.has(node.id))
}
function shortLabel(label: string) { return label.length > 12 ? label.slice(0, 12) + '…' : label }
function labelWidth(label: string) {
  const width = Array.from(shortLabel(label)).reduce((total, char) => total + ((char.codePointAt(0) || 0) > 255 ? 12 : 7), 18)
  return Math.min(184, width)
}
function labelX(node: KnowledgeGraphNode) { return (positions.value.get(node.id)?.x || 0) > 760 ? -labelWidth(node.label) - 14 : 14 }
function choose(item: GraphItem) {
  selected.value = item
  showEvidence.value = false
}
function resetView() { zoom.value = 1; center.value = { x: 500, y: 340 } }
function zoomBy(factor: number) { zoom.value = Math.max(.5, Math.min(4, zoom.value * factor)) }
function startPan(event: PointerEvent) {
  moved = false
  if (event.button !== 0 || (event.target as Element).closest('[data-graph-item]')) return
  const svg = event.currentTarget as SVGSVGElement
  const bounds = svg.getBoundingClientRect()
  drag = { x: event.clientX, y: event.clientY, center: { ...center.value }, width: bounds.width, height: bounds.height }
  moved = false
  svg.setPointerCapture(event.pointerId)
}
function pan(event: PointerEvent) {
  if (!drag) return
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y
  if (Math.abs(dx) + Math.abs(dy) > 3) moved = true
  // SVG采用等比缩放，横纵位移共享同一比例，宽屏与窄屏拖动保持跟手。
  const scale = Math.min(drag.width / (1000 / zoom.value), drag.height / (680 / zoom.value))
  center.value = { x: drag.center.x - dx / scale, y: drag.center.y - dy / scale }
}
function endPan(event: PointerEvent) {
  drag = null
  const svg = event.currentTarget as SVGSVGElement
  if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId)
}
function clearCanvasSelection() {
  if (!moved) { selected.value = null; showEvidence.value = false }
  moved = false
}
function syncFullscreen() { fullscreen.value = document.fullscreenElement === canvas.value }
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement === canvas.value) await document.exitFullscreen()
    else await canvas.value?.requestFullscreen()
  } catch { notice.value = '当前浏览器无法进入全屏，可使用缩放控件浏览。' }
}
async function load(reset = false) {
  if (reset) focus.value = undefined
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  selected.value = null
  showEvidence.value = false
  graph.value = null
  sourceId.value = ''
  try {
    const [kb, data] = await Promise.all([getKnowledgeBase(kbId.value), getKnowledgeGraph(kbId.value, { q: query.value, focus: focus.value, limit: limit.value })])
    if (version !== requestVersion) return
    name.value = kb.name
    graph.value = data
    resetView()
  } catch (err) {
    if (version === requestVersion) error.value = err instanceof Error ? err.message : '加载图谱失败'
  } finally { if (version === requestVersion) loading.value = false }
}
function expand() {
  if (selectedNode.value?.kind !== 'entity') return
  focus.value = selectedNode.value.id
  query.value = ''
  void load()
}
watch([entityType, sourceId], () => { selected.value = null; showEvidence.value = false })
watch(kbId, () => { query.value = ''; focus.value = undefined; void load() }, { immediate: true })
onMounted(() => document.addEventListener('fullscreenchange', syncFullscreen))
onBeforeUnmount(() => { requestVersion++; document.removeEventListener('fullscreenchange', syncFullscreen) })
</script>

<template>
  <section class="knowledge-graph" :aria-busy="loading" @keydown.esc="selected = null; filterOpen = false">
    <header class="graph-heading">
      <div class="breadcrumb"><button type="button" @click="router.push(`/knowledge-bases/${kbId}`)">{{ name || '知识库' }}</button><span>/</span><h1>知识图谱</h1></div>
      <div class="view-switch" role="tablist" aria-label="图谱视图">
        <button id="graph-list-tab" type="button" role="tab" :aria-selected="view === 'list'" aria-controls="graph-list-panel" :class="{ active: view === 'list' }" @click="view = 'list'"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.list" /></svg>列表</button>
        <button id="graph-2d-tab" type="button" role="tab" :aria-selected="view === 'graph'" aria-controls="graph-2d-panel" :class="{ active: view === 'graph' }" @click="view = 'graph'"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.network" /></svg>2D 图谱</button>
      </div>
    </header>
    <div ref="canvas" class="graph-stage" :class="{ 'is-list': view === 'list' }">
      <div class="graph-toolbar">
        <form class="search-box" @submit.prevent="load(true)"><button aria-label="搜索实体" :disabled="loading"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.search" /></svg></button><input v-model="query" aria-label="搜索实体名称" maxlength="100" placeholder="搜索实体…" /></form>
        <div class="toolbar-actions">
          <select v-model="sourceId" aria-label="筛选当前子图来源"><option value="">全部来源</option><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.title }}</option></select>
          <select v-model.number="limit" aria-label="节点展示上限" :disabled="loading" @change="load()"><option :value="40">40 节点</option><option :value="80">80 节点</option><option :value="150">150 节点</option></select>
          <button type="button" class="icon-tool" :class="{ active: activeFilter || filterOpen }" aria-label="图谱筛选" :aria-expanded="filterOpen" aria-controls="graph-filters" @click="filterOpen = !filterOpen"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.filter" /></svg></button>
          <button type="button" class="icon-tool" aria-label="刷新图谱" :disabled="loading" @click="load()"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.refresh" /></svg></button>
          <button type="button" class="icon-tool fullscreen-tool" :aria-label="fullscreen ? '退出全屏' : '全屏图谱'" @click="toggleFullscreen"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.fullscreen" /></svg></button>
        </div>
      </div>
      <div v-if="filterOpen" id="graph-filters" class="filter-popover glass-panel">
        <label>节点类型<select v-model="entityType"><option value="all">实体与事件</option><option value="entity">仅实体</option><option value="event">仅事件</option></select></label>
        <label class="label-check"><input v-model="showAllLabels" type="checkbox" />显示全部标签</label>
        <button type="button" class="text-action" @click="entityType = 'all'; sourceId = ''; showAllLabels = false">重置筛选</button>
      </div>
      <div v-if="graph && view === 'graph'" class="graph-legend glass-panel" role="status">
        <div><span><i class="entity-dot" />实体</span><span><i class="event-dot" />事件</span></div>
        <small>{{ nodes.filter(node => node.kind === 'entity').length }} 实体 · {{ nodes.filter(node => node.kind === 'event').length }} 事件 · {{ edges.length }} 关系</small>
      </div>
      <div v-if="loading || error || !nodes.length" class="canvas-message" :role="error ? 'alert' : 'status'">
        <span v-if="loading" class="loading-ring" />
        <h2>{{ loading ? '正在加载图谱' : error ? '图谱加载失败' : graph?.nodes.length ? '没有匹配的节点' : stateText }}</h2>
        <p v-if="error">{{ error }}</p>
        <p v-else-if="!loading">{{ graph?.meta.state === 'disabled' ? '开启实体或关系索引后，在这里浏览知识之间的联系。' : '调整搜索条件，或检查知识库的索引状态。' }}</p>
        <button v-if="!loading && graph?.meta.state === 'disabled'" type="button" class="stage-action" @click="router.push(`/knowledge-bases/${kbId}/edit`)">配置图谱索引<svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.arrow" /></svg></button>
        <button v-else-if="!loading" type="button" class="stage-action" @click="query = ''; sourceId = ''; entityType = 'all'; load(true)">重新加载</button>
      </div>
      <svg v-show="view === 'graph' && nodes.length" id="graph-2d-panel" class="network-canvas" :viewBox="viewBox" role="tabpanel" aria-labelledby="graph-2d-tab" tabindex="0" @pointerdown="startPan" @pointermove="pan" @pointerup="endPan" @pointercancel="endPan" @click.self="clearCanvasSelection" @wheel.prevent="zoomBy($event.deltaY > 0 ? .9 : 1.1)" @keydown.left.prevent="center.x -= 40 / zoom" @keydown.right.prevent="center.x += 40 / zoom" @keydown.up.prevent="center.y -= 40 / zoom" @keydown.down.prevent="center.y += 40 / zoom">
        <g v-for="edge in edges" :key="edge.id" data-graph-item class="graph-edge" :class="{ participation: edge.kind === 'participation', highlighted: highlighted(edge), muted: selected && !highlighted(edge) }" role="button" tabindex="0" :aria-label="`查看关系 ${edge.label}`" @click.stop="choose(edge)" @keydown.enter.prevent="choose(edge)" @keydown.space.prevent="choose(edge)" @mouseenter="hovered = edge.id" @mouseleave="hovered = null" @focus="hovered = edge.id" @blur="hovered = null">
          <title>{{ edge.label }}</title><path class="edge-target" :d="graphEdgePath(positions.get(edge.source)!, positions.get(edge.target)!)" /><path class="edge-line" :d="graphEdgePath(positions.get(edge.source)!, positions.get(edge.target)!)" />
          <text v-if="relationLabels.has(edge.id) || hovered === edge.id" :x="(positions.get(edge.source)!.x + positions.get(edge.target)!.x) / 2" :y="(positions.get(edge.source)!.y + positions.get(edge.target)!.y) / 2 - 9">{{ shortLabel(edge.label) }}</text>
        </g>
        <g v-for="node in nodes" :key="node.id" data-graph-item class="graph-node" :class="{ event: node.kind === 'event', concept: node.entity_type === 'concept', active: selected?.id === node.id, related: adjacent.has(node.id), muted: selected && !adjacent.has(node.id) }" :transform="`translate(${positions.get(node.id)!.x}, ${positions.get(node.id)!.y})`" role="button" tabindex="0" :aria-label="`查看${node.kind === 'event' ? '事件' : '实体'} ${node.label}`" @click.stop="choose(node)" @keydown.enter.prevent="choose(node)" @keydown.space.prevent="choose(node)" @mouseenter="hovered = node.id" @mouseleave="hovered = null" @focus="hovered = node.id" @blur="hovered = null">
          <title>{{ node.label }}</title><circle class="node-target" r="18" /><circle v-if="selected?.id === node.id" class="selection-ring" r="14" /><circle class="node-dot" :r="node.kind === 'event' ? 6 : 5" />
          <g v-if="labelVisible(node)" class="node-caption"><rect :x="labelX(node)" y="-14" :width="labelWidth(node.label)" height="27" rx="6" /><text :x="labelX(node) + 9" y="4">{{ shortLabel(node.label) }}</text></g>
        </g>
      </svg>
      <div v-show="view === 'list' && nodes.length" id="graph-list-panel" class="graph-list" role="tabpanel" aria-labelledby="graph-list-tab">
        <table><thead><tr><th>名称</th><th>类型</th><th>当前来源</th><th>操作</th></tr></thead><tbody><tr v-for="node in nodes" :key="node.id" :class="{ chosen: selected?.id === node.id }"><td><button type="button" class="node-name" @click="choose(node)"><i :class="node.kind === 'event' ? 'event-dot' : 'entity-dot'" />{{ node.label }}</button></td><td>{{ node.kind === 'event' ? '事件' : '实体' }}</td><td>{{ node.evidence[0]?.document_title || '—' }}</td><td><button type="button" class="text-action" @click="choose(node); showEvidence = true">查看证据</button></td></tr></tbody></table>
      </div>
      <div v-if="view === 'graph'" class="zoom-controls glass-panel"><button type="button" aria-label="放大图谱" @click="zoomBy(1.2)">+</button><button type="button" aria-label="缩小图谱" @click="zoomBy(1 / 1.2)">−</button><button type="button" aria-label="复位视图" @click="resetView"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.fit" /></svg></button></div>
      <div class="canvas-footer"><span v-if="focus">实体邻接<button type="button" @click="query = ''; load(true)">返回概览</button></span><span v-else>{{ graph?.meta.truncated ? '当前子图 · 已达展示上限' : '当前子图' }}</span><span v-if="notice" role="status">{{ notice }}</span><span v-if="view === 'graph'">力导向</span></div>
      <aside v-if="selected" class="selection-card glass-panel" aria-live="polite" aria-label="选中项详情">
        <button type="button" class="close-selection" aria-label="关闭详情" @click="selected = null"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.close" /></svg></button>
        <h2>{{ selected.label }}</h2><span class="kind-badge">{{ selectedKind }}</span><p class="selection-counts">{{ selectedNode ? edges.filter(edge => edge.source === selectedNode!.id || edge.target === selectedNode!.id).length + ' 个关联 · ' : '' }}{{ selectedSources }} 篇来源</p>
        <div class="selection-actions"><button type="button" class="text-action" :aria-expanded="showEvidence" @click="showEvidence = !showEvidence">{{ showEvidence ? '收起证据' : '查看原文' }}<svg viewBox="0 0 24 24" aria-hidden="true"><path :d="icons.arrow" /></svg></button><button v-if="selectedNode?.kind === 'entity'" type="button" class="text-action" :disabled="loading" @click="expand">展开邻接</button></div>
        <div v-if="showEvidence" class="evidence-content">
          <p v-if="selectedNode?.scope && !selectedNode.scope.startsWith('document:')" class="scope-note">限定范围 {{ selectedNode.scope }}</p>
          <dl v-if="selected.qualifiers && Object.keys(selected.qualifiers).length" class="qualifiers"><template v-for="(value, key) in selected.qualifiers" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl>
          <article v-for="(proof, index) in selected.evidence" :key="index" class="proof"><strong>{{ proof.document_title }}</strong><small v-if="proof.page_no">第 {{ proof.page_no }} 页</small><blockquote>{{ proof.quote }}{{ proof.quote_truncated ? '…' : '' }}</blockquote><div><button type="button" class="text-action" @click="router.push(`/chunks/${proof.chunk_id}`)">原文切片</button><button type="button" class="text-action" @click="router.push(`/documents/${proof.document_id}/content`)">打开文档</button></div></article>
          <p class="evidence-note">派生知识 · 引文来自当前有效来源</p>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.knowledge-graph { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.graph-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 44px; }
.breadcrumb { display: flex; align-items: center; gap: 16px; min-width: 0; }.breadcrumb h1 { margin: 0; font-size: 18px; white-space: nowrap; }.breadcrumb > span { color: var(--c-border); }
.breadcrumb button { background: none; color: var(--t-secondary); padding: 8px 0; box-shadow: none; border: 0; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.view-switch { display: flex; padding: 3px; border: 1px solid var(--c-border); border-radius: 10px; background: #fff; }.view-switch button { display: flex; align-items: center; gap: 8px; border: 0; padding: 8px 15px; border-radius: 7px; color: var(--t-secondary); background: none; box-shadow: none; }.view-switch button.active { color: var(--c-primary); background: var(--c-primary-light); }
svg { width: 19px; height: 19px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
.graph-stage { position: relative; height: calc(100dvh - 104px); min-height: 540px; overflow: hidden; border: 1px solid var(--c-border); border-radius: 15px; color: var(--t-secondary); background: radial-gradient(circle, #dbe2e9 0.65px, transparent 0.8px) 0 0 / 28px 28px, var(--c-surface-alt); }
.graph-stage:fullscreen { height: 100dvh; border-radius: 0; }.graph-toolbar { position: absolute; z-index: 5; top: 20px; left: 20px; right: 20px; display: flex; justify-content: space-between; gap: 16px; pointer-events: none; }.graph-toolbar > * { pointer-events: auto; }
.graph-stage button, .graph-stage select, .graph-stage input { font: inherit; }.graph-stage button { box-shadow: none; transition: background .15s, color .15s; }.graph-stage button:hover:not(:disabled) { transform: none; box-shadow: none; }.graph-stage button:disabled { opacity: .45; cursor: wait; }.graph-stage :is(button, input, select, [tabindex]):focus-visible { outline: 2px solid var(--c-primary); outline-offset: 3px; }
.search-box { display: flex; width: min(300px, 32%); height: 42px; border: 1px solid var(--c-border); border-radius: 9px; background: var(--c-surface); }.search-box button { display: grid; place-items: center; padding: 0 11px; background: none; border: 0; color: var(--t-secondary); }.search-box button:hover { background: none; }.search-box input { min-width: 0; width: 100%; background: transparent; border: 0; color: var(--t-primary); outline: none; padding: 8px 9px 8px 0; }.search-box input::placeholder { color: #718096; }
.toolbar-actions { display: flex; align-items: start; gap: 8px; }.toolbar-actions select, .icon-tool { background: var(--c-surface); color: var(--t-secondary); border: 1px solid var(--c-border); border-radius: 9px; height: 42px; }.toolbar-actions select { width: auto; max-width: 180px; padding: 0 10px; }.icon-tool { width: 42px; display: grid; place-items: center; padding: 0; }.icon-tool:hover, .icon-tool.active { background: var(--c-primary-light); border-color: #a8c6b8; }
.glass-panel { background: rgba(255, 255, 255, .97); border: 1px solid var(--c-border); border-radius: 10px; box-shadow: 0 6px 24px rgba(15, 23, 42, .06); }.graph-legend { position: absolute; z-index: 3; top: 81px; left: 20px; padding: 12px 15px; pointer-events: none; }.graph-legend > div { display: flex; gap: 20px; margin-bottom: 7px; }.graph-legend span { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; }.graph-legend small { color: #64748b; font-size: 12px; }
.entity-dot, .event-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #5b86b2; box-shadow: 0 0 9px transparent; flex-shrink: 0; }.event-dot { background: #ba9149; box-shadow: 0 0 9px transparent; }
.network-canvas { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: grab; stroke: none; }.network-canvas:active { cursor: grabbing; }.graph-edge { cursor: pointer; }.graph-edge path { fill: none; }.edge-line { stroke: #9baabc; stroke-width: .85; opacity: .5; }.graph-edge.participation .edge-line { stroke-dasharray: 5 5; }.graph-edge .edge-target { stroke: transparent; stroke-width: 16; }.graph-edge.highlighted .edge-line, .graph-edge:focus .edge-line { stroke: var(--c-primary); stroke-width: 1.7; opacity: 1; }.graph-edge.muted .edge-line { opacity: .12; }.graph-edge text { text-anchor: middle; fill: var(--c-primary-dark); font-size: 12px; stroke: var(--c-surface-alt); stroke-width: 4; paint-order: stroke; }
.graph-node { cursor: pointer; --node-color: #5b86b2; }.graph-node.event { --node-color: #ba9149; }.graph-node.concept { --node-color: #9980b4; }.node-target { fill: transparent; stroke: none; }.node-dot { fill: var(--node-color); stroke: var(--node-color); stroke-width: 1; filter: none; }.selection-ring { fill: #1f7a5c12; stroke: var(--c-primary); stroke-width: 1.5; }.graph-node.active .node-dot { r: 8; }.graph-node.related .node-dot, .graph-node:hover .node-dot, .graph-node:focus .node-dot { stroke: var(--node-color); stroke-width: 2; filter: none; }.graph-node.muted { opacity: .28; }.graph-node.muted:hover, .graph-node.muted:focus { opacity: 1; }.node-caption { pointer-events: auto; }.node-caption rect { fill: #ffffffed; stroke: #dce3eb; stroke-width: .8; }.node-caption text { fill: var(--t-secondary); font-size: 12px; font-weight: 500; stroke: none; }.graph-node.event .node-caption rect { stroke: #ded2ba; }.graph-node.active .node-caption rect { stroke: var(--c-primary); stroke-width: 1.4; }
.filter-popover { position: absolute; z-index: 7; top: 74px; right: 117px; width: 230px; padding: 16px; display: grid; gap: 15px; }.filter-popover label { display: grid; gap: 8px; font-size: 13px; }.filter-popover select { border: 1px solid var(--c-border); background: var(--c-surface); color: var(--t-secondary); border-radius: 6px; padding: 8px; }.filter-popover .label-check { display: flex; align-items: center; gap: 8px; }.label-check input { width: 16px; height: 16px; accent-color: var(--c-primary); }
.zoom-controls { position: absolute; z-index: 4; bottom: 50px; left: 20px; display: grid; overflow: hidden; }.zoom-controls button { width: 40px; height: 38px; padding: 0; display: grid; place-items: center; background: none; border: 0; border-radius: 0; color: var(--t-secondary); font-size: 23px; }.zoom-controls button + button { border-top: 1px solid var(--c-border-light); }.zoom-controls button:hover { background: var(--c-surface-alt); }
.canvas-footer { position: absolute; bottom: 16px; left: 20px; right: 20px; display: flex; justify-content: space-between; gap: 10px; color: #64748b; font-size: 12px; pointer-events: none; }.canvas-footer button { pointer-events: auto; background: none; border: 0; color: var(--c-primary); padding: 0 10px; font-size: 12px; }
.selection-card { position: absolute; z-index: 6; right: 24px; bottom: 50px; width: 300px; padding: 18px; max-height: calc(100% - 140px); overflow: auto; }.selection-card h2 { font-size: 18px; color: var(--t-primary); line-height: 1.5; margin: 0 24px 9px 0; overflow-wrap: anywhere; max-height: 108px; overflow-y: auto; }.close-selection { position: absolute; right: 9px; top: 9px; border: 0; background: none; color: #64748b; padding: 6px; display: grid; place-items: center; }.kind-badge { display: inline-block; font-size: 12px; color: var(--c-primary); border: 1px solid #c5ddd3; background: var(--c-primary-light); border-radius: 5px; padding: 2px 7px; }.selection-counts { margin: 9px 0 14px; font-size: 13px; color: var(--t-secondary); }.selection-actions { border-top: 1px solid var(--c-border-light); display: flex; justify-content: space-between; padding-top: 11px; gap: 12px; }.text-action { color: var(--c-primary); background: none; border: 0; padding: 3px 0; display: inline-flex; align-items: center; gap: 7px; font-size: 13px; text-align: left; }.text-action:hover { color: var(--c-primary-hover); background: transparent; }.text-action svg { width: 15px; height: 15px; }
.evidence-content { padding-top: 12px; }.scope-note, .evidence-note { color: #64748b; font-size: 12px; overflow-wrap: anywhere; }.qualifiers { display: grid; grid-template-columns: auto 1fr; gap: 5px 10px; font-size: 12px; }.qualifiers dt { color: #64748b; }.qualifiers dd { margin: 0; overflow-wrap: anywhere; }.proof { border-top: 1px solid var(--c-border-light); padding: 13px 0 5px; font-size: 13px; }.proof strong { color: var(--t-secondary); font-weight: 500; }.proof small { display: block; margin-top: 4px; color: #64748b; }.proof blockquote { margin: 10px 0; padding-left: 10px; border-left: 2px solid #c5ddd3; color: var(--t-secondary); line-height: 1.7; overflow-wrap: anywhere; }.proof > div { display: flex; gap: 16px; }
.canvas-message { position: absolute; inset: 140px 24px 100px; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 14px; }.canvas-message h2 { font-size: 20px; font-weight: 500; color: var(--t-primary); margin: 0; }.canvas-message p { font-size: 14px; color: var(--t-secondary); max-width: 440px; margin: 0; }.stage-action { display: inline-flex; gap: 8px; align-items: center; background: var(--c-primary-light); border: 1px solid #c5ddd3; padding: 9px 16px; color: var(--c-primary-dark); margin-top: 6px; }.loading-ring { width: 24px; height: 24px; border: 2px solid var(--c-border); border-top-color: var(--c-primary); border-radius: 50%; animation: spin 1s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.graph-list { position: absolute; inset: 85px 20px 50px; overflow: auto; border: 1px solid var(--c-border); border-radius: 9px; background: var(--c-surface); }.graph-list table { width: 100%; border-collapse: collapse; font-size: 13px; }.graph-list th { color: var(--t-secondary); background: var(--c-surface-alt); position: sticky; top: 0; text-align: left; font-weight: 500; }.graph-list :is(th, td) { padding: 13px 16px; border-bottom: 1px solid var(--c-border-light); color: var(--t-secondary); }.graph-list tr.chosen td { background: var(--c-primary-light); }.graph-list td:first-child { max-width: 360px; }.node-name { display: inline-flex; gap: 10px; align-items: center; text-align: left; color: var(--t-primary); border: 0; background: none; padding: 0; }
@media (max-width: 800px) { .graph-toolbar { top: 14px; left: 14px; right: 14px; flex-wrap: wrap; gap: 10px; }.search-box { width: 100%; }.toolbar-actions { width: 100%; }.toolbar-actions select:first-child { flex: 1; min-width: 0; }.graph-legend { top: 128px; left: 14px; }.filter-popover { top: 126px; right: 14px; }.graph-stage { min-height: 620px; height: calc(100dvh - 100px); }.selection-card { right: 14px; bottom: 43px; width: min(300px, calc(100% - 28px)); max-height: 55%; }.graph-list { top: 126px; left: 14px; right: 14px; }.graph-heading { gap: 8px; }.breadcrumb { gap: 8px; }.breadcrumb button { max-width: 130px; font-size: 13px; }.breadcrumb h1 { font-size: 16px; }.view-switch button { padding: 7px 10px; }.view-switch svg { display: none; }.fullscreen-tool { margin-left: auto; } }
@media (prefers-reduced-motion: reduce) { .loading-ring { animation: none; }.graph-stage button { transition: none; } }
</style>
