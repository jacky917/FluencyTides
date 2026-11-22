# FEAT — 知識圖譜改用共用的圖主題（新增經典白、石板灰），單字卡樣式退役

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-22 |
| **性質** | 外觀系統整併 + 實作工作項 |
| **狀態** | 🚧 實作中 |
| **範圍** | `graph-views/core/theme.ts` 的平台預設補上學習狀態顏色（原有的值不變）；新增主題「經典白」「石板灰」（都繼承預設）；知識圖譜（單字卡力導向圖、三語卡主題樹）改讀圖主題，設定選單的「Label Style」換成主題選單；主題樹搬進 `graph-views/engines/topic-tree/`；刪掉其餘四種標籤樣式 |
| **不動** | 平台預設主題的外觀（`default`，原有的顏色、尺寸、字型都不變）；`sabbat-night`、`senren-hoori` 兩個主題檔（`theme.json`、`theme.css`、素材）；劇情流程圖的元件；字級與「多大的縮放才顯示文字」兩個設定（屬於個人偏好，不屬於主題）；知識圖譜整頁搬成 `GraphView`＋`engines/force`（關係圖草案 D5，之後另做） |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `story_flowchart_FEAT_2026-10-31.md` D15（主題系統）、`relation_graph_FEAT_draft_2026-11-01.md` D5（排版引擎）、`trilingual_topic_tree_FEAT_2026-11-22.md`（主題樹） |

---

## 1. 問題與動機

- 外觀有兩套互不相通的設定：劇情流程圖用共用的**圖主題**（`graph-views/core/theme.ts`，`public/story-themes/<id>/theme.json` 轉成 CSS 變數）；知識圖譜的單字卡用頁面自己的 `FONT_STYLES`（6 種標籤文字顏色）。
- 三語卡的主題樹（`components/graph/TopicTreeGraph.tsx`）配色寫死，兩套都沒用，所以設定選單的樣式切換對它無效（2026-11-22 調查）。位置也不在 `graph-views` 底下。
- 使用者決定（2026-11-22）：把單字卡樣式升級成圖主題，只留「經典白」和「石板灰」，其餘四種刪掉；**原本的預設主題維持不變**，新增的主題盡量繼承預設、只覆寫需要的部分；知識圖譜也要支援現有的魔女之夜、穗織之櫻；學習狀態顏色隨主題變、有預設值。

## 2. 目標與非目標

**目標**
- G1 平台預設主題（灰階）維持原樣，知識圖譜沒選主題時也用它。
- G2 新增主題「經典白」「石板灰」，都 `extends: default`，只覆寫深色底需要改的顏色；主題清單是：預設（灰階）、經典白、石板灰、魔女之夜、穗織之櫻，知識圖譜與劇情流程圖共用這份清單。
- G3 知識圖譜（單字卡、三語卡）的設定選單改成選主題，畫布、節點、文字、連線、徽章、學習狀態都依主題著色；選擇存在瀏覽器。
- G4 預設主題的外觀不變；魔女之夜、穗織之櫻的主題檔一個字都不改，在知識圖譜上也能用。

**非目標**
- 知識圖譜整頁改成 `GraphView`＋`engines/force`（D5）：這次只讓它讀主題。
- `theme.css` 套到知識圖譜：它寫的是劇情流程圖 DOM 的選擇器（`.sf-*`），對 canvas 畫的圖無效；知識圖譜只用主題的顏色、字型與背景圖。

## 3. 設計決策

### D1 預設主題不動，新主題繼承它（使用者 2026-11-22 決定）
- 先前考慮過「讓經典白直接取代 `default`」，使用者否決：原本的預設（灰階）維持原樣。
- 經典白、石板灰都寫成 `extends: default`，只覆寫深色底必須改的顏色（畫布、節點、文字、框線、章節帶、分頁、小地圖、捲動按鈕等）；強調色（選取路徑 `edge-path`、命中 `node-highlight`、徽章、結局、學習狀態）沿用預設。尺寸與字型全部沿用預設。
- 唯一動到預設的地方是 D2 新增的 `status-*` 鍵：只是新增，既有的鍵與值都不變，所以預設主題與劇情流程圖的外觀不變。

### D2 主題補上知識圖譜要用的鍵，現有主題靠繼承取得
- 新增鍵（放在 `DEFAULT_THEME`，所以現有主題繼承後自動有值）：`status-new`、`status-learning`、`status-review`、`status-suspended`、`status-ghost`（單字卡節點的學習狀態），預設沿用現在的藍、橘、綠、黃、灰。
- 其餘直接用現有的鍵：

| 用途 | 主題的鍵 |
|---|---|
| 畫布底色 | `canvas`（另有背景圖時疊上 `assets.background`） |
| 單字卡標籤文字／描邊 | `canvas-text`／`canvas` |
| 單字卡連線 | `edge` |
| 主題樹卡片框、文字、邊框 | `node-bg`、`node-text`、`node-border`（根節點用 `node-border-strong`） |
| 主題樹牌組框（虛線） | `node-dim-bg`、`node-dim-text`、`node-border` |
| 子卡數徽章 | `badge-bg`、`badge-text` |
| 標題重複的提示框 | `node-highlight` |
| 字型 | `fonts.node` |

### D3 「經典白」與「石板灰」的配色
- 由原本的標籤樣式延伸成深色主題，在 `/story/_playground` 實際看過劇情流程圖。
  - 經典白（`public/story-themes/classic-white/theme.json`）：炭灰底 `#2b2b30`，白字，黑色描邊，灰框。
  - 石板灰（`public/story-themes/slate-gray/theme.json`）：藍灰底 `#1e293b`，近白字，石板色框。

### D4 知識圖譜的主題選擇
- 新的 hook 讀取主題（沿用 `loadTheme` 與主題清單），選擇存在 `localStorage` 的 `kg_theme`；沒選過就用 `default`。
- 舊的 `kg_fontStyleId` 換算一次，讓看慣的樣子不變：`classic`→`classic-white`、`slate`→`slate-gray`；被刪掉的四種（深色文字配深色描邊）→`classic-white`。換算後刪掉舊鍵。
- 設定選單：「Label Style」換成主題清單；字級、「Label Appears At (Zoom)」保留。

### D5 主題樹搬進 `graph-views/engines/topic-tree/`
- 照劇情流程圖的規矩：模組只有一個公開入口，不 import 網站的 API、頁面、hooks；版面計算（現在的 `lib/topicTreeLayout.ts`）一起搬進引擎的 `core/`。
- 畫法維持 canvas（D2 的顏色都能直接用；`theme.css` 本來就套不上）；字級與縮放門檻一併接上。

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `frontend/src/features/graph-views/core/theme.ts` | `DEFAULT_THEME` 加 `status-*`（既有的值不變） |
| `frontend/public/story-themes/index.json` | 加 `classic-white`、`slate-gray` |
| `frontend/public/story-themes/classic-white/theme.json`、`slate-gray/theme.json`（新） | 經典白、石板灰（`extends: default`） |
| `frontend/src/features/graph-views/engines/topic-tree/`（新） | 主題樹引擎（由 `components/graph/` 與 `lib/topicTreeLayout.ts` 搬過來，改讀主題） |
| `frontend/src/hooks/useGraphTheme.ts`（新） | 知識圖譜的主題選擇與舊設定換算 |
| `frontend/src/pages/KnowledgeGraph.tsx` | 刪 `FONT_STYLES`；力導向圖改讀主題；設定選單改成主題清單 |
| 測試 | `theme.test.ts`（`status-*` 由現有主題繼承）、主題樹版面、舊設定換算 |

## 5. 驗收標準

- [ ] 劇情流程圖：預設、魔女之夜、穗織之櫻的外觀和改之前一樣；經典白、石板灰可以選、看得清楚（`/story/_playground`）
- [ ] 知識圖譜：五個主題都能切換，單字卡與三語卡都跟著變色；字級、縮放門檻照常
- [ ] 舊的樣式設定換算正確（Classic→經典白、Slate→石板灰、其他→經典白）
- [ ] `default`、`sabbat-night`、`senren-hoori` 的主題檔沒有任何改動（`git diff` 為空）
- [ ] 全部測試通過
