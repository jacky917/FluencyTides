# Expression Correction 重構計畫 (翻譯題型子卡片 & JSON 解析母卡片)

本計畫旨在根據您的要求，對 `Expression_Correction` 進行以下結構性修改：
1. **子卡片 (Micro Cards) 改為「母語翻譯外語」題型**：不再使用克漏字。正面顯示「母語意思」與「母語情境提示」，背面顯示「正確外語」與「完整外語句子」。
2. **母卡片 (Master Cards) 詳細解析 JSON 化**：將 `Detailed_Explanation` 轉換為結構化的 JSON 陣列，並在卡片背面使用 JavaScript 渲染為精美的**可點擊展開/收合的手風琴 (Accordion)** 樣式，對齊 `Speaking_Coach_Dark` 的設計標準。

## User Review Required

> [!WARNING]
> **欄位變更會影響已建立的 Anki 卡片與模型**
> 本次修改將會更動 `Expression_Micro_Dark` 的欄位（將 `Context_Sentence_Cloze` 替換為 `Context_Sentence`，並新增 `Context_Hint`）。如果您的 Anki 已經有了舊版的卡片，模型欄位名稱變更會導致腳本無法自動更新現有模型。
> **解決方案**：在實作完成後，您需要進入 Anki 桌面端，手動刪除舊的 `Expression_Micro_Dark` 與 `Expression_Master_Dark` 模型，然後讓腳本重新建立最新版的模型。

## Open Questions

> [!IMPORTANT]
> 1. **目前計畫的欄位更動是否符合您的期待？**
>    子卡片正面：`Native_Translation` (母語意思) + `Context_Hint` (LLM 產生的上下文提示)
>    子卡片背面：`Target_Phrase` (正確解答) + `Context_Sentence` (完整外文例句) + `Error_Hint` (錯誤提示)
> 2. **母卡片的手風琴 (Accordion) 預設狀態**：
>    目前計畫預設為「展開」狀態（以防使用者忽略），但可以點擊收合。如果您希望預設為「收合」狀態以保持版面最精簡，請告訴我。

## Proposed Changes

---

### 1. Schemas (LLM 結構定義)
**檔案**: `app/schemas/llm/expression.py`
- 新增 `LLMExplanationPoint` Model：包含 `point` (標題) 與 `explanation` (解說內容)。
- `LLMExpressionCorrectionResult`: 將 `detailed_explanation` 改為 `list[LLMExplanationPoint]`。
- `LLMMicroPoint`:
  - 移除 `context_sentence_cloze`，改為 `context_sentence` (完整目標語言句子)。
  - **新增 `context_hint` (str)**：指示 LLM 產生簡短的母語情境提示。

### 2. Prompts (提示詞)
**檔案**: `app/templates/prompts/anki/expression_correction.j2`
- 更新詳細解析 (Detailed Explanation) 的指示，要求輸出包含 `point` 與 `explanation` 的 JSON 陣列。
- 更新子卡片 (Micro Points) 的指示，要求提供完整的目標語言例句，並額外生成 `context_hint` (母語情境提示)。

### 3. Anki Models (卡片模板)
**檔案**: `app/anki_models/Expression_Micro_Dark.json` 及對應的 HTML
- **欄位變更**：`Context_Sentence_Cloze` -> `Context_Sentence`，並新增 `Context_Hint` 欄位。
- **正面 HTML**：移除原本的克漏字區塊，改為顯示 `{{Native_Translation}}` 與 `{{Context_Hint}}`。
- **背面 HTML**：移除原本的 JS 克漏字還原邏輯，直接顯示 `{{Context_Sentence}}`。

**檔案**: `app/anki_models/Expression_Master_Dark_back.html`
- **背面 HTML**：隱藏原本直接輸出的 `{{Detailed_Explanation}}`，加上對應的 `<div id="explanation-data" style="display:none;">{{Detailed_Explanation}}</div>`。
- **加入 JS 渲染**：讀取 JSON 資料，動態生成與 `Speaking_Coach_Dark` 相同的 Accordion DOM 結構。

### 4. Handlers (業務邏輯)
**檔案**: `app/services/task_handlers/expression_handler.py`
- 在組裝母卡片 `master_fields` 時，使用 `json.dumps(..., ensure_ascii=False)` 序列化 `correction_result.detailed_explanation`。
- 更新子卡片組裝時的對應欄位名稱 (`Context_Sentence`, `Context_Hint`)。

## Verification Plan

1. (使用者操作) 在 Anki 刪除舊模型。
2. 執行 `generate_expression_cards.py` 測試生成流程。
3. 檢查母卡片背面的 JSON 手風琴 UI 是否正常渲染。
4. 檢查子卡片正反面，確認克漏字已被翻譯題取代，並帶有情境提示。
