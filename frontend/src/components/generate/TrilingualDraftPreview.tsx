/**
 * 三語對話卡草稿的預覽：卡面的情境與補充可修改；三種語言的說法依熟識度排序，預設全選、可取消勾選。
 * 刻度條的配色與卡片背面相同（`lib/trilingualDraft.ts`）。
 *
 * 三種語言各是一個可折疊的區塊（標題列顯示已選幾句），句子多時可以收起已看完的語言。
 *
 * Preview of a trilingual draft: the prompt and context are editable; the
 * sayings are sorted by familiarity, all selected by default.
 */

import { useId, useState } from 'react'
import {
  FAMILIARITY_COLORS,
  REFERENCE_LANGS,
  familiarityOrder,
  type DraftSelection,
  type ReferenceKey,
} from '../../lib/trilingualDraft'
import { cn } from '../../lib/utils'
import type { TrilingualCreateParams } from '../../types/api'
import { CollapsibleSection, TEXTAREA_CLASS } from './formParts'

/**
 * 熟識度刻度條：十格，熟識度 N 填滿 N+1 格，右邊是數字（配色同卡片背面）。
 * The familiarity bar, as on the card back.
 * @param props.value - 熟識度 0–9。The familiarity.
 * @returns 刻度條 JSX。The bar JSX.
 */
function FamiliarityBar({ value }: { value: number }) {
  const color = FAMILIARITY_COLORS[value]
  return (
    <span className="inline-flex shrink-0 items-center gap-[2px]" title={`熟識度 ${value}／9`}>
      {FAMILIARITY_COLORS.map((_, index) => (
        <span
          key={index}
          className="h-[10px] w-[6px] rounded-[1px]"
          style={{ backgroundColor: index <= value ? color : 'hsl(var(--muted))' }}
        />
      ))}
      {/* 數字用一般文字色：淺藍等低熟識度的顏色在亮色主題上太淡 */}
      <b className="ml-1 w-3 text-xs tabular-nums text-foreground/70">{value}</b>
    </span>
  )
}

/**
 * 三語草稿的預覽。The trilingual draft preview.
 * @param props.draft - 草稿。The draft.
 * @param props.selection - 勾選狀態。The selection.
 * @param props.onSelectionChange - 勾選改變時。Called with the new selection.
 * @param props.prompt - 卡面情境（可修改）。The prompt.
 * @param props.onPromptChange - 情境改變時。Called on prompt edits.
 * @param props.context - 補充（可修改）。The context.
 * @param props.onContextChange - 補充改變時。Called on context edits.
 * @param props.disabled - 建立中時停用。Disabled while creating.
 * @returns 預覽 JSX。The preview JSX.
 */
export function TrilingualDraftPreview({
  draft,
  selection,
  onSelectionChange,
  prompt,
  onPromptChange,
  context,
  onContextChange,
  disabled,
}: {
  draft: TrilingualCreateParams
  selection: DraftSelection
  onSelectionChange: (selection: DraftSelection) => void
  prompt: string
  onPromptChange: (value: string) => void
  context: string
  onContextChange: (value: string) => void
  disabled: boolean
}) {
  const id = useId()
  const [closed, setClosed] = useState<Partial<Record<ReferenceKey, boolean>>>({})

  const toggle = (key: ReferenceKey, index: number) =>
    onSelectionChange({ ...selection, [key]: selection[key].map((on, i) => (i === index ? !on : on)) })
  const setAll = (key: ReferenceKey, on: boolean) =>
    onSelectionChange({ ...selection, [key]: selection[key].map(() => on) })

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <label htmlFor={`${id}-prompt`} className="text-sm font-medium">
          卡面情境<span className="ml-1 text-destructive">*</span>
        </label>
        <textarea
          id={`${id}-prompt`}
          value={prompt}
          disabled={disabled}
          onChange={(e) => onPromptChange(e.target.value)}
          className={cn(TEXTAREA_CLASS, !prompt.trim() && 'border-destructive')}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${id}-context`} className="text-sm font-medium">
          補充
        </label>
        <textarea
          id={`${id}-context`}
          value={context}
          disabled={disabled}
          placeholder="（選填）"
          onChange={(e) => onContextChange(e.target.value)}
          className={cn(TEXTAREA_CLASS, 'min-h-[48px]')}
        />
      </div>

      {REFERENCE_LANGS.map(({ key, label }) => {
        const references = draft[key]
        const chosen = selection[key].filter(Boolean).length
        return (
          <CollapsibleSection
            key={key}
            title={label}
            summary={`已選 ${chosen}／${references.length}`}
            open={!closed[key]}
            onOpenChange={(open) => setClosed((prev) => ({ ...prev, [key]: !open }))}
          >
            {references.length > 0 && (
              <div className="mb-2 flex justify-end">
                <button
                  type="button"
                  disabled={disabled}
                  className="text-xs text-primary underline-offset-4 hover:underline disabled:opacity-50"
                  onClick={() => setAll(key, chosen < references.length)}
                >
                  {chosen < references.length ? '全選' : '全不選'}
                </button>
              </div>
            )}
            {references.length === 0 ? (
              <p className="text-xs text-muted-foreground">沒有生成這個語言的說法。</p>
            ) : (
              <ul className="space-y-2">
                {familiarityOrder(references).map((index) => {
                  const reference = references[index]
                  const checked = selection[key][index]
                  return (
                    <li key={index}>
                      <label
                        className={cn(
                          'flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2',
                          checked ? 'border-primary/50 bg-primary/5' : 'opacity-60',
                          disabled && 'cursor-not-allowed',
                        )}
                      >
                        <input
                          type="checkbox"
                          className="mt-1.5"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggle(key, index)}
                        />
                        <span className="min-w-0 flex-1 space-y-1">
                          {reference.familiarity !== null && <FamiliarityBar value={reference.familiarity} />}
                          <span className="block break-words text-base leading-relaxed">{reference.content}</span>
                          {reference.note && (
                            <span className="block break-words border-l-2 pl-2 text-xs leading-relaxed text-muted-foreground">
                              {reference.note}
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
            )}
          </CollapsibleSection>
        )
      })}
    </div>
  )
}
