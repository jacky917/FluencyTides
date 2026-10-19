import { describe, expect, it } from 'vitest'
import {
  DEFAULT_FLIP_PREFERENCE,
  FACE_SPEEDS,
  FACE_TRANSITIONS,
  faceTiming,
  faceTransitionStyle,
  isFlip,
  sanitizeFlipPreference,
} from './faceTransitions'

describe('sanitizeFlipPreference', () => {
  it('keeps known values', () => {
    expect(sanitizeFlipPreference({ animation: 'slide', speed: 'slow' })).toEqual({ animation: 'slide', speed: 'slow' })
  })

  it.each([null, 'flip', {}, { animation: 'spin', speed: 'warp' }, { animation: 3 }])('defaults %j', (raw) => {
    expect(sanitizeFlipPreference(raw)).toEqual(DEFAULT_FLIP_PREFERENCE)
  })
})

describe('faceTransitionStyle', () => {
  it('plays both faces for the whole duration, mirrored by direction', () => {
    const preference = { animation: 'flip', speed: 'normal' } as const
    const { ms, easing } = faceTiming(preference)
    const leave = faceTransitionStyle(preference, 'leave', -1) as Record<string, unknown>
    expect(ms).toBe(FACE_SPEEDS.normal.ms)
    expect(leave.animation).toBe(`face-flip-leave ${ms}ms ${easing} both`)
    expect(leave['--face-dir']).toBe(-1)
    expect(faceTransitionStyle({ animation: 'fade', speed: 'fast' }, 'enter', 1)?.animation).toContain('face-fade-enter')
  })

  it('hides the backfaces of flips only', () => {
    expect(faceTransitionStyle({ animation: 'flip-vertical', speed: 'fast' }, 'enter', 1)?.backfaceVisibility).toBe('hidden')
    expect(faceTransitionStyle({ animation: 'slide', speed: 'fast' }, 'enter', 1)?.backfaceVisibility).toBeUndefined()
  })

  it('has no style for no animation', () => {
    expect(faceTransitionStyle({ animation: 'none', speed: 'slow' }, 'enter', 1)).toBeUndefined()
  })

  it('knows which animations flip', () => {
    expect(isFlip('flip')).toBe(true)
    expect(isFlip('flip-vertical')).toBe(true)
    expect(isFlip('fade')).toBe(false)
  })

  it('lists every animation once', () => {
    const ids = FACE_TRANSITIONS.map((option) => option.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toContain(DEFAULT_FLIP_PREFERENCE.animation)
  })
})
