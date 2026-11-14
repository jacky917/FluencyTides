import { useQuery } from '@tanstack/react-query'
import { FluencyTidesAPI } from '../api/client'
import { isCategoryDeck } from '../lib/deckTree'

/**
 * 所選牌組的狀態：`all` 沒選牌組（全部牌組）、`loading` 牌組清單還在載入、`error` 清單載入失敗
 * （無法確認牌組是否存在）、`category` Anki 裡沒有這個牌組但底下有牌組（牌組樹的分類節點，可以補建）、
 * `missing` 牌組已不存在、`ok` 牌組存在。
 * The state of the selected deck: none selected, list loading, list failed
 * (existence unknown), only a category above existing decks, deleted, or present.
 */
export type DeckStatus = 'all' | 'loading' | 'error' | 'category' | 'missing' | 'ok'

/**
 * 確認記住的牌組還在：它可能已在 Anki 被刪除或改名，也可能只是牌組樹的分類節點。與牌組樹共用同一份
 * `['decks']` 查詢。
 * Checks that a remembered deck still exists; shares the deck tree's query.
 * @param deck - 所選牌組；null 為全部牌組。The selected deck; null for all decks.
 * @returns 牌組狀態。The deck status.
 */
export function useDeckStatus(deck: string | null): DeckStatus {
  const { data: decks, isError } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  if (deck === null) return 'all'
  if (decks === undefined) return isError ? 'error' : 'loading'
  const names = decks.map((d) => d.deck_name)
  if (names.includes(deck)) return 'ok'
  return isCategoryDeck(names, deck) ? 'category' : 'missing'
}
