import { describe, expect, it } from 'vitest'
import {
  MAX_REMEMBERED_DECKS,
  MAX_REMEMBERED_NODES,
  collectNodePositions,
  sanitizeGraphView,
  seedNodePositions,
  touchDeckIndex,
} from './graphView'

describe('sanitizeGraphView', () => {
  it('keeps a valid view and drops broken node positions', () => {
    expect(
      sanitizeGraphView({ zoom: 2, center: { x: 1, y: -2 }, nodes: { a: [1, 2], b: ['x', 2], c: [Number.NaN, 1] } }),
    ).toEqual({ zoom: 2, center: { x: 1, y: -2 }, nodes: { a: [1, 2] } })
  })

  it.each([null, 3, {}, { zoom: 0, center: { x: 0, y: 0 } }, { zoom: 1 }, { zoom: 1, center: { x: 'a', y: 0 } }])(
    'rejects %j',
    (raw) => {
      expect(sanitizeGraphView(raw)).toBeNull()
    },
  )
})

describe('node positions', () => {
  it('collects rounded positions of laid-out nodes only', () => {
    expect(collectNodePositions([{ id: 'a', x: 1.234, y: 5.678 }, { id: 'b' }, { x: 1, y: 2 }])).toEqual({ a: [1.2, 5.7] })
  })

  it('caps how many positions are kept', () => {
    const nodes = Array.from({ length: MAX_REMEMBERED_NODES + 10 }, (_, index) => ({ id: index, x: index, y: 0 }))
    expect(Object.keys(collectNodePositions(nodes))).toHaveLength(MAX_REMEMBERED_NODES)
  })

  it('seeds remembered positions and reports whether every node had one', () => {
    const view = { zoom: 1, center: { x: 0, y: 0 }, nodes: { a: [3, 4] as [number, number] } }
    const nodes: { id: string; x?: number; y?: number }[] = [{ id: 'a' }, { id: 'b' }]
    expect(seedNodePositions(nodes, view)).toBe(false)
    expect(nodes[0]).toMatchObject({ x: 3, y: 4 })
    expect(nodes[1].x).toBeUndefined()
    expect(seedNodePositions([{ id: 'a' }], view)).toBe(true)
    expect(seedNodePositions([{ id: 'a' }], null)).toBe(false)
  })
})

describe('touchDeckIndex', () => {
  it('moves the deck to the most recent end', () => {
    expect(touchDeckIndex(['a', 'b', 'c'], 'a')).toEqual({ kept: ['b', 'c', 'a'], evicted: [] })
  })

  it('evicts the least recently used decks beyond the limit', () => {
    const full = Array.from({ length: MAX_REMEMBERED_DECKS }, (_, index) => `d${index}`)
    expect(touchDeckIndex(full, 'new')).toEqual({ kept: [...full.slice(1), 'new'], evicted: ['d0'] })
  })
})
