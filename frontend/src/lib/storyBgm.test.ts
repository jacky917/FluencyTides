import { describe, expect, it } from 'vitest'
import { bgmTooltip, bgmTrackTitle, bgmTracks, canPlayBgm } from './storyBgm'
import type { StoryLineBgm } from '../types/story'

const BGM02: StoryLineBgm = { code: 'BGM02', ord: 0, titles: { 'ja-JP': '気の合う仲間たち', 'zh-Hant': '意氣相投的朋友們' }, gallery_no: 4 }
const JA_ONLY: StoryLineBgm = { code: 'BGM05', ord: 1, titles: { 'ja-JP': '朝の陽気' }, gallery_no: null }
const ZH_ONLY: StoryLineBgm = { code: 'BGM07', ord: 0, titles: { 'zh-Hant': '夜晚' }, gallery_no: null }
const UNTITLED: StoryLineBgm = { code: 'BGM51', ord: 1, titles: {}, gallery_no: null }

describe('storyBgm', () => {
  it('曲名跟著台詞語言，退回規則同說話者，最後退回代號', () => {
    expect(bgmTrackTitle(BGM02, 'zh-Hant')).toBe('意氣相投的朋友們')
    expect(bgmTrackTitle(BGM02, 'ja-JP')).toBe('気の合う仲間たち')
    expect(bgmTrackTitle(BGM02, 'both')).toBe('気の合う仲間たち')
    expect(bgmTrackTitle(JA_ONLY, 'zh-Hant')).toBe('朝の陽気')
    expect(bgmTrackTitle(ZH_ONLY, 'ja-JP')).toBe('夜晚')
    expect(bgmTrackTitle(UNTITLED, 'zh-Hant')).toBe('BGM51')
    expect(bgmTrackTitle({ ...UNTITLED, titles: { 'ja-JP': '' } }, 'both')).toBe('BGM51')
  })

  it('曲目依 ord 排列，不改動原陣列', () => {
    const bgm = [UNTITLED, BGM02]
    expect(bgmTracks({ bgm }).map((t) => t.code)).toEqual(['BGM02', 'BGM51'])
    expect(bgm[0]).toBe(UNTITLED)
    expect(bgmTracks({ bgm: [] })).toEqual([])
  })

  it('提示框：每首一行，加上判定方式與切換提示；沒有 BGM 時是空字串', () => {
    expect(bgmTooltip({ bgm: [UNTITLED, BGM02], bgm_detect: '實測', bgm_may_change: true })).toBe(
      'BGM BGM02（鑑賞 No.4）：気の合う仲間たち / 意氣相投的朋友們\nBGM BGM51\n判定：逐句驗證確認\n這句播放中 BGM 會切換',
    )
    expect(bgmTooltip({ bgm: [JA_ONLY], bgm_detect: 'X', bgm_may_change: false })).toBe('BGM BGM05：朝の陽気\n判定：X')
    expect(bgmTooltip({ bgm: [BGM02], bgm_detect: null, bgm_may_change: false })).toBe(
      'BGM BGM02（鑑賞 No.4）：気の合う仲間たち / 意氣相投的朋友們',
    )
    expect(bgmTooltip({ bgm: [], bgm_detect: '實測', bgm_may_change: false })).toBe('')
  })

  it('播放鍵只在 capabilities.bgm 為 true 時顯示', () => {
    expect(canPlayBgm({ bgm: true })).toBe(true)
    expect(canPlayBgm({ bgm: false })).toBe(false)
    expect(canPlayBgm({} as { bgm: boolean })).toBe(false)
  })
})
