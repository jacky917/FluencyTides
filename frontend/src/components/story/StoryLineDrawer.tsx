/**
 * 劇情流程圖的台詞抽屜（D12）：列出節點的台詞，有卡的台詞可以跳到卡片，有語音的台詞可以播放，
 * 有 point 的台詞可以下載存檔。
 * The line drawer: a node's lines, card chips that jump to /cards, voice
 * playback, and save downloads.
 *
 * - `kind=choice`：顯示成選項清單，每個選項標出它通往哪個節點。
 * - `kind=bgv`：背景語音，較淡的樣式；存檔改用它的播放位置（後端處理）。
 * - 語音：存檔鍵左邊的播放鍵；同一時間只播一句，換節點、關閉抽屜時停止。沒有語音（旁白）
 *   或後端沒有素材來源（`capabilities.voices=false`）時改成停用的靜音喇叭圖示（不顯示文字或提示框）。選項列不放。
 * - BGM：說話者旁顯示這句的曲名（`StoryLine.bgm`，跟著台詞語言，規則見 `lib/storyBgm`），播放中切換曲子的
 *   台詞以「→」連接兩首。BGM 播放鍵只在 `capabilities.bgm=true` 時出現（目前 BGM 音檔還沒有，不顯示）；
 *   它是另一個播放器：循環播放、音量較低，和語音可以同時播（就像遊戲裡語音疊在 BGM 上），BGM 自己一次只播
 *   一首。播放中的鍵是曲目代號，所以同一首 BGM 的所有台詞都顯示播放中。換節點、關閉抽屜時停止。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ChevronDown, Download, Loader2, Play, Square, VolumeX, X } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { StoryAPI } from '../../api/story'
import { useStoryLines } from '../../hooks/useStoryLines'
import { useLocalStorage } from '../../hooks/useStoredState'
import { useVoicePlayer } from '../../hooks/useVoicePlayer'
import { describeApiError } from '../../lib/apiError'
import { bgmTooltip, bgmTrackTitle, bgmTracks, canPlayBgm } from '../../lib/storyBgm'
import { cardPageUrl } from '../../lib/storyParams'
import { cn } from '../../lib/utils'
import { describeVoiceFailure } from '../../lib/voicePlayer'
import { pathTo, pathVariables, localizedTitle, type StoryGraph, type StoryNode } from '@/features/graph-views/engines/story-flowchart'
import type { StoryCardAnnotation, StoryLine, StoryManifest } from '../../types/story'
import { Button } from '../ui/button'
import { ErrorNote } from '../shared/ErrorNote'

/** 台詞顯示語言。Text display mode. */
type TextMode = 'both' | 'ja-JP' | 'zh-Hant'

const TYPE_LABEL: Record<string, string> = {
  scene: '場景', choice: '選項場景', option: '選項', merge: '合流', condition: '條件分歧', ending: '結局', route_exit: '路線出口',
}

/** BGM 播放器的音訊設定：循環、音量壓低，和語音同時播時聽得到台詞。BGM audio: looping, quieter than voices. */
const BGM_AUDIO = { loop: true, volume: 0.5 }

/** {@link StoryLineDrawer} 的 props。Props of {@link StoryLineDrawer}. */
export interface StoryLineDrawerProps {
  gameId: string
  manifest: StoryManifest
  graph: StoryGraph
  node: StoryNode
  selectedLine: number | null
  kindFilter: string | null
  onSelectLine: (lineId: number | null) => void
  onOpenNode: (nodeKey: string) => void
  onClose: () => void
}

/**
 * 台詞抽屜。The line drawer.
 * @param props - 見 {@link StoryLineDrawerProps}。See {@link StoryLineDrawerProps}.
 * @returns 抽屜 JSX。Drawer JSX.
 */
export function StoryLineDrawer({
  gameId, manifest, graph, node, selectedLine, kindFilter, onSelectLine, onOpenNode, onClose,
}: StoryLineDrawerProps) {
  const navigate = useNavigate()
  const [textMode, setTextMode] = useLocalStorage<TextMode>('story-text-mode', 'both')
  const [slot, setSlot] = useLocalStorage<string>(`story-save-slot:${gameId}`, manifest.save_slots[0]?.id ?? 'quick')
  const [preset, setPreset] = useState<string>('')
  const [minSeq, setMinSeq] = useState(0)
  const [pendingSeq, setPendingSeq] = useState<number | null>(null)
  const [saving, setSaving] = useState<number | null>(null)
  const [jumping, setJumping] = useState<number | null>(null)
  const lines = useStoryLines(gameId, manifest.build_id, node.line_count ? node.key : null, minSeq)
  const all = useMemo(() => lines.data?.pages.flatMap((p) => p.lines) ?? [], [lines.data])
  const total = lines.data?.pages[0]?.total ?? node.line_count ?? 0
  const listRef = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const canSave = manifest.capabilities.saves && manifest.save_slots.length > 0
  const canPlay = manifest.capabilities.voices === true
  // 播放鍵是 line_id（不同台詞可能共用同一個語音檔，用檔名當鍵會讓兩個按鈕一起顯示播放中）
  const voiceOf = useRef(new Map<number, string>())
  const onVoiceError = useCallback((lineId: number, _url: string, cause: unknown) => {
    const voice = voiceOf.current.get(lineId)
    const probe = voice ? () => StoryAPI.probeVoice(gameId, voice) : () => Promise.resolve()
    void describeVoiceFailure(cause, probe, (e) => describeApiError(e).message)
      .then((message) => toast.error(`語音播放失敗：${message}`, { description: voice }))
  }, [gameId])
  const voice = useVoicePlayer<number>(onVoiceError)
  const { stop: stopVoice } = voice
  // BGM（預留）：播放鍵是曲目代號；只有 capabilities.bgm 為 true 時才會呼叫
  const canPlayMusic = canPlayBgm(manifest.capabilities)
  const onBgmError = useCallback((code: string, _url: string, cause: unknown) => {
    void describeVoiceFailure(cause, () => StoryAPI.probeBgm(gameId, code), (e) => describeApiError(e).message)
      .then((message) => toast.error(`BGM 播放失敗：${message}`, { description: code }))
  }, [gameId])
  const bgm = useVoicePlayer<string>(onBgmError, BGM_AUDIO)
  const { stop: stopBgm } = bgm
  const tabTitles = useMemo(() => Object.fromEntries(graph.tabs.map((t) => [t.key, localizedTitle(t, 'zh-Hant')])), [graph.tabs])
  const byKey = useMemo(() => new Map(graph.nodes.map((n) => [n.key, n])), [graph])
  const pathVars = useMemo(() => pathVariables(pathTo(graph, node.key)), [graph, node.key])
  const choiceEdges = useMemo(
    () => graph.edges.filter((e) => e.from === node.key && (e.kind ?? 'normal') === 'branch').sort((a, b) => (a.order ?? 0) - (b.order ?? 0)),
    [graph.edges, node.key],
  )

  // 換節點時重設（語音與 BGM 也停掉；關閉抽屜時元件卸載，由 useVoicePlayer 停止）
  useEffect(() => {
    setMinSeq(0)
    setPendingSeq(null)
    listRef.current?.scrollTo({ top: 0 })
    stopVoice()
    stopBgm()
  }, [node.key, gameId, stopVoice, stopBgm])

  const playVoice = (line: StoryLine) => {
    if (!line.voice) return
    voiceOf.current.set(line.line_id, line.voice)
    voice.toggle(line.line_id, StoryAPI.voiceUrl(gameId, line.voice))
  }

  // 網址指定的台詞（`line=` 是 line_id）：先問它的 seq，再確定會載入到它所在的頁。
  // 第一頁最多讀到 MAX_LINES_PAGE，更後面的由下面的 pending 機制逐頁往下讀。
  // 換節點或換台詞時舊的 locate 結果作廢（cancelled），不會套到新選的節點上。
  const selectedInList = selectedLine !== null ? all.find((l) => l.line_id === selectedLine) : undefined
  useEffect(() => {
    if (selectedLine === null || selectedInList || !node.line_count) return
    let cancelled = false
    const nodeKey = node.key
    StoryAPI.locate(gameId, { lineId: selectedLine })
      .then((loc) => {
        if (cancelled) return
        if (loc.node_key !== nodeKey) {
          toast.warning(`台詞 ${selectedLine} 不在這個節點`, {
            description: `它在「${localizedTitle(byKey.get(loc.node_key) ?? { title: loc.node_key }, 'ja-JP')}」`,
          })
          return
        }
        setMinSeq(loc.seq)
        setPendingSeq(loc.seq)
      })
      .catch((e) => {
        if (!cancelled) toast.error(`無法定位台詞 ${selectedLine}：${describeApiError(e).message}`)
      })
    return () => { cancelled = true }
  }, [gameId, node.key, node.line_count, selectedLine, selectedInList, byKey])

  // 選取的台詞捲到可見範圍
  useEffect(() => {
    if (!selectedInList) return
    listRef.current?.querySelector(`[data-line="${selectedInList.line_id}"]`)?.scrollIntoView({ block: 'center' })
  }, [selectedInList])

  // 「下一句有卡」與網址指定的台詞：逐頁往下讀，等到那一句載入後選取；全部讀完仍找不到就提示並放棄
  useEffect(() => {
    if (pendingSeq === null || !lines.data) return
    const found = all.find((l) => l.seq === pendingSeq)
    if (found) {
      setPendingSeq(null)
      if (found.line_id !== selectedLine) onSelectLine(found.line_id)
    } else if (lines.isError) {
      // 讀下一頁失敗：放棄（錯誤已由下方的 ErrorNote 顯示），不要一直重試
      setPendingSeq(null)
    } else if (lines.hasNextPage) {
      if (!lines.isFetchingNextPage) lines.fetchNextPage()
    } else if (!lines.isFetching) {
      setPendingSeq(null)
      toast.warning(`這個節點沒有第 ${pendingSeq + 1} 句（資料可能已更新）`)
    }
  }, [pendingSeq, all, lines, onSelectLine, selectedLine])

  // 捲到底自動讀下一頁
  useEffect(() => {
    const el = sentinel.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && lines.hasNextPage && !lines.isFetchingNextPage) lines.fetchNextPage()
    }, { root: listRef.current, rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
  }, [lines])

  const matchesKind = (line: StoryLine) =>
    (line.annotations.cards ?? []).some((c) => !kindFilter || c.card_type === kindFilter)

  const nextAnnotated = async () => {
    const current = selectedInList?.seq ?? -1
    const local = all.find((l) => l.seq > current && matchesKind(l))
    if (local) return onSelectLine(local.line_id)
    try {
      const page = await StoryAPI.getLines(gameId, node.key, 0, 1, current)
      if (page.next_annotated_seq === null) toast.info('後面沒有有卡的台詞了')
      else setPendingSeq(page.next_annotated_seq)
    } catch (e) {
      toast.error(describeApiError(e).message)
    }
  }

  const jumpToCards = async (line: StoryLine, card: StoryCardAnnotation) => {
    setJumping(line.line_id)
    try {
      // 後端只給 note id（不依賴 Anki）；這裡才查 card id，Anki 沒開時才會失敗
      const page = await FluencyTidesAPI.browseCards({ q: `nid:${card.note_ids.join(',')}`, pageSize: 50 })
      navigate(cardPageUrl(card.note_ids, page.cards[0]?.card_id))
    } catch (e) {
      toast.error(`無法查詢卡片：${describeApiError(e).message}`)
    } finally {
      setJumping(null)
    }
  }

  const download = async (line: StoryLine) => {
    const slotInfo = manifest.save_slots.find((s) => s.id === slot) ?? manifest.save_slots[0]
    if (!slotInfo) return
    setSaving(line.line_id)
    try {
      const blob = await StoryAPI.createSave(gameId, { line_id: line.line_id, slot: slotInfo.id, route_preset: preset || null })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = slotInfo.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast.success(`已下載 ${slotInfo.filename}`, { description: manifest.save_install_hint ?? undefined })
    } catch (e) {
      toast.error(`存檔產生失敗：${describeApiError(e).message}`)
    } finally {
      setSaving(null)
    }
  }

  /** 說話者旁的 BGM 曲名（沒有 BGM 時不顯示）；有播放能力時每首曲名是播放鍵。BGM titles beside the speaker. */
  const bgmChip = (line: StoryLine): ReactNode => {
    const tracks = bgmTracks(line)
    if (!tracks.length) return null
    return (
      <span className="flex min-w-0 items-center gap-0.5" title={bgmTooltip(line)}>
        <span aria-hidden="true">♪</span>
        {tracks.map((t, i) => {
          const title = bgmTrackTitle(t, textMode)
          const playingThis = bgm.playing === t.code
          return (
            <Fragment key={`${t.ord}:${t.code}`}>
              {i > 0 && <span aria-hidden="true">→</span>}
              {canPlayMusic ? (
                <button type="button" aria-pressed={playingThis}
                  aria-label={playingThis ? `停止播放 BGM：${title}` : `播放 BGM：${title}`}
                  className={cn('inline-flex min-w-0 items-center gap-0.5 rounded px-1 hover:bg-accent',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', playingThis && 'bg-accent text-foreground')}
                  onClick={(ev) => { ev.stopPropagation(); bgm.toggle(t.code, StoryAPI.bgmUrl(gameId, t.code)) }}>
                  {playingThis ? <Square className="h-2.5 w-2.5 shrink-0 fill-current" /> : <Play className="h-2.5 w-2.5 shrink-0" />}
                  <span className="truncate">{title}</span>
                </button>
              ) : (
                <span className="truncate">{title}</span>
              )}
            </Fragment>
          )
        })}
      </span>
    )
  }

  const text = (line: StoryLine) => {
    const ja = line.texts['ja-JP']
    const zh = line.texts['zh-Hant']
    if (textMode === 'ja-JP') return [ja]
    if (textMode === 'zh-Hant') return [zh ?? ja]
    return zh ? [ja, zh] : [ja]
  }

  return (
    <aside className="absolute inset-y-0 right-0 z-30 flex w-full flex-col border-l bg-background shadow-xl sm:w-[440px]"
      aria-label="台詞">
      <header className="space-y-2 border-b p-3">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">
              {TYPE_LABEL[node.type] ?? node.type}
              {node.chapter_label && ` · ${node.chapter_label}`}
              {` · ${total.toLocaleString()} 句`}
            </p>
            <h3 className="truncate text-base font-semibold" title={localizedTitle(node, 'ja-JP') || node.condition || node.key}>
              {localizedTitle(node, 'ja-JP') || node.condition || node.key}
            </h3>
            {node.condition && node.type === 'condition' && localizedTitle(node, 'ja-JP') && (
              <p className="text-xs text-muted-foreground">{node.condition}</p>
            )}
          </div>
          <Button size="icon" variant="ghost" className="h-8 w-8" onClick={onClose} aria-label="關閉">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="台詞語言">
            {(['both', 'ja-JP', 'zh-Hant'] as const).map((m) => (
              <button key={m} type="button" onClick={() => setTextMode(m)} aria-pressed={textMode === m}
                className={cn('rounded px-2 py-0.5', textMode === m ? 'bg-primary text-primary-foreground' : 'hover:bg-accent')}>
                {m === 'both' ? '雙語' : m === 'ja-JP' ? '日' : '繁'}
              </button>
            ))}
          </div>
          {node.line_count ? (
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={nextAnnotated}>
              <ChevronDown className="mr-1 h-3 w-3" />下一句有卡
            </Button>
          ) : null}
          {canSave && (
            <>
              <select className="h-7 rounded-md border bg-background px-1" value={slot} onChange={(e) => setSlot(e.target.value)}
                aria-label="存檔欄位">
                {manifest.save_slots.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
              {manifest.route_presets.length > 0 && (
                <select className="h-7 rounded-md border bg-background px-1" value={preset} onChange={(e) => setPreset(e.target.value)}
                  aria-label="路線旗標" title="疊上進入這條路線的好感旗標（之後的選項仍會影響路線）">
                  <option value="">不加路線旗標</option>
                  {manifest.route_presets.map((p) => <option key={p} value={p}>{tabTitles[p] ?? p} 路線旗標</option>)}
                </select>
              )}
            </>
          )}
        </div>
        {Object.keys(pathVars).length > 0 && (
          <p className="truncate text-[11px] text-muted-foreground" title="沿選取路徑累加的變數，存檔會寫入這些值">
            路徑變數：{Object.entries(pathVars).map(([k, v]) => `${k}=${v}`).join(', ')}
          </p>
        )}
      </header>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {node.type === 'condition' || node.type === 'route_exit' ? (
          <div className="space-y-2 p-4 text-sm">
            <p className="text-muted-foreground">{node.type === 'condition' ? '這是自動分歧點，沒有台詞。去向：' : '路線出口。'}</p>
            {graph.edges.filter((e) => e.from === node.key).map((e) => (
              <button key={e.to} type="button" className="block w-full rounded-md border px-3 py-2 text-left hover:bg-accent"
                onClick={() => onOpenNode(e.to)}>
                {e.label && <span className="mr-2 text-xs text-muted-foreground">{e.label}</span>}
                {localizedTitle(byKey.get(e.to) ?? { title: e.to }, 'ja-JP')}
              </button>
            ))}
          </div>
        ) : lines.isError ? (
          <div className="p-3"><ErrorNote title="無法載入台詞" error={lines.error} /></div>
        ) : lines.isLoading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />載入台詞…</div>
        ) : (
          <ol className="divide-y">
            {all.map((line) => {
              const cards = (line.annotations.cards ?? []).filter((c) => !kindFilter || c.card_type === kindFilter)
              const selected = line.line_id === selectedLine
              // 選項列是選單、不是台詞，不放播放鍵；其他台詞都放，沒有語音時停用
              const showPlay = line.kind !== 'choice'
              const playable = canPlay && !!line.voice
              const playingThis = voice.playing === line.line_id
              return (
                <li key={line.line_id} data-line={line.line_id}
                  className={cn('group relative px-3 py-2 text-sm', selected && 'bg-amber-50 dark:bg-amber-950/30',
                    line.kind === 'bgv' && 'opacity-60')}
                  onClick={() => onSelectLine(selected ? null : line.line_id)}>
                  <div className="flex gap-2">
                    {manifest.capabilities.thumbs && line.thumb && (
                      <img src={StoryAPI.thumbUrl(gameId, line.thumb)} alt="" loading="lazy"
                        className="mt-0.5 h-[52px] w-[92px] shrink-0 rounded object-cover"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }} />
                    )}
                    <div className="min-w-0 flex-1 space-y-0.5">
                      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <span>#{line.seq + 1}</span>
                        {line.speaker && <span className="font-medium text-foreground">{line.speakers['zh-Hant'] && textMode === 'zh-Hant' ? line.speakers['zh-Hant'] : line.speaker}</span>}
                        {line.kind === 'bgv' && <span className="rounded bg-muted px-1">背景語音</span>}
                        {line.attrs.is_h === true && <span className="rounded bg-pink-100 px-1 text-pink-700 dark:bg-pink-950 dark:text-pink-300">H</span>}
                        {bgmChip(line)}
                      </div>
                      {line.kind === 'choice' ? (
                        <div className="space-y-1">
                          <p className="text-xs text-muted-foreground">選項</p>
                          {choiceEdges.length ? choiceEdges.map((e) => (
                            <button key={e.to} type="button" className="block w-full rounded border px-2 py-1 text-left hover:bg-accent"
                              onClick={(ev) => { ev.stopPropagation(); onOpenNode(e.to) }}>
                              {e.label ?? localizedTitle(byKey.get(e.to) ?? { title: e.to }, 'ja-JP')}
                              <span className="ml-1 text-xs text-muted-foreground">→ {localizedTitle(byKey.get(e.to) ?? { title: e.to }, 'ja-JP')}</span>
                            </button>
                          )) : text(line).map((t, i) => <p key={i}>{t}</p>)}
                        </div>
                      ) : (
                        text(line).map((t, i) => (
                          <p key={i} className={cn('whitespace-pre-wrap break-words', i === 1 && 'text-muted-foreground')}>{t}</p>
                        ))
                      )}
                      {(cards.length > 0 || (canSave && line.can_save) || showPlay) && (
                        <div className="flex flex-wrap items-center gap-1 pt-1">
                          {cards.map((c) => (
                            <button key={`${c.card_type}:${c.verb}`} type="button" disabled={jumping === line.line_id}
                              className="rounded-full border border-blue-300 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
                              title={`跳到卡片（${c.note_ids.length} 張筆記）`}
                              onClick={(ev) => { ev.stopPropagation(); jumpToCards(line, c) }}>
                              {c.label}{c.verb ? `：${c.verb}` : ''}
                            </button>
                          ))}
                          <div className="ml-auto flex items-center gap-1">
                            {showPlay && (playable ? (
                              <button type="button" aria-pressed={playingThis}
                                aria-label={playingThis ? '停止播放語音' : '播放這一句的語音'}
                                title={playingThis ? '停止播放' : '播放語音'}
                                className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground',
                                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                  playingThis ? 'bg-accent text-foreground opacity-100' : 'opacity-70 hover:bg-accent hover:opacity-100 group-hover:opacity-100')}
                                onClick={(ev) => { ev.stopPropagation(); playVoice(line) }}>
                                {playingThis ? <Square className="h-3 w-3 fill-current" /> : <Play className="h-3 w-3" />}
                                {playingThis ? '停止' : '語音'}
                              </button>
                            ) : (
                              // 沒有語音：只用「靜音喇叭」圖示表示，不顯示文字或提示框（螢幕閱讀器讀 aria-label）
                              <button type="button" disabled aria-label={canPlay ? '這句沒有語音' : '沒有語音素材來源'}
                                className="inline-flex cursor-not-allowed items-center rounded px-1.5 py-0.5 text-muted-foreground opacity-40"
                                onClick={(ev) => ev.stopPropagation()}>
                                <VolumeX className="h-3 w-3" aria-hidden="true" />
                              </button>
                            ))}
                            {canSave && line.can_save && (
                              <button type="button" disabled={saving === line.line_id}
                                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground opacity-70 hover:bg-accent hover:opacity-100 group-hover:opacity-100"
                                title={line.kind === 'bgv' ? '背景語音沒有自己的位置：下載它播放位置那句的存檔' : '下載這一句的存檔'}
                                onClick={(ev) => { ev.stopPropagation(); download(line) }}>
                                {saving === line.line_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                                存檔
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
        <div ref={sentinel} className="h-8">
          {lines.isFetchingNextPage && <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
      </div>
    </aside>
  )
}
