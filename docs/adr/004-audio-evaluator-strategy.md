# ADR 004: 採用策略模式實作 Audio Evaluator 與引入 google-genai SDK

## Context (背景與上下文)

在 Phase 8 開發 `Speaking_Coach_Dark` 卡片類型的專屬 Telegram 互動工作流（Workflow B：語音評分）時，系統需要具備接收使用者傳送的語音檔案，並將其交由 LLM 進行分析與評分的能力。

先前的實作主要依賴 `AsyncOpenAI` SDK（相容層）來呼叫 LLM 服務。然而，對於多模態（尤其是語音）的處理：
1. OpenAI ChatCompletions API 在音訊輸入的支援與格式要求上，經常有變動或對非官方模型（如 Gemini）相容性不佳。
2. Google 最新的 Gemini 1.5/2.0 模型對於語音辨識與評分有極佳的表現，但其原生 SDK (`google-genai`) 支援更直接且可靠的 `inline_data` 上傳方式。
3. 為了避免未來被單一供應商（Vendor Lock-in）綁死，我們需要一個靈活的架構，能夠在不同供應商（例如 OpenAI 的 `gpt-4o-audio` 與 Google 的 `gemini-2.5-flash`）之間無縫切換。

## Decision (決策)

1. **採用策略模式 (Strategy Pattern)**：
   我們在 `app/infrastructure/audio_evaluator/` 中定義了 `BaseAudioEvaluator` 抽象基底類別。並實作了兩種策略：
   - `OpenAIAudioEvaluator`：使用 `AsyncOpenAI`，將音檔轉為 Base64 透過相容 API 傳送。
   - `GeminiNativeAudioEvaluator`：使用 Google 最新官方 SDK `google-genai`，直接傳送原生的多模態 `inline_data`。

2. **採用工廠模式 (Factory Pattern) 與延遲匯入**：
   透過 `factory.py` 中的 `create_audio_evaluator()`，根據環境變數 `AUDIO_EVALUATOR_PROVIDER` 動態實例化對應的策略。在工廠內部使用延遲匯入 (Lazy Import)，確保若只使用 OpenAI 時，系統不需要安裝或載入 `google-genai`。

3. **新增外部依賴 `google-genai`**：
   在 `backend/requirements.txt` 中正式引入 `google-genai>=0.2.0` 作為官方依賴，以支援原生 Gemini 語音處理能力。

## Consequences (結果與影響)

### 正面影響 (Pros)
- **依賴反轉**：業務邏輯層（Telegram Handlers）完全不需要依賴特定的 LLM SDK，只需呼叫 `BaseAudioEvaluator.evaluate_audio()` 即可，符合 Clean Architecture。
- **靈活性高**：未來若有新的模型（如 Claude 3.5 Sonnet Audio）推出，只需新增一個實作類別，完全不需修改業務邏輯。
- **穩定性**：透過原生的 `google-genai` 處理音檔，減少了相容層轉換可能帶來的非預期錯誤。

### 負面影響 (Cons)
- **依賴增加**：專案增加了一個外部相依套件 (`google-genai`)。
- **環境變數複雜化**：增加了 `AUDIO_EVALUATOR_PROVIDER` 與 `GEMINI_NATIVE_API_KEY` 等設定，部署時需額外配置。
