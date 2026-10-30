/**
 * 建卡時的標籤輸入（計畫書 `docs/wip/trilingual_search_tags_FEAT_2026-10-30.md` §6.3）：已選的標籤以小方塊顯示，
 * 輸入框按 Enter 或逗號加入，從這種卡片用過的標籤自動完成。這種卡片沒用過的標籤以虛線並標「新」，提醒
 * 建卡時會新增一個標籤（避免同一個主題長出不同寫法）。
 *
 * Tag input for card creation: chips, Enter or comma to add, autocomplete
 * from the tags this kind of card already uses; new tags are marked.
 */

import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { addTag, isKnownTag, removeTag } from '../../lib/tags'
import { cn } from '../../lib/utils'
import { Input } from '../ui/input'

/**
 * 標籤輸入。The tag input.
 * @param props.handlerName - handler 名稱（查用過的標籤）。The handler.
 * @param props.tags - 已選的標籤。The selected tags.
 * @param props.onChange - 標籤改變時。Called with the new tags.
 * @param props.disabled - 是否停用。Whether disabled.
 * @returns 標籤輸入 JSX。The tag input JSX.
 */
export function TagInput({
  handlerName,
  tags,
  onChange,
  disabled = false,
}: {
  handlerName: string
  tags: string[]
  onChange: (tags: string[]) => void
  disabled?: boolean
}) {
  const id = useId()
  const [text, setText] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const { data: known } = useQuery({
    queryKey: ['tags', handlerName],
    queryFn: () => FluencyTidesAPI.listTags(handlerName),
    staleTime: 60_000,
  })
  const knownTags = (known ?? []).map((item) => item.tag)

  const commit = (value: string) => {
    const result = addTag(tags, value)
    setHint(result.error)
    if (result.error) return
    onChange(result.tags)
    setText('')
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={`${id}-input`} className="text-sm font-medium">
        標籤
      </label>
      {tags.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="已選的標籤">
          {tags.map((tag) => {
            const isNew = known !== undefined && !isKnownTag(knownTags, tag)
            return (
              <li
                key={tag}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs',
                  isNew ? 'border-dashed border-amber-500 text-amber-700 dark:text-amber-400' : 'bg-secondary',
                )}
                title={isNew ? '這種卡片還沒用過這個標籤，建立時會新增' : undefined}
              >
                {tag}
                {isNew && <span className="text-[10px] font-medium">新</span>}
                <button
                  type="button"
                  className="rounded-full p-0.5 hover:bg-accent disabled:opacity-50"
                  disabled={disabled}
                  aria-label={`移除標籤 ${tag}`}
                  onClick={() => onChange(removeTag(tags, tag))}
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <Input
        id={`${id}-input`}
        value={text}
        disabled={disabled}
        list={`${id}-known`}
        placeholder="輸入後按 Enter 加入"
        aria-invalid={hint ? true : undefined}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => {
          const value = e.target.value
          // 輸入逗號（含全形）時把前面的文字當成一個標籤
          if (/[,，]$/.test(value)) {
            commit(value.slice(0, -1))
            return
          }
          setText(value)
          setHint(null)
        }}
        onKeyDown={(e) => {
          // 輸入法選字中的 Enter 不算
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
            e.preventDefault()
            commit(text)
          }
        }}
      />
      <datalist id={`${id}-known`}>
        {(known ?? [])
          .filter((item) => !isKnownTag(tags, item.tag))
          .map((item) => (
            <option key={item.tag} value={item.tag} label={`${item.count} 張`} />
          ))}
      </datalist>
      {hint ? (
        <p id={`${id}-hint`} role="alert" className="text-xs text-destructive">
          {hint}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">從用過的標籤挑可以維持同一種寫法；虛線標「新」的會新增一個標籤。</p>
      )}
    </div>
  )
}
