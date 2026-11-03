import { useQuery } from '@tanstack/react-query'
import { FluencyTidesAPI } from '../api/client'

/**
 * 所選牌組的狀態：`all` 沒選牌組（全部牌組）、`loading` 牌組清單還在載入、`error` 清單載入失敗
 * （無法確認牌組是否存在）、`missing` 牌組已不存在、`ok` 牌組存在。
 * The state of the selected deck: none selected, list loading, list failed
 * (existence unknown), deleted, or present.
 */
export type DeckStatus = 'all' | 'loading' | 'error' | 'missing' | 'ok'

/**
 * 確認記住的牌組還在：它可能已在 Anki 被刪除或改名。與牌組樹共用同一份 `['decks']` 查詢。
 * Checks that a remembered deck still exists; shares the deck tree's query.
 * @param deck - 所選牌組；null 為全部牌組。The selected deck; null for all decks.
 * @returns 牌組狀態。The deck status.
 */
export function useDeckStatus(deck: string | null): DeckStatus {
  const { data: decks, isError } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  if (deck === null) return 'all'
  if (decks === undefined) return isError ? 'error' : 'loading'
  return decks.some((d) => d.deck_name === deck) ? 'ok' : 'missing'
}
