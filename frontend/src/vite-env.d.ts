/// <reference types="vite/client" />

/**
 * Vite 環境變數類型聲明。
 *
 * 透過此介面，TypeScript 能正確識別 `import.meta.env.VITE_*` 環境變數，
 * 避免 TS2339 ("Property 'env' does not exist on type 'ImportMeta'") 錯誤。
 */
interface ImportMetaEnv {
  /** 前端預設使用的 Anki 牌組名稱 */
  readonly VITE_DEFAULT_DECK: string
  /** 前端預設使用的 Anki 模型檔案名稱 */
  readonly VITE_DEFAULT_MODEL_FILE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
