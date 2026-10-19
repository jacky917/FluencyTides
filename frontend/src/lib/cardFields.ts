/**
 * 卡片欄位編輯用的純函式：找出負責的 handler、決定能否編輯與刪除、判斷哪些欄位不該手動改、
 * 算出改了哪些欄位。
 * Pure helpers for editing card fields: the owning handler, the edit and
 * delete policy, which fields must not be hand-edited, and what changed.
 */

import type { CardDetail, HandlerInfo } from '../types/api'

/**
 * 支援 `update_fields`（直接覆寫欄位）的 handler。其他 handler 的 `execute_update` 不支援這個
 * 動作：外語糾錯只有 `add_audio`、自他動詞與核心動詞是空實作（呼叫會回成功但什麼都沒改），
 * 所以不能讓網頁對它們送出欄位修改。這是手動維護的清單：後端
 * （`backend/app/services/task_handlers/`）有 handler 新增或移除 `update_fields` 支援時，這裡要
 * 跟著改。
 *
 * Handlers whose `execute_update` supports `update_fields`; kept in sync by
 * hand with backend/app/services/task_handlers/.
 */
export const FIELD_EDIT_HANDLERS = new Set(['vocabulary_mining', 'speaking_coach', 'speaking_trilingual'])

/**
 * 依筆記類型找出負責的 handler。
 * Finds the handler that owns a note type.
 * @param handlers - `/handlers/` 的清單。The handler list.
 * @param modelName - 筆記類型。Note type.
 * @returns handler，沒有時為 undefined。The handler, if any.
 */
export function handlerForModel(handlers: HandlerInfo[] | undefined, modelName: string): HandlerInfo | undefined {
  return handlers?.find((handler) => handler.supported_models.includes(modelName))
}

/** 欄位彈窗能做的事。What the field modal may do. */
export interface FieldEditPolicy {
  /** 寫入要送給哪個 handler；不能寫入時為 null。The handler for writes, or null. */
  handlerName: string | null
  canEdit: boolean
  canDelete: boolean
  /** 不能編輯或刪除時給使用者的說明。Why editing or deleting is unavailable. */
  notice: string | null
}

/**
 * 依 handler 清單的狀態決定彈窗能否編輯、刪除，以及要顯示的說明。
 * Decides whether the modal may edit or delete, and what to tell the user.
 *
 * @param state.handlers - handler 清單；還沒載入時為 undefined。The handlers, if loaded.
 * @param state.handlersError - 清單載入失敗的說明；沒有失敗時為 null。The load failure.
 * @param state.modelName - 筆記類型。Note type.
 * @returns 能做的事與說明。The policy.
 */
export function fieldEditPolicy(state: {
  handlers: HandlerInfo[] | undefined
  handlersError: string | null
  modelName: string
}): FieldEditPolicy {
  if (state.handlersError !== null) {
    return {
      handlerName: null,
      canEdit: false,
      canDelete: false,
      notice: `無法取得 handler 清單，暫時無法修改或刪除：${state.handlersError}`,
    }
  }
  if (state.handlers === undefined) {
    return { handlerName: null, canEdit: false, canDelete: false, notice: null }
  }
  const handler = handlerForModel(state.handlers, state.modelName)
  if (!handler) {
    return {
      handlerName: null,
      canEdit: false,
      canDelete: false,
      notice: `沒有 handler 負責 ${state.modelName}，網頁無法修改或刪除這種筆記。`,
    }
  }
  const canEdit = FIELD_EDIT_HANDLERS.has(handler.handler_name)
  return {
    handlerName: handler.handler_name,
    canEdit,
    canDelete: true,
    notice: canEdit ? null : `${handler.handler_name} 不支援直接修改欄位，這裡只能檢視；刪除仍可使用。`,
  }
}

/**
 * 是否為結構化欄位（JSON）：欄位名含 JSON，或內容去掉 HTML 標記、還原 `&quot;` `&amp;` 後
 * 以 `[` / `{` 開頭。這類欄位由模板解析，手動改壞就整張卡顯示不出來，網頁只顯示不編輯。
 * 只放 `[sound:…]` 的音檔欄位也會被判成結構化而唯讀——寧可誤判成唯讀，這是刻意的取捨。
 *
 * Whether a field holds structured JSON, which the templates parse; such
 * fields (and, deliberately, `[sound:…]`-only fields) are read-only.
 *
 * @param name - 欄位名。Field name.
 * @param value - 欄位原始值。Raw value.
 * @returns 是否為結構化欄位。Whether it is structured.
 */
export function isStructuredField(name: string, value: string): boolean {
  if (name.includes('JSON')) return true
  const text = value
    .replace(/<[^>]*>/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .trim()
  return text.startsWith('[') || text.startsWith('{')
}

/**
 * 依筆記類型的順序列出欄位值。Field values in note-type order.
 * @param detail - 筆記詳情。The note detail.
 * @returns `[欄位名, 值]` 陣列。`[name, value]` pairs.
 */
export function orderedFields(detail: CardDetail): [string, string][] {
  return Object.entries(detail.fields)
    .sort(([, a], [, b]) => a.order - b.order)
    .map(([name, field]) => [name, field.value])
}

/**
 * 只取出值有變的欄位，避免把沒動到的欄位（尤其是 JSON）原樣寫回。
 * Only the fields whose value changed.
 * @param original - 原本的值。Original values.
 * @param edited - 編輯後的值。Edited values.
 * @returns 改變的欄位。The changed fields.
 */
export function changedFields(original: Record<string, string>, edited: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(edited).filter(([name, value]) => original[name] !== value))
}

/**
 * 清單中顯示的摘要：第一個有內容、非 JSON 的欄位，去掉 HTML 標記與所有 `[...]`（振假名讀音、
 * sound 標記等）。回傳完整文字，放不下時由畫面截斷。
 * The list preview: the first non-empty, non-JSON field as plain text, in
 * full (the UI clamps it).
 * @param fields - 欄位名 → 原始值（依順序）。Field values in order.
 * @returns 摘要；沒有可顯示的欄位時為空字串。The preview, or ''.
 */
export function cardPreview(fields: Record<string, string>): string {
  for (const [name, value] of Object.entries(fields)) {
    if (isStructuredField(name, value)) continue
    const text = value
      .replace(/<br\s*\/?>/gi, ' ')
      .replace(/<[^>]*>/g, '')
      .replace(/\[[^\]]*\]/g, '')
      .replace(/&nbsp;/g, ' ')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, '&')
      .replace(/\s+/g, ' ')
      .trim()
    if (text) return text
  }
  return ''
}
