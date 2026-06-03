import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Skeleton } from '../components/ui/skeleton'

export default function CardGenerator() {
  const [word, setWord] = useState('')
  const defaultDeck = import.meta.env.VITE_DEFAULT_DECK || 'Default'
  const defaultModel = import.meta.env.VITE_DEFAULT_MODEL_FILE || 'TOEIC_Coach_Dark.json'
  
  const [selectedDeck, setSelectedDeck] = useState(defaultDeck)
  const [selectedModel, setSelectedModel] = useState(defaultModel)

  // Fetch options
  const { data: decks } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  const { data: models } = useQuery({ queryKey: ['models'], queryFn: FluencyTidesAPI.listModels })

  // Mutation
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: FluencyTidesAPI.generateCard,
    onSuccess: (data) => {
      toast.success('Card Generated!', {
        description: `Note ID: ${data.note_id} in deck ${data.deck_name}`,
      })
      queryClient.invalidateQueries({ queryKey: ['graph'] }) // 確保圖譜資料更新
      setWord('') // clear input
    },
    onError: (error: any) => {
      toast.error('Failed to generate card', {
        description: error.message || 'An unknown error occurred.',
      })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!word.trim()) return

    const modelInfo = models?.find(m => m.model_file_name === selectedModel)
    
    mutation.mutate({
      user_input: word.trim(),
      deck_name: selectedDeck,
      model_name: modelInfo?.model_name || 'TOEIC_Coach_Dark',
      model_file_name: selectedModel,
      primary_field_name: 'Expression',
      tags: ['FrontendGen']
    })
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Card Generator</h2>
        <p className="text-muted-foreground mt-2">
          Enter a word or phrase to automatically generate an Anki card.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Generate New Card</CardTitle>
          <CardDescription>Select a deck and model, then type your word.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Deck</label>
                <select 
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={selectedDeck}
                  onChange={(e) => setSelectedDeck(e.target.value)}
                >
                  {decks?.map(d => (
                    <option key={d.deck_id} value={d.deck_name}>{d.deck_name}</option>
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
                  {models?.map(m => (
                    <option key={m.model_file_name} value={m.model_file_name}>{m.model_name}</option>
                  ))}
                </select>
              </div>
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
                <Button type="submit" disabled={mutation.isPending || !word.trim()}>
                  {mutation.isPending ? 'Generating...' : 'Generate'}
                </Button>
              </div>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Progress / Loading State indicator */}
      {mutation.isPending && (
        <Card className="border-primary/50 bg-primary/5">
          <CardContent className="pt-6">
            <div className="flex flex-col items-center justify-center space-y-4">
              <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
              <p className="text-sm font-medium text-primary">Calling LLM and generating your card...</p>
              <Skeleton className="h-4 w-[250px] bg-primary/20" />
              <Skeleton className="h-4 w-[200px] bg-primary/20" />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
