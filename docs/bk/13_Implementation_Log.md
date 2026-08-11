# 13. 實作紀錄（多語言口說教練支援）

產生日期：2026-07-13

本文檔記錄新增「多語言口說教練支援 (Multi-Language Target Support)」在 `Speaking_Coach_Dark` 卡片與 Telegram Bot 上的實作，該改動目的在於提升語音評分的準確度，確保 LLM 在評估日文、英文或其他語言時，能根據「目標語言」為基準進行糾錯。

## 1. 成果總覽

| 模組 | 主要改動 |
|------|------|
| **Schema 定義** | 新增 `TargetLanguage` 列舉 (Enum)，支援 `en-US`, `ja-JP`, `zh-TW`, `other`。 |
| **Anki 筆記模型** | `Speaking_Coach_Dark` 結構新增 `Target_Language` 欄位（索引 5），並調整 HTML 模板增加徽章顯示。 |
| **自動化更新腳本** | 新增 `update_templates.py` 專屬腳本，用於對已存在於本地端 Anki 的模型動態注入新欄位與 CSS/HTML 樣式，避免手動操作導致的結構破壞。 |
| **Telegram FSM** | 於 `speaking_fsm.py` 狀態機新增 `waiting_for_language` 狀態。導入 InlineKeyboard 按鈕提供快速選取語言選項。 |
| **Audio Evaluator** | 重構 `BaseAudioEvaluator` 及各大客戶端 (`OpenAIAudioEvaluator`, `GeminiAudioEvaluator`, `ProxyAudioEvaluator`) 簽名，使其接受 `target_language`。 |
| **提示詞 (Jinja2)** | `prompts/audio_evaluator.j2` 新增目標語言上下文，強化 LLM 的多語種語音辨識與文法糾錯能力。 |

## 2. 實作細節與設計決策 (ADR 關聯)

### 2.1 型別與欄位擴充 (Schema & Anki Model)
- **零 any 原則**：不採用自由字串，而在 `backend/app/schemas/language.py` 定義了 `TargetLanguage(str, Enum)`。這確保前後端與 TG 端的防呆一致性。
- **Anki 欄位新增**：原本 `Speaking_Coach_Dark.json` 沒有語言標識，因此所有對話預設依賴 LLM 的自動檢測。新增 `Target_Language` 於 `References` 後方，UI 會將此資訊顯示為帶有國旗 Emoji 的前端 Badge (`.lang-badge`)。

### 2.2 Telegram FSM 動態引導
為了兼顧流暢度與可擴充性，當使用者觸發 `/newcard` 建立 `Speaking_Coach_Dark` 牌組時，狀態機會跳轉到 `waiting_for_language` 狀態，呈現 InlineKeyboard 包含 `en-US`、`ja-JP`、`zh-TW` 等按鈕，使用者亦可自行輸入未在列表中的特定語言。這些資料將被注入 `Target_Language` 寫回 Anki 卡片。

### 2.3 Audio Evaluator 提示詞注入
將 `target_language` 變數加入 `evaluate_audio` 函數簽名，傳遞給 Jinja2 模板 `audio_evaluator.j2` 進行渲染：
```jinja2
{% if target_language %}
## 目標語言 (Target Language)
本次練習的目標語言為：{{ target_language }}。請務必使用此語言來辨識使用者的發音與語句，並依此標準給予文法與發音上的評分回饋。
{% endif %}
```
這項變更確保就算中轉 API 丟失了部份多模態上下文，LLM 仍能根據系統提示，將音頻辨識為指定語言（例如避免把日文的「あつい」辨識成其他語系的雜音）。

## 3. 部署與後續待辦
- 本次改動需要與本地 Anki 進行全量強制同步，已編寫 `backend/scripts/local_anki/Speaking_Coach_Dark/update_templates.py` 輔助開發者一鍵更新本地資料庫的 Note Schema 與 Templates。
- 在後續迭代中，可以考慮讓 Frontend 知識圖譜編輯器也原生支援 Enum 選單，以對齊 Telegram 體驗。
