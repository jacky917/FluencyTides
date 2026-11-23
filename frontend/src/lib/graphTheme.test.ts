import { describe, expect, it } from 'vitest'
import { fallbackTheme, themeForLegacyStyle } from './graphTheme'

describe('themeForLegacyStyle', () => {
  it('keeps the two surviving styles and maps the retired ones to classic white', () => {
    expect(themeForLegacyStyle('classic')).toBe('classic-white')
    expect(themeForLegacyStyle('slate')).toBe('slate-gray')
    for (const retired of ['neon', 'sunset', 'mint', 'cyber']) expect(themeForLegacyStyle(retired)).toBe('classic-white')
  })

  it('returns null when nothing usable was stored', () => {
    expect(themeForLegacyStyle(null)).toBeNull()
    expect(themeForLegacyStyle('unknown')).toBeNull()
    expect(themeForLegacyStyle(3)).toBeNull()
  })
})

describe('fallbackTheme', () => {
  it('uses the default in light mode and classic white in dark mode', () => {
    expect(fallbackTheme(false)).toBeNull()
    expect(fallbackTheme(true)).toBe('classic-white')
  })
})
