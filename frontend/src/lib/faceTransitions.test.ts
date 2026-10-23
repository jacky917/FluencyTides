import { describe, expect, it } from 'vitest'
import {
  DEFAULT_FACE_PREFERENCE,
  FACE_SPEEDS,
  FACE_TRANSITIONS,
  faceMarkup,
  faceTiming,
  sanitizeFacePreference,
} from './faceTransitions'

describe('sanitizeFacePreference', () => {
  it('keeps known values', () => {
    expect(sanitizeFacePreference({ animation: 'slide', speed: 'slow' })).toEqual({ animation: 'slide', speed: 'slow' })
  })

  it('migrates renamed animations', () => {
    expect(sanitizeFacePreference({ animation: 'flip', speed: 'fast' })).toEqual({ animation: 'page', speed: 'fast' })
    expect(sanitizeFacePreference({ animation: 'flip-vertical' }).animation).toBe('page')
    expect(sanitizeFacePreference({ animation: 'fade' }).animation).toBe('morph')
  })

  it.each([null, 'page', {}, { animation: 'spin', speed: 'warp' }, { animation: 3 }])('defaults %j', (raw) => {
    expect(sanitizeFacePreference(raw)).toEqual(DEFAULT_FACE_PREFERENCE)
  })
})

describe('faceTiming', () => {
  it('scales the base duration per animation', () => {
    expect(faceTiming({ animation: 'slide', speed: 'normal' }).ms).toBe(FACE_SPEEDS.normal.ms)
    expect(faceTiming({ animation: 'page', speed: 'normal' }).ms).toBeGreaterThan(FACE_SPEEDS.normal.ms)
    expect(faceTiming({ animation: 'none', speed: 'slow' }).ms).toBe(0)
  })
})

describe('faceMarkup', () => {
  it('plays the forward parts going to the back', () => {
    expect(faceMarkup({ animation: 'page', speed: 'normal' }, 'leaving', 1)).toMatchObject({
      'data-face-anim': 'page',
      'data-face-part': 'leave',
      style: { '--face-play': 'normal' },
    })
    expect(faceMarkup({ animation: 'page', speed: 'normal' }, 'entering', 1)?.['data-face-part']).toBe('enter')
  })

  it('plays the forward parts reversed, roles swapped, going back', () => {
    // 往回：舊的一面倒放「進來」那段，新的一面倒放「離開」那段
    expect(faceMarkup({ animation: 'slide', speed: 'fast' }, 'leaving', -1)).toMatchObject({
      'data-face-part': 'enter',
      style: { '--face-play': 'reverse' },
    })
    expect(faceMarkup({ animation: 'slide', speed: 'fast' }, 'entering', -1)?.['data-face-part']).toBe('leave')
  })

  it('carries the duration and easing as CSS variables', () => {
    const { ms, easing } = faceTiming({ animation: 'zoom', speed: 'slow' })
    expect(faceMarkup({ animation: 'zoom', speed: 'slow' }, 'entering', 1)?.style).toMatchObject({
      '--face-duration': `${ms}ms`,
      '--face-easing': easing,
    })
  })

  it('splits a morph at the shorter height, marking the taller face', () => {
    const morph = { animation: 'morph', speed: 'normal' } as const
    // 長變短：舊的一面較高
    expect(faceMarkup(morph, 'leaving', 1, { from: 500, to: 300.5 })).toMatchObject({
      'data-face-span': 'long',
      style: { '--face-split': '300.5px' },
    })
    expect(faceMarkup(morph, 'entering', 1, { from: 500, to: 300.5 })?.['data-face-span']).toBe('short')
    // 短變長（往回也一樣，只看高度）：新的一面較高
    expect(faceMarkup(morph, 'entering', -1, { from: 300, to: 500 })).toMatchObject({
      'data-face-span': 'long',
      style: { '--face-split': '300px' },
    })
    expect(faceMarkup(morph, 'leaving', -1, { from: 300, to: 500 })?.style).not.toHaveProperty('--face-split')
  })

  it('does not split equal heights, unknown heights or other animations', () => {
    const morph = { animation: 'morph', speed: 'normal' } as const
    expect(faceMarkup(morph, 'leaving', 1, { from: 300, to: 300 })).not.toHaveProperty('data-face-span')
    expect(faceMarkup(morph, 'leaving', 1, { from: 300 })).not.toHaveProperty('data-face-span')
    expect(faceMarkup(morph, 'leaving', 1)).not.toHaveProperty('data-face-span')
    expect(faceMarkup({ animation: 'zoom', speed: 'normal' }, 'leaving', 1, { from: 500, to: 300 })).not.toHaveProperty(
      'data-face-span',
    )
  })

  it('has no markup without animation', () => {
    expect(faceMarkup({ animation: 'none', speed: 'normal' }, 'leaving', 1)).toBeNull()
  })

  it('lists every animation once, including the default', () => {
    const ids = FACE_TRANSITIONS.map((option) => option.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toContain(DEFAULT_FACE_PREFERENCE.animation)
  })
})
