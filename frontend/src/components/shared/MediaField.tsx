/**
 * 媒體欄位：上傳音檔或圖片，取得後端產生的檔名（`docs/wip/media_upload_FEAT_2026-10-20.md` D4）。
 * 建卡表單與筆記欄位彈窗共用。
 *
 * - 上傳：選檔或把檔案拖進來；顯示進度，完成後顯示檔名與大小，音檔可以試聽、圖片顯示縮圖。
 * - 錄音（只有音檔）：用瀏覽器的 `MediaRecorder` 錄；停止後先試聽，決定「使用這段錄音」才上傳，
 *   也可以重錄或放棄。後端會把瀏覽器錄的 webm／m4a 轉成 mp3（少數瀏覽器錄成 ogg，原樣保留）。
 *   每一段錄音的麥克風、計時器各自綁在那段錄音上：放棄、出錯、卸載時當場放開麥克風，晚到的舊錄音事件
 *   不會動到新的錄音。
 * - 上傳和「使用」是分開的（計畫書 D1）：上傳只把檔案放在後端的暫存區、把檔名交給表單，Anki 不會被動到；
 *   表單送出**成功**時，後端才把檔案存進 Anki 並寫進筆記，失敗時 Anki 和送出前一樣、暫存檔留著可以直接
 *   重送。所以上傳後移除、換檔或沒送出表單，都不會在 Anki 留下沒用到的檔案（暫存檔一天後自動清掉）。
 *   還沒送出的檔案預覽時讀暫存區（`GET /media/{檔名}`），已經在筆記裡的讀 Anki（`GET /anki-media/{檔名}`）。
 * - 上傳中、等待麥克風授權、錄音中、錄好還沒決定要不要用時，透過 `onBusyChange` 告訴表單先不要送出，
 *   元件本身的其他按鈕也停用（一次只做一件事）。
 * - 錯誤（格式不符 415、太大 413、轉檔失敗 422、後端沒有 ffmpeg 503、麥克風不能用）顯示在元件裡。
 *
 * A media field: uploads (stages) an audio file or image and hands the
 * backend-generated name to the form; the file reaches Anki only when the
 * form's create or update succeeds. Audio can also be recorded in the
 * browser. Shared by the create form and the note field modal.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Image as ImageIcon, Loader2, Mic, Music, RotateCcw, Square, Trash2, Undo2, Upload } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { describeApiError, isBackendError, retryTransient } from '../../lib/apiError'
import {
  MAX_MEDIA_BYTES,
  MAX_RECORDING_SECONDS,
  MEDIA_ACCEPT,
  MEDIA_FORMATS,
  MEDIA_LABEL,
  formatBytes,
  formatDuration,
  oversizeMessage,
  pickRecordingType,
  recordingErrorMessage,
  recordingFileName,
  type MediaFieldState,
  type MediaKind,
} from '../../lib/mediaUpload'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'

/** {@link MediaField} 的 props。Props of {@link MediaField}. */
export interface MediaFieldProps {
  /** 「選擇檔案」按鈕的 id（label 指向它）。The id of the choose-file button. */
  id: string
  kind: MediaKind
  /** 欄位名稱。The field label. */
  label: ReactNode
  required?: boolean
  description?: string | null
  /** 目前顯示的內容（見 `mediaFieldState`）。What the field currently holds. */
  state: MediaFieldState
  /** 表單送出後後端回報的錯誤（例如 422）。An error reported for this field by the form. */
  error?: string
  /** 表單正在送出：所有操作停用。The form is submitting; every action is disabled. */
  disabled: boolean
  /** 上傳完成：把檔名填進表單。Called with the stored file name. */
  onUploaded: (filename: string) => void
  /** 移除目前的檔案（欄位清空）。Clears the field. */
  onClear: () => void
  /** 還原成筆記原本的值；只有編輯既有筆記、而且已經改過時才傳。Restores the original value. */
  onRevert?: () => void
  /**
   * 有事情還沒做完時傳原因（上傳中、等待麥克風授權、錄音中、錄音還沒決定），做完傳 null；表單據此停用送出。
   * Reports why the form should not be submitted yet, or null.
   */
  onBusyChange?: (reason: string | null) => void
}

/**
 * 元件目前在做什麼。`starting` 是等使用者在瀏覽器的提示裡允許麥克風（可能很久）。
 * What the field is doing; `starting` waits for microphone permission.
 */
type Phase = 'idle' | 'starting' | 'recording' | 'recorded' | 'uploading'

/** 上傳失敗後回到哪個狀態：一般回到 `idle`，錄音的回到 `recorded`（不必重錄就能再試）。 */
type UploadReturn = 'idle' | 'recorded'

/**
 * 進行中的一段錄音：麥克風、錄音器與計時器綁在一起，放開時一起放開。`discard` 表示停止後不要留下錄到的內容
 * （放棄、出錯、卸載）。每段錄音各自一份，舊錄音晚到的 `stop` 事件不會影響新的錄音。
 * One recording in progress, with its own stream, recorder, timer and discard flag.
 */
interface ActiveRecording {
  recorder: MediaRecorder
  stream: MediaStream
  timer: number
  discard: boolean
}

/** 錄好、還沒上傳的錄音。A finished recording not uploaded yet. */
interface Recorded {
  blob: Blob
  url: string
  seconds: number
}

/** 這個元件掛載期間上傳的檔案：留著本機的內容，預覽時不必再向後端要。A file uploaded while mounted. */
interface LocalFile {
  name: string
  url: string
  size: number
}

/**
 * 媒體欄位。A media field.
 * @returns 欄位 JSX。The field JSX.
 */
export function MediaField({
  id,
  kind,
  label,
  required,
  description,
  state,
  error,
  disabled,
  onUploaded,
  onClear,
  onRevert,
  onBusyChange,
}: MediaFieldProps) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [recorded, setRecorded] = useState<Recorded | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [local, setLocal] = useState<LocalFile | null>(null)
  const [dragging, setDragging] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)
  const mounted = useRef(true)
  const abortRef = useRef<AbortController | null>(null)
  // 進行中的錄音，與「正在開啟麥克風」：用 ref 而不是 phase 判斷，同一個事件裡先放棄再重錄也不會被擋
  const activeRef = useRef<ActiveRecording | null>(null)
  const startingRef = useRef(false)
  const urlsRef = useRef<Set<string>>(new Set())

  const makeUrl = useCallback((blob: Blob) => {
    const url = URL.createObjectURL(blob)
    urlsRef.current.add(url)
    return url
  }, [])
  const dropUrl = useCallback((url: string | undefined) => {
    if (!url) return
    URL.revokeObjectURL(url)
    urlsRef.current.delete(url)
  }, [])

  /** 放開一段錄音的麥克風與計時器（可以重複呼叫）。Releases a recording's microphone and timer. */
  const releaseRecording = useCallback((active: ActiveRecording) => {
    window.clearInterval(active.timer)
    active.stream.getTracks().forEach((track) => track.stop())
    if (activeRef.current === active) activeRef.current = null
  }, [])

  /** 丟掉進行中的錄音：當場放開麥克風，停止後的內容不保留。Drops the recording in progress. */
  const abandonRecording = useCallback(() => {
    const active = activeRef.current
    if (!active) return
    active.discard = true
    releaseRecording(active)
    if (active.recorder.state !== 'inactive') active.recorder.stop()
  }, [releaseRecording])

  // 卸載：取消上傳、丟掉錄音並放開麥克風、釋放預覽用的網址
  useEffect(() => {
    mounted.current = true
    const urls = urlsRef.current
    return () => {
      mounted.current = false
      abortRef.current?.abort()
      abandonRecording()
      urls.forEach((url) => URL.revokeObjectURL(url))
      urls.clear()
    }
  }, [abandonRecording])

  // 告訴表單現在能不能送出；卸載時一定要清掉，否則表單會一直卡著
  const BUSY_REASONS: Record<Phase, string | null> = {
    idle: null,
    starting: '正在開啟麥克風',
    recording: '還在錄音',
    recorded: '錄好的音還沒決定要不要用',
    uploading: `${MEDIA_LABEL[kind]}還在上傳`,
  }
  const busyReason = BUSY_REASONS[phase]
  const busyRef = useRef(onBusyChange)
  busyRef.current = onBusyChange
  useEffect(() => {
    busyRef.current?.(busyReason)
  }, [busyReason])
  useEffect(() => () => busyRef.current?.(null), [])

  const upload = async (blob: Blob, fileName: string, backTo: UploadReturn) => {
    const tooLarge = oversizeMessage(kind, blob.size)
    if (tooLarge) {
      setProblem(tooLarge)
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setProblem(null)
    setProgress(0)
    setPhase('uploading')
    try {
      const result = await FluencyTidesAPI.uploadMedia(blob, kind, {
        fileName,
        signal: controller.signal,
        onProgress: (fraction) => mounted.current && setProgress(fraction),
      })
      if (!mounted.current) return
      setLocal((previous) => {
        dropUrl(previous?.url)
        return { name: result.filename, url: makeUrl(blob), size: result.size }
      })
      setRecorded((previous) => {
        dropUrl(previous?.url)
        return null
      })
      setPhase('idle')
      onUploaded(result.filename)
    } catch (err) {
      if (!mounted.current || controller.signal.aborted) return
      if (!isBackendError(err)) console.error('[MediaField] 上傳失敗', err)
      const info = describeApiError(err)
      // 代理直接擋下的 413 沒有後端的錯誤格式：補一句人看得懂的
      setProblem(
        info.status === 413 && !isBackendError(err)
          ? '上傳失敗：檔案太大，被伺服器擋下了（HTTP 413）'
          : `上傳失敗：${info.message}`,
      )
      // 錄音上傳失敗時留著錄音，可以直接再試，不必重錄
      setPhase(backTo)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0]
    if (!file) return
    void upload(file, file.name, 'idle')
  }

  const startRecording = async () => {
    // 已經在開麥克風或在錄音：不再開第二個（連按兩次、或狀態還沒更新時）
    if (startingRef.current || activeRef.current) return
    setProblem(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setProblem('這個瀏覽器不能錄音（需要 HTTPS 或 localhost，而且瀏覽器要支援 MediaRecorder）；可以改用「選擇檔案」。')
      return
    }
    startingRef.current = true
    setPhase('starting')
    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mounted.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const type = pickRecordingType((candidate) => MediaRecorder.isTypeSupported(candidate))
      const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
      const chunks: Blob[] = []
      const startedAt = Date.now()
      const active: ActiveRecording = { recorder, stream, timer: 0, discard: false }
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data)
      }
      recorder.onstop = () => {
        releaseRecording(active)
        if (active.discard || !mounted.current) return
        const blob = new Blob(chunks, { type: recorder.mimeType || type || 'audio/webm' })
        if (blob.size === 0) {
          setProblem('沒有錄到聲音，請再試一次。')
          setPhase('idle')
          return
        }
        setRecorded({ blob, url: makeUrl(blob), seconds: (Date.now() - startedAt) / 1000 })
        setPhase('recorded')
      }
      recorder.onerror = (event) => {
        // 真正的原因在 event.error（DOMException）
        const cause = (event as Event & { error?: DOMException }).error
        console.error('[MediaField] 錄音失敗', cause ?? event)
        active.discard = true
        releaseRecording(active)
        if (recorder.state !== 'inactive') recorder.stop()
        if (!mounted.current) return
        setProblem(`錄音中斷了${cause?.name ? `（${cause.name}）` : ''}，請再試一次。`)
        setPhase('idle')
      }
      recorder.start()
      active.timer = window.setInterval(() => {
        const seconds = (Date.now() - startedAt) / 1000
        setElapsed(seconds)
        if (seconds >= MAX_RECORDING_SECONDS && recorder.state === 'recording') recorder.stop()
      }, 250)
      activeRef.current = active
      setElapsed(0)
      setPhase('recording')
    } catch (err) {
      // 建立錄音器失敗時麥克風已經打開了：這裡一定要放開
      stream?.getTracks().forEach((track) => track.stop())
      if (!mounted.current) return
      const name = (err as { name?: string } | null)?.name ?? ''
      // 使用者拒絕、沒有麥克風是預期內的；其他（例如瀏覽器說支援、實際建不起來）留下錯誤供除錯
      const expected = ['NotAllowedError', 'SecurityError', 'NotFoundError', 'OverconstrainedError', 'NotReadableError']
      if (expected.includes(name)) console.warn('[MediaField] 無法開始錄音', err)
      else console.error('[MediaField] 無法開始錄音', err)
      setProblem(recordingErrorMessage(err))
      setPhase('idle')
    } finally {
      startingRef.current = false
    }
  }

  const stopRecording = () => {
    const recorder = activeRef.current?.recorder
    if (recorder?.state === 'recording') recorder.stop()
  }

  const discardRecording = () => {
    setRecorded((previous) => {
      dropUrl(previous?.url)
      return null
    })
    setProblem(null)
    setPhase('idle')
  }

  const idle = phase === 'idle'
  const canAct = idle && !disabled
  const shownError = problem ?? error
  const describedBy = shownError ? `${id}-error` : description ? `${id}-desc` : `${id}-hint`
  const Icon = kind === 'audio' ? Music : ImageIcon

  return (
    <div className="space-y-1" role="group" aria-labelledby={`${id}-label`}>
      <label id={`${id}-label`} htmlFor={id} className="text-sm font-medium">
        {label}
        {required && <span className="ml-1 text-destructive">*</span>}
      </label>

      <div
        className={cn(
          'space-y-2 rounded-md border border-dashed px-3 py-3 text-sm transition-colors',
          dragging && 'border-primary bg-primary/5',
          shownError && 'border-destructive',
        )}
        onDragOver={(event) => {
          if (!canAct) return
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          setDragging(false)
          if (!canAct) return
          event.preventDefault()
          handleFiles(event.dataTransfer.files)
        }}
      >
        {/* 目前的內容 */}
        {state.kind === 'file' && idle && (
          <div className="space-y-2">
            <div className="flex items-start gap-2">
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="break-all font-mono text-xs">{state.name}</p>
                <p className="text-xs text-muted-foreground">
                  {state.uploaded ? '已上傳・送出成功後才存進 Anki' : '筆記目前的檔案'}
                  {local?.name === state.name && `・${formatBytes(local.size)}`}
                </p>
              </div>
            </div>
            <MediaPreview
              kind={kind}
              name={state.name}
              staged={state.uploaded}
              localUrl={local?.name === state.name ? local.url : null}
            />
          </div>
        )}
        {state.kind === 'other' && idle && (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              這一欄目前的內容不是單一{MEDIA_LABEL[kind]}，無法預覽；上傳新檔會整個取代它。
            </p>
            <p className="max-h-20 overflow-auto break-all rounded bg-muted/40 px-2 py-1 font-mono text-xs">{state.raw}</p>
          </div>
        )}

        {/* 等使用者允許麥克風 */}
        {phase === 'starting' && (
          <p className="flex items-center gap-2" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在開啟麥克風：請在瀏覽器的提示中允許使用麥克風。
          </p>
        )}

        {/* 錄音中 */}
        {phase === 'recording' && (
          <div className="flex flex-wrap items-center gap-3" role="status">
            <span className="inline-flex items-center gap-2 font-medium text-destructive">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-destructive" aria-hidden="true" />
              錄音中 {formatDuration(elapsed)}
            </span>
            <span className="text-xs text-muted-foreground">最長 {formatDuration(MAX_RECORDING_SECONDS)}</span>
            <Button type="button" size="sm" variant="destructive" className="h-8" onClick={stopRecording}>
              <Square className="mr-1.5 h-3.5 w-3.5" />
              停止
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-8"
              onClick={() => {
                abandonRecording()
                setPhase('idle')
              }}
            >
              放棄
            </Button>
          </div>
        )}

        {/* 錄好了：先試聽，決定要不要用 */}
        {phase === 'recorded' && recorded && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              錄好了（{formatDuration(recorded.seconds)}・{formatBytes(recorded.blob.size)}）：先試聽，要用再上傳。
            </p>
            <audio controls src={recorded.url} className="h-9 w-full" aria-label="試聽剛錄的音" />
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                className="h-8"
                disabled={disabled}
                onClick={() => void upload(recorded.blob, recordingFileName(recorded.blob.type), 'recorded')}
              >
                <Upload className="mr-1.5 h-3.5 w-3.5" />
                使用這段錄音
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8"
                disabled={disabled}
                onClick={() => {
                  discardRecording()
                  void startRecording()
                }}
              >
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                重錄
              </Button>
              <Button type="button" size="sm" variant="ghost" className="h-8" onClick={discardRecording}>
                放棄
              </Button>
            </div>
          </div>
        )}

        {/* 上傳中 */}
        {phase === 'uploading' && (
          <div className="space-y-1.5" role="status">
            <p className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              {/* 瀏覽器算不出總量（null）時無法顯示百分比，改用不定進度條 */}
              {progress === null ? '上傳中…' : progress < 1 ? `上傳中 ${Math.round(progress * 100)}%` : '已送出，正在處理…'}
            </p>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label="上傳進度"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress !== null ? Math.round(progress * 100) : undefined}
            >
              <div
                className={cn('h-full bg-primary transition-[width]', (progress === null || progress >= 1) && 'animate-pulse')}
                style={{ width: `${progress === null ? 100 : Math.max(4, Math.round(progress * 100))}%` }}
              />
            </div>
            <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => {
              abortRef.current?.abort()
              setPhase(recorded ? 'recorded' : 'idle')
            }}>
              取消上傳
            </Button>
          </div>
        )}

        {/* 動作 */}
        {idle && (
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={inputRef}
              type="file"
              accept={MEDIA_ACCEPT[kind]}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              disabled={!canAct}
              onChange={(event) => {
                handleFiles(event.target.files)
                // 清掉選取，同一個檔案才能再選一次（例如上傳失敗後重試）
                event.target.value = ''
              }}
            />
            <Button
              id={id}
              type="button"
              size="sm"
              variant="outline"
              className="h-8"
              disabled={!canAct}
              aria-describedby={describedBy}
              aria-invalid={shownError ? true : undefined}
              onClick={() => inputRef.current?.click()}
            >
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              {state.kind === 'empty' ? '選擇檔案' : '換一個檔案'}
            </Button>
            {kind === 'audio' && (
              <Button type="button" size="sm" variant="outline" className="h-8" disabled={!canAct} onClick={() => void startRecording()}>
                <Mic className="mr-1.5 h-3.5 w-3.5" />
                錄音
              </Button>
            )}
            {state.kind !== 'empty' && (
              <Button type="button" size="sm" variant="ghost" className="h-8 text-muted-foreground" disabled={!canAct} onClick={onClear}>
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                移除
              </Button>
            )}
            {onRevert && (
              <Button type="button" size="sm" variant="ghost" className="h-8 text-muted-foreground" disabled={!canAct} onClick={onRevert}>
                <Undo2 className="mr-1.5 h-3.5 w-3.5" />
                還原
              </Button>
            )}
            {state.kind === 'empty' && <span className="text-xs text-muted-foreground">或把檔案拖到這裡</span>}
          </div>
        )}
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {MEDIA_FORMATS[kind]}，最大 {MAX_MEDIA_BYTES[kind] / (1024 * 1024)} MB
          {kind === 'audio' && '；m4a、webm（瀏覽器錄音多半是這兩種）會轉成 mp3'}
        </p>
      </div>

      {description && !shownError && (
        <p id={`${id}-desc`} className="text-xs text-muted-foreground">
          {description}
        </p>
      )}
      {shownError && (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {shownError}
        </p>
      )}
    </div>
  )
}

/**
 * 預覽一個媒體檔：音檔是播放器、圖片是縮圖。有本機的內容（這個元件剛上傳的）就直接用；否則還沒送出的上傳
 * 讀暫存區（`GET /media/{檔名}`，例如重新整理後），已經在筆記裡的讀 Anki（`GET /anki-media/{檔名}`）。
 * Previews a media file: a player for audio, a thumbnail for images.
 * @param props.kind - 種類。The kind.
 * @param props.name - 檔名。The file name.
 * @param props.staged - 是還沒送出的上傳（在暫存區，不在 Anki）。Whether it is a staged upload.
 * @param props.localUrl - 本機內容的網址；沒有時為 null。A local object URL, or null.
 * @returns 預覽 JSX。The preview JSX.
 */
function MediaPreview({
  kind,
  name,
  staged,
  localUrl,
}: {
  kind: MediaKind
  name: string
  staged: boolean
  localUrl: string | null
}) {
  const { data, error, isPending } = useQuery({
    queryKey: [staged ? 'staged-media' : 'anki-media', name],
    queryFn: () => (staged ? FluencyTidesAPI.getStagedMedia(name) : FluencyTidesAPI.getAnkiMedia(name)),
    enabled: localUrl === null,
    retry: retryTransient,
    staleTime: 5 * 60_000,
  })
  const [remoteUrl, setRemoteUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!data) {
      setRemoteUrl(null)
      return
    }
    const url = URL.createObjectURL(data)
    setRemoteUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [data])

  const url = localUrl ?? remoteUrl
  if (!url) {
    if (localUrl === null && error) {
      const info = describeApiError(error)
      return (
        <p role="alert" className="text-xs text-destructive">
          {staged && info.status === 404
            ? '這個上傳已經不在了（超過一天，或後端重新部署過），請移除後重新上傳。'
            : `無法載入預覽：${info.message}`}
        </p>
      )
    }
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
        載入預覽中…
      </p>
    )
  }
  return kind === 'audio' ? (
    <audio controls src={url} className="h-9 w-full" aria-label={`試聽 ${name}`} />
  ) : (
    <img src={url} alt={`${name} 的預覽`} className="max-h-40 max-w-full rounded border object-contain" />
  )
}
