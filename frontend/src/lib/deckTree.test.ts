import { describe, expect, it } from 'vitest'
import {
  MAX_DECK_NAME_LENGTH,
  buildDeckTree,
  deckAncestors,
  deckCreateErrorPlacement,
  isCategoryDeck,
  joinDeckName,
  relativeDeckPath,
  validateDeckName,
} from './deckTree'

describe('buildDeckTree', () => {
  it('nests decks by :: and sorts each level', () => {
    const tree = buildDeckTree(['日本語::外語糾錯::母卡片', 'Default', '日本語', '日本語::外語糾錯', '日本語::自他動詞'])

    expect(tree.map((node) => node.name)).toEqual(['Default', '日本語'])
    const japanese = tree[1]
    expect(japanese.children.map((node) => node.fullName)).toEqual(['日本語::外語糾錯', '日本語::自他動詞'])
    expect(japanese.children[0].children[0]).toEqual({
      name: '母卡片',
      fullName: '日本語::外語糾錯::母卡片',
      exists: true,
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

  it('marks levels missing from the list as not existing', () => {
    // Anki 只有最底下那一層：上兩層是為了分層補上的分類節點
    const [top] = buildDeckTree(['FluencyTides::Speaking_Trilingual::buy_flowers'])

    expect(top.exists).toBe(false)
    expect(top.children[0].exists).toBe(false)
    expect(top.children[0].children[0].exists).toBe(true)
  })

  it('marks a level as existing even when a deeper deck is listed first', () => {
    const [top] = buildDeckTree(['A::B::C', 'A::B', 'A'])

    expect(top.exists).toBe(true)
    expect(top.children[0].exists).toBe(true)
  })
})

describe('isCategoryDeck', () => {
  const decks = ['A::B::C', 'Default']

  it('is true for a level that only has decks below it', () => {
    expect(isCategoryDeck(decks, 'A')).toBe(true)
    expect(isCategoryDeck(decks, 'A::B')).toBe(true)
  })

  it('is false for real decks and for names with nothing below', () => {
    expect(isCategoryDeck(decks, 'A::B::C')).toBe(false)
    expect(isCategoryDeck(decks, 'Default')).toBe(false)
    expect(isCategoryDeck(decks, 'Gone')).toBe(false) // 真的不存在（被刪掉或改名）
    expect(isCategoryDeck(decks, 'A::')).toBe(false)
    expect(isCategoryDeck(['AB::C'], 'A')).toBe(false) // 名稱前綴相同但不是它的上層
  })
})

describe('joinDeckName', () => {
  it('prefixes the parent, or returns the typed name at the top level', () => {
    expect(joinDeckName(null, '日本語')).toBe('日本語')
    expect(joinDeckName('日本語', '面接')).toBe('日本語::面接')
    expect(joinDeckName('日本語', '面接::二次')).toBe('日本語::面接::二次')
  })

  it('rebuilds a category node from its parent and its own name', () => {
    // 牌組樹「建立這一層」送出的名稱：上層＋這一層的名稱，要正好是節點自己
    const [top] = buildDeckTree(['A::B::C'])
    const middle = top.children[0]

    expect(joinDeckName(deckAncestors(middle.fullName).at(-2) ?? null, middle.name)).toBe('A::B')
    expect(joinDeckName(deckAncestors(top.fullName).at(-2) ?? null, top.name)).toBe('A')
  })
})

describe('deckCreateErrorPlacement', () => {
  it('shows a backend 400 or 409 under the input', () => {
    expect(deckCreateErrorPlacement(400, true)).toBe('inline')
    expect(deckCreateErrorPlacement(409, true)).toBe('inline')
  })

  it('sends everything else to a toast', () => {
    expect(deckCreateErrorPlacement(502, true)).toBe('toast') // Anki 連不上或拒絕
    expect(deckCreateErrorPlacement(401, true)).toBe('toast')
    expect(deckCreateErrorPlacement(undefined, false)).toBe('toast') // 沒有回應
    expect(deckCreateErrorPlacement(409, false)).toBe('toast') // 不是後端的錯誤格式（例如代理回的頁面）
  })
})

// 與後端 tests/test_deck_service.py 的 normalize_deck_name 是同一組案例
describe('validateDeckName', () => {
  it('accepts valid names', () => {
    for (const name of ['日本語', '  日本語  ', '日本語 :: 面接', 'a::b::c', ' a ::b:: c ', '全部牌組', 'a:b', '中 文::with space']) {
      expect(validateDeckName(name), name).toBeNull()
    }
  })

  it('rejects empty names and empty levels', () => {
    expect(validateDeckName('')).toBe('牌組名稱不能是空的')
    expect(validateDeckName('   ')).toBe('牌組名稱不能是空的')
    expect(validateDeckName('::a')).toBe('牌組名稱的第 1 層是空的')
    expect(validateDeckName('a::')).toBe('牌組名稱的第 2 層是空的')
    expect(validateDeckName('a::::b')).toBe('牌組名稱的第 2 層是空的')
    expect(validateDeckName('a::  ::b')).toBe('牌組名稱的第 2 層是空的')
    expect(validateDeckName('::')).toBe('牌組名稱的第 1 層是空的')
  })

  it('limits the length of the trimmed full name', () => {
    expect(validateDeckName('a'.repeat(MAX_DECK_NAME_LENGTH))).toBeNull()
    expect(validateDeckName(`  ${'a'.repeat(MAX_DECK_NAME_LENGTH)}  `)).toBeNull()
    expect(validateDeckName('a'.repeat(MAX_DECK_NAME_LENGTH + 1))).toBe(
      `牌組名稱最長 ${MAX_DECK_NAME_LENGTH} 字（目前 ${MAX_DECK_NAME_LENGTH + 1} 字）`,
    )
  })

  it('counts characters the way the backend does', () => {
    // 表情符號在 JavaScript 是兩個 UTF-16 碼元，後端（Python）算一個字
    expect(validateDeckName('😀'.repeat(MAX_DECK_NAME_LENGTH))).toBeNull()
  })

  it('validates a child name together with its parent', () => {
    expect(validateDeckName(joinDeckName('日本語', '面接'))).toBeNull()
    expect(validateDeckName(joinDeckName('日本語', '面接::'))).toBe('牌組名稱的第 3 層是空的')
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
