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
  ...graphViewsIsolation(),
)

/**
 * 圖檢視模組（`src/features/graph-views/`，含劇情流程圖）不依賴網站，可以整包搬走（劇情流程圖 D7）。
 * The graph-views module must not depend on the site, so it can be moved as a whole.
 *
 * 擋下的 import：
 * - `react-router-dom`（互動以 callback 回報）；
 * - 任何指向模組外的 `@/` 別名：`@/api`、`@/pages`、`@/hooks`、`@/components`、`@/lib`、`@/types` 等全部
 *   （只允許 `@/features/graph-views/...`）；
 * - 任何指向模組外的 `src/...` 路徑；
 * - 任何爬出模組資料夾的相對路徑（例 `../../../../../api/story`）。
 *
 * `no-restricted-imports` 比對的是 import 字串本身、不會解析路徑（專案沒有 eslint-plugin-import 的
 * `import/no-restricted-paths`），所以相對路徑依檔案深度產生規則：位在模組下第 d 層資料夾的檔案，
 * 連續 d+1 個 `../` 就出了模組。flat config 同一條規則後面的設定會整個蓋掉前面的，所以每個深度的
 * 設定都帶完整的清單。
 * Blocked: react-router-dom, every `@/` alias and `src/` path outside the
 * module, and every relative import that climbs out of it. The rule matches
 * import strings (no resolution), so one config per directory depth is
 * generated; each carries the full option set because flat config replaces
 * rule options rather than merging them.
 */
function graphViewsIsolation(maxDepth = 8) {
  const message = '圖檢視模組不得依賴網站（API、頁面、hooks、元件、lib、types、路由）；資料由 props 傳入、互動以 callback 回報。'
  const outsideAlias = { regex: '^@/(?!features/graph-views(/|$))', message }
  const outsideSrc = { regex: '(^|/)src/(?!features/graph-views(/|$))', message }
  return Array.from({ length: maxDepth + 1 }, (_, depth) => ({
    files: [`src/features/graph-views/${'*/'.repeat(depth)}*.{ts,tsx}`],
    rules: {
      'no-restricted-imports': ['error', {
        paths: [{ name: 'react-router-dom', message }],
        patterns: [outsideAlias, outsideSrc, { regex: `^(\\.\\./){${depth + 1},}`, message }],
      }],
    },
  }))
}
