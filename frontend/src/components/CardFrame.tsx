import { useEffect, useMemo, useRef, useState } from 'react'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, isBackendError } from '../lib/apiError'
import { buildCardDocument, createFrameMessageHandler } from '../lib/ankiFrame'

/** iframe 的最小高度（內容回報高度前先用這個）。Minimum frame height. */
const MIN_HEIGHT = 240

/** 一個載入失敗的媒體檔與原因。A media file that failed to load. */
interface MediaFailure {
  name: string
  reason: string
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
 * @returns iframe，以及媒體或腳本錯誤的提示。The frame and any errors.
 */
export function CardFrame({
  html,
  ord,
  title,
  onBrowse,
  onFaceClick,
}: {
  html: string
  ord: number
  title: string
  onBrowse: (noteIds: number[]) => void
  onFaceClick?: () => void
}) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(MIN_HEIGHT)
  const [failedMedia, setFailedMedia] = useState<MediaFailure[]>([])
  const [frameErrors, setFrameErrors] = useState<string[]>([])
  const [notices, setNotices] = useState<string[]>([])
  // 用 ref 取最新的 onBrowse：不必因為父元件重新渲染就重掛訊息處理函式
  const onBrowseRef = useRef(onBrowse)
  const onFaceClickRef = useRef(onFaceClick)
  useEffect(() => {
    onBrowseRef.current = onBrowse
    onFaceClickRef.current = onFaceClick
  }, [onBrowse, onFaceClick])
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
      onHeight: (value) => setHeight(Math.max(MIN_HEIGHT, value)),
      onMediaFailed: (name, reason, error) => {
        // 檔案不存在等業務錯誤是預期內的，只有網路或程式錯誤才記成 error
        if (error !== undefined && !isBackendError(error)) console.error(`[CardFrame] 媒體檔 ${name} 載入失敗`, error)
        else console.warn(`[CardFrame] 媒體檔 ${name} 載入失敗：${reason}`)
        setFailedMedia((prev) => (prev.some((item) => item.name === name) ? prev : [...prev, { name, reason }]))
      },
      onFrameError: (message) => setFrameErrors((prev) => (prev.includes(message) ? prev : [...prev, message])),
      onBrowse: (noteIds) => onBrowseRef.current(noteIds),
      onFaceClick: () => onFaceClickRef.current?.(),
      onNotice: (message) => setNotices((prev) => (prev.includes(message) ? prev : [...prev, message])),
    })
    window.addEventListener('message', onMessage)
    return () => {
      active = false
      window.removeEventListener('message', onMessage)
    }
  }, [nonce])

  return (
    <div className="space-y-2">
      {/* 邊框放在外層：iframe 是 border-box，自己有邊框會吃掉回報的高度而出現捲軸 */}
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
