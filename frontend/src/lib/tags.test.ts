import { describe, expect, it } from 'vitest'
import { addTag, isKnownTag, removeTag } from './tags'

describe('addTag', () => {
  it('trims and appends', () => {
    expect(addTag(['Interview'], '  FlowerShop ')).toEqual({ tags: ['Interview', 'FlowerShop'], error: null })
  })

  it('skips blanks and case-insensitive duplicates without a hint', () => {
    expect(addTag(['Interview'], '   ')).toEqual({ tags: ['Interview'], error: null })
    expect(addTag(['Interview'], 'interview')).toEqual({ tags: ['Interview'], error: null })
  })

  it('rejects whitespace inside a tag', () => {
    const result = addTag([], 'Hair Salon')
    expect(result.tags).toEqual([])
    expect(result.error).toContain('空白')
  })
})

describe('removeTag / isKnownTag', () => {
  it('removes exactly that tag', () => {
    expect(removeTag(['a', 'b'], 'a')).toEqual(['b'])
  })

  it('matches known tags case-insensitively', () => {
    expect(isKnownTag(['Interview'], 'interview')).toBe(true)
    expect(isKnownTag(['Interview'], 'HairSalon')).toBe(false)
  })
})
