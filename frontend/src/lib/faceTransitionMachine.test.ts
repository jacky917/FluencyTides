import { describe, expect, it } from 'vitest'
import { initialFaceState, reduceFace, type FaceState } from './faceTransitionMachine'
import type { FacePreference } from './faceTransitions'

// q、a 是同一張卡片的正反面，b 是另一張卡片
type Side = 'q' | 'a' | 'b'
const idle = initialFaceState<Side>(1, 'q', -1)
const page: FacePreference = { animation: 'page', speed: 'normal' }
const off: FacePreference = { animation: 'none', speed: 'normal' }
const sync = (
  state: FaceState<Side>,
  value: Side,
  extra: { key?: unknown; preference?: FacePreference; height?: number } = {},
) =>
  reduceFace(state, {
    type: 'sync',
    key: extra.key ?? 1,
    value,
    direction: value === 'q' ? -1 : 1,
    preference: extra.preference ?? page,
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

  it('keeps the animation chosen when the switch started', () => {
    const slide: FacePreference = { animation: 'slide', speed: 'fast' }
    const preparing = sync(idle, 'a', { preference: slide })
    expect(preparing.preference).toEqual(slide)
    // 播放中外部換了設定（例如翻面接著換卡）：這一次仍播原本的動畫
    expect(sync(preparing, 'a', { preference: page })).toBe(preparing)
    const animating = reduceFace(preparing, { type: 'ready' })
    expect(sync(animating, 'a', { preference: page })).toBe(animating)
    expect(reduceFace(animating, { type: 'finished' }).preference).toBeUndefined()
  })

  it('retargets a switch that has not started yet', () => {
    // 新卡片還在排版時又點了另一張：改準備最新的那一張，不先播已經不要的那一面
    const slide: FacePreference = { animation: 'slide', speed: 'normal' }
    const preparing = sync(idle, 'a', { height: 300 })
    expect(sync(preparing, 'b', { preference: slide })).toMatchObject({
      stage: 'preparing',
      current: 'q',
      incoming: 'b',
      direction: 1,
      preference: slide,
      fromHeight: 300,
    })
  })

  it('finishes a running switch before heading to a third value', () => {
    const slide: FacePreference = { animation: 'slide', speed: 'normal' }
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    expect(sync(animating, 'b', { preference: slide })).toBe(animating)
    const done = reduceFace(animating, { type: 'finished' })
    expect(sync(done, 'b', { preference: slide })).toMatchObject({
      stage: 'preparing',
      current: 'a',
      incoming: 'b',
      preference: slide,
    })
  })

  it('jumps to a third value whose animation is off, ending the running switch', () => {
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    expect(sync(animating, 'b', { preference: off })).toMatchObject({ stage: 'idle', current: 'b', incoming: null })
  })

  it('switches immediately without animation', () => {
    expect(sync(idle, 'a', { preference: off })).toMatchObject({ stage: 'idle', current: 'a', incoming: null })
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
    expect(sync(animating, 'a', { preference: off })).toMatchObject({ stage: 'idle', current: 'a', incoming: null })
  })

  it('resets without animation when the key changes', () => {
    const animating = reduceFace(sync(idle, 'a'), { type: 'ready' })
    expect(sync(animating, 'q', { key: 2 })).toEqual(initialFaceState(2, 'q', -1))
  })
})
