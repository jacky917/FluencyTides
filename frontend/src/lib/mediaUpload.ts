/**
 * 上傳媒體到 Anki 用的純函式（`docs/wip/media_upload_FEAT_2026-10-20.md` D2–D4）：種類、大小上限、
 * 從欄位值取出檔名、錄音格式的選擇、顯示用的大小與時間。
 *
 * Pure helpers for uploading media to Anki: kinds, size limits, reading the
 * file name out of a field value, picking a recording format, and display
 * formatting.
 *
 * 大小與格式的檢查以後端為準（`POST /media` 依檔案內容判斷）；這裡只在送出前擋掉明顯超過上限的檔案，
 * 省一次上傳。上限要和後端 `media_service.MAX_MEDIA_BYTES` 一致。
 */

/** 媒體種類。A media kind. */
export type MediaKind = 'audio' | 'image'

/** 大小上限（位元組），和後端相同。Size limits in bytes, matching the backend. */
export const MAX_MEDIA_BYTES: Record<MediaKind, number> = {
  audio: 10 * 1024 * 1024,
  image: 5 * 1024 * 1024,
}

/** 選檔視窗的篩選（只是方便選，後端另外依內容檢查）。The file picker filter. */
export const MEDIA_ACCEPT: Record<MediaKind, string> = {
  audio: 'audio/*,.mp3,.ogg,.wav,.m4a,.webm',
  image: 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp',
}

/** 顯示給使用者看的格式說明。The accepted formats, as shown to the user. */
export const MEDIA_FORMATS: Record<MediaKind, string> = {
  audio: 'mp3、ogg、wav、m4a、webm',
  image: 'jpg、png、webp',
}

/** 種類的中文名稱。The kind's label. */
export const MEDIA_LABEL: Record<MediaKind, string> = { audio: '音檔', image: '圖片' }

/**
 * schema 的 `x-ui` 是不是媒體欄位；不是時為 null。The media kind of an `x-ui` value, or null.
 * @param xui - `x-ui` 的值。The `x-ui` value.
 * @returns 種類，或 null。The kind, or null.
 */
export function mediaKindOf(xui: unknown): MediaKind | null {
  return xui === 'audio' || xui === 'image' ? xui : null
}

/**
 * 檔案太大時的說明（送出前先擋）；沒超過時為 null。Why a file is too large, or null.
 * @param kind - 種類。The kind.
 * @param size - 檔案大小（位元組）。The file size in bytes.
 * @returns 說明，或 null。The message, or null.
 */
export function oversizeMessage(kind: MediaKind, size: number): string | null {
  const limit = MAX_MEDIA_BYTES[kind]
  if (size <= limit) return null
  return `${MEDIA_LABEL[kind]}最大 ${limit / (1024 * 1024)} MB，這個檔案是 ${formatBytes(size)}`
}

/**
 * 位元組數寫成 KB、MB。Formats a byte count.
 * @param size - 位元組數。The size in bytes.
 * @returns 例如 `48 KB`、`1.3 MB`。The text.
 */
export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * 秒數寫成 `分:秒`。Formats seconds as `m:ss`.
 * @param seconds - 秒數。Seconds.
 * @returns 例如 `0:07`、`1:23`。The text.
 */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** 後端 `POST /media` 產生的檔名（`web_{種類}_{UTC 時間}_{隨機 6 碼}.{副檔名}`）。 */
const UPLOADED_NAME = /^web_(audio|image)_\d{8}_\d{6}_[0-9a-f]{6}\.[a-z0-9]+$/

/**
 * 是不是 `POST /media` 產生的檔名。Whether a name was produced by `POST /media`.
 * @param name - 檔名。The name.
 * @returns 是否符合格式。Whether it matches.
 */
export function isUploadedName(name: string): boolean {
  return UPLOADED_NAME.test(name)
}

/**
 * 從媒體欄位的值取出檔名：`[sound:檔名]` 或 `<img src="檔名">`（正好一個、沒有其他內容）。欄位是空的、
 * 有多個檔案或夾了其他文字時回傳 null——那種內容不能當成單一檔案預覽。
 *
 * Reads the single file name out of a media field value (`[sound:…]` or
 * `<img src="…">`); null when the value is empty or holds anything else.
 *
 * @param kind - 欄位的種類。The field's kind.
 * @param value - 欄位的原始值。The raw field value.
 * @returns 檔名，或 null。The file name, or null.
 */
export function mediaNameFromField(kind: MediaKind, value: string): string | null {
  const text = value.trim()
  const match =
    kind === 'audio' ? /^\[sound:([^\]]+)\]$/.exec(text) : /^<img\s+src=(?:"([^"]+)"|'([^']+)')\s*\/?>$/i.exec(text)
  const name = match ? (match[1] ?? match[2]) : null
  if (!name) return null
  return decodeEntities(name)
}

/** 還原檔名裡常見的 HTML 跳脫（`<img src>` 裡的 `&amp;` 等）。Decodes common HTML entities. */
function decodeEntities(text: string): string {
  return text.replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
}

/**
 * 媒體欄位目前顯示的狀態（建卡時「原本的值」是空字串）：
 * - `file`：一個可以預覽的檔案（原本的，或剛上傳的）。
 * - `other`：欄位有內容，但不是單一檔案（多個音檔、夾了文字）——照原樣顯示，上傳新檔會取代它。
 * - `empty`：沒有內容。
 *
 * What a media field shows while editing a note.
 */
export type MediaFieldState =
  | { kind: 'empty' }
  | { kind: 'file'; name: string; uploaded: boolean }
  | { kind: 'other'; raw: string }

/**
 * 算出媒體欄位目前顯示的狀態。編輯中的值有三種：和原本一樣（還沒動）、空字串（清掉了）、
 * 剛上傳取得的檔名（後端寫入時才包成 Anki 的寫法）。
 *
 * The display state of a media field being edited.
 *
 * @param kind - 欄位的種類。The field's kind.
 * @param original - 欄位原本的值。The field's original raw value.
 * @param edited - 編輯中的值。The value being edited.
 * @returns 顯示的狀態。The display state.
 */
export function mediaFieldState(kind: MediaKind, original: string, edited: string): MediaFieldState {
  if (edited !== original) {
    const name = edited.trim()
    return name ? { kind: 'file', name, uploaded: true } : { kind: 'empty' }
  }
  if (!original.trim()) return { kind: 'empty' }
  const name = mediaNameFromField(kind, original)
  return name ? { kind: 'file', name, uploaded: false } : { kind: 'other', raw: original }
}

/** 錄音最長幾秒：到了自動停止，免得忘了按停止一直錄下去。The longest recording, in seconds. */
export const MAX_RECORDING_SECONDS = 300

/** 瀏覽器錄音可能支援的格式，依偏好排序（後端都收：webm、m4a 會轉成 mp3，ogg 原樣保留）。 */
const RECORDING_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

/**
 * 選一個這個瀏覽器能錄的格式；都不支援時回傳 undefined（交給瀏覽器用預設值）。
 * Picks a recording MIME type the browser supports, or undefined for its default.
 * @param isSupported - `MediaRecorder.isTypeSupported`。The support check.
 * @returns MIME 類型，或 undefined。The MIME type, or undefined.
 */
export function pickRecordingType(isSupported: (type: string) => boolean): string | undefined {
  return RECORDING_TYPES.find((type) => isSupported(type))
}

/**
 * 錄音的檔名（只給上傳表單用，後端不採用）：副檔名依實際錄到的格式。
 * A file name for a recording; the extension follows the recorded type.
 * @param mimeType - 錄到的 MIME 類型。The recorded MIME type.
 * @returns 檔名。The file name.
 */
export function recordingFileName(mimeType: string): string {
  const type = mimeType.toLowerCase()
  const extension = type.includes('mp4') || type.includes('aac') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'
  return `recording.${extension}`
}

/**
 * 麥克風錄音失敗時給使用者看的說明（依 `getUserMedia` 的錯誤名稱）。
 * A readable message for a microphone failure.
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 說明。The message.
 */
export function recordingErrorMessage(error: unknown): string {
  const name = (error as { name?: string } | null)?.name
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return '瀏覽器不允許使用麥克風：請在網址列允許這個網站使用麥克風後再試（需要 HTTPS 或 localhost）。'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return '找不到麥克風。'
  if (name === 'NotReadableError') return '麥克風被其他程式佔用，或無法讀取。'
  const message = (error as { message?: string } | null)?.message
  return `無法開始錄音${message ? `：${message}` : ''}`
}
