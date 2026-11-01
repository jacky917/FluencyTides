/**
 * 卡片頁的「在流程圖中顯示」（D17）：筆記類型屬於某款遊戲的卡片類型時才顯示；點了查出台詞位置，
 * 導向 `/story/{game}?tab=&node=&line=`。筆記類型與遊戲的對應由 `GET /story/games` 提供，不寫死。
 * The card page's "show in flowchart" button; the note-type mapping comes
 * from `GET /story/games`.
 *
 * - 只有 `STORY_LINE_NOT_FOUND` 代表「這款遊戲沒有這句，換下一款」；其他錯誤（含別種 404）直接提示。
 * - 遊戲清單讀不到時不顯示按鈕，只在 console 記錯誤：清單失敗就不知道這張卡和哪款遊戲有關，
 *   若改成顯示停用的按鈕，每一張卡（包括和遊戲無關的筆記類型）都會多一個灰掉的按鈕，太吵。
 * Only `STORY_LINE_NOT_FOUND` moves on to the next game; other errors are
 * shown. When the game list fails the button is hidden and the error goes
 * to the console, because a disabled button would appear on every card.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { GitBranch, Loader2 } from 'lucide-react'
import { StoryAPI } from '../../api/story'
import { useStoryGames } from '../../hooks/useStoryGame'
import { describeApiError } from '../../lib/apiError'
import { formatStoryParams } from '../../lib/storyParams'
import { Button } from '../ui/button'

/**
 * 在流程圖中顯示。Shows the card's line on the story flowchart.
 * @param props.noteId - 筆記 ID。The note.
 * @param props.modelName - 筆記類型名稱。The note type.
 * @returns 按鈕，或不相關時為 null。The button, or null.
 */
export function ShowInStoryButton({ noteId, modelName }: { noteId: number | undefined; modelName: string | undefined }) {
  const navigate = useNavigate()
  const games = useStoryGames(!!modelName)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (games.isError) console.error('劇情流程圖的遊戲清單讀取失敗，卡片頁不顯示「流程圖」按鈕：', games.error)
  }, [games.isError, games.error])
  const candidates = (games.data ?? []).filter((g) => g.card_types.some((t) => t.note_types.includes(modelName ?? '')))
  if (!noteId || !modelName || !candidates.length) return null

  const open = async () => {
    setBusy(true)
    try {
      for (const game of candidates) {
        try {
          const loc = await StoryAPI.locate(game.game_id, { noteId })
          navigate(`/story/${encodeURIComponent(game.game_id)}?${formatStoryParams({ tab: loc.tab, node: loc.node_key, line: loc.line_id })}`)
          return
        } catch (e) {
          if ((e as { error_code?: string }).error_code !== 'STORY_LINE_NOT_FOUND') throw e
        }
      }
      toast.info('這張卡在流程圖上找不到對應的台詞')
    } catch (e) {
      toast.error(`查詢失敗：${describeApiError(e).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button size="sm" variant="outline" onClick={open} disabled={busy} title="在劇情流程圖中顯示這張卡的例句">
      {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <GitBranch className="mr-1 h-4 w-4" />}
      流程圖
    </Button>
  )
}
