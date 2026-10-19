import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

// Vite React + TypeScript 範本的標準設定。
// Standard config from the Vite React + TypeScript template.
export default tseslint.config(
  // vite.config.js / .d.ts 是 `tsc -b` 產生的檔案
  // vite.config.js / .d.ts are emitted by `tsc -b`.
  { ignores: ['dist', 'vite.config.js', 'vite.config.d.ts'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // 既有的知識圖譜與卡片詳情仍有 any（force-graph 節點、axios 錯誤），先列為警告，
      // 新程式不應再增加。
      // Existing graph/card-detail code still uses `any`; warn until it is typed.
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },
)
