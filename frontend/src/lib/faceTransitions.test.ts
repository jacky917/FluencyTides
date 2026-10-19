import { describe, expect, it } from 'vitest'
import {
  DEFAULT_FLIP_PREFERENCE,
  FACE_SPEEDS,
  FACE_TRANSITIONS,
  faceTransitionStyle,
  needsPerspective,
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
  it('plays each half for half the duration, mirrored by direction', () => {
    const style = faceTransitionStyle({ animation: 'flip', speed: 'normal' }, 'out', -1)
    expect(style?.animation).toBe(`face-flip-out ${FACE_SPEEDS.normal.ms / 2}ms cubic-bezier(0.4, 0, 1, 1) both`)
    expect((style as Record<string, unknown>)['--face-dir']).toBe(-1)
    expect(faceTransitionStyle({ animation: 'fade', speed: 'fast' }, 'in', 1)?.animation).toContain('face-fade-in')
  })

  it('has no style for no animation', () => {
    expect(faceTransitionStyle({ animation: 'none', speed: 'slow' }, 'in', 1)).toBeUndefined()
  })

  it('uses a perspective for the flips only', () => {
    expect(needsPerspective('flip')).toBe(true)
    expect(needsPerspective('flip-vertical')).toBe(true)
    expect(needsPerspective('fade')).toBe(false)
  })

  it('lists every animation once', () => {
    const ids = FACE_TRANSITIONS.map((option) => option.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toContain(DEFAULT_FLIP_PREFERENCE.animation)
  })
})
