/**
 * 外語糾錯草稿的預覽（`docs/wip/server_drafts_FEAT_2026-11-04.md` D10.2）：母卡的內容（原文與錯誤標示、
 * 文法修正、重新組織）一定會建；子卡預設全選、可取消勾選，對應 TG 流程的「輸入編號選子卡」。
 * 生成前選了覆蓋舊卡或允許重複時，在最上方提醒；生成前沒查到重複（例如 Anki 連不上）時提醒，並可改成允許重複。
 *
 * Preview of an expression correction draft: the master is always created;
 * micro cards are all selected by default.
 */

import { Fragment } from 'react'
import { markedSegments, selectableEntries, type ExpressionDraftState } from '../../lib/expressionDraft'
import { cn } from '../../lib/utils'
import { ErrorNote } from '../shared/ErrorNote'
import type { PreviewBodyProps } from './previewTypes'

/**
 * 一段可能有多行的文字（LLM 以換行分段）。A block of possibly multi-line text.
 * @param props.label - 標題。The label.
 * @param props.children - 內容。The content.
 * @returns 區塊 JSX。The block JSX.
 */
function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      <div className="whitespace-pre-line break-words rounded-md border px-3 py-2 text-base leading-relaxed">
        {children}
      </div>
    </div>
  )
}

/**
 * 外語糾錯的預覽。The expression correction preview.
 * @param props.state - 編輯狀態。The editing state.
 * @param props.onChange - 勾選改變時。Called with the new state.
 * @param props.disabled - 建立中時停用。Disabled while busy.
 * @returns 預覽 JSX。The preview JSX.
 */
export function ExpressionPreviewBody({ state, onChange, disabled }: PreviewBodyProps<ExpressionDraftState>) {
  const draft = state.draft
  if (!draft) {
    return (
      <ErrorNote
        title="草稿內容不完整"
        error={{ error_code: 'DRAFT_MALFORMED', message: '生成結果的格式不對，請廢棄後重新生成。' }}
      />
    )
  }
  const result = draft.llm_result
  const entries = selectableEntries(result)
  const chosen = new Set(state.selected)
  const toggle = (number: number) =>
    onChange({
      ...state,
      selected: chosen.has(number) ? state.selected.filter((n) => n !== number) : [...state.selected, number],
    })
  const allChosen = entries.length > 0 && entries.every((entry) => chosen.has(entry.number))

  return (
    <div className="space-y-5">
      {draft.replace_note_id !== null && (
        <p role="status" className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm">
          覆蓋舊卡（Note ID {draft.replace_note_id}）：建立成功後會刪掉這張舊母卡與它的子卡。
        </p>
      )}
      {draft.replace_note_id === null && state.checkSkipped !== null && (
        // 生成前查不到重複（例如 Anki 暫時連不上）：真的重複時建立會被 Anki 擋下，這裡可以改成允許重複
        <div role="status" className="space-y-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm">
          <p>{state.checkSkipped}，生成前沒有檢查重複。若 Anki 已有同一句原文的卡，建立會被擋下。</p>
          <label className={cn('flex items-center gap-2', disabled ? 'cursor-not-allowed' : 'cursor-pointer')}>
            <input
              type="checkbox"
              checked={draft.allow_duplicate}
              disabled={disabled}
              onChange={(e) => onChange({ ...state, draft: { ...draft, allow_duplicate: e.target.checked } })}
            />
            允許重複（已有同一句原文的卡也照樣建立）
          </label>
        </div>
      )}
      {draft.replace_note_id === null && state.checkSkipped === null && draft.allow_duplicate && (
        <p role="status" className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm">
          另建新卡：Anki 裡會有兩張相同原文的卡。
        </p>
      )}

      <Block label="原文（錯的地方畫底線）">
        {markedSegments(result.error_comparison || draft.original_text).map((segment, index) => (
          <Fragment key={index}>
            {segment.marked ? (
              <span className="text-destructive underline decoration-destructive decoration-2 underline-offset-4">
                {segment.text}
              </span>
            ) : (
              segment.text
            )}
          </Fragment>
        ))}
      </Block>
      <Block label="文法修正">{result.grammar_correction}</Block>
      <Block label="重新組織">{result.reorganized_expression}</Block>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium">
            子卡（已選 {state.selected.length}／{entries.length}）
          </p>
          {entries.length > 0 && (
            <button
              type="button"
              disabled={disabled}
              className="text-xs text-primary underline-offset-4 hover:underline disabled:opacity-50"
              onClick={() => onChange({ ...state, selected: allChosen ? [] : entries.map((entry) => entry.number) })}
            >
              {allChosen ? '全不選' : '全選'}
            </button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">母卡一定會建立；子卡只建勾選的。</p>
        {entries.length === 0 ? (
          <p className="text-xs text-muted-foreground">沒有子卡，只會建立母卡。</p>
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => {
              const checked = chosen.has(entry.number)
              const { point } = entry
              return (
                <li key={entry.number}>
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
                      onChange={() => toggle(entry.number)}
                    />
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="flex flex-wrap items-baseline gap-2">
                        <span className="text-xs tabular-nums text-muted-foreground">#{entry.number}</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{entry.label}</span>
                        <span className="break-words text-base font-medium">{point.target_phrase}</span>
                      </span>
                      {point.context_sentence && (
                        <span className="block break-words leading-relaxed">{point.context_sentence}</span>
                      )}
                      {point.native_translation && (
                        <span className="block break-words text-sm text-muted-foreground">{point.native_translation}</span>
                      )}
                      {point.error_hint && (
                        <span className="block break-words border-l-2 pl-2 text-xs leading-relaxed text-muted-foreground">
                          {point.error_hint}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
