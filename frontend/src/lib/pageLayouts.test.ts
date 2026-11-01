import { describe, expect, it } from 'vitest'
import {
  CARD_LAYOUT,
  DECK_DEFAULT,
  DECK_MAX,
  DECK_MIN,
  GENERATE_LAYOUT,
  GRAPH_LAYOUT,
  LIST_MIN,
  MAIN_MIN,
  THREE_COLUMN_MIN,
  TWO_COLUMN_MIN,
  VIEWER_DEFAULT,
  VIEWER_DEFAULT_WIDE,
  VIEWER_MIN,
  WIDE_SCREEN,
} from './pageLayouts'
import { defaultWidths, panelWidthsKey, sanitizeWidths, splitMode } from './splitLayout'

// 各頁版面設定的特性測試：斷點、預設寬度與上下限改了就會失敗，確認是刻意的再更新
// Characterization tests of the page layouts.

/** 一條分隔線（含左右 margin）最多佔的寬度。The most a separator takes, margins included. */
const HANDLE_ALLOWANCE = 24

/**
 * 並排的欄都縮到最窄、加上分隔線後的總寬度。Total width of side-by-side columns at their minimums.
 * @param mins - 各欄最窄寬度。The columns' minimum widths.
 */
const minRowWidth = (...mins: number[]) => mins.reduce((sum, min) => sum + min, 0) + HANDLE_ALLOWANCE * (mins.length - 1)

describe('card browsing page layout', () => {
  it('picks the columns from the content width', () => {
    const { breakpoints } = CARD_LAYOUT
    expect(splitMode(THREE_COLUMN_MIN, breakpoints)).toBe('full')
    expect(splitMode(THREE_COLUMN_MIN - 1, breakpoints)).toBe('partial')
    expect(splitMode(TWO_COLUMN_MIN, breakpoints)).toBe('partial')
    expect(splitMode(TWO_COLUMN_MIN - 1, breakpoints)).toBe('stacked')
    expect(splitMode(0, breakpoints)).toBe('stacked')
  })

  it('widens the viewer on screens wider than 1920px', () => {
    expect(defaultWidths(WIDE_SCREEN, CARD_LAYOUT.specs)).toEqual({ deck: DECK_DEFAULT, viewer: VIEWER_DEFAULT })
    expect(defaultWidths(WIDE_SCREEN + 1, CARD_LAYOUT.specs).viewer).toBe(VIEWER_DEFAULT_WIDE)
  })

  it('keeps valid stored widths, rounded', () => {
    expect(sanitizeWidths({ deck: 300.4, viewer: 900 }, CARD_LAYOUT.specs)).toEqual({ deck: 300, viewer: 900 })
  })

  it('keeps only the panels the user set', () => {
    expect(sanitizeWidths({ deck: 300 }, CARD_LAYOUT.specs)).toEqual({ deck: 300 })
  })

  it('clamps out-of-range widths', () => {
    expect(sanitizeWidths({ deck: 10, viewer: 99999 }, CARD_LAYOUT.specs).deck).toBe(DECK_MIN)
    expect(sanitizeWidths({ deck: 99999, viewer: 10 }, CARD_LAYOUT.specs)).toEqual({ deck: DECK_MAX, viewer: VIEWER_MIN })
  })

  it.each([null, 'x', 3, {}, { deck: 'wide', viewer: Number.NaN }])('drops invalid values in %j', (raw) => {
    expect(sanitizeWidths(raw, CARD_LAYOUT.specs)).toEqual({})
  })

  it('fits every column at its minimum width at each breakpoint', () => {
    const { specs, fluidMin, breakpoints } = CARD_LAYOUT
    expect(fluidMin).toEqual({ list: LIST_MIN })
    expect(minRowWidth(specs.deck.min, fluidMin.list, specs.viewer.min)).toBeLessThanOrEqual(breakpoints.full)
    expect(minRowWidth(specs.deck.min, fluidMin.list)).toBeLessThanOrEqual(breakpoints.partial!)
  })

  it('keeps its existing storage key so stored widths survive the refactor', () => {
    expect(panelWidthsKey(CARD_LAYOUT)).toBe('cards_panel_widths_v2')
  })
})

describe('generate and graph page layouts', () => {
  it.each([GENERATE_LAYOUT, GRAPH_LAYOUT])('puts the deck tree beside the main area from TWO_COLUMN_MIN on ($id)', (layout) => {
    expect(splitMode(TWO_COLUMN_MIN, layout.breakpoints)).toBe('full')
    expect(splitMode(TWO_COLUMN_MIN - 1, layout.breakpoints)).toBe('stacked')
  })

  it.each([GENERATE_LAYOUT, GRAPH_LAYOUT])('fits the deck tree and the main area at their minimums ($id)', (layout) => {
    expect(layout.fluidMin).toEqual({ main: MAIN_MIN })
    expect(minRowWidth(layout.specs.deck.min, layout.fluidMin.main)).toBeLessThanOrEqual(layout.breakpoints.full)
  })

  it('gives the shared deck tree its usual width', () => {
    expect(defaultWidths(1920, GENERATE_LAYOUT.specs)).toEqual({ deck: DECK_DEFAULT })
  })

  it('stores each page under its own key', () => {
    const keys = [CARD_LAYOUT, GENERATE_LAYOUT, GRAPH_LAYOUT].map((layout) => panelWidthsKey(layout))
    expect(new Set(keys).size).toBe(3)
    expect(panelWidthsKey(GENERATE_LAYOUT)).toBe('generate_panel_widths')
    expect(panelWidthsKey(GRAPH_LAYOUT)).toBe('graph_panel_widths')
  })
})
