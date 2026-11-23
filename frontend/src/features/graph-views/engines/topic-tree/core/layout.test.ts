import { describe, expect, it } from 'vitest'
import type { TopicTreeLink, TopicTreeNode } from './types'
import { LEVEL_GAP, ROW_GAP, deckCrumbs, isFolded, layoutTree, nodeAction } from './layout'

const node = (id: string, extra: Partial<TopicTreeNode> = {}): TopicTreeNode => ({ id, label: id, ...extra })
const link = (source: string, target: string): TopicTreeLink => ({ source, target, label: 'child' })

describe('layoutTree', () => {
  it('puts levels in columns, leaves in rows and parents between their children', () => {
    const nodes = ['root', 'a', 'a1', 'a2', 'b'].map((id) => node(id))
    const links = [link('root', 'a'), link('a', 'a1'), link('a', 'a2'), link('root', 'b')]
    const pos = layoutTree(nodes, links)

    expect(pos.get('a1')).toEqual({ x: 2 * LEVEL_GAP, y: 0 })
    expect(pos.get('a2')).toEqual({ x: 2 * LEVEL_GAP, y: ROW_GAP })
    expect(pos.get('a')).toEqual({ x: LEVEL_GAP, y: ROW_GAP / 2 })
    expect(pos.get('b')).toEqual({ x: LEVEL_GAP, y: 2 * ROW_GAP })
    expect(pos.get('root')).toEqual({ x: 0, y: (ROW_GAP / 2 + 2 * ROW_GAP) / 2 })
  })

  it('accepts links whose ends force-graph already replaced with node objects', () => {
    const nodes = [node('root'), node('a')]
    const links = [{ source: { id: 'root' }, target: { id: 'a' } } as unknown as TopicTreeLink]
    expect(layoutTree(nodes, links).get('a')).toEqual({ x: LEVEL_GAP, y: 0 })
  })

  it('skips unreachable nodes, unknown ends and cycles', () => {
    const nodes = [node('root'), node('a'), node('lonely')]
    const links = [link('root', 'a'), link('a', 'root'), link('a', 'ghost')]
    const pos = layoutTree(nodes, links)
    expect([...pos.keys()].sort()).toEqual(['a', 'root'])
  })

  it('returns nothing without a root', () => {
    expect(layoutTree([node('a')], []).size).toBe(0)
  })
})

describe('isFolded', () => {
  it('is true only when the node has children and none is drawn', () => {
    const links = [link('root', 'a')]
    expect(isFolded(node('a', { child_count: 2 }), links)).toBe(true)
    expect(isFolded(node('root', { child_count: 1 }), links)).toBe(false)
    expect(isFolded(node('b', { child_count: 0 }), links)).toBe(false)
  })
})

describe('nodeAction', () => {
  it('enters a node with children, otherwise opens its note', () => {
    expect(nodeAction(node('a', { kind: 'card', child_deck: 'D::a', child_count: 3, note_id: 1 }))).toEqual({
      enter: 'D::a',
    })
    expect(nodeAction(node('b', { kind: 'card', child_deck: null, child_count: 0, note_id: 2 }))).toEqual({ open: 2 })
    expect(nodeAction(node('d', { kind: 'deck', child_deck: 'D::x', child_count: 1 }))).toEqual({ enter: 'D::x' })
  })

  it('opens the root card instead of entering the deck it is already in', () => {
    expect(nodeAction(node('root', { kind: 'root', child_deck: 'D', child_count: 3, note_id: 9 }))).toEqual({
      open: 9,
    })
    expect(nodeAction(node('root', { kind: 'root', child_deck: 'D', child_count: 3 }))).toBeNull()
  })
})

describe('deckCrumbs', () => {
  it('lists every level of the path, starting from all decks', () => {
    expect(deckCrumbs('日常會話::日本語面接')).toEqual([
      { label: '全部牌組', deck: null },
      { label: '日常會話', deck: '日常會話' },
      { label: '日本語面接', deck: '日常會話::日本語面接' },
    ])
    expect(deckCrumbs(null)).toEqual([{ label: '全部牌組', deck: null }])
  })
})
