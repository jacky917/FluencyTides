import { describe, expect, it } from 'vitest'
import { isBackNavigation, nextShownFace, parseFaceValue, rememberLatest, toFaceValue, type ShownFace } from './cardFaces'

describe('face values', () => {
  it('round-trips the card and the side', () => {
    expect(parseFaceValue(toFaceValue(1788711955446, 'answer'))).toEqual({ cardId: 1788711955446, side: 'answer' })
  })
})

describe('nextShownFace', () => {
  const front: ShownFace = { value: '1:question', occasion: 'flip', direction: -1 }

  it('shows the first face as a flip (no animation, nothing to switch from)', () => {
    expect(nextShownFace(null, 1, 'question', false)).toEqual(front)
  })

  it('returns the same object when nothing changes', () => {
    expect(nextShownFace(front, 1, 'question', true)).toBe(front)
  })

  it('flips forward to the back and back to the front, whatever the navigation', () => {
    const back = nextShownFace(front, 1, 'answer', true)
    expect(back).toEqual({ value: '1:answer', occasion: 'flip', direction: 1 })
    expect(nextShownFace(back, 1, 'question', false)).toMatchObject({ occasion: 'flip', direction: -1 })
  })

  it('switches forward to another card', () => {
    expect(nextShownFace(front, 2, 'question', false)).toEqual({ value: '2:question', occasion: 'switch', direction: 1 })
  })

  it('switches backward when the navigation went back', () => {
    // 從背面回到上一張：仍是換卡（不是翻面），倒著播
    const onBack = nextShownFace(front, 1, 'answer', false)
    expect(nextShownFace(onBack, 2, 'question', true)).toEqual({ value: '2:question', occasion: 'switch', direction: -1 })
  })
})

describe('isBackNavigation', () => {
  it('compares history positions for browser back and forward', () => {
    expect(isBackNavigation('POP', null, 2, 3)).toBe(true)
    expect(isBackNavigation('POP', null, 4, 3)).toBe(false)
  })

  it('treats a POP with unknown positions as back', () => {
    expect(isBackNavigation('POP', null, undefined, 3)).toBe(true)
  })

  it('counts a replace only when it is a “back”', () => {
    expect(isBackNavigation('REPLACE', { back: true }, 3, 3)).toBe(true)
    expect(isBackNavigation('REPLACE', { jumpFrom: 1 }, 3, 3)).toBe(false)
    expect(isBackNavigation('REPLACE', null, 3, 3)).toBe(false)
  })

  it('never counts a push', () => {
    expect(isBackNavigation('PUSH', { back: true }, 1, 3)).toBe(false)
  })
})

describe('rememberLatest', () => {
  it('evicts the oldest entries beyond the limit', () => {
    let map: Map<string, number> = new Map()
    for (const key of ['a', 'b', 'c']) map = rememberLatest(map, key, 0, 2)
    expect([...map.keys()]).toEqual(['b', 'c'])
  })

  it('moves a re-remembered entry to the newest', () => {
    const map = rememberLatest(new Map([['a', 1], ['b', 2]]), 'a', 3, 2)
    expect([...map.entries()]).toEqual([['b', 2], ['a', 3]])
    expect([...rememberLatest(map, 'c', 4, 2).keys()]).toEqual(['a', 'c'])
  })

  it('does not change the given map', () => {
    const map = new Map([['a', 1]])
    rememberLatest(map, 'b', 2, 1)
    expect([...map.keys()]).toEqual(['a'])
  })
})
