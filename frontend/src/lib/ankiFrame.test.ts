import { describe, expect, it, vi } from 'vitest'
import {
  FRAME_BRIDGE_SCRIPT,
  MAX_FRAME_HEIGHT,
  buildCardDocument,
  createFrameMessageHandler,
  CARD_FRAME_CSP,
  MAX_BROWSE_NOTES,
  isBareMediaName,
  isLocalMediaName,
  parseAnkiConnectCall,
  parseNoteQuery,
  parseScrollGesture,
  type FrameMessageDeps,
} from './ankiFrame'

describe('media names', () => {
  it.each(['rec 1.ogg', '_avatar.png', 'sub/a.mp3', '絡まる.mp3'])('intercepts the relative name %j', (name) => {
    expect(isLocalMediaName(name)).toBe(true)
  })

  it.each(['blob:http://x/1', 'data:audio/mp3;base64,AA', 'https://a/b.mp3', '/abs.mp3', '#x', '?q', 'none', '', null, 3])(
    'leaves %j to the browser',
    (value) => {
      expect(isLocalMediaName(value)).toBe(false)
    },
  )

  it('fetches bare file names only', () => {
    expect(isBareMediaName('rec 1.ogg')).toBe(true)
    for (const name of ['', '.', '..', 'sub/a.mp3', 'a\\b.mp3', 'a\0.mp3']) {
      expect(isBareMediaName(name)).toBe(false)
    }
  })
})

describe('AnkiConnect calls', () => {
  const browse = JSON.stringify({ action: 'guiBrowse', version: 6, params: { query: 'nid:1788443669167' } })

  it.each([
    'http://127.0.0.1:8765',
    'http://localhost:8765/',
    'HTTP://127.0.0.1',
    ' http://127.0.0.1:8765 ',
    'http://127.0.0.1:8765?x=1',
    'http://127.0.0.1:8765#x',
    '//127.0.0.1:8765',
    'http://127.1:8765',
    'http://2130706433:8765',
    'http://0x7f.0.0.1:8765',
    'http://localhost.:8765',
    'http://[::1]:8765',
    'http://0.0.0.0:8765',
  ])(
    'intercepts %j',
    (url) => {
      expect(parseAnkiConnectCall(url, browse)).toEqual({ action: 'guiBrowse', params: { query: 'nid:1788443669167' } })
    },
  )

  it.each(['https://ft.example/api', 'http://127.0.0.1:8000', 'http://10.0.0.1:8765', 'blob:x', 'ftp://127.0.0.1:8765', null])(
    'lets %j through',
    (url) => {
      expect(parseAnkiConnectCall(url, browse)).toBeNull()
    },
  )

  it('still intercepts an AnkiConnect call whose body it cannot read', () => {
    expect(parseAnkiConnectCall('http://127.0.0.1:8765', '{not json')).toEqual({ action: '', params: null })
    expect(parseAnkiConnectCall('http://127.0.0.1:8765', null)).toEqual({ action: '', params: null })
  })

  it('reads note IDs from nid: queries only', () => {
    expect(parseNoteQuery('nid:12')).toEqual([12])
    expect(parseNoteQuery(' nid:12,34,12 ')).toEqual([12, 34])
    for (const query of ['deck:日本語', 'nid:', 'nid:12 deck:x', 'nid:0', 'nid:99999999999999999999', '']) {
      expect(parseNoteQuery(query)).toBeNull()
    }
    const many = Array.from({ length: MAX_BROWSE_NOTES + 5 }, (_, index) => index + 1)
    expect(parseNoteQuery(`nid:${many.join(',')}`)).toEqual(many.slice(0, MAX_BROWSE_NOTES))
  })
})

describe('bridge script', () => {
  it('is valid JavaScript after the nonce is filled in', () => {
    // String.raw 模板中誤寫的 ${ 會被插值成別的內容，tsc 與 eslint 都看不到
    expect(() => new Function(FRAME_BRIDGE_SCRIPT.replace('__NONCE__', 'abc123'))).not.toThrow()
  })

  it('builds the frame document with the bridge before the card', () => {
    const doc = buildCardDocument('<div id="face">x</div>', 1, 'n0nce')

    expect(doc.indexOf(CARD_FRAME_CSP)).toBeLessThan(doc.indexOf('<script>'))
    expect(doc.indexOf('<script>')).toBeLessThan(doc.indexOf('<div id="face">'))
    expect(doc).toContain("const nonce = 'n0nce'")
    expect(doc).not.toContain('__NONCE__')
    expect(doc).toContain('<body class="card card2">')
    expect(doc).toContain('<base target="_blank">')
  })
})

describe('createFrameMessageHandler', () => {
  const setup = (overrides: Partial<FrameMessageDeps> = {}) => {
    const frameWindow = { postMessage: vi.fn() } as unknown as Window & { postMessage: ReturnType<typeof vi.fn> }
    const deps: FrameMessageDeps = {
      frameWindow: () => frameWindow,
      nonce: 'n1',
      fetchMedia: vi.fn(async () => new Blob(['x'])),
      describeError: (error) => `described: ${String(error)}`,
      onHeight: vi.fn(),
      onMediaFailed: vi.fn(),
      onFrameError: vi.fn(),
      onBrowse: vi.fn(),
      onNotice: vi.fn(),
      onFaceClick: vi.fn(),
      onScrollGesture: vi.fn(),
      isActive: () => true,
      ...overrides,
    }
    const handle = createFrameMessageHandler(deps)
    const send = (data: unknown, source: unknown = frameWindow) => handle({ data, source } as MessageEvent)
    return { deps, frameWindow, send }
  }
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

  it('ignores other windows and other documents', () => {
    const { deps, send } = setup()

    send({ type: 'anki-frame-height', nonce: 'n1', height: 300 }, {})
    send({ type: 'anki-frame-height', nonce: 'old', height: 300 })

    expect(deps.onHeight).not.toHaveBeenCalled()
  })

  it('caps the height and rejects non-finite values', () => {
    const { deps, send } = setup()

    send({ type: 'anki-frame-height', nonce: 'n1', height: 300 })
    send({ type: 'anki-frame-height', nonce: 'n1', height: 10 ** 9 })
    send({ type: 'anki-frame-height', nonce: 'n1', height: Infinity })
    send({ type: 'anki-frame-height', nonce: 'n1', height: -1 })

    expect(vi.mocked(deps.onHeight).mock.calls).toEqual([[300], [MAX_FRAME_HEIGHT]])
  })

  it('forwards script errors from the frame', () => {
    const { deps, send } = setup()

    send({ type: 'anki-frame-error', nonce: 'n1', message: 'TypeError: x' })

    expect(deps.onFrameError).toHaveBeenCalledWith('TypeError: x')
  })

  it('forwards valid scroll gestures only', () => {
    const { deps, send } = setup()

    send({ type: 'anki-scroll-gesture', nonce: 'n1', gesture: { kind: 'wheel', deltaY: 5000 } })
    send({ type: 'anki-scroll-gesture', nonce: 'n1', gesture: { kind: 'touch', phase: 'move', y: 'x' } })
    send({ type: 'anki-scroll-gesture', nonce: 'n1', gesture: { kind: 'touch', phase: 'end' } })

    expect(deps.onScrollGesture).toHaveBeenCalledTimes(2)
    expect(deps.onScrollGesture).toHaveBeenNthCalledWith(1, { kind: 'wheel', deltaY: 1000 })
    expect(deps.onScrollGesture).toHaveBeenNthCalledWith(2, { kind: 'touch', phase: 'end' })
  })

  it('rejects malformed gestures', () => {
    for (const raw of [null, 3, {}, { kind: 'wheel' }, { kind: 'wheel', deltaY: Number.NaN }, { kind: 'touch', phase: 'drag' }]) {
      expect(parseScrollGesture(raw)).toBeNull()
    }
    expect(parseScrollGesture({ kind: 'touch', phase: 'move', y: 120 })).toEqual({ kind: 'touch', phase: 'move', y: 120 })
  })

  it('forwards clicks on the face', () => {
    const { deps, send } = setup()

    send({ type: 'anki-face-click', nonce: 'n1' })
    send({ type: 'anki-face-click', nonce: 'old' })

    expect(deps.onFaceClick).toHaveBeenCalledOnce()
  })

  it('opens the notes of a nid: search and explains other searches', () => {
    const { deps, send } = setup()

    send({ type: 'anki-browse', nonce: 'n1', query: 'nid:5,6' })
    send({ type: 'anki-browse', nonce: 'n1', query: 'deck:x' })
    send({ type: 'anki-frame-notice', nonce: 'n1', message: '網頁版不支援：updateNoteFields' })

    expect(deps.onBrowse).toHaveBeenCalledExactlyOnceWith([5, 6])
    expect(deps.onNotice).toHaveBeenCalledTimes(2)
    expect(deps.onNotice).toHaveBeenLastCalledWith('網頁版不支援：updateNoteFields')
  })

  it('answers names with a path with an error instead of leaving the frame waiting', () => {
    const { deps, frameWindow, send } = setup()

    send({ type: 'anki-media-request', nonce: 'n1', id: 4, name: 'sub/a.mp3' })

    expect(deps.fetchMedia).not.toHaveBeenCalled()
    expect(deps.onMediaFailed).toHaveBeenCalledWith('sub/a.mp3', expect.stringContaining('單一檔名'))
    expect(frameWindow.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'anki-media-response', nonce: 'n1', id: 4, error: expect.any(String) }),
      '*',
    )
  })

  it('replies with the blob, or with the described failure', async () => {
    const blob = new Blob(['audio'])
    const fetchMedia = vi.fn(async (name: string) => {
      if (name === 'missing.mp3') throw new Error('404')
      return blob
    })
    const { deps, frameWindow, send } = setup({ fetchMedia })

    send({ type: 'anki-media-request', nonce: 'n1', id: 1, name: 'ok.mp3' })
    send({ type: 'anki-media-request', nonce: 'n1', id: 2, name: 'missing.mp3' })
    await flush()

    expect(frameWindow.postMessage).toHaveBeenCalledWith({ type: 'anki-media-response', nonce: 'n1', id: 1, blob }, '*')
    expect(frameWindow.postMessage).toHaveBeenCalledWith(
      { type: 'anki-media-response', nonce: 'n1', id: 2, error: 'described: Error: 404' },
      '*',
    )
    expect(deps.onMediaFailed).toHaveBeenCalledWith('missing.mp3', 'described: Error: 404', expect.any(Error))
  })

  it('drops replies once the document is no longer current', async () => {
    let active = true
    const { deps, frameWindow, send } = setup({ isActive: () => active })

    send({ type: 'anki-media-request', nonce: 'n1', id: 1, name: 'ok.mp3' })
    active = false
    await flush()

    expect(frameWindow.postMessage).not.toHaveBeenCalled()
    expect(deps.onMediaFailed).not.toHaveBeenCalled()
  })
})
