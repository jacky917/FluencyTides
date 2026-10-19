import { describe, expect, it } from 'vitest'
import { initialFaceState, reduceFace, type FaceState } from './faceTransitionMachine'

type Side = 'q' | 'a'
const idle = initialFaceState<Side>(1, 'q', -1)
const sync = (state: FaceState<Side>, value: Side, extra: { key?: unknown; animated?: boolean; height?: number } = {}) =>
  reduceFace(state, {
    type: 'sync',
    key: extra.key ?? 1,
    value,
    direction: value === 'a' ? 1 : -1,
    animated: extra.animated ?? true,
    measure: () => extra.height,
  })

describe('reduceFace', () => {
  it('returns the same object when nothing changes', () => {
    expect(sync(idle, 'q')).toBe(idle)
    expect(reduceFace(idle, { type: 'ready', height: 10 })).toBe(idle)
    expect(reduceFace(idle, { type: 'finished' })).toBe(idle)
  })

  it('prepares, animates and settles on the new face', () => {
    const preparing = sync(idle, 'a', { height: 300 })
    expect(preparing).toMatchObject({ stage: 'preparing', current: 'q', incoming: 'a', direction: 1, fromHeight: 300 })

    const animating = reduceFace(preparing, { type: 'ready', height: 500 })
    expect(animating).toMatchObject({ stage: 'animating', fromHeight: 300, toHeight: 500 })
    // 播放中再同步同一個值不會改變狀態
    expect(sync(animating, 'a')).toBe(animating)

    const done = reduceFace(animating, { type: 'finished' })
    expect(done).toMatchObject({ stage: 'idle', current: 'a', incoming: null, fromHeight: undefined, toHeight: undefined })
  })

  it('switches immediately without animation', () => {
    expect(sync(idle, 'a', { animated: false })).toMatchObject({ stage: 'idle', current: 'a', incoming: null })
  })

  it('cancels when toggled back before the animation starts', () => {
    const preparing = sync(idle, 'a')
    expect(sync(preparing, 'q')).toMatchObject({ stage: 'idle', current: 'q', incoming: null })
  })

  it('finishes a switch, then follows a later change', () => {
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    // 播放中切回正面：先播完，回到 idle 後下一次同步再開始往回
    expect(sync(animating, 'q')).toBe(animating)
    const done = reduceFace(animating, { type: 'finished' })
    expect(sync(done, 'q')).toMatchObject({ stage: 'preparing', current: 'a', incoming: 'q', direction: -1 })
  })

  it('jumps to the result when animation is turned off mid-switch', () => {
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    expect(sync(animating, 'a', { animated: false })).toMatchObject({ stage: 'idle', current: 'a', incoming: null })
  })

  it('resets without animation when the key changes', () => {
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    expect(sync(animating, 'q', { key: 2 })).toEqual(initialFaceState(2, 'q', -1))
  })
})
