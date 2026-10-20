/**
 * Card Generator 頁面。
 * Card Generator page.
 *
 * 版面與 Cards 相同：牌組樹（並排時在左、窄螢幕時在上方）決定新卡建在哪個牌組，選「全部牌組」
 * 或已不存在的牌組時無法產生；另一側是建卡表單。
 * The same layout as Cards: the deck tree decides the deck; the form is next
 * to it (or below it on narrow screens).
 *
 * Phase 9 重構：改用 Handler-based API。
 * Phase 9 refactor: switched to the Handler-based API.
 * - 透過 `listHandlers()` 動態取得可用處理器。
 * - 透過 `createCard(handlerName, payload)` 建立卡片。
 * - Model 下拉選單根據所選 Handler 的 `supported_models` 動態更新。
 */

import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlusCircle } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { ErrorNote } from '../components/shared/ErrorNote'
import { PageHeader } from '../components/shared/PageHeader'
import { Panel } from '../components/shared/Panel'
import { SplitLayout } from '../components/shared/SplitLayout'
import { Button } from '../components/ui/button'
import { Card, CardContent } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Skeleton } from '../components/ui/skeleton'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { GENERATE_LAYOUT } from '../lib/cardLayout'
import type { HandlerInfo } from '../types/api'

/** 表單區塊最窄的寬度。Narrowest form area. */
const FORM_MIN = 320

/**
 * 卡片產生器頁面：在牌組樹選牌組，再選擇 Handler 與模型後輸入單字產生卡片。
 * Card generator page: pick the deck in the tree, then a handler and model,
 * and enter a word to generate a card.
 * @returns 卡片產生器頁面 JSX。Card generator page JSX.
 */
export default function CardGenerator() {
  const [selectedDeck, setSelectedDeck] = useLocalStorage<string | null>(
    'generate_selectedDeck',
    import.meta.env.VITE_DEFAULT_DECK || null,
  )
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('generate_deck_toggled', {})
  const panels = usePanelWidths(GENERATE_LAYOUT)
  const [selectedHandler, setSelectedHandler] = useState('')
  const [selectedModel, setSelectedModel] = useState('')
  const [word, setWord] = useState('')

  const {
    data: handlers,
    isError: handlersFailed,
    error: handlersError,
  } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
  })
  // 與牌組樹共用同一份查詢：記住的牌組可能已在 Anki 被刪除或改名，後端遇到不存在的牌組會自動建立，
  // 所以送出前要確認它還在
  const { data: decks } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  const deckMissing = selectedDeck !== null && decks !== undefined && !decks.some((d) => d.deck_name === selectedDeck)
  const deckReady = selectedDeck !== null && !deckMissing

  // 初始選中第一個 Handler
  useEffect(() => {
    if (handlers && handlers.length > 0 && !selectedHandler) {
      setSelectedHandler(handlers[0].handler_name)
    }
  }, [handlers, selectedHandler])

  // 當 Handler 改變時，自動選中該 Handler 的第一個 supported model
  const currentHandler: HandlerInfo | undefined = handlers?.find(
    (h) => h.handler_name === selectedHandler
  )

  useEffect(() => {
    if (currentHandler && currentHandler.supported_models.length > 0) {
      setSelectedModel(currentHandler.supported_models[0])
    }
  }, [currentHandler])

  // Mutation
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (deck: string) =>
      FluencyTidesAPI.createCard(selectedHandler, {
        deck_name: deck,
        model_name: selectedModel,
        parameters: { word: word.trim() },
      }),
    onSuccess: (data) => {
      toast.success('Card Generated!', {
        description: `Note ID: ${data.note_id} — ${data.message}`,
      })
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      setWord('')
    },
    onError: (error: { message?: string; error_code?: string }) => {
      const msg = error.message || 'An unknown error occurred.'
      toast.error(`Failed: ${error.error_code || 'ERROR'}`, {
        description: msg,
      })
    },
  })

  const canSubmit = deckReady && Boolean(selectedHandler) && Boolean(selectedModel) && Boolean(word.trim())

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit || selectedDeck === null) return
    mutation.mutate(selectedDeck)
  }

  const formPanel = (sideBySide: boolean) => (
    <Panel
      scrollable={sideBySide}
      title={
        <>
          <PlusCircle className="h-4 w-4" />
          Generate New Card
        </>
      }
      contentClassName="space-y-6"
    >
      <p className="text-sm text-muted-foreground">
        Using handler: <code className="text-primary">{selectedHandler || '...'}</code>
      </p>

      {handlersFailed && <ErrorNote title="無法載入 Task Handler 清單，暫時無法產生卡片" error={handlersError} />}

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* 牌組：由牌組樹決定 */}
        <div className="space-y-2">
          <label className="text-sm font-medium">Deck</label>
          {selectedDeck === null ? (
            <p id="generate-deck-hint" role="status" className="text-sm text-muted-foreground">
              請先在牌組樹選擇一個牌組（選「全部牌組」時無法產生卡片）。
            </p>
          ) : deckMissing ? (
            <p id="generate-deck-hint" role="status" className="text-sm text-destructive break-all">
              牌組「{selectedDeck}」已不存在，請在牌組樹重新選擇。
            </p>
          ) : (
            <p className="text-sm break-all">{selectedDeck}</p>
          )}
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Task Handler</label>
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={selectedHandler}
            onChange={(e) => setSelectedHandler(e.target.value)}
          >
            {handlers?.map((h) => (
              <option key={h.handler_name} value={h.handler_name}>
                {h.handler_name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Model</label>
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
          >
            {currentHandler?.supported_models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-2 pt-2">
          <label className="text-sm font-medium">Word or Phrase</label>
          <div className="flex gap-2">
            <Input
              placeholder="e.g. ubiquitous"
              value={word}
              onChange={(e) => setWord(e.target.value)}
              disabled={mutation.isPending}
            />
            <Button
              type="submit"
              disabled={mutation.isPending || !canSubmit}
              aria-describedby={deckReady ? undefined : 'generate-deck-hint'}
            >
              {mutation.isPending ? 'Generating...' : 'Generate'}
            </Button>
          </div>
        </div>
      </form>

      {/* Loading State */}
      {mutation.isPending && (
        <Card className="border-primary/50 bg-primary/5">
          <CardContent className="pt-6">
            <div className="flex flex-col items-center justify-center space-y-4">
              <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
              <p className="text-sm font-medium text-primary">
                Creating card via {selectedHandler}...
              </p>
              <Skeleton className="h-4 w-[250px] bg-primary/20" />
              <Skeleton className="h-4 w-[200px] bg-primary/20" />
            </div>
          </CardContent>
        </Card>
      )}
    </Panel>
  )

  return (
    <SplitLayout
      header={
        <PageHeader
          title="Card Generator"
          description="Select a deck in the deck tree, then a task handler and model, and enter your word."
        />
      }
      panels={panels}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => (
            <DeckTreePanel
              scrollable={sideBySide}
              toggled={deckToggled}
              onToggled={setDeckToggled}
              selected={selectedDeck}
              onSelect={setSelectedDeck}
            />
          ),
        },
        { id: 'form', minWidth: FORM_MIN, render: formPanel },
      ]}
    />
  )
}
