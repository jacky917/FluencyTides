import { describe, expect, it } from 'vitest'
import { cardPageUrl, formatStoryParams, parseStoryParams, scrollStorageKey, shouldPushHistory, verbHits } from './storyParams'

describe('storyParams', () => {
  it('預設值：總覽、沒有抽屜', () => {
    expect(parseStoryParams(new URLSearchParams())).toEqual({ tab: null, node: null, line: null, kind: null, verb: '' })
  })

  it('讀出全部參數；不合法的 line 退回 null', () => {
    const p = parseStoryParams(new URLSearchParams('tab=common&node=sw:009&line=9021&kind=jp_core_verb&verb=%20見る%20'))
    expect(p).toEqual({ tab: 'common', node: 'sw:009', line: 9021, kind: 'jp_core_verb', verb: '見る' })
    expect(parseStoryParams(new URLSearchParams('line=1.5')).line).toBeNull()
    expect(parseStoryParams(new URLSearchParams('line=-3')).line).toBeNull()
  })

  it('往返不變，只寫有值的鍵', () => {
    const p = parseStoryParams(new URLSearchParams('tab=nene&node=sw:101&verb=見る'))
    expect(formatStoryParams(p).toString()).toBe(new URLSearchParams('tab=nene&node=sw:101&verb=見る').toString())
    expect(formatStoryParams({}).toString()).toBe('')
  })

  it('選節點、換分頁新增歷史；篩選、搜尋、選台詞用取代', () => {
    const base = parseStoryParams(new URLSearchParams('tab=common'))
    expect(shouldPushHistory(base, { ...base, node: 'sw:001' })).toBe(true)
    expect(shouldPushHistory(base, { ...base, tab: 'nene' })).toBe(true)
    expect(shouldPushHistory(base, { ...base, verb: '見る' })).toBe(false)
    expect(shouldPushHistory(base, { ...base, kind: 'jp_core_verb' })).toBe(false)
    expect(shouldPushHistory(base, { ...base, line: 3 })).toBe(false)
  })

  it('卡片頁網址與捲動鍵', () => {
    expect(cardPageUrl([1, 2], 9)).toBe('/cards?q=nid%3A1%2C2&card=9')
    expect(cardPageUrl([1])).toBe('/cards?q=nid%3A1')
    expect(scrollStorageKey('G', 'common')).toBe('story-scroll:G:common')
  })

  it('動詞命中與各分頁數量；有篩選時用該類型的索引', () => {
    const index = { verbs: { 見る: ['a', 'b', 'c'] }, 'verbs:jp_core_verb': { 見る: ['a'] } }
    const tabOf = (k: string) => (k === 'c' ? 'nene' : 'common')
    expect(verbHits(index, '見る', null, tabOf).perTab).toEqual({ common: 2, nene: 1 })
    expect([...verbHits(index, '見る', 'jp_core_verb', tabOf).nodes]).toEqual(['a'])
    expect(verbHits(index, '', null, tabOf).nodes.size).toBe(0)
    expect(verbHits(undefined, '見る', null, tabOf).nodes.size).toBe(0)
  })
})
