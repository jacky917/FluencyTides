import { describe, expect, it } from 'vitest'
import {
  DECK_DEFAULT,
  DECK_MAX,
  DECK_MIN,
  THREE_COLUMN_MIN,
  TWO_COLUMN_MIN,
  VIEWER_DEFAULT,
  VIEWER_DEFAULT_WIDE,
  VIEWER_MIN,
  WIDE_SCREEN,
  defaultPanelWidths,
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

describe('defaultPanelWidths', () => {
  it('widens the viewer on screens wider than 1920px', () => {
    expect(defaultPanelWidths(WIDE_SCREEN)).toEqual({ deck: DECK_DEFAULT, viewer: VIEWER_DEFAULT })
    expect(defaultPanelWidths(WIDE_SCREEN + 1).viewer).toBe(VIEWER_DEFAULT_WIDE)
  })
})

describe('sanitizePanelWidths', () => {
  it('keeps valid widths, rounded', () => {
    expect(sanitizePanelWidths({ deck: 300.4, viewer: 900 })).toEqual({ deck: 300, viewer: 900 })
  })

  it('keeps only the panels the user set', () => {
    expect(sanitizePanelWidths({ deck: 300 })).toEqual({ deck: 300 })
  })

  it('clamps out-of-range widths', () => {
    expect(sanitizePanelWidths({ deck: 10, viewer: 99999 }).deck).toBe(DECK_MIN)
    expect(sanitizePanelWidths({ deck: 99999, viewer: 10 })).toEqual({ deck: DECK_MAX, viewer: VIEWER_MIN })
  })

  it.each([null, 'x', 3, {}, { deck: 'wide', viewer: Number.NaN }])('drops invalid values in %j', (raw) => {
    expect(sanitizePanelWidths(raw)).toEqual({})
  })
})
