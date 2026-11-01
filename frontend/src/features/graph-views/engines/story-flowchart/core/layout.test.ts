import { describe, expect, it } from 'vitest'
import { DEFAULT_SIZE } from '../../../core/theme'
import { sabbatFixture } from '../fixtures/sabbat'
import { senrenCh3Fixture } from '../fixtures/senrenCh3'
import { assign, layoutTab, nodeWidth, project } from './layout'
import type { StoryGraph } from './types'

const size = DEFAULT_SIZE

function fan(k: number): StoryGraph {
  const kids = Array.from({ length: k }, (_, i) => `c${i}`)
  return {
    schema_version: 1,
    tabs: [{ key: 't', title: 'T', order: 1, start: 'p' }],
    nodes: [
      { key: 'p', tab: 't', type: 'choice', title: 'P' },
      ...kids.map((key) => ({ key, tab: 't', type: 'option' as const, title: key })),
      { key: 'm', tab: 't', type: 'merge', title: 'M' },
    ],
    edges: [
      ...kids.map((to, i) => ({ from: 'p', to, kind: 'branch' as const, order: i })),
      ...kids.map((from) => ({ from, to: 'm', kind: 'merge' as const })),
    ],
  }
}

describe('assign: 分歧的欄位偏移', () => {
  it.each([
    [2, [-0.5, 0.5]],
    [3, [-1, 0, 1]],
    [4, [-1.5, -0.5, 0.5, 1.5]],
  ])('%i 個選項', (k, lanes) => {
    const grid = assign(fan(k), 't', size)
    expect(Array.from({ length: k }, (_, i) => grid.cells[`c${i}`].lane)).toEqual(lanes)
    expect(grid.cells.m.lane).toBe(0) // 合流回到分歧點的欄
    expect(grid.cells.m.rank).toBe(2)
  })

  it('選項依 order 排列，不是邊的順序', () => {
    const g = fan(2)
    g.edges[0].order = 1
    g.edges[1].order = 0
    const grid = assign(g, 't', size)
    expect(grid.cells.c1.lane).toBe(-0.5)
  })
})

describe('assign: 層與合流', () => {
  it('合流的層 = 最長的分支 + 1（千戀 単独行動 → 部屋に戻って）', () => {
    const grid = assign(senrenCh3Fixture, 'common', size)
    const { cells } = grid
    expect(cells['sb:heya'].rank).toBe(cells['sb:d_a3'].rank + 1)
    expect(cells['sb:heya'].rank - cells['sb:d_c'].rank).toBeGreaterThan(1)
    expect(cells['sb:heya'].lane).toBe(cells['sb:doushiyou'].lane)
    expect([cells['sb:d_a'].lane, cells['sb:d_b'].lane, cells['sb:d_c'].lane]).toEqual([-1, 0, 1])
  })

  it('back 邊不影響分層', () => {
    const g = fan(2)
    const without = assign(g, 't', size)
    g.edges.push({ from: 'm', to: 'p', kind: 'back' })
    const withBack = assign(g, 't', size)
    expect(withBack.cells).toEqual(without.cells)
    expect(withBack.backEdges).toHaveLength(1)
  })

  it('巢狀分歧不重疊（魔女的夜宴 009）', () => {
    const grid = assign(sabbatFixture, 'common', size)
    const byRank = new Map<number, string[]>()
    for (const key of grid.order) {
      const r = grid.cells[key].rank
      byRank.set(r, [...(byRank.get(r) ?? []), key])
    }
    const nodes = new Map(sabbatFixture.nodes.map((n) => [n.key, n]))
    for (const keys of byRank.values()) {
      const sorted = [...keys].sort((a, b) => grid.cells[a].lane - grid.cells[b].lane)
      for (let i = 1; i < sorted.length; i++) {
        const a = grid.cells[sorted[i - 1]]
        const b = grid.cells[sorted[i]]
        const need = (nodeWidth(nodes.get(a.key)!, a.narrow, size) + nodeWidth(nodes.get(b.key)!, b.narrow, size)) / 2
        expect((b.lane - a.lane) * size.colGap).toBeGreaterThanOrEqual(need)
      }
    }
  })

  it('019 的 4 個選項與路線分歧菱形', () => {
    const grid = assign(sabbatFixture, 'common', size)
    const opts = ['sw:019_02A', 'sw:019_02B', 'sw:019_02C', 'sw:019_02D'].map((k) => grid.cells[k].lane)
    const choice = grid.cells['sw:019_01com'].lane
    expect(opts.map((l) => l - choice)).toEqual([-1.5, -0.5, 0.5, 1.5].map((x) => expect.closeTo(x * (opts[1] - opts[0]), 5)))
    expect(grid.cells['sw:019_02com#cond']).toBeDefined()
  })
})

describe('assign: 章節帶', () => {
  it.each([
    ['千戀', senrenCh3Fixture, 'common', ['CHAPTER 2', 'CHAPTER 3']],
    ['魔女', sabbatFixture, 'common', ['CHAPTER 1', 'CHAPTER 2', 'CHAPTER 3', 'CHAPTER 4']],
  ])('%s：章節改變的那一層之前插一條', (_name, graph, tab, labels) => {
    const grid = assign(graph as StoryGraph, tab as string, size)
    expect(grid.bands.map((b) => b.label)).toEqual(labels)
    expect(grid.bands[0].rank).toBe(0)
    for (const band of grid.bands) {
      const first = grid.order.find((k) => grid.cells[k].rank === band.rank && (graph as StoryGraph).nodes.find((n) => n.key === k)?.chapter_label === band.label)
      expect(first).toBeDefined()
    }
  })

  it('寧々 RESTART 的章節帶帶前綴', () => {
    const grid = assign(sabbatFixture, 'nene', size)
    expect(grid.bands.map((b) => b.label)).toContain('RESTART · CHAPTER 1')
    expect(grid.bands.at(-1)!.label).toBe('After Story')
  })
})

describe('project', () => {
  it.each([
    ['千戀', senrenCh3Fixture, 'common'],
    ['魔女 共通', sabbatFixture, 'common'],
    ['魔女 寧々', sabbatFixture, 'nene'],
    ['魔女 和奏', sabbatFixture, 'wakana'],
  ])('%s：每條邊的終點是目標節點的頂部中點', (_name, graph, tab) => {
    const layout = layoutTab(graph as StoryGraph, tab as string, size)
    expect(layout.edges.length).toBeGreaterThan(0)
    for (const e of layout.edges) {
      const t = layout.nodeMap[e.to]
      expect(e.end).toEqual({ x: t.x + t.w / 2, y: t.y })
      expect(e.d.endsWith(`${t.x + t.w / 2} ${t.y}`)).toBe(true)
    }
  })

  it('節點都在畫布內、同層的 y 相同，章節帶在節點上方', () => {
    const layout = layoutTab(sabbatFixture, 'common', size)
    for (const node of layout.nodes) {
      expect(node.x).toBeGreaterThanOrEqual(0)
      expect(node.x + node.w).toBeLessThanOrEqual(layout.width)
      expect(node.y + node.h).toBeLessThanOrEqual(layout.height)
    }
    const ys = new Map<number, number>()
    for (const node of layout.nodes) {
      if (ys.has(node.rank)) expect(node.y).toBe(ys.get(node.rank))
      ys.set(node.rank, node.y)
    }
    for (const band of layout.bands) {
      const below = layout.nodes.find((n) => n.rank === band.rank)!
      expect(band.y + band.height).toBeLessThanOrEqual(below.y)
    }
  })

  it('分歧從側邊出發、合流用 S 曲線、同欄直線', () => {
    const layout = project(assign(fan(2), 't', size), fan(2), size)
    const shapes = Object.fromEntries(layout.edges.map((e) => [`${e.from}>${e.to}`, e.shape]))
    expect(shapes['p>c0']).toBe('side')
    expect(shapes['c0>m']).toBe('curve')
    const three = project(assign(fan(3), 't', size), fan(3), size)
    expect(three.edges.find((e) => e.to === 'c1')!.shape).toBe('straight')
    const p = layout.nodeMap.p
    expect(layout.edges.find((e) => e.to === 'c0')!.start).toEqual({ x: p.x, y: p.y + p.h / 2 })
  })

  it('back 邊畫成節點右下角的小圖示', () => {
    const layout = layoutTab(sabbatFixture, 'nene', size)
    expect(layout.backs.length).toBeGreaterThan(0)
    for (const b of layout.backs) {
      const s = layout.nodeMap[b.from]
      expect(b.x).toBeGreaterThan(s.x + s.w / 2)
      expect(b.y).toBeGreaterThan(s.y + s.h)
    }
  })

  it('不認得的分頁回空的排版', () => {
    const layout = layoutTab(sabbatFixture, 'nope', size)
    expect(layout.nodes).toEqual([])
  })
})
