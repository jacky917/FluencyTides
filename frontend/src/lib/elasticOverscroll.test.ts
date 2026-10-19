import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_MAX_PULL, createElasticOverscroll, nextPull, rubberBand, scrollEdges } from './elasticOverscroll'

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

describe('createElasticOverscroll', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  const setup = () => {
    vi.useFakeTimers()
    // 測試環境是 Node：控制器只用到 window.setTimeout / clearTimeout
    vi.stubGlobal('window', globalThis)
    const scroller = { scrollTop: 0, scrollHeight: 1000, clientHeight: 400 } as HTMLElement
    const content = { style: {} } as HTMLElement
    const onPull = vi.fn()
    const controller = createElasticOverscroll(scroller, content, { onPull })
    const offset = () => Number(/translate3d\(0, (-?[\d.]+)px/.exec(content.style.transform)?.[1] ?? 0)
    return { scroller, content, onPull, controller, offset }
  }

  it('never moves the content past the limit, either way', () => {
    const { scroller, controller, offset } = setup()
    for (let i = 0; i < 50; i += 1) controller.wheel(-500)
    expect(offset()).toBeGreaterThan(0)
    expect(offset()).toBeLessThan(DEFAULT_MAX_PULL)

    vi.runAllTimers()
    scroller.scrollTop = 600
    for (let i = 0; i < 50; i += 1) controller.wheel(500)
    expect(offset()).toBeLessThan(0)
    expect(offset()).toBeGreaterThan(-DEFAULT_MAX_PULL)
  })

  it('reports the displacement for hints and springs back to zero', () => {
    const { content, onPull, controller } = setup()
    controller.wheel(-80)
    expect(onPull).toHaveBeenLastCalledWith(expect.any(Number), false)
    expect(onPull.mock.lastCall?.[0]).toBeGreaterThan(0)

    vi.advanceTimersByTime(200)
    expect(onPull).toHaveBeenLastCalledWith(0, true)
    expect(content.style.transform).toBe('')
  })

  it('ignores scrolling away from an edge', () => {
    const { onPull, controller } = setup()
    controller.wheel(80)
    expect(onPull).not.toHaveBeenCalled()
  })
})
