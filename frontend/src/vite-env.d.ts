/// <reference types="vite/client" />

/**
 * Vite 環境變數類型聲明。
 *
 * 透過此介面，TypeScript 能正確識別 `import.meta.env.VITE_*` 環境變數，
 * 避免 TS2339 ("Property 'env' does not exist on type 'ImportMeta'") 錯誤。
 */
interface ImportMetaEnv {
  /** 知識圖譜預設選中的 Anki 牌組名稱 */
  readonly VITE_DEFAULT_DECK: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
