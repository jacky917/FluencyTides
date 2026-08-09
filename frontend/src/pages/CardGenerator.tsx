/**
 * Card Generator 頁面。
 * Card Generator page.
 *
 * Phase 9 重構：改用 Handler-based API。
 * Phase 9 refactor: switched to the Handler-based API.
 * - 透過 `listHandlers()` 動態取得可用處理器。
 * - 透過 `createCard(handlerName, payload)` 建立卡片。
 * - Model 下拉選單根據所選 Handler 的 `supported_models` 動態更新。
 */

import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Skeleton } from '../components/ui/skeleton'
import type { HandlerInfo } from '../types/api'

/**
 * 卡片產生器頁面：選擇 Handler / 牌組 / 模型後輸入單字產生卡片。
 * Card generator page: pick a handler, deck, and model, then enter a word to generate a card.
 * @returns 卡片產生器頁面 JSX。Card generator page JSX.
 */
export default function CardGenerator() {
  const defaultDeck = import.meta.env.VITE_DEFAULT_DECK || 'Default'

  const [selectedDeck, setSelectedDeck] = useState(defaultDeck)
  const [selectedHandler, setSelectedHandler] = useState('')
  const [selectedModel, setSelectedModel] = useState('')
  const [word, setWord] = useState('')

  // 取得可用的 Handlers 與 Decks
  const { data: handlers } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
  })
  const { data: decks } = useQuery({
    queryKey: ['decks'],
    queryFn: FluencyTidesAPI.listDecks,
  })

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
    mutationFn: () =>
      FluencyTidesAPI.createCard(selectedHandler, {
        deck_name: selectedDeck,
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!word.trim() || !selectedHandler) return
    mutation.mutate()
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Card Generator</h2>
        <p className="text-muted-foreground mt-2">
          Select a task handler, deck, and model, then enter your word.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Generate New Card</CardTitle>
          <CardDescription>
            Using handler: <code className="text-primary">{selectedHandler || '...'}</code>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Row 1: Handler + Deck */}
            <div className="grid grid-cols-2 gap-4">
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
                <label className="text-sm font-medium">Deck</label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={selectedDeck}
                  onChange={(e) => setSelectedDeck(e.target.value)}
                >
                  {decks?.map((d) => (
                    <option key={d.deck_id} value={d.deck_name}>
                      {d.deck_name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Row 2: Model (dynamic based on Handler) */}
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

            {/* Row 3: Word input */}
            <div className="space-y-2 pt-2">
              <label className="text-sm font-medium">Word or Phrase</label>
              <div className="flex gap-2">
                <Input
                  placeholder="e.g. ubiquitous"
                  value={word}
                  onChange={(e) => setWord(e.target.value)}
                  disabled={mutation.isPending}
                />
                <Button type="submit" disabled={mutation.isPending || !word.trim()}>
                  {mutation.isPending ? 'Generating...' : 'Generate'}
                </Button>
              </div>
            </div>
          </form>
        </CardContent>
      </Card>

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
    </div>
  )
}
