import { describe, expect, it } from 'vitest'
import { parseBrowseParams, positiveInt } from './cardBrowse'

describe('parseBrowseParams', () => {
  it('reads valid parameters', () => {
    expect(parseBrowseParams(new URLSearchParams('deck=日本語::口說&q=note:A&page=3&card=1782033909954'))).toEqual({
      deck: '日本語::口說',
      search: 'note:A',
      page: 3,
      cardId: 1782033909954,
    })
  })

  it('falls back to defaults instead of sending requests the backend rejects', () => {
    expect(parseBrowseParams(new URLSearchParams('deck=&page=1.5&card=12abc'))).toEqual({
      deck: null,
      search: '',
      page: 1,
      cardId: null,
    })
  })

  it.each(['0', '-3', '1.5', 'abc', '1e3', '', ' 2', '99999999999999999999'])('rejects %j as a positive integer', (raw) => {
    expect(positiveInt(raw)).toBeNull()
  })
})
