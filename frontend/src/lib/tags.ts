/**
 * 建卡時加的標籤（計畫書 `docs/wip/trilingual_search_tags_FEAT_2026-10-30.md` §6.3）：加入、去重、格式提示。
 * 系統標籤等完整的規則由後端檢查（`app/services/tag_rules.py`），這裡只先擋明顯不合格的。
 *
 * Tags given at creation: add, de-duplicate, format hints; the backend has the full rules.
 */

/** 加入標籤的結果。The result of adding a tag. */
export interface AddTagResult {
  tags: string[]
  /** 不合格時的提示；null 表示沒問題（含「已經有了」這種不需要提示的情況）。The hint, or null. */
  error: string | null
}

/**
 * 加入標籤：以逗號（含全形）分開的會拆成多個（例如貼上 `Interview, FlowerShop`）；每個去前後空白；
 * 含空白的不加並提示（Anki 的標籤不能有空白）；與已有的只差大小寫時不重複加（Anki 的標籤不分大小寫）。
 * 其中一個不合格時一個都不加，讓使用者改好再按一次。
 * Adds comma-separated tags, trimming each, rejecting whitespace and skipping
 * case-insensitive duplicates; nothing is added when one is invalid.
 * @param tags - 目前的標籤。The current tags.
 * @param raw - 輸入的文字。The typed text.
 * @returns 新的標籤與提示。The new tags and a hint.
 */
export function addTag(tags: string[], raw: string): AddTagResult {
  const parts = raw
    .split(/[,，]/)
    .map((part) => part.trim())
    .filter(Boolean)
  const invalid = parts.find((part) => /\s/.test(part))
  if (invalid) return { tags, error: `「${invalid}」含空白；標籤不能有空白（可以用 _ 或 :: 代替）` }
  let next = tags
  for (const tag of parts) {
    if (!next.some((t) => t.toLowerCase() === tag.toLowerCase())) next = [...next, tag]
  }
  return { tags: next, error: null }
}

/**
 * 移除一個標籤。Removes a tag.
 * @param tags - 目前的標籤。The current tags.
 * @param tag - 要移除的。The tag to remove.
 * @returns 新的標籤。The new tags.
 */
export function removeTag(tags: string[], tag: string): string[] {
  return tags.filter((t) => t !== tag)
}

/**
 * 這種卡片是否用過這個標籤（不分大小寫）；沒用過的標籤建卡時會新增一個標籤。
 * Whether the tag is already used by this kind of card.
 * @param known - 用過的標籤。The known tags.
 * @param tag - 標籤。The tag.
 * @returns 是否用過。Whether it exists.
 */
export function isKnownTag(known: string[], tag: string): boolean {
  const folded = tag.toLowerCase()
  return known.some((t) => t.toLowerCase() === folded)
}
