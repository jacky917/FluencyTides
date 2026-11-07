/**
 * 後端建卡草稿的純函式：輪詢間隔、錯誤的分類、編輯狀態的初始值、要記住哪份草稿、何時通知
 * （`docs/wip/server_drafts_FEAT_2026-11-04.md`）。畫面與 hook 只負責呼叫，判斷都在這裡，方便測試。
 *
 * Pure helpers for the backend create draft.
 */

import type { DraftStatus, DuplicateCard, ServerDraft } from '../types/api'

/** 目前草稿的 query key（全站共用一份快取）。Query key of the current draft. */
export const CURRENT_DRAFT_QUERY_KEY = ['draft', 'current'] as const

/**
 * localStorage：記住看過的生成草稿 ID。後端說沒有草稿、這裡卻還記得時，表示草稿在別處建立或廢棄，
 * 或伺服器重新啟動過（D11）。填表建卡的佔用（`form_creating`）不記。
 * localStorage key remembering the last seen generated draft ID.
 */
export const CURRENT_DRAFT_ID_KEY = 'current_draft_id'

/**
 * localStorage：編輯狀態的備份，每次修改都先寫一份，存回後端後刪除（D5）。
 * localStorage key of the editing state backup.
 * @param draftId - 草稿 ID。The draft.
 * @returns 鍵。The key.
 */
export function draftBackupKey(draftId: string): string {
  return `draft_state_backup:${draftId}`
}

/** 沒存回後端的編輯狀態備份。An unsaved editing state. */
export interface DraftBackup<S> {
  /** 編輯時讀到的草稿版本。The draft version the edit was based on. */
  version: number
  state: S
}

/** 會附上目前草稿的錯誤碼（409）。Error codes that carry the current draft. */
const DRAFT_ERROR_CODES = new Set([
  'CREATE_SESSION_BUSY',
  'DRAFT_BUSY',
  'DRAFT_VERSION_CONFLICT',
  'DRAFT_NOT_FAILED',
])

/**
 * 還在進行（生成中、建立中、填表建卡佔用），要輪詢。Whether the draft is still in progress.
 * @param status - 草稿狀態。The status.
 * @returns 是否進行中。Whether in progress.
 */
export function isInProgress(status: DraftStatus): boolean {
  return status === 'generating' || status === 'creating' || status === 'form_creating'
}

/**
 * TanStack Query 的 `refetchInterval`：進行中才輪詢。Poll only while in progress.
 * @param draft - 目前的草稿。The current draft.
 * @param ms - 間隔毫秒。The interval.
 * @returns 間隔，或 false（不輪詢）。The interval or false.
 */
export function pollInterval(draft: ServerDraft | null | undefined, ms: number): number | false {
  return draft && isInProgress(draft.status) ? ms : false
}

/**
 * 要不要記住這份草稿的 ID：只記生成草稿，填表建卡的佔用不記（它結束時本來就會消失，不是遺失）。
 * Whether to remember the draft ID; form create placeholders are not remembered.
 * @param draft - 草稿。The draft.
 * @returns 是否記住。Whether to remember it.
 */
export function shouldRemember(draft: ServerDraft | null | undefined): draft is ServerDraft {
  return Boolean(draft) && draft!.status !== 'form_creating'
}

/**
 * 從 API 錯誤取出附上的目前草稿（409 `CREATE_SESSION_BUSY`、`DRAFT_*`）；404 `DRAFT_NOT_FOUND` 為 null；
 * 其他錯誤為 undefined。附草稿的錯誤碼卻沒附草稿時也是 null（後端一律會附，不該發生）。
 * The current draft carried by an API error: a draft, null when it is gone, or undefined otherwise.
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 草稿、null 或 undefined。
 */
export function draftFromError(error: unknown): ServerDraft | null | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const err = error as { error_code?: string; draft?: ServerDraft | null }
  if (err.error_code === 'DRAFT_NOT_FOUND') return null
  if (err.error_code && DRAFT_ERROR_CODES.has(err.error_code)) return err.draft ?? null
  return undefined
}

/**
 * 409 `DUPLICATE_FOUND`（生成前發現 Anki 已有同一張卡）附上的舊卡；其他錯誤為 null。
 * The existing card carried by a `DUPLICATE_FOUND` error, or null.
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 舊卡或 null。The old card or null.
 */
export function duplicateFromError(error: unknown): DuplicateCard | null {
  if (typeof error !== 'object' || error === null) return null
  const err = error as { error_code?: string; duplicate?: unknown }
  if (err.error_code !== 'DUPLICATE_FOUND') return null
  const duplicate = err.duplicate as Partial<DuplicateCard> | null | undefined
  // 後端一律會附；沒附時仍讓使用者決定，只是看不到舊卡內容
  return duplicate && typeof duplicate.note_id === 'number' ? (duplicate as DuplicateCard) : { note_id: 0 }
}

/**
 * 錯誤碼。The backend error code, if any.
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 錯誤碼。The error code.
 */
export function errorCode(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  return (error as { error_code?: string }).error_code
}

/**
 * 自動存檔失敗的分類，決定接下來怎麼做（D5）。
 * - `conflict`：另一個地方改過，讓使用者選載入最新或覆蓋。
 * - `gone`：草稿已不在，告知使用者。
 * - `busy`：草稿現在不能改（另一個分頁正在建立等），保留修改，等它回到可建立再送。
 * - `transient`：沒有回應或伺服器錯誤（5xx），稍後自動重送。
 * - `rejected`：後端拒絕（例如內容太大、未授權），重送也不會成功，顯示原因。
 * The kind of an autosave failure.
 */
export type SaveFailure =
  | { kind: 'conflict'; latest: ServerDraft }
  | { kind: 'gone' }
  | { kind: 'busy'; latest: ServerDraft }
  | { kind: 'transient' }
  | { kind: 'rejected' }

/**
 * 自動存檔失敗的分類。Classifies an autosave failure.
 * @param error - 捕捉到的錯誤（API client 的 rejection）。The caught error.
 * @returns 分類。The failure kind.
 */
export function classifySaveError(error: unknown): SaveFailure {
  const latest = draftFromError(error)
  if (latest === null) return { kind: 'gone' }
  if (latest !== undefined) {
    return errorCode(error) === 'DRAFT_VERSION_CONFLICT' ? { kind: 'conflict', latest } : { kind: 'busy', latest }
  }
  const status = (error as { status?: number; response?: { status?: number } } | null)?.status
    ?? (error as { response?: { status?: number } } | null)?.response?.status
  return status === undefined || status >= 500 ? { kind: 'transient' } : { kind: 'rejected' }
}

/**
 * 打開一份草稿時的編輯狀態：有本機備份就用備份（還沒存回去的修改），否則用後端存的，都沒有用預設。
 * 備份若是根據較舊的版本做的，仍然用它並標成沒存：送出時會因版本不符而讓使用者選「載入最新」或
 * 「用這裡的覆蓋」，不會默默丟掉這台裝置的修改。
 * The editing state when opening a draft.
 * @param draft - 後端的草稿。The draft.
 * @param backup - 本機的備份。The local backup.
 * @param fallback - 預設的編輯狀態（由生成結果產生）。The default state.
 * @returns 編輯狀態、它依據的版本、是否有沒存回去的修改。The state, its base version and whether it is unsaved.
 */
export function initialEditState<S>(
  draft: ServerDraft,
  backup: DraftBackup<S> | null,
  fallback: () => S,
): { state: S; version: number; dirty: boolean } {
  if (backup) return { state: backup.state, version: backup.version, dirty: true }
  if (draft.state) return { state: draft.state as S, version: draft.version, dirty: false }
  return { state: fallback(), version: draft.version, dirty: false }
}

/**
 * 後端的草稿版本比這裡新時怎麼辦：
 * - `rebase`：內容沒變（只是建立中、退回這類狀態轉換讓版本加了），保留這裡的內容，改用新版本；
 * - `adopt`：別處改了內容、這裡沒有沒存的修改，直接採用最新的；
 * - `conflict`：別處改了內容、這裡也有沒存的修改，讓使用者選「載入最新」或「用這裡的覆蓋」；
 * - `none`：版本沒有更新。
 * What to do when the backend draft has a newer version.
 */
export type RebaseDecision = 'none' | 'rebase' | 'adopt' | 'conflict'

/**
 * 比對後端最新的草稿與這裡最後知道的內容。Decides how to follow a newer draft.
 * @param latest - 後端最新的草稿。The latest draft.
 * @param knownVersion - 這裡目前依據的版本。The version the local state is based on.
 * @param knownServerState - 這裡最後知道的後端內容（JSON 字串，null 的內容為 `"null"`）。The last known server state.
 * @param dirty - 這裡有沒有沒存回去的修改。Whether there are unsaved edits.
 * @returns 處理方式。The decision.
 */
export function rebaseDecision(
  latest: ServerDraft,
  knownVersion: number,
  knownServerState: string,
  dirty: boolean,
): RebaseDecision {
  if (latest.version <= knownVersion) return 'none'
  if (JSON.stringify(latest.state ?? null) === knownServerState) return 'rebase'
  return dirty ? 'conflict' : 'adopt'
}

/** 全站通知要做的事。What the site-wide watcher should do. */
export interface DraftNotice {
  /** 要記住的草稿 ID；null 表示忘掉；undefined 表示不動。The ID to remember, null to forget. */
  remember: string | null | undefined
  /** 要跳的通知。The notice to show. */
  notify: 'lost' | 'ready' | 'failed' | null
}

/**
 * 全站通知的判斷（D9、D11）：記住看過的生成草稿；後端沒有草稿、本機卻記得時通知「已不在」；
 * 同一份草稿從生成中變成可建立或失敗時通知。
 * Decides the site-wide notices.
 * @param before - 上一次看到的草稿（id 與狀態）。The previously seen draft.
 * @param current - 這次讀到的草稿。The draft just read.
 * @param remembered - localStorage 記住的草稿 ID。The remembered ID.
 * @returns 要記住的 ID 與要跳的通知。What to remember and notify.
 */
export function draftNotice(
  before: Pick<ServerDraft, 'id' | 'status'> | null,
  current: ServerDraft | null,
  remembered: string | null,
): DraftNotice {
  if (!current) {
    return remembered ? { remember: null, notify: 'lost' } : { remember: undefined, notify: null }
  }
  const remember = shouldRemember(current) && remembered !== current.id ? current.id : undefined
  const finished =
    before !== null && before.id === current.id && before.status === 'generating' && current.status !== 'generating'
  if (finished && current.status === 'ready') return { remember, notify: 'ready' }
  if (finished && current.status === 'failed') return { remember, notify: 'failed' }
  return { remember, notify: null }
}

/**
 * 時間的顯示（本地時區，時:分:秒）。A local HH:mm:ss label.
 * @param iso - ISO 時間。The time.
 * @returns 顯示用字串；沒有時為空字串。The label.
 */
export function timeLabel(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-TW', { hour12: false })
}

/**
 * 草稿的輸入轉成表單的字串值（表單只處理字串）。The draft input as form strings.
 * @param input - 草稿的輸入。The draft input.
 * @returns 欄位名到字串。Field values.
 */
export function inputAsValues(input: Record<string, unknown> | null): Record<string, string> {
  const values: Record<string, string> = {}
  for (const [key, value] of Object.entries(input ?? {})) {
    if (value === null || value === undefined) continue
    values[key] = typeof value === 'string' ? value : String(value)
  }
  return values
}
