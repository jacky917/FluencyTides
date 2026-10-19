import { describe, expect, it } from 'vitest'
import {
  MAX_TRAIL,
  parseBrowseParams,
  parseTrail,
  pickNoteCard,
  positiveInt,
  pushTrail,
  searchToRemember,
} from './cardBrowse'

describe('parseBrowseParams', () => {
  it('reads valid parameters', () => {
    expect(parseBrowseParams(new URLSearchParams('deck=日本語::口說&q=note:A&page=3&card=1782033909954'))).toEqual({
      deck: '日本語::口說',
      search: 'note:A',
      page: 3,
      cardId: 1782033909954,
      side: 'question',
      trail: [],
    })
  })

  it('reads the face and the jump trail', () => {
    expect(parseBrowseParams(new URLSearchParams('card=3&side=answer&trail=1,2'))).toMatchObject({
      cardId: 3,
      side: 'answer',
      trail: [1, 2],
    })
  })

  it('falls back to defaults instead of sending requests the backend rejects', () => {
    expect(parseBrowseParams(new URLSearchParams('deck=&page=1.5&card=12abc'))).toEqual({
      deck: null,
      search: '',
      page: 1,
      cardId: null,
      side: 'question',
      trail: [],
    })
  })

  it.each(['0', '-3', '1.5', 'abc', '1e3', '', ' 2', '99999999999999999999'])('rejects %j as a positive integer', (raw) => {
    expect(positiveInt(raw)).toBeNull()
  })
})

describe('jump trail', () => {
  it('drops the whole trail when any entry is invalid', () => {
    expect(parseTrail('1,x,3')).toEqual([])
    expect(parseTrail('1,,3')).toEqual([])
    expect(parseTrail('')).toEqual([])
  })

  it('keeps only the most recent entries', () => {
    const long = Array.from({ length: MAX_TRAIL + 5 }, (_, index) => index + 1)
    expect(parseTrail(long.join(','))).toEqual(long.slice(-MAX_TRAIL))
    expect(pushTrail(long.slice(0, MAX_TRAIL), 999).split(',')).toHaveLength(MAX_TRAIL)
    expect(pushTrail([1, 2], 3)).toBe('1,2,3')
  })
})

describe('pickNoteCard', () => {
  const cards = [
    { card_id: 11, note_id: 1, ord: 1 },
    { card_id: 10, note_id: 1, ord: 0 },
    { card_id: 20, note_id: 2, ord: 0 },
  ]

  it('takes the lowest template ordinal of the first note that has cards', () => {
    expect(pickNoteCard(cards, [1, 2])).toBe(10)
    expect(pickNoteCard(cards, [3, 2])).toBe(20)
  })

  it('returns null when none of the notes has cards', () => {
    expect(pickNoteCard(cards, [3])).toBeNull()
  })
})

describe('searchToRemember', () => {
  it('keeps the position without the jump trail, in a stable order', () => {
    expect(searchToRemember(new URLSearchParams('card=5&trail=1,2&deck=日本語&side=answer&x=1'))).toBe(
      new URLSearchParams('deck=日本語&card=5&side=answer').toString(),
    )
    expect(searchToRemember(new URLSearchParams(''))).toBe('')
  })
})
