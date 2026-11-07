/**
 * 後端建卡草稿的讀取與自動存檔（`docs/wip/server_drafts_FEAT_2026-11-04.md` D5、D9）。
 *
 * - `useCurrentDraft`：目前的草稿（全站最多一份）；進行中時輪詢，回到分頁時重讀。
 * - `useSetCurrentDraft`：把請求回來的草稿直接放進快取，並同步 localStorage 記住的草稿 ID。
 * - `useDraftEditor`：預覽中的修改，800 ms 後存回後端（帶版本）。每次修改都先備份在 localStorage；
 *   存不了時依原因處理（`classifySaveError`）：暫時的錯誤自動重送，版本衝突讓使用者選，草稿已不在時告知。
 *
 * Reading and autosaving the backend create draft.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, isBackendError, retryTransient } from '../lib/apiError'
import {
  CURRENT_DRAFT_ID_KEY,
  CURRENT_DRAFT_QUERY_KEY,
  classifySaveError,
  draftBackupKey,
  initialEditState,
  pollInterval,
  rebaseDecision,
  shouldRemember,
  type DraftBackup,
} from '../lib/serverDraft'
import type { ServerDraft } from '../types/api'
import { readStored, removeStored, writeStored } from './useStoredState'

/** 預覽中修改後多久存回後端。Debounce before saving an edit. */
const SAVE_DELAY_MS = 800
/** 暫時存不了時多久重送一次。Retry interval for transient save failures. */
const RETRY_DELAY_MS = 10_000

/**
 * 目前的建卡草稿。The current create draft.
 * @param pollMs - 進行中時的輪詢間隔。Polling interval while in progress.
 * @returns TanStack Query 的結果；`data` 為草稿或 null。The query result.
 */
export function useCurrentDraft(pollMs = 3000) {
  return useQuery({
    queryKey: CURRENT_DRAFT_QUERY_KEY,
    queryFn: FluencyTidesAPI.getCurrentDraft,
    refetchInterval: (query) => pollInterval(query.state.data, pollMs),
    // 手機從別的 App 回來、切回這個分頁時立刻讀一次
    refetchOnWindowFocus: true,
    retry: retryTransient,
  })
}

/**
 * 把請求回來的草稿放進快取（不必再讀一次），並同步記住的草稿 ID：生成草稿記住；填表建卡的佔用不記；
 * 傳 null（這個分頁自己建立、廢棄，或收到草稿已不在而已經告知過）時忘掉，之後就不會再跳「草稿已不在」。
 * Puts a draft from a response into the cache and syncs the remembered ID.
 * @returns 設定函數。The setter.
 */
export function useSetCurrentDraft() {
  const queryClient = useQueryClient()
  return useCallback(
    (draft: ServerDraft | null) => {
      if (shouldRemember(draft)) writeStored('local', CURRENT_DRAFT_ID_KEY, draft.id)
      else if (draft === null) removeStored('local', CURRENT_DRAFT_ID_KEY)
      queryClient.setQueryData(CURRENT_DRAFT_QUERY_KEY, draft)
    },
    [queryClient],
  )
}

/** 存回後端的狀態。Save status. */
export type SaveStatus = 'saved' | 'saving' | 'unsaved' | 'offline' | 'conflict'

/** {@link useDraftEditor} 的結果。The editor. */
export interface DraftEditor<S> {
  /** 目前的編輯狀態。The editing state. */
  state: S
  /** 修改（之後自動存回後端）。Edits the state. */
  setState: (next: S) => void
  saveStatus: SaveStatus
  /** 存回後端用的版本；建立時帶上。The version the state is based on. */
  version: number
  /** 版本衝突時的最新草稿。The latest draft on a conflict. */
  conflict: ServerDraft | null
  /** 衝突時：放棄這裡的修改，改用最新的。Drops local edits for the latest draft. */
  loadLatest: () => void
  /** 衝突時：用這裡的修改覆蓋。Overwrites with the local edits. */
  overwrite: () => void
  /** 暫停自動存檔（開始建立、廢棄前），直到 {@link DraftEditor.resume}。Pauses autosaving. */
  pause: () => void
  /**
   * 恢復自動存檔（建立、廢棄失敗後）：給了最新的草稿就先跟上它（見 `rebaseDecision`；別處改過內容時進入衝突），
   * 有沒存的修改就送出。Resumes autosaving, following the latest draft if given.
   */
  resume: (latest?: ServerDraft) => void
}

/**
 * 一份可建立的草稿的編輯狀態與自動存檔。換了另一份草稿（`id` 不同）時要重新掛載（以 `key={draft.id}`）。
 * The editing state of a ready draft, autosaved; remount per draft.
 * @param draft - 後端的草稿（`ready` 或建立中）。The draft.
 * @param fallback - 沒有存過的編輯狀態時，由生成結果產生的預設值。The default state.
 * @returns 編輯器。The editor.
 */
export function useDraftEditor<S extends Record<string, unknown>>(
  draft: ServerDraft,
  fallback: () => S,
): DraftEditor<S> {
  const setCurrent = useSetCurrentDraft()
  const backupKey = draftBackupKey(draft.id)
  const [initial] = useState(() =>
    initialEditState(draft, readStored<DraftBackup<S> | null>('local', backupKey, null), fallback),
  )
  const [state, setLocalState] = useState<S>(initial.state)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(initial.dirty ? 'unsaved' : 'saved')
  const [conflict, setConflict] = useState<ServerDraft | null>(null)
  const [version, setVersion] = useState(initial.version)

  // 用 ref 而不是 state：非同步的存檔要讀到最新的值，計時器與送出中的請求也不需要觸發重繪
  const stateRef = useRef(state)
  const versionRef = useRef(initial.version)
  const dirtyRef = useRef(initial.dirty)
  const editSeq = useRef(0)
  const timer = useRef<number | null>(null)
  const inFlight = useRef(false)
  const paused = useRef(false)
  const conflictRef = useRef<ServerDraft | null>(null)
  // 這裡最後知道的後端內容：用來分辨版本加了是因為別處改了內容，還是只是狀態轉換（建立中、退回）
  const serverStateRef = useRef(JSON.stringify(draft.state ?? null))

  const clearTimer = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }

  const save = useCallback(async () => {
    clearTimer()
    if (paused.current || conflictRef.current || !dirtyRef.current || inFlight.current) return
    const seq = editSeq.current
    inFlight.current = true
    setSaveStatus('saving')
    try {
      const saved = await FluencyTidesAPI.saveDraftState(draft.id, stateRef.current, versionRef.current)
      versionRef.current = saved.version
      serverStateRef.current = JSON.stringify(saved.state ?? null)
      setVersion(saved.version)
      setCurrent(saved)
      if (editSeq.current === seq) {
        dirtyRef.current = false
        removeStored('local', backupKey)
        setSaveStatus('saved')
      } else {
        // 送出期間又改了：接著再存一次
        timer.current = window.setTimeout(() => void save(), SAVE_DELAY_MS)
      }
    } catch (err) {
      const failure = classifySaveError(err)
      switch (failure.kind) {
        case 'conflict':
          conflictRef.current = failure.latest
          setConflict(failure.latest)
          setSaveStatus('conflict')
          break
        case 'gone':
          // 草稿在別處建立、廢棄，或伺服器重新啟動過：這裡的修改已無處可存，明確告知
          paused.current = true
          removeStored('local', backupKey)
          toast.info('這份草稿已不在', {
            description: '可能已在其他地方建立或廢棄，或伺服器重新啟動過；這裡還沒存回去的修改無法再建立。',
          })
          setCurrent(null)
          break
        case 'busy':
          // 另一個分頁正在建立等：修改與備份留著，草稿回到可建立時再送（見下方的 effect）
          setSaveStatus('unsaved')
          setCurrent(failure.latest)
          break
        case 'transient':
          if (!isBackendError(err)) console.error('[useDraftEditor] 存檔失敗，稍後重送', err)
          setSaveStatus('offline')
          timer.current = window.setTimeout(() => void save(), RETRY_DELAY_MS)
          break
        case 'rejected':
          // 重送也不會成功（例如內容太大）：顯示原因，下一次修改再試
          setSaveStatus('unsaved')
          toast.error('無法儲存修改', { description: describeApiError(err).message })
          break
      }
    } finally {
      inFlight.current = false
    }
  }, [backupKey, draft.id, setCurrent])

  const setState = useCallback(
    (next: S) => {
      stateRef.current = next
      dirtyRef.current = true
      editSeq.current += 1
      setLocalState(next)
      setSaveStatus((prev) => (prev === 'conflict' || prev === 'offline' ? prev : 'unsaved'))
      // 先備份一份：還沒存回去就關掉分頁，同一台裝置回來也不會遺失
      writeStored<DraftBackup<S>>('local', backupKey, { version: versionRef.current, state: next })
      if (conflictRef.current) return
      clearTimer()
      timer.current = window.setTimeout(() => void save(), SAVE_DELAY_MS)
    },
    [backupKey, save],
  )

  /**
   * 後端的版本比這裡新時跟上（`rebaseDecision`）：只是狀態轉換就只對齊版本；別處改了內容而這裡沒有沒存的修改
   * 就採用最新的；兩邊都改了就進入衝突，讓使用者選。Follows a newer backend draft.
   */
  const follow = useCallback((latest: ServerDraft) => {
    if (conflictRef.current) return
    switch (rebaseDecision(latest, versionRef.current, serverStateRef.current, dirtyRef.current)) {
      case 'none':
        return
      case 'rebase':
        versionRef.current = latest.version
        setVersion(latest.version)
        return
      case 'adopt':
        stateRef.current = latest.state as S
        versionRef.current = latest.version
        serverStateRef.current = JSON.stringify(latest.state ?? null)
        setLocalState(latest.state as S)
        setVersion(latest.version)
        return
      case 'conflict':
        conflictRef.current = latest
        setConflict(latest)
        setSaveStatus('conflict')
        return
    }
  }, [])

  // 讀到較新的草稿時跟上（例如回到分頁時重讀、另一個分頁建立失敗而退回）
  useEffect(() => {
    if (!inFlight.current) follow(draft)
  }, [draft, follow])

  // 草稿回到可建立（例如另一個分頁建立失敗）時，送出先前因忙碌沒存的修改；版本不符會走衝突讓使用者選
  useEffect(() => {
    if (draft.status === 'ready' && dirtyRef.current && !paused.current) void save()
  }, [draft.status, save])

  // 恢復連線時馬上重送；開啟時就有沒存的備份也送出
  useEffect(() => {
    const online = () => void save()
    window.addEventListener('online', online)
    if (initial.dirty) void save()
    return () => {
      window.removeEventListener('online', online)
      clearTimer()
    }
  }, [initial.dirty, save])

  const loadLatest = () => {
    const latest = conflictRef.current
    if (!latest) return
    const next = initialEditState(latest, null, fallback).state
    stateRef.current = next
    versionRef.current = latest.version
    serverStateRef.current = JSON.stringify(latest.state ?? null)
    dirtyRef.current = false
    conflictRef.current = null
    removeStored('local', backupKey)
    setLocalState(next)
    setVersion(latest.version)
    setConflict(null)
    setSaveStatus('saved')
    setCurrent(latest)
  }

  const overwrite = () => {
    const latest = conflictRef.current
    if (!latest) return
    versionRef.current = latest.version
    conflictRef.current = null
    setConflict(null)
    void save()
  }

  const pause = () => {
    paused.current = true
    clearTimer()
  }

  const resume = (latest?: ServerDraft) => {
    if (latest && latest.id !== draft.id) return
    paused.current = false
    // 不直接把版本推到最新：別處改過內容時要走衝突，不能默默蓋掉
    if (latest) follow(latest)
    if (dirtyRef.current) void save()
  }

  return { state, setState, saveStatus, version, conflict, loadLatest, overwrite, pause, resume }
}
