import { describe, expect, it } from 'vitest'
import {
  MAX_MEDIA_BYTES,
  formatBytes,
  formatDuration,
  isUploadedName,
  mediaFieldState,
  mediaKindOf,
  mediaNameFromField,
  oversizeMessage,
  pickRecordingType,
  recordingErrorMessage,
  recordingFileName,
} from './mediaUpload'

const AUDIO = 'web_audio_20261114_010203_0a1b2c.mp3'
const IMAGE = 'web_image_20261114_010203_0a1b2c.jpg'

describe('mediaKindOf', () => {
  it('recognizes the media x-ui values only', () => {
    expect(mediaKindOf('audio')).toBe('audio')
    expect(mediaKindOf('image')).toBe('image')
    expect(mediaKindOf('textarea')).toBeNull()
    expect(mediaKindOf(undefined)).toBeNull()
  })
})

describe('oversizeMessage', () => {
  it('allows files up to the limit', () => {
    expect(oversizeMessage('audio', MAX_MEDIA_BYTES.audio)).toBeNull()
    expect(oversizeMessage('image', 1)).toBeNull()
  })

  it('explains the limit and the actual size', () => {
    expect(oversizeMessage('audio', MAX_MEDIA_BYTES.audio + 1)).toBe('音檔最大 10 MB，這個檔案是 10.0 MB')
    expect(oversizeMessage('image', 7.5 * 1024 * 1024)).toBe('圖片最大 5 MB，這個檔案是 7.5 MB')
  })
})

describe('formatting', () => {
  it('formats sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(48213)).toBe('47 KB')
    expect(formatBytes(1.3 * 1024 * 1024)).toBe('1.3 MB')
  })

  it('formats durations', () => {
    expect(formatDuration(0)).toBe('0:00')
    expect(formatDuration(7.9)).toBe('0:07')
    expect(formatDuration(83)).toBe('1:23')
    expect(formatDuration(-1)).toBe('0:00')
  })
})

describe('isUploadedName', () => {
  it('matches names produced by POST /media', () => {
    expect(isUploadedName(AUDIO)).toBe(true)
    expect(isUploadedName(IMAGE)).toBe(true)
  })

  it('rejects other names', () => {
    expect(isUploadedName('rec_SC_1_20261020_153012.ogg')).toBe(false) // TG 錄音
    expect(isUploadedName(`[sound:${AUDIO}]`)).toBe(false)
    expect(isUploadedName('')).toBe(false)
  })
})

describe('mediaNameFromField', () => {
  it('reads the single file of an audio field', () => {
    expect(mediaNameFromField('audio', `[sound:${AUDIO}]`)).toBe(AUDIO)
    expect(mediaNameFromField('audio', '  [sound:yuzu 001.ogg]  ')).toBe('yuzu 001.ogg')
  })

  it('reads the single file of an image field', () => {
    expect(mediaNameFromField('image', `<img src="${IMAGE}">`)).toBe(IMAGE)
    expect(mediaNameFromField('image', "<img src='a.png' />")).toBe('a.png')
    expect(mediaNameFromField('image', '<img src="a&amp;b.png">')).toBe('a&b.png')
  })

  it('returns null for empty, mixed or multiple contents', () => {
    expect(mediaNameFromField('audio', '')).toBeNull()
    expect(mediaNameFromField('audio', '[sound:a.mp3][sound:b.mp3]')).toBeNull()
    expect(mediaNameFromField('audio', '慢速 [sound:a.mp3]')).toBeNull()
    expect(mediaNameFromField('audio', '<img src="a.png">')).toBeNull() // 種類不符
    expect(mediaNameFromField('image', '<img src="a.png"><img src="b.png">')).toBeNull()
    expect(mediaNameFromField('image', '<img class="x" src="a.png">')).toBeNull()
  })
})

describe('mediaFieldState', () => {
  it('is empty for a new form without an upload', () => {
    expect(mediaFieldState('audio', '', '')).toEqual({ kind: 'empty' })
  })

  it('shows a fresh upload in a new form', () => {
    expect(mediaFieldState('audio', '', AUDIO)).toEqual({ kind: 'file', name: AUDIO, uploaded: true })
  })

  it("shows the note's current file while untouched", () => {
    const raw = '[sound:old.mp3]'
    expect(mediaFieldState('audio', raw, raw)).toEqual({ kind: 'file', name: 'old.mp3', uploaded: false })
  })

  it('shows the new upload once replaced, and empty once cleared', () => {
    const raw = '[sound:old.mp3]'
    expect(mediaFieldState('audio', raw, AUDIO)).toEqual({ kind: 'file', name: AUDIO, uploaded: true })
    expect(mediaFieldState('audio', raw, '')).toEqual({ kind: 'empty' })
  })

  it('keeps contents that are not a single file as they are', () => {
    const raw = '[sound:a.mp3][sound:b.mp3]'
    expect(mediaFieldState('audio', raw, raw)).toEqual({ kind: 'other', raw })
    expect(mediaFieldState('image', '  ', '  ')).toEqual({ kind: 'empty' })
  })
})

describe('recording', () => {
  it('picks the first supported type', () => {
    expect(pickRecordingType(() => true)).toBe('audio/webm;codecs=opus')
    expect(pickRecordingType((type) => type === 'audio/mp4')).toBe('audio/mp4') // Safari
    expect(pickRecordingType(() => false)).toBeUndefined()
  })

  it('names the recording after its actual type', () => {
    expect(recordingFileName('audio/webm;codecs=opus')).toBe('recording.webm')
    expect(recordingFileName('audio/mp4')).toBe('recording.m4a')
    expect(recordingFileName('audio/ogg;codecs=opus')).toBe('recording.ogg')
    expect(recordingFileName('')).toBe('recording.webm')
  })

  it('explains microphone failures', () => {
    expect(recordingErrorMessage({ name: 'NotAllowedError' })).toContain('不允許使用麥克風')
    expect(recordingErrorMessage({ name: 'NotFoundError' })).toBe('找不到麥克風。')
    expect(recordingErrorMessage({ name: 'NotReadableError' })).toContain('佔用')
    expect(recordingErrorMessage(new Error('boom'))).toBe('無法開始錄音：boom')
    expect(recordingErrorMessage(null)).toBe('無法開始錄音')
  })
})
