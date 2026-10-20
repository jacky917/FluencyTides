import { describe, expect, it } from 'vitest'
import { defaultWidths, sanitizeWidths, splitMode, type PanelSpecs } from './splitLayout'

const SPECS: PanelSpecs<'side' | 'extra'> = {
  side: { min: 100, max: 300, defaultWidth: () => 200 },
  extra: { min: 50, max: 500, defaultWidth: (windowWidth) => (windowWidth > 1000 ? 400 : 250) },
}

describe('splitMode', () => {
  it('has a partial mode only when a partial breakpoint is given', () => {
    const withPartial = { full: 1200, partial: 640 }
    expect(splitMode(1200, withPartial)).toBe('full')
    expect(splitMode(1199, withPartial)).toBe('partial')
    expect(splitMode(640, withPartial)).toBe('partial')
    expect(splitMode(639, withPartial)).toBe('stacked')

    const twoColumns = { full: 640 }
    expect(splitMode(640, twoColumns)).toBe('full')
    expect(splitMode(639, twoColumns)).toBe('stacked')
    expect(splitMode(0, twoColumns)).toBe('stacked')
  })
})

describe('defaultWidths', () => {
  it('asks each spec for its default at the window width', () => {
    expect(defaultWidths(800, SPECS)).toEqual({ side: 200, extra: 250 })
    expect(defaultWidths(1200, SPECS)).toEqual({ side: 200, extra: 400 })
  })

  it('clamps a default that falls outside the panel range, like stored widths', () => {
    const specs: PanelSpecs<'wide' | 'narrow'> = {
      wide: { min: 100, max: 300, defaultWidth: () => 999 },
      narrow: { min: 100, max: 300, defaultWidth: () => 10.4 },
    }
    expect(defaultWidths(1000, specs)).toEqual({ wide: 300, narrow: 100 })
  })
})

describe('sanitizeWidths', () => {
  it('keeps only the specified panels the user set, clamped and rounded', () => {
    expect(sanitizeWidths({ side: 150.6, unknown: 999 }, SPECS)).toEqual({ side: 151 })
    expect(sanitizeWidths({ side: 1, extra: 9999 }, SPECS)).toEqual({ side: 100, extra: 500 })
  })

  it.each([null, 'x', 3, {}, { side: 'wide', extra: Number.NaN }])('drops invalid values in %j', (raw) => {
    expect(sanitizeWidths(raw, SPECS)).toEqual({})
  })
})
