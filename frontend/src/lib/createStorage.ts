/**
 * 建卡畫面存在瀏覽器的鍵，集中在這裡（`docs/wip/retire_generate_page_FEAT_2026-11-23.md` D5）。
 * The browser storage keys of the create form, in one place.
 *
 * - localStorage：選的卡片類型、卡片類型選單是否展開、填表內容與標籤（關掉分頁再開還在，同一個瀏覽器的分頁共用）。
 * - sessionStorage：建立成功的提示（只在這個分頁）。
 */

/** 選的卡片類型（handler 名稱）。The chosen card type. */
export const CREATE_HANDLER_KEY = 'create_handler'

/** 卡片類型選單是否展開。Whether the card type picker is open. */
export const CREATE_PICKER_OPEN_KEY = 'create_picker_open'

/**
 * 這種卡片的填表內容（預覽流程是生成前的輸入）。The form values of a card type.
 * @param handler - 卡片類型。The handler name.
 * @returns 鍵。The key.
 */
export function createFormKey(handler: string): string {
  return `create_form:${handler}`
}

/**
 * 這種卡片的標籤草稿。The tag draft of a card type.
 * @param handler - 卡片類型。The handler name.
 * @returns 鍵。The key.
 */
export function createTagsKey(handler: string): string {
  return `create_tags:${handler}`
}

/**
 * 這種卡片「建立成功」的提示（sessionStorage）。The success notice of a card type.
 * @param handler - 卡片類型。The handler name.
 * @returns 鍵。The key.
 */
export function createdNoticeKey(handler: string): string {
  return `create_created:${handler}`
}
