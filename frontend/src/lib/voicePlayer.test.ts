import { describe, expect, it } from 'vitest'
import { VoicePlayer, describeVoiceFailure, type VoiceAudio } from './voicePlayer'

/** 可以手動觸發事件、決定 play() 結果的假音訊元素。A controllable fake audio element. */
class FakeAudio implements VoiceAudio {
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = false
  resolve!: () => void
  reject!: (e: unknown) => void
  readonly url: string
  constructor(url: string) {
    this.url = url
  }
  play(): Promise<void> {
    return new Promise<void>((res, rej) => {
      this.resolve = res
      this.reject = rej
    })
  }
  pause(): void {
    this.paused = true
  }
}

function setup() {
  const audios: FakeAudio[] = []
  const changes: (number | null)[] = []
  const errors: { key: number; url: string; cause: unknown }[] = []
  const player = new VoicePlayer<number>({
    createAudio: (url) => {
      const a = new FakeAudio(url)
      audios.push(a)
      return a
    },
    onChange: (key) => changes.push(key),
    onError: (key, url, cause) => errors.push({ key, url, cause }),
  })
  return { player, audios, changes, errors }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('VoicePlayer', () => {
  it('按一下播放、再按同一句停止', () => {
    const { player, audios, changes } = setup()
    player.toggle(1, '/v/a')
    expect(player.playing).toBe(1)
    expect(audios[0].url).toBe('/v/a')
    player.toggle(1, '/v/a')
    expect(player.playing).toBeNull()
    expect(audios[0].paused).toBe(true)
    expect(changes).toEqual([1, null])
  })

  it('改播別句會先停掉目前這句', () => {
    const { player, audios, changes } = setup()
    player.toggle(1, '/v/a')
    player.toggle(2, '/v/b')
    expect(player.playing).toBe(2)
    expect(audios[0].paused).toBe(true)
    expect(audios[0].onended).toBeNull()
    expect(changes).toEqual([1, 2])
  })

  it('播完自動回到停止', () => {
    const { player, audios, changes } = setup()
    player.toggle(1, '/v/a')
    audios[0].onended?.()
    expect(player.playing).toBeNull()
    expect(changes).toEqual([1, null])
  })

  it('stop() 在沒播放時不通知', () => {
    const { player, changes } = setup()
    player.stop()
    expect(changes).toEqual([])
  })

  it('失敗只回報一次並重設（error 事件與 play() 拒絕同時發生）', async () => {
    const { player, audios, changes, errors } = setup()
    player.toggle(1, '/v/a')
    audios[0].onerror?.()
    audios[0].reject(new Error('NotSupportedError'))
    await flush()
    expect(player.playing).toBeNull()
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatchObject({ key: 1, url: '/v/a' })
    expect(changes).toEqual([1, null])
  })

  it('停止後才抵達的 AbortError 不影響下一句', async () => {
    const { player, audios, errors } = setup()
    player.toggle(1, '/v/a')
    player.toggle(2, '/v/b')
    audios[0].reject(new DOMException('aborted', 'AbortError'))
    await flush()
    expect(errors).toEqual([])
    expect(player.playing).toBe(2)
  })

  it('play() 同步丟例外也算失敗', () => {
    const errors: unknown[] = []
    const player = new VoicePlayer<number>({
      createAudio: () => ({ onended: null, onerror: null, pause() {}, play() { throw new Error('boom') } }),
      onChange: () => {},
      onError: (_k, _u, cause) => errors.push(cause),
    })
    player.toggle(1, '/v/a')
    expect(player.playing).toBeNull()
    expect(errors).toHaveLength(1)
  })
})

describe('describeVoiceFailure', () => {
  const describe_ = (e: unknown) => `API:${(e as { message: string }).message}`

  it('重新請求失敗時用後端的錯誤訊息', async () => {
    const msg = await describeVoiceFailure(new Error('x'), () => Promise.reject({ message: '語音 a 不存在' }), describe_)
    expect(msg).toBe('API:語音 a 不存在')
  })

  it('檔案在但播不了', async () => {
    expect(await describeVoiceFailure(new Error('x'), () => Promise.resolve(), describe_)).toBe('瀏覽器無法播放這個音訊檔')
  })

  it('被瀏覽器擋下時不重新請求', async () => {
    let probed = false
    const msg = await describeVoiceFailure(new DOMException('no', 'NotAllowedError'), async () => { probed = true }, describe_)
    expect(msg).toContain('擋下')
    expect(probed).toBe(false)
  })
})
