/**
 * 台詞抽屜的 BGM 顯示規則（純函式，可以在 node 環境測試）。
 * BGM display rules for the line drawer (pure, testable without a DOM).
 *
 * - 曲名跟著抽屜的台詞語言，退回規則和說話者一樣：「繁」顯示繁中曲名、沒有時退回日文；「日」與「雙語」
 *   顯示日文曲名、沒有時退回繁中；兩種都沒有（例：標題畫面的曲子）顯示代號。
 * - 播放鍵只在 `capabilities.bgm` 為 true 時出現（目前 BGM 音檔還沒有，一律不顯示）。
 *
 * - Titles follow the drawer's text mode with the speaker fallback: zh-Hant
 *   mode prefers the Chinese title, other modes the Japanese one; the code is
 *   the last resort.
 * - The play control exists only when `capabilities.bgm` is true.
 */

import type { StoryCapabilities, StoryLine, StoryLineBgm } from '../types/story'

/** 抽屜的台詞語言（同 `StoryLineDrawer` 的 `TextMode`）。The drawer's text mode. */
export type BgmTextMode = 'both' | 'ja-JP' | 'zh-Hant'

/**
 * `bgm_detect` 的說明（提示框用，見語料庫計畫書的 `game_lines` 欄位表）；不在表裡的值原樣顯示。
 * Labels for `bgm_detect` (see the corpus plan's column table).
 */
const DETECT_LABEL: Record<string, string> = {
  A: '直接監看到',
  實測: '逐句驗證確認',
  B推定: '由曲名浮窗推得（推定）',
}

/**
 * 一首 BGM 在目前語言的曲名。A track's title in the current text mode.
 * @param track - 曲目。The track.
 * @param mode - 抽屜的台詞語言。The text mode.
 * @returns 曲名；兩種語言都沒有時是代號。The title, or the code when none.
 */
export function bgmTrackTitle(track: StoryLineBgm, mode: BgmTextMode): string {
  const ja = track.titles['ja-JP']
  const zh = track.titles['zh-Hant']
  return (mode === 'zh-Hant' ? zh || ja : ja || zh) || track.code
}

/**
 * 依 `ord` 排好的曲目（後端已排序；這裡再排一次，避免依賴回應順序）。Tracks in `ord` order.
 * @param line - 台詞。The line.
 * @returns 曲目（新陣列）。The tracks (a new array).
 */
export function bgmTracks(line: Pick<StoryLine, 'bgm'>): StoryLineBgm[] {
  return [...(line.bgm ?? [])].sort((a, b) => a.ord - b.ord)
}

/**
 * BGM 的提示框文字：每首一行（代號、鑑賞編號、各語言曲名），再加上偵測方式與「可能切換」。
 * Tooltip text: one row per track, then how it was detected and whether it may change.
 * @param line - 台詞。The line.
 * @returns 多行文字；沒有 BGM 時是空字串。Multi-line text, or '' when no BGM.
 */
export function bgmTooltip(line: Pick<StoryLine, 'bgm' | 'bgm_detect' | 'bgm_may_change'>): string {
  const tracks = bgmTracks(line)
  if (!tracks.length) return ''
  const rows = tracks.map((t) => {
    const titles = [t.titles['ja-JP'], t.titles['zh-Hant']].filter(Boolean).join(' / ')
    const gallery = t.gallery_no !== null ? `（鑑賞 No.${t.gallery_no}）` : ''
    return `BGM ${t.code}${gallery}${titles ? `：${titles}` : ''}`
  })
  if (line.bgm_detect) rows.push(`判定：${DETECT_LABEL[line.bgm_detect] ?? line.bgm_detect}`)
  if (line.bgm_may_change || tracks.length > 1) rows.push('這句播放中 BGM 會切換')
  return rows.join('\n')
}

/**
 * 能不能顯示 BGM 播放鍵：只看後端的 `capabilities.bgm`。Whether to render the BGM play control.
 * @param caps - 遊戲的功能開關。The game's capabilities.
 * @returns 是否顯示。Whether to show it.
 */
export function canPlayBgm(caps: Pick<StoryCapabilities, 'bgm'>): boolean {
  return caps.bgm === true
}
