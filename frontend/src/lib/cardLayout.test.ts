import { describe, expect, it } from 'vitest'
import {
  DECK_DEFAULT,
  DECK_MAX,
  DECK_MIN,
  THREE_COLUMN_MIN,
  TWO_COLUMN_MIN,
  VIEWER_DEFAULT,
  VIEWER_MIN,
  layoutMode,
  sanitizePanelWidths,
} from './cardLayout'

describe('layoutMode', () => {
  it('picks the columns from the content width', () => {
    expect(layoutMode(THREE_COLUMN_MIN)).toBe('three')
    expect(layoutMode(THREE_COLUMN_MIN - 1)).toBe('two')
    expect(layoutMode(TWO_COLUMN_MIN)).toBe('two')
    expect(layoutMode(TWO_COLUMN_MIN - 1)).toBe('stacked')
    expect(layoutMode(0)).toBe('stacked')
  })
})

describe('sanitizePanelWidths', () => {
  it('keeps valid widths, rounded', () => {
    expect(sanitizePanelWidths({ deck: 300.4, viewer: 900 })).toEqual({ deck: 300, viewer: 900 })
  })

  it('clamps out-of-range widths', () => {
    expect(sanitizePanelWidths({ deck: 10, viewer: 99999 }).deck).toBe(DECK_MIN)
    expect(sanitizePanelWidths({ deck: 99999, viewer: 10 })).toEqual({ deck: DECK_MAX, viewer: VIEWER_MIN })
  })

  it.each([null, 'x', 3, {}, { deck: 'wide', viewer: Number.NaN }])('falls back to defaults for %j', (raw) => {
    expect(sanitizePanelWidths(raw)).toEqual({ deck: DECK_DEFAULT, viewer: VIEWER_DEFAULT })
  })
})
