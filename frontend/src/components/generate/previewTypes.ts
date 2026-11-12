/**
 * 預覽流程各卡片類型共用的介面（`PreviewCreate.tsx`）：每種卡片提供自己的預覽畫面與「編輯狀態 → 建立參數」，
 * 建立、廢棄、自動存檔、衝突處理由 `PreviewCreate` 統一處理。
 *
 * The per-card-type pieces of the preview create flow.
 */

import type { ComponentType } from 'react'
import type { ServerDraft } from '../../types/api'

/**
 * 一份草稿的編輯狀態：存回後端的 `state`，是 JSON 物件（各卡片類型以 type 而不是 interface 宣告），
 * 一律帶著要加的標籤。The editing state saved as the draft's `state`.
 */
export type DraftStateBase = Record<string, unknown> & {
  /** 要加的標籤（LLM 建議的預設全加，可增減）。The tags to add. */
  tags: string[]
}

/** 預覽畫面的 props。Props of a preview body. */
export interface PreviewBodyProps<S extends DraftStateBase> {
  state: S
  /** 修改（自動存回後端）。Edits the state. */
  onChange: (next: S) => void
  /** 建立中、廢棄中時停用。Disabled while busy. */
  disabled: boolean
}

/** 一種卡片的預覽。One card type's preview. */
export interface PreviewKind<S extends DraftStateBase> {
  /** 生成中的說明（要多久、在做什麼）。What generating means for this card type. */
  generatingHint: string
  /** 沒有存過的編輯狀態時，由生成結果產生的預設值。The default state from the result. */
  initialState: (draft: ServerDraft) => S
  /** 建立參數。The create parameters. */
  params: (state: S) => Record<string, unknown>
  /** 還不能建立的原因（牌組以外的），可以建立時為 null。Why it cannot be created yet. */
  blockedReason: (state: S) => string | null
  /** 建立按鈕的文字。The create button label. */
  createLabel: (state: S) => string
  /** 預覽上方顯示的輸入摘要；不需要時為 null。A one-line summary of the input. */
  inputSummary: (input: Record<string, string>) => string | null
  Body: ComponentType<PreviewBodyProps<S>>
}
