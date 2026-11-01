import { describe, expect, it } from 'vitest'
import { DEFAULT_THEME, loadTheme, mergeTheme, resolveAssetUrl, themeToCssVars, type GraphThemeFile } from './theme'

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
