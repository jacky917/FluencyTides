import { describe, expect, it } from 'vitest'
import { MAX_LINES_PAGE, STORY_LINES_PAGE, firstPageSize } from './useStoryLines'

describe('firstPageSize（第一頁不超過後端的 MAX_LINES_PAGE）', () => {
  it('至少一頁、涵蓋到 minSeq、不超過上限', () => {
    expect(firstPageSize(0)).toBe(STORY_LINES_PAGE)
    expect(firstPageSize(299)).toBe(STORY_LINES_PAGE)
    expect(firstPageSize(300)).toBe(2 * STORY_LINES_PAGE)
    expect(firstPageSize(1999)).toBe(MAX_LINES_PAGE)
    expect(firstPageSize(5000)).toBe(MAX_LINES_PAGE)
  })
})
