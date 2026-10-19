import { describe, expect, it, vi } from 'vitest'
import {
  FRAME_BRIDGE_SCRIPT,
  MAX_FRAME_HEIGHT,
  buildCardDocument,
  createFrameMessageHandler,
  isBareMediaName,
  isLocalMediaName,
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

describe('bridge script', () => {
  it('is valid JavaScript after the nonce is filled in', () => {
    // String.raw 模板中誤寫的 ${ 會被插值成別的內容，tsc 與 eslint 都看不到
    expect(() => new Function(FRAME_BRIDGE_SCRIPT.replace('__NONCE__', 'abc123'))).not.toThrow()
  })

  it('builds the frame document with the bridge before the card', () => {
    const doc = buildCardDocument('<div id="face">x</div>', 1, 'n0nce')

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
