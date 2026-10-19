import { describe, expect, it } from 'vitest'
import { buildDeckTree, deckAncestors, relativeDeckPath } from './deckTree'

describe('buildDeckTree', () => {
  it('nests decks by :: and sorts each level', () => {
    const tree = buildDeckTree(['日本語::外語糾錯::母卡片', 'Default', '日本語', '日本語::外語糾錯', '日本語::自他動詞'])

    expect(tree.map((node) => node.name)).toEqual(['Default', '日本語'])
    const japanese = tree[1]
    expect(japanese.children.map((node) => node.fullName)).toEqual(['日本語::外語糾錯', '日本語::自他動詞'])
    expect(japanese.children[0].children[0]).toEqual({
      name: '母卡片',
      fullName: '日本語::外語糾錯::母卡片',
      children: [],
    })
  })

  it('sorts case-insensitively', () => {
    expect(buildDeckTree(['b', 'A', 'C']).map((node) => node.name)).toEqual(['A', 'b', 'C'])
  })

  it('adds missing intermediate levels once', () => {
    const tree = buildDeckTree(['A::B::C', 'A::B::D'])

    expect(tree).toHaveLength(1)
    expect(tree[0].children).toHaveLength(1)
    expect(tree[0].children[0].children.map((node) => node.name)).toEqual(['C', 'D'])
  })
})

describe('deck paths', () => {
  it('lists ancestors top-down', () => {
    expect(deckAncestors('A::B::C')).toEqual(['A', 'A::B', 'A::B::C'])
  })

  it('shows sub-deck paths relative to the selection', () => {
    expect(relativeDeckPath('A::B::C', 'A')).toBe('B::C')
    expect(relativeDeckPath('A', 'A')).toBe('')
    expect(relativeDeckPath('AB::C', 'A')).toBe('AB::C') // 名稱前綴相同但不是子牌組
    expect(relativeDeckPath('A::B', null)).toBe('A::B')
  })
})
