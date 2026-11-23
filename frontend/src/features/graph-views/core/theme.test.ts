import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_THEME,
  loadTheme,
  mergeTheme,
  resolveAssetUrl,
  themeToCssVars,
  type GraphThemeFile,
  type ThemeFetcher,
} from './theme'

const files: Record<string, GraphThemeFile> = {
  '/story-themes/night/theme.json': {
    id: 'night', extends: 'base', colors: { canvas: '#000' }, size: { nodeW: 300 },
    assets: { background: 'bg.jpg', nodeMasks: { choice: 'masks/choice.svg' } }, css: 'theme.css',
  },
  '/story-themes/base/theme.json': {
    id: 'base', colors: { edge: '#123456', canvas: '#fff' }, assets: { chapterIcon: 'https://cdn.example/icon.png' },
  },
  '/story-themes/loop-a/theme.json': { id: 'loop-a', extends: 'loop-b' },
  '/story-themes/loop-b/theme.json': { id: 'loop-b', extends: 'loop-a' },
}
const fetcher = async (url: string) => {
  if (!files[url]) throw new Error(`404 ${url}`)
  return files[url]
}

describe('theme', () => {
  it('繼承：子主題只覆寫差異，其餘來自父主題與預設', async () => {
    const theme = await loadTheme('night', '/story-themes', fetcher)
    expect(theme.colors.canvas).toBe('#000')
    expect(theme.colors.edge).toBe('#123456')
    expect(theme.colors['node-text']).toBe(DEFAULT_THEME.colors['node-text'])
    expect(theme.size.nodeW).toBe(300)
    expect(theme.size.rowGap).toBe(DEFAULT_THEME.size.rowGap)
  })

  it('素材網址相對於各自的主題資料夾，絕對網址不變', async () => {
    const theme = await loadTheme('night', '/story-themes', fetcher)
    expect(theme.assets.background).toBe('/story-themes/night/bg.jpg')
    expect(theme.assets.nodeMasks?.choice).toBe('/story-themes/night/masks/choice.svg')
    expect(theme.assets.chapterIcon).toBe('https://cdn.example/icon.png')
    expect(theme.css).toBe('/story-themes/night/theme.css')
    expect(resolveAssetUrl('./a.png', '/t/')).toBe('/t/a.png')
    expect(resolveAssetUrl('data:image/png;base64,xx', '/t/')).toBe('data:image/png;base64,xx')
  })

  it('預設主題不需要讀檔；繼承迴圈會報錯', async () => {
    expect(await loadTheme('default', '/x', fetcher)).toBe(DEFAULT_THEME)
    await expect(loadTheme('loop-a', '/story-themes', fetcher)).rejects.toThrow('迴圈')
  })

  it('可以直接用網址載入主題 manifest', async () => {
    const remote = { 'https://themes.example/x/theme.json': { id: 'x', assets: { arrow: 'arrow.svg' } } as GraphThemeFile }
    const theme = await loadTheme('https://themes.example/x/theme.json', '/story-themes', async (u) => remote[u as keyof typeof remote])
    expect(theme.assets.arrow).toBe('https://themes.example/x/arrow.svg')
  })

  it('CSS 變數輸出；缺的素材不輸出（由 CSS 退回）', () => {
    const vars = themeToCssVars(mergeTheme(DEFAULT_THEME, { id: 't', assets: { background: '/bg.jpg', nodeMasks: { scene: '/m.svg' } } }))
    expect(vars['--sf-color-canvas']).toBe(DEFAULT_THEME.colors.canvas)
    expect(vars['--sf-size-nodeW']).toBe(`${DEFAULT_THEME.size.nodeW}px`)
    expect(vars['--sf-font-node']).toBe(DEFAULT_THEME.fonts.node)
    expect(vars['--sf-asset-background']).toBe('url("/bg.jpg")')
    expect(vars['--sf-asset-mask-scene']).toBe('url("/m.svg")')
    expect(vars['--sf-asset-node-texture']).toBeUndefined()
    expect(Object.keys(themeToCssVars(DEFAULT_THEME)).some((k) => k.startsWith('--sf-asset-'))).toBe(false)
  })
})

describe('內建主題（public/story-themes）', () => {
  // 讀真正的主題檔：知識圖譜與劇情流程圖共用這份清單（graph_themes 計畫書 D1–D3）
  const root = resolve(__dirname, '../../../../public/story-themes')
  const builtIn: ThemeFetcher = async (url) => JSON.parse(readFileSync(resolve(root, url.replace(/^\/story-themes\//, '')), 'utf8'))
  const ids = (JSON.parse(readFileSync(resolve(root, 'index.json'), 'utf8')) as { themes: { id: string }[] }).themes.map(
    (theme) => theme.id,
  )

  it('lists the two new themes next to the existing ones', () => {
    expect(ids).toEqual(['default', 'classic-white', 'slate-gray', 'sabbat-night', 'senren-hoori'])
  })

  it.each(ids)('%s has every colour key, including the learning-status colours', async (id) => {
    const theme = await loadTheme(id, '/story-themes', builtIn)
    for (const key of Object.keys(DEFAULT_THEME.colors)) expect(theme.colors[key], key).toBeTruthy()
  })

  it.each(['classic-white', 'slate-gray'])('%s extends the default and only overrides known keys', async (id) => {
    const file = await builtIn(`/story-themes/${id}/theme.json`)
    expect(file.extends).toBe('default')
    for (const key of Object.keys(file.colors ?? {})) expect(Object.keys(DEFAULT_THEME.colors), key).toContain(key)
    // 尺寸與字型全部沿用預設
    expect(file.size).toBeUndefined()
    expect(file.fonts).toBeUndefined()
  })

  it('existing themes get the learning-status colours from the default without editing their files', async () => {
    for (const id of ['sabbat-night', 'senren-hoori']) {
      const theme = await loadTheme(id, '/story-themes', builtIn)
      expect(theme.colors['status-new']).toBe(DEFAULT_THEME.colors['status-new'])
    }
  })
})
