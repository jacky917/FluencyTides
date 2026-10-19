import { describe, expect, it } from 'vitest'
import { nextPull, rubberBand, scrollEdges } from './elasticOverscroll'

describe('rubberBand', () => {
  it('grows ever slower and never reaches the limit', () => {
    const near = rubberBand(50, 200)
    const far = rubberBand(5000, 200)
    expect(near).toBeGreaterThan(0)
    expect(near).toBeLessThan(50)
    expect(far).toBeLessThan(200)
    expect(rubberBand(100, 200) - rubberBand(50, 200)).toBeLessThan(rubberBand(50, 200))
  })

  it('is symmetric and zero at rest', () => {
    expect(rubberBand(-80, 200)).toBeCloseTo(-rubberBand(80, 200))
    expect(rubberBand(0, 200)).toBe(0)
    expect(rubberBand(10, 0)).toBe(0)
  })
})

describe('scrollEdges', () => {
  it('detects the edges with a pixel of tolerance', () => {
    expect(scrollEdges({ scrollTop: 0, scrollHeight: 1000, clientHeight: 400 })).toEqual({ atTop: true, atBottom: false })
    expect(scrollEdges({ scrollTop: 599.5, scrollHeight: 1000, clientHeight: 400 })).toEqual({ atTop: false, atBottom: true })
    expect(scrollEdges({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 })).toEqual({ atTop: true, atBottom: true })
  })
})

describe('nextPull', () => {
  const middle = { atTop: false, atBottom: false }
  const top = { atTop: true, atBottom: false }
  const bottom = { atTop: false, atBottom: true }

  it('only starts pulling past an edge', () => {
    expect(nextPull(0, 30, middle)).toBe(0)
    expect(nextPull(0, 30, top)).toBe(30)
    expect(nextPull(0, -30, top)).toBe(0)
    expect(nextPull(0, -30, bottom)).toBe(-30)
  })

  it('cancels a pull before scrolling the other way', () => {
    expect(nextPull(40, -10, middle)).toBe(30)
    expect(nextPull(40, -100, middle)).toBe(0)
    expect(nextPull(-40, 100, middle)).toBe(0)
  })
})
