/** 确定性有界力布局，固定迭代次数，刷新相同子图时节点位置保持一致。 */
export function layoutGraph(ids: string[], edges: Array<{ source: string; target: string }>) {
  const positions = new Map(ids.map((id, index) => [id, {
    x: 500 + Math.cos(index * 2.4) * (50 + 12 * Math.sqrt(index)),
    y: 340 + Math.sin(index * 2.4) * (50 + 12 * Math.sqrt(index))
  }]))
  for (let step = 0; step < 100; step++) {
    const forces = new Map(ids.map(id => [id, { x: 0, y: 0 }]))
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      const a = positions.get(ids[i])!, b = positions.get(ids[j])!
      const dx = a.x - b.x, dy = a.y - b.y, distance = Math.max(1, Math.hypot(dx, dy))
      const strength = 3500 / (distance * distance)
      const fa = forces.get(ids[i])!, fb = forces.get(ids[j])!
      fa.x += dx / distance * strength; fa.y += dy / distance * strength
      fb.x -= dx / distance * strength; fb.y -= dy / distance * strength
    }
    for (const edge of edges) {
      const a = positions.get(edge.source), b = positions.get(edge.target)
      if (!a || !b) continue
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy))
      const strength = (distance - 150) * .025
      const fa = forces.get(edge.source)!, fb = forces.get(edge.target)!
      fa.x += dx / distance * strength; fa.y += dy / distance * strength
      fb.x -= dx / distance * strength; fb.y -= dy / distance * strength
    }
    for (const id of ids) {
      const point = positions.get(id)!, force = forces.get(id)!
      point.x = Math.max(85, Math.min(915, point.x + Math.max(-10, Math.min(10, force.x)) + (500 - point.x) * .005))
      point.y = Math.max(65, Math.min(615, point.y + Math.max(-10, Math.min(10, force.y)) + (340 - point.y) * .005))
    }
  }
  return positions
}

/** 端点接到节点圆心，由后绘制的节点覆盖线头，避免半径留白造成视觉断连。 */
export function graphEdgePath(a: { x: number; y: number }, b: { x: number; y: number }) {
  const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy)
  if (distance < 1) return `M ${a.x} ${a.y} C ${a.x + 85} ${a.y - 100}, ${a.x + 100} ${a.y + 25}, ${a.x} ${a.y}`
  return `M ${a.x} ${a.y} L ${b.x} ${b.y}`
}
