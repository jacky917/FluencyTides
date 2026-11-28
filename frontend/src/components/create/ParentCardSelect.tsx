/**
 * 三語卡建卡時「放在哪張卡底下」的選單（`docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md` D6）：列出選的牌組裡
 * 直接放著、而且有可用標題的三語卡（讀主題樹，只展開一層）。選了之後新卡會放進「那張卡的牌組::那張卡的標題」，
 * 子牌組不存在時後端會建立。
 *
 * The "create under which card" select for trilingual cards: lists the titled cards directly in the chosen deck.
 */

import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FluencyTidesAPI } from '../../api/client'

/** 三語卡的 handler 名稱。The trilingual handler. */
const TRILINGUAL_HANDLER = 'speaking_trilingual'

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * 上層卡的選單。The parent card select.
 * @param props.deck - 要建卡的牌組；null 時還不能選。The chosen deck, or null.
 * @param props.value - 選的上層卡 Note ID；null 為直接放在牌組裡。The parent's note ID, or null.
 * @param props.onChange - 改變時。Called with the new parent, or null.
 * @param props.disabled - 建立中時停用。Disabled while busy.
 * @returns 選單 JSX。The select JSX.
 */
export function ParentCardSelect({
  deck,
  value,
  onChange,
  disabled,
}: {
  deck: string | null
  value: number | null
  onChange: (noteId: number | null) => void
  disabled: boolean
}) {
  const id = useId()
  const { data, isLoading, error } = useQuery({
    queryKey: ['graph', TRILINGUAL_HANDLER, deck, null, null, 1],
    queryFn: () => FluencyTidesAPI.getHandlerGraph(TRILINGUAL_HANDLER, { deck, depth: 1 }),
    enabled: deck !== null,
  })
  const parents = (data?.nodes ?? []).filter((node) => node.kind === 'card' && node.depth === 1 && node.title)
  const chosen = parents.find((node) => node.note_id === value)

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm font-medium">
        放在哪張卡底下
      </label>
      <select
        id={id}
        className={SELECT_CLASS}
        value={value ?? ''}
        disabled={disabled || deck === null || isLoading}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">（直接放在選的牌組）</option>
        {/* 記住的上層卡不在這個牌組了（換了牌組）：保留選項並註明，避免畫面和送出的值不一致 */}
        {value !== null && !chosen && <option value={value}>Note {value}（不在這個牌組）</option>}
        {parents.map((node) => (
          <option key={node.id} value={node.note_id ?? ''}>
            {node.title}
          </option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">
        {error
          ? '讀不到這個牌組的三語卡，只能直接放在選的牌組。'
          : chosen
            ? `新卡會放進「${deck}::${chosen.title}」（沒有就自動建立）。`
            : '選一張有標題的卡，新卡就會成為它的子卡，放進同名的子牌組。'}
      </p>
    </div>
  )
}
