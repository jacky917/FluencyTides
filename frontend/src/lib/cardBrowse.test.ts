import { describe, expect, it } from 'vitest'
import {
  browseNoteTypes,
  createFormPath,
  isCreateFormOpen,
  restoreCardDepth,
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
      creating: false,
    })
  })

  it('reads the create form flag only from new=1', () => {
    expect(parseBrowseParams(new URLSearchParams('deck=A&card=3&new=1'))).toMatchObject({ cardId: 3, creating: true })
    for (const raw of ['new=0', 'new=true', 'new=', '']) {
      expect(parseBrowseParams(new URLSearchParams(raw)).creating).toBe(false)
    }
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
      creating: false,
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

  it('does not remember the create form', () => {
    expect(searchToRemember(new URLSearchParams('deck=A&card=5&new=1'))).toBe(new URLSearchParams('deck=A&card=5').toString())
  })
})

describe('createFormPath', () => {
  const remembered = new URLSearchParams('deck=English::Vocabulary&card=7').toString()

  it('returns to the remembered position from another page', () => {
    expect(createFormPath('/', '', remembered)).toBe(`/cards?${remembered}&new=1`)
  })

  it('opens on all decks when nothing is remembered', () => {
    expect(createFormPath('/graph', '?deck=X', '')).toBe('/cards?new=1')
  })

  it('stays at the current position on the card page', () => {
    expect(createFormPath('/cards', '?deck=A&page=2', remembered)).toBe('/cards?deck=A&page=2&new=1')
  })

  it('does not add new twice', () => {
    expect(createFormPath('/cards', '?deck=A&new=1', remembered)).toBe('/cards?deck=A&new=1')
  })
})

describe('isCreateFormOpen', () => {
  it('is open on the card page with new=1, however the deck is encoded', () => {
    expect(isCreateFormOpen('/cards', '?deck=A::B&new=1')).toBe(true)
    expect(isCreateFormOpen('/cards', '?deck=A%3A%3AB&new=1')).toBe(true)
  })

  it('is closed without new=1 or on another page', () => {
    expect(isCreateFormOpen('/cards', '?deck=A')).toBe(false)
    expect(isCreateFormOpen('/graph', '?new=1')).toBe(false)
  })
})

describe('restoreCardDepth', () => {
  it('keeps the offered depths and falls back to all', () => {
    for (const depth of [0, 1, 2, 3]) expect(restoreCardDepth(depth)).toBe(depth)
    for (const bad of [4, -1, '1', null, undefined, 1.5]) expect(restoreCardDepth(bad)).toBe(0)
  })
})

describe('browseNoteTypes', () => {
  it('uses the chosen note type, else every note type of the card type, else no filter', () => {
    expect(browseNoteTypes(['A', 'B'], 'B')).toEqual(['B'])
    expect(browseNoteTypes(['A', 'B'], null)).toEqual(['A', 'B'])
    // 換了卡片類型、記住的筆記類型已不屬於它
    expect(browseNoteTypes(['A', 'B'], 'C')).toEqual(['A', 'B'])
    expect(browseNoteTypes(undefined, 'A')).toBeUndefined()
    expect(browseNoteTypes([], null)).toBeUndefined()
  })
})
