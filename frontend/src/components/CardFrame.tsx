import { useEffect, useMemo, useRef, useState } from 'react'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, isBackendError } from '../lib/apiError'
import { buildCardDocument, createFrameMessageHandler, type ScrollGesture } from '../lib/ankiFrame'
import { cn } from '../lib/utils'

/** iframe 的最小高度（內容回報高度前先用這個）。Minimum frame height. */
const MIN_HEIGHT = 240

/** 一個載入失敗的媒體檔與原因。A media file that failed to load. */
interface MediaFailure {
  name: string
  reason: string
}

/** 卡面的狀態訊息：載入失敗的媒體檔、卡面呼叫了網頁版不支援的功能、腳本錯誤。The messages of a face. */
export interface CardFrameStatus {
  failedMedia: MediaFailure[]
  notices: string[]
  frameErrors: string[]
}

/** 每份 iframe 文件的識別碼（只含英數字，會嵌進注入的腳本）。A per-document nonce. */
const newNonce = () => Math.random().toString(36).slice(2) + Date.now().toString(36)

/**
 * 以 sandbox iframe 顯示 Anki 渲染好的卡面，並代為下載卡面要用的媒體檔。
 * Shows an Anki-rendered card face in a sandboxed iframe and fetches the
 * media files it asks for (see `lib/ankiFrame.ts`).
 *
 * @param props.html - 正面或背面的 HTML。The rendered face.
 * @param props.ord - 卡片模板序號。Template ordinal.
 * @param props.title - iframe 的無障礙名稱。Accessible name.
 * @param props.onBrowse - 卡面要打開這些筆記（「查看母卡片」等按鈕）。Opens the notes a template asks for.
 * @param props.onFaceClick - 點了卡面上不是按鈕的地方；不給時不理會。A click outside any control.
 * @param props.onHeight - 卡面回報內容高度時。Called with each reported content height.
 * @param props.onScrollGesture - 卡面內的捲動手勢。Scroll gestures inside the face.
 * @param props.onStatus - 狀態訊息（掛載時與每次變化時）。CardFrame 自己不顯示訊息，不給時訊息
 *   不會出現；呼叫端用 {@link CardFrameMessages} 畫在卡面外，切換動畫中才會跟著外框下緣移動。
 *   Called on mount and on every change; CardFrame shows no messages itself.
 * @returns 有邊框的 iframe。The framed iframe.
 */
export function CardFrame({
  html,
  ord,
  title,
  onBrowse,
  onFaceClick,
  onHeight,
  onScrollGesture,
  onStatus,
}: {
  html: string
  ord: number
  title: string
  onBrowse: (noteIds: number[]) => void
  onFaceClick?: () => void
  onHeight?: (height: number) => void
  onScrollGesture?: (gesture: ScrollGesture) => void
  onStatus?: (status: CardFrameStatus) => void
}) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(MIN_HEIGHT)
  const [failedMedia, setFailedMedia] = useState<MediaFailure[]>([])
  const [frameErrors, setFrameErrors] = useState<string[]>([])
  const [notices, setNotices] = useState<string[]>([])
  // 用 ref 取最新的 onBrowse：不必因為父元件重新渲染就重掛訊息處理函式
  const onBrowseRef = useRef(onBrowse)
  const onFaceClickRef = useRef(onFaceClick)
  const onHeightRef = useRef(onHeight)
  const onScrollGestureRef = useRef(onScrollGesture)
  const onStatusRef = useRef(onStatus)
  useEffect(() => {
    onBrowseRef.current = onBrowse
    onFaceClickRef.current = onFaceClick
    onHeightRef.current = onHeight
    onScrollGestureRef.current = onScrollGesture
    onStatusRef.current = onStatus
  }, [onBrowse, onFaceClick, onHeight, onScrollGesture, onStatus])
  useEffect(() => {
    onStatusRef.current?.({ failedMedia, notices, frameErrors })
  }, [failedMedia, notices, frameErrors])
  // 內容一變就換 nonce：舊文件還在進行中的下載不會被新文件誤收
  const { srcDoc, nonce } = useMemo(() => {
    const documentNonce = newNonce()
    return { srcDoc: buildCardDocument(html, ord, documentNonce), nonce: documentNonce }
  }, [html, ord])

  useEffect(() => {
    let active = true
    setFailedMedia([])
    setFrameErrors([])
    setNotices([])
    setHeight(MIN_HEIGHT)
    const onMessage = createFrameMessageHandler({
      frameWindow: () => frameRef.current?.contentWindow,
      nonce,
      fetchMedia: FluencyTidesAPI.getAnkiMedia,
      describeError: (error) => describeApiError(error).message,
      isActive: () => active,
      onHeight: (value) => {
        setHeight(Math.max(MIN_HEIGHT, value))
        onHeightRef.current?.(value)
      },
      onMediaFailed: (name, reason, error) => {
        // 檔案不存在等業務錯誤是預期內的，只有網路或程式錯誤才記成 error
        if (error !== undefined && !isBackendError(error)) console.error(`[CardFrame] 媒體檔 ${name} 載入失敗`, error)
        else console.warn(`[CardFrame] 媒體檔 ${name} 載入失敗：${reason}`)
        setFailedMedia((prev) => (prev.some((item) => item.name === name) ? prev : [...prev, { name, reason }]))
      },
      onFrameError: (message) => setFrameErrors((prev) => (prev.includes(message) ? prev : [...prev, message])),
      onBrowse: (noteIds) => onBrowseRef.current(noteIds),
      onFaceClick: () => onFaceClickRef.current?.(),
      onScrollGesture: (gesture) => onScrollGestureRef.current?.(gesture),
      onNotice: (message) => setNotices((prev) => (prev.includes(message) ? prev : [...prev, message])),
    })
    window.addEventListener('message', onMessage)
    return () => {
      active = false
      window.removeEventListener('message', onMessage)
    }
  }, [nonce])

  return (
    // 邊框放在外層：iframe 是 border-box，自己有邊框會吃掉回報的高度而出現捲軸
    <div className="overflow-hidden rounded-lg border bg-[#121212]">
      <iframe
        ref={frameRef}
        title={title}
        srcDoc={srcDoc}
        // 不給 allow-same-origin：模板腳本可以執行，但碰不到網頁與 API。
        // allow-popups 讓 target=_blank 的連結（TG 深連結）能開新分頁；escape-sandbox 讓開出去
        // 的外部頁面不受這個 sandbox 限制，正常運作。
        sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
        // 跨來源 iframe 預設不能自己播放有聲音的媒體；點頭像播放語音需要這個權限
        allow="autoplay"
        className="block w-full"
        style={{ height }}
      />
    </div>
  )
}

/**
 * 卡面的狀態訊息（媒體或腳本錯誤、不支援的功能）；沒有訊息時不畫任何東西。
 * The messages of a face; renders nothing without any.
 * @param props.status - 卡面回報的狀態。The reported status.
 * @param props.className - 外框的 class。Wrapper class.
 * @returns 訊息 JSX 或 null。Messages JSX or null.
 */
export function CardFrameMessages({ status, className }: { status: CardFrameStatus; className?: string }) {
  const { failedMedia, notices, frameErrors } = status
  if (failedMedia.length === 0 && notices.length === 0 && frameErrors.length === 0) return null
  return (
    <div className={cn('space-y-2', className)}>
      {failedMedia.length > 0 && (
        <div className="text-xs text-amber-700 break-all" role="status">
          <p>{failedMedia.length} 個媒體檔載入失敗：</p>
          <ul className="list-disc pl-5">
            {failedMedia.map((item) => (
              <li key={item.name}>
                {item.name}：{item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {notices.length > 0 && (
        <ul className="text-xs text-muted-foreground break-all" role="status">
          {notices.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
      {frameErrors.length > 0 && (
        <p className="text-xs text-amber-700 break-all" role="status">
          卡面腳本出錯（卡面可能顯示不完整）：{frameErrors.slice(0, 3).join('；')}
          {frameErrors.length > 3 && ` 等 ${frameErrors.length} 項`}
        </p>
      )}
    </div>
  )
}
