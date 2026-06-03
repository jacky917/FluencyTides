import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FluencyTidesAPI } from '../api/client'
import { CardDetail } from '../types/api'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Loader2, Trash2, X } from 'lucide-react'

interface CardDetailModalProps {
  isOpen: boolean
  onClose: () => void
  cardDetail: CardDetail | null
  isLoading: boolean
}

export function CardDetailModal({ isOpen, onClose, cardDetail, isLoading }: CardDetailModalProps) {
  const queryClient = useQueryClient()
  const [fields, setFields] = useState<Record<string, string>>({})
  const [isDeleting, setIsDeleting] = useState(false)

  // Sync internal state when card detail is loaded
  useEffect(() => {
    if (cardDetail) {
      setFields(cardDetail.fields)
    }
  }, [cardDetail])

  const updateMutation = useMutation({
    mutationFn: (updatedFields: Record<string, string>) => 
      FluencyTidesAPI.updateCard(cardDetail!.note_id, updatedFields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      onClose()
    },
    onError: (err: any) => {
      alert(`Failed to update card: ${err.message || 'Unknown error'}`)
    }
  })

  const deleteMutation = useMutation({
    mutationFn: () => FluencyTidesAPI.deleteCard(cardDetail!.note_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      onClose()
    },
    onError: (err: any) => {
      alert(`Failed to delete card: ${err.message || 'Unknown error'}`)
      setIsDeleting(false)
    }
  })

  if (!isOpen) return null

  const handleFieldChange = (key: string, value: string) => {
    setFields(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = () => {
    if (cardDetail) {
      updateMutation.mutate(fields)
    }
  }

  const handleDelete = () => {
    if (confirm('Are you sure you want to delete this card? This action cannot be undone and will remove related graph connections.')) {
      setIsDeleting(true)
      deleteMutation.mutate()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-background border shadow-lg rounded-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex justify-between items-center px-6 py-4 border-b">
          <div>
            <h2 className="text-xl font-semibold">Edit Card Details</h2>
            {cardDetail && (
              <p className="text-sm text-muted-foreground mt-1">
                Model: {cardDetail.model_name}
              </p>
            )}
          </div>
          <button 
            onClick={onClose}
            className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-full transition-colors"
          >
            <X className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <div className="flex justify-center items-center h-32">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>
          ) : cardDetail ? (
            <div className="space-y-4">
              {Object.entries(fields).map(([key, value]) => {
                // Ignore internal JSON fields to prevent manual syntax breaking
                if (key.includes('JSON')) return null;
                
                // For longer fields like ExampleSentence, use textarea, else input
                const isLongText = key === 'ExampleSentence' || value.length > 50;

                return (
                  <div key={key} className="space-y-1">
                    <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                      {key}
                    </label>
                    {isLongText ? (
                      <textarea
                        className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={value}
                        onChange={(e) => handleFieldChange(key, e.target.value)}
                      />
                    ) : (
                      <Input
                        value={value}
                        onChange={(e) => handleFieldChange(key, e.target.value)}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center text-muted-foreground">
              Failed to load card details.
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between items-center px-6 py-4 border-t bg-slate-50/50 dark:bg-slate-900/50 rounded-b-xl">
          <Button 
            variant="destructive" 
            onClick={handleDelete}
            disabled={isLoading || isDeleting || !cardDetail}
          >
            {isDeleting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Trash2 className="w-4 h-4 mr-2" />}
            Delete Card
          </Button>
          <div className="space-x-2">
            <Button variant="outline" onClick={onClose} disabled={updateMutation.isPending || isDeleting}>
              Cancel
            </Button>
            <Button 
              onClick={handleSave} 
              disabled={isLoading || updateMutation.isPending || isDeleting || !cardDetail}
            >
              {updateMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Save Changes
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
