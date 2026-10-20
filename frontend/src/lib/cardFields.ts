/**
 * 卡片欄位編輯用的純函式：依後端回傳的權限與 handler 宣告決定能否編輯、刪除與哪些欄位唯讀，
 * 算出改了哪些欄位，以及清單的摘要。
 * Pure helpers for editing card fields: the edit and delete policy from the
 * backend's permissions and the handler's declarations, what changed, and
 * the list preview.
 */

import type { GenericNoteDetail, HandlerInfo, NoteDetail, NotePermissions } from '../types/api'

/** 欄位彈窗能做的事。What the field modal may do. */
export interface NoteEditPolicy {
  /** 寫入要送給哪個 handler；沒有 handler 負責時為 null。The handler for writes, or null. */
  handlerName: string | null
  canEdit: boolean
  canDelete: boolean
  /** 不能修改時的說明（後端給的原因或欄位設定載入失敗）。Why editing is unavailable. */
  editNotice: string | null
  /** 不能刪除時的說明（後端給的原因）。Why deleting is unavailable. */
  deleteNotice: string | null
  /** 唯讀欄位 → 標示（「JSON」或「媒體」），依 handler 宣告。Read-only fields and their label. */
  lockedFields: Map<string, string>
}

/**
 * 依通用讀取回傳的 `permissions` 與 handler 宣告的 JSON、媒體欄位，決定彈窗能做的事
 * （handlers 計畫書 D3、D4）。前端不自行猜測：能否改、刪看後端，唯讀欄位看宣告。
 *
 * Decides what the modal may do from the backend's permissions and the
 * handler's declared JSON and media fields.
 *
 * @param state.note - 筆記與權限。The note with its permissions.
 * @param state.handler - 負責的 handler 的資訊；還沒載入或沒有 handler 時為 undefined。The handler info.
 * @param state.handlersError - handler 清單載入失敗的說明；沒有失敗時為 null。The load failure.
 * @returns 能做的事與說明。The policy.
 */
export function noteEditPolicy(state: {
  note: GenericNoteDetail
  handler: HandlerInfo | undefined
  handlersError: string | null
}): NoteEditPolicy {
  const { note, handler, handlersError } = state
  const { update_fields: update, delete: remove } = note.permissions
  const lockedFields = new Map<string, string>()
  for (const name of handler?.json_fields ?? []) lockedFields.set(name, 'JSON')
  for (const name of handler?.media_fields ?? []) lockedFields.set(name, '媒體')

  let canEdit = update.allowed && note.handler !== null
  let editNotice = update.allowed ? null : update.reason
  // 允許修改，但還不知道哪些欄位唯讀：清單載入失敗就說明，還在載入就先不開放
  if (canEdit && handler === undefined) {
    canEdit = false
    if (handlersError !== null) editNotice = `無法取得欄位設定，暫時無法修改：${handlersError}`
  }
  return {
    handlerName: note.handler,
    canEdit,
    canDelete: remove.allowed && note.handler !== null,
    editNotice,
    deleteNotice: remove.allowed ? null : remove.reason,
    lockedFields,
  }
}

/**
 * 從 handler 的能力算出對它的筆記能做的事，與後端通用讀取的 `permissions` 同一規則（D4）；
 * 給從 handler 讀取筆記的地方（例如圖譜點節點）用。
 * The permissions implied by a handler's capabilities, for notes read
 * through that handler.
 * @param handler - handler 資訊。The handler.
 * @returns 權限。The permissions.
 */
export function permissionsFromCapabilities(handler: HandlerInfo): NotePermissions {
  const { update_fields: update, delete: remove } = handler.capabilities
  return {
    update_fields: { allowed: update.supported, reason: update.supported ? null : update.reason },
    delete: { allowed: remove.supported, reason: remove.supported ? null : remove.reason },
  }
}

/**
 * 欄位名 → 值（依筆記類型的順序；後端已排序）。Field values in note-type order.
 * @param note - 筆記。The note.
 * @returns 欄位名 → 值。Field name to value.
 */
export function fieldValues(note: NoteDetail): Record<string, string> {
  return Object.fromEntries(note.fields.map((field) => [field.name, field.value]))
}

/**
 * 是否為結構化欄位（JSON）：欄位名含 JSON，或內容去掉 HTML 標記、還原 `&quot;` `&amp;` 後
 * 以 `[` / `{` 開頭。只用於清單摘要（跳過這類欄位）；彈窗的唯讀欄位改看 handler 宣告
 * （{@link noteEditPolicy}），不再用它猜。
 *
 * Whether a field looks like structured JSON; only used to skip such fields
 * in the list preview.
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
