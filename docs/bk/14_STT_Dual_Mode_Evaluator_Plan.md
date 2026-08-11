# 14. STT 雙模式語音評分整合計畫（STT+Diff / STT+LLM）

> 狀態：**已實作**（2026-08-09，§7 步驟 0–6 全部完成；自動化測試 18/18 通過，
> 手動驗證〔§5〕與部署清單〔§6〕待執行）
> 實作備註：樣板分支因 TemplateEngine 使用 StrictUndefined，實際寫法為
> `{% if user_transcript is defined and user_transcript %}`（計畫原假設 falsy 即可）。
> 測試基線採 §2.9 選項 (b)：新建 backend/tests/ 起步。
> 日期：2026-08-09
> 前置：自架 Speaches (faster-whisper) 服務已於區網 `192.168.50.171:8000` 驗證可用（OpenAI 相容 `/v1/audio/transcriptions` 端點，測試腳本見開發機 `WorkSpace/Python/AI/main.py`）

---

## 1. 背景與目標

現行語音評分（Workflow B，[voice.py](../backend/app/bot/handlers/voice.py)）將整段 `.ogg` 音檔以多模態方式送交 LLM（`gemini_native` / `openai` / `proxy` 三個 provider），每次評分都要付出音訊上傳與多模態推論的成本。本計畫新增兩個**獨立的評分模式**，作為 `AUDIO_EVALUATOR_PROVIDER` 的新選項，與現有 provider 並列、可透過 Telegram `/setconfig`（`MODIFY_` 機制）動態切換：

| 模式 | Provider 值 | 原理 | 成本 | 適用場景 |
|------|-------------|------|------|----------|
| STT + Diff | `stt_diff` | 本地 Whisper 轉文字 → `difflib` 與參考答案逐字比對 | **零 API 費用** | Shadowing／逐字覆誦類卡片（有明確標準答案） |
| STT + LLM | `stt_llm` | 本地 Whisper 轉文字 → 逐字稿以**純文字**送輕量 LLM 評分 | 低（無音訊 payload、可用便宜模型） | 自由應答類卡片（需要語意評價但不需要 LLM 聽音檔） |

兩模式共用同一個新的 `WhisperClient` 基礎設施。

### 明確的取捨（納入設計前提）

- STT 模式**失去發音／語調評估能力**：Whisper 只輸出文字，「文法與發音（30%）」「語氣與語調（15%）」維度（見 [audio_evaluator.j2](../backend/app/templates/prompts/audio_evaluator.j2) 權重）在 STT 模式下無法基於聲學特徵評估。`stt_llm` 樣板分支需明確告知 LLM 只評內容與文法；`stt_diff` 則只反映文字相似度。這是「省錢換精度」的預期行為，不是缺陷。
- Whisper 有自動糾錯傾向（會把輕微口誤「聽成」正確的詞），`stt_diff` 分數可能偏寬鬆。可接受。

---

## 2. 可行性調查結論（逐項對照現有代碼）

**總結論：可行。介面與樣板層零阻力；但有一個必須修的架構缺口（§2.3 動態切換）、兩個必須處理的資料流細節（§2.4 語言映射、§2.5 feedback 雙格式）。**

### 2.1 ✅ Evaluator 介面完全相容

[base.py](../backend/app/infrastructure/audio_evaluator/base.py) 的 `evaluate_audio()` 簽名已包含新模式需要的一切：

```python
async def evaluate_audio(
    self,
    audio_data: bytes,            # STT 模式：送 Whisper 而非 LLM
    audio_filename: str,          # 副檔名判斷格式（.ogg）
    prompt_text: str,
    context_text: str,
    reference_answers: list[str], # stt_diff 的比對基準
    target_language: str | None,  # 決定 Whisper language 參數
    template_name: str,           # stt_llm 沿用卡片專屬樣板
) -> AudioEvaluationResult: ...
```

回傳的 `AudioEvaluationResult`（[schemas/llm/speaking.py](../backend/app/schemas/llm/speaking.py)）為 `score: int (0-100) / feedback: str / transcript: str`。兩個新 evaluator 實作同一介面即可，[voice.py](../backend/app/bot/handlers/voice.py) 的呼叫端與寫回 Anki 的 `RecordingItem` 流程零改動（除 §2.5 的一處可選增強）。

### 2.2 ✅ 樣板策略：不新建，改現有樣板加分支

語音評分樣板現況：

| 樣板 | 路徑 | 使用時機 |
|------|------|----------|
| `audio_evaluator.j2` | `templates/prompts/` | `Speaking_Coach_Dark` 通用路徑 |
| `Speaking_Trilingual_{JA,ZH,EN}.j2` | `templates/prompts/anki/` | 三語卡（由 `voice.py:147` 依欄位後綴選擇） |

四個樣板的變數介面一致（`prompt_text / context_text / reference_answers / target_language / disable_markdown`）。若 `stt_llm` 強制改用單一新樣板，三語卡的專屬評分邏輯（如 JA 版的嚴格採點基準、feedback 全日文要求）會遺失。因此**在全部四個樣板各加一個 `{% if user_transcript %}` 分支**（詳見 §3.5），未傳入 `user_transcript` 時渲染結果與現在逐字元相同——對現有三個 provider 完全無感。

`TemplateEngine.render()`（[template_engine.py:78](../backend/app/core/template_engine.py)）接受任意 `**template_vars`，多傳 `user_transcript` 不需要改引擎。

### 2.3 ⚠️ 缺口一（必修）：`/setconfig` 切換 provider 不會生效

- `audio_evaluator` 是**啟動時**由工廠建立的 Singleton：[main.py:111](../backend/app/main.py) `app.state.audio_evaluator = create_audio_evaluator()`，之後由 [bot/dependencies.py:143](../backend/app/bot/dependencies.py) 每次請求從 `app.state` 注入。
- `/setconfig` 的套用邏輯只有 `setattr(settings, key, value)`（[callbacks_config.py:121](../backend/app/bot/handlers/callbacks_config.py)），**不會重建 evaluator 實例**。
- 現有代碼對此已有局部補救先例：[gemini_client.py:105](../backend/app/infrastructure/audio_evaluator/gemini_client.py) 在**每次呼叫時**動態讀 `settings.AUDIO_MODEL_NAME`（註解明言「以支援 /setconfig 的變更」）——即「模型名」可熱切換，但「provider 本身」不行。

**解法（雙管齊下）**：
1. **Provider 切換 → 重建實例**：在 `handle_setconfig_value_selection` 套用 `setattr` 後，若 `key == "AUDIO_EVALUATOR_PROVIDER"`，取得 FastAPI app 實例並執行 `app.state.audio_evaluator = create_audio_evaluator()`（失敗時保留舊實例並回報錯誤訊息給管理員）。aiogram handler 內可透過 dispatcher workflow data 或在 middleware 注入 `app` 取得（實作時擇一，建議沿用 [dependencies.py](../backend/app/bot/dependencies.py) 已持有 `app` 的既有管道注入）。
2. **模型名／URL 熱讀取**：兩個新 evaluator 內部一律在**呼叫時**讀取 `settings.STT_MODEL_NAME` / `settings.STT_LLM_MODEL_NAME`（比照 gemini_client 先例），使 `MODIFY_STT_LLM_MODEL_NAME` 等白名單無需重建即生效。

### 2.4 ⚠️ 缺口二（必修）：locale → Whisper 語言代碼映射

`voice.py` 取得的 `target_language` 是 BCP-47 locale（三語卡經 `LANG_TO_LOCALE` 映射為 `ja-JP` 等；`Speaking_Coach_Dark` 讀 `Target_Language` 欄位，值域 `en-US / ja-JP / zh-TW / other`，見 [13_Implementation_Log.md](13_Implementation_Log.md)）。Whisper API 的 `language` 參數要求 ISO 639-1（`ja` / `en` / `zh`）。

實測（測試腳本）已證明**不指定語言時 Whisper 自動偵測會誤判**：日文語音被判為韓文、信心度僅 0.29。因此：

- `WhisperClient` 負責映射：取 locale 的語言前綴（`ja-JP → ja`），`other` 或空值 → 不傳 `language`（回退自動偵測）。
- 映射規則：`locale.split("-")[0].lower()`，並對 `other` 特判為 `None`。

### 2.5 ⚠️ 缺口三（必修）：feedback 的 TG / Anki 雙格式問題

`result.feedback` 目前**同一字串走兩條路**：

1. TG 訊息直接內嵌：`voice.py:276` `f"...{result.feedback[:500]}..."`（訊息以 HTML parse mode 發送）。
2. 寫回 Anki：`voice.py:227` 放入 `recording["comment"]`，最終渲染於卡片背面（支援完整 HTML）。

`stt_diff` 的 feedback 是差異標記字串，但 **Telegram HTML 白名單不含 `<span>`**（僅 `<b>/<i>/<s>/<u>/<code>/<pre>/<a>` 等），夾帶 `<span style="color:red">` 會使 `edit_text` 直接拋 `TelegramAPIError`。而 Anki 端反而需要 `<span>` 紅字才有視覺效果。

**解法**：`AudioEvaluationResult` 新增**可選欄位** `feedback_anki_html: str | None = None`（預設 None，對現有 provider 與 JSON Schema 皆向後相容——三個現存 evaluator 的 schema 不含此欄位，Pydantic 以預設值補上）：

- `feedback`：一律為 **TG-safe** 標記（`<s>` 刪除、`<b>` 插入/修正），所有 provider 直接可顯示。
- `feedback_anki_html`：`stt_diff` 專用，含標準 `<span style="color:red">` 的完整 HTML 差異。
- `voice.py` 組裝 `new_recording` 時改為 `"comment": result.feedback_anki_html or result.feedback`（一行改動，其他 provider 行為不變）。

### 2.6 ✅ 音檔格式

Telegram 語音為 `.ogg` (Opus)。Speaches 底層 faster-whisper 經 ffmpeg/PyAV 解碼，直接接受 ogg 上傳，無需轉檔（測試腳本以 mp3 驗證過同一端點）。`WhisperClient` 上傳時以 `(audio_filename, audio_data)` tuple 傳給 `client.audio.transcriptions.create(file=...)` 即可，不落地暫存檔。

### 2.7 ✅ 錯誤處理鏈相容

`voice.py:201` 捕獲 `FluencyTidesError`（[core/exceptions/base.py](../backend/app/core/exceptions/base.py)）統一顯示錯誤。新增 `STTServiceError(InfrastructureBaseError)` 於 [core/exceptions/infrastructure.py](../backend/app/core/exceptions/infrastructure.py)（與 `LLMServiceError` 同層），STT 服務連不上／超時即拋此錯——自動落入現有錯誤 UI，並可在 `voice.py` 的錯誤分類中補一條「STT 服務離線」的友善訊息（區網服務比雲端 API 更常見的故障模式）。

### 2.8 修正原計畫的錯誤

- `STT_LLM_MODEL_NAME` 預設值統一為 `gemini-2.5-flash`（原計畫一處誤寫為不存在的 `gemini-3.5-flash`）。
- 原計畫遺漏 `proxy` 為現存合法 provider（factory 有三個分支，不是兩個）。
- 原計畫的樣板路徑寫的是已遷移前的 `services/prompts/`，實際為 `templates/prompts/`。

### 2.9 ⚠️ 前置修復（2026-08-09 全項目掃描後新增，實作前必修）

第三輪可行性複查（全項目 bug 掃描）發現三個直接影響本計畫的外部前提：

1. **`json_modifier.py` 靜默清空風險（High，必修）**：`append_to_list` 在欄位 JSON 解析失敗時
   靜默回傳 `[]` 再寫回單元素陣列，會**清空整個錄音歷史**。STT 模式（尤其 `stt_diff` 秒回、
   使用頻率預期上升）會放大此風險。實作本計畫前先修：解析失敗時拋例外而非回傳空列表。
2. **`voice.py` feedback 未 escape（Med，隨本計畫一併修）**：現有代碼將 `result.feedback`
   未轉義直接嵌入 TG HTML 訊息。`stt_diff` 的 feedback 天生含標記，§3.3 的 escape 要求
   必須同時套用到 `voice.py` 的訊息組裝端（對其餘 provider 的純文字 feedback 也一併
   `html.escape` 後再包安全標籤）。
3. **測試基線不在 main 上（影響 §5 驗證計畫）**：docs 記載的 48+11 測試實際位於未合併的
   本地分支 `claude/distracted-borg-2cfed8`（第二～四輪工作，342 檔差異），main 無
   `backend/tests/`。§5 的自動化測試需要先擇一：(a) 先合併該分支恢復測試基線（推薦，
   否則 CI 的 pytest 防線也不存在）；(b) 為本計畫新建獨立的 `backend/tests/` 起步。
   **處置（2026-08-10）**：採 (b)，`backend/tests/` 已建立，目前 38 個測試
   （STT 雙模式 18 ＋ High 修復回歸 20）。(a) 的合併決策仍待處理。

**上述三項的最新狀態（2026-08-10）**：S001 與 S013 已隨 STT 實作修復；其餘 High 級別
缺陷（S002–S011）亦已全數修復，詳見 [15_Bug_Scan_Report.md](15_Bug_Scan_Report.md)
的「High 實作紀錄」。

另註：掃描亦確認三個現存 audio evaluator 重試策略不一致（gemini 有退避重試、openai/proxy
無）。兩個新 evaluator 對 STT 呼叫採單次嘗試＋明確錯誤（區網服務失敗重試意義低）；
`stt_llm` 的 LLM 呼叫比照 openai_client 現狀（不重試），統一重試策略留待獨立重構。

---

## 3. 設計方案（逐檔案）

### 3.1 Configuration

#### [MODIFY] [backend/.env.example](../backend/.env.example)

在「LLM 設定」區塊後新增：

```dotenv
# --------------------------------------------------------------------
# STT (自架 Whisper / Speaches) 設定
# --------------------------------------------------------------------
# Speaches 服務的 OpenAI 相容端點 (含 /v1)
STT_SERVER_URL=http://192.168.50.171:8000/v1
# faster-whisper 模型 ID
STT_MODEL_NAME=Systran/faster-whisper-large-v3
# 自架服務可任意填寫，但 OpenAI SDK 要求非空
STT_API_KEY=speaches
# stt_llm 模式專用的純文字評分模型 (與多模態 AUDIO_MODEL_NAME 脫鉤)
STT_LLM_MODEL_NAME=gemini-2.5-flash
```

並更新動態修改白名單：

```dotenv
MODIFY_AUDIO_EVALUATOR_PROVIDER=['gemini_native', 'openai', 'stt_diff', 'stt_llm']
MODIFY_STT_LLM_MODEL_NAME=['gemini-2.5-flash', 'gemini-2.0-flash']
```

同步修改 `AUDIO_EVALUATOR_PROVIDER` 的註解，合法值為 `gemini_native / openai / proxy / stt_diff / stt_llm`。

#### [MODIFY] [backend/app/core/config.py](../backend/app/core/config.py)

`Settings` 新增四個欄位（放在 Audio Evaluator 區塊旁）：

```python
STT_SERVER_URL: str | None = Field(default=None, description="自架 Speaches/Whisper 的 OpenAI 相容端點 (含 /v1)")
STT_MODEL_NAME: str = Field(default="Systran/faster-whisper-large-v3", description="faster-whisper 模型 ID")
STT_API_KEY: str = Field(default="speaches", description="自架 STT 服務金鑰 (SDK 要求非空即可)")
STT_LLM_MODEL_NAME: str = Field(default="gemini-2.5-flash", description="stt_llm 模式的純文字評分模型")
```

`AUDIO_EVALUATOR_PROVIDER` 的 description 補上兩個新值。不加 validator 強制值域（維持現狀：非法值由工廠拋 `ValueError`，啟動時被 main.py 捕獲降級為 warning）。

### 3.2 Infrastructure：STT Client

#### [NEW] `backend/app/infrastructure/stt/__init__.py` + `whisper_client.py`

```python
class WhisperClient:
    """自架 Speaches (faster-whisper) 的轉錄客戶端。"""

    def __init__(self) -> None:
        if not settings.STT_SERVER_URL:
            raise STTServiceError("STT_SERVER_URL 未設定，無法初始化 WhisperClient。")
        self._client = AsyncOpenAI(api_key=settings.STT_API_KEY, base_url=settings.STT_SERVER_URL)

    @staticmethod
    def to_whisper_language(target_language: str | None) -> str | None:
        """locale → ISO 639-1（'ja-JP' → 'ja'）；'other'/空值 → None (自動偵測)。"""

    async def transcribe(
        self, audio_data: bytes, audio_filename: str, target_language: str | None
    ) -> str:
        """回傳逐字稿純文字。連線失敗/超時拋 STTServiceError。"""
```

實作要點：
- `transcriptions.create(model=settings.STT_MODEL_NAME, file=(audio_filename, audio_data), language=..., response_format="json")` —— 模型名**呼叫時讀取**（§2.3 解法 2）。
- 以 `asyncio.wait_for(..., timeout=60.0)` 包裹（比照各 evaluator 的 90s 慣例，STT 較快故取 60s）。
- **不做**測試腳本中的 `ensure_model_exists` 自動下載：生產路徑上一次 3-5 分鐘的模型下載會撞爆 TG handler 超時。模型未安裝時 Speaches 回錯誤 → 包成 `STTServiceError`，錯誤訊息中提示管理員手動安裝（模型安裝屬一次性部署動作，寫入 §6 部署清單）。

#### [MODIFY] [backend/app/core/exceptions/infrastructure.py](../backend/app/core/exceptions/infrastructure.py)

新增 `class STTServiceError(InfrastructureBaseError)`（`error_code = "STT_SERVICE_ERROR"`），並於 exceptions 套件的 `__init__` re-export。

### 3.3 模式一：`stt_diff`（STT + difflib，零 API 成本）

#### [NEW] `backend/app/infrastructure/audio_evaluator/stt_diff_evaluator.py`

`class STTDiffEvaluator(BaseAudioEvaluator)`，`__init__` 建立 `WhisperClient`。`evaluate_audio` 流程：

1. **轉錄**：`transcript = await self._whisper.transcribe(...)`。
2. **空語音防護**：逐字稿去空白後為空 → 回傳 `score=0, feedback="未能偵測到語音內容。", transcript="（無語音）"`（與樣板既有慣例一致）。
3. **無參考答案防護**：`reference_answers` 為空 → 回傳 `score=0`、transcript 照給、feedback 提示「此卡片無參考答案，不支援 stt_diff 純比對模式，請切換至 stt_llm 或其他模式」。
4. **正規化後比對**：對 transcript 與每條 reference 做比對前正規化——`unicodedata.normalize("NFKC", s)`（全半形統一）、移除空白與常見標點（`。、．，,.!?！？「」『』…・ ` 等）、英文 lowercase。**逐字元**（非分詞）`difflib.SequenceMatcher` 對日文即可良好運作。
5. **擇優**：對多條 reference 各算 `ratio()`，取最高者為比對對象。
6. **計分**：`score = round(best_ratio * 100)`。
7. **產生雙格式差異**（§2.5）：以 `SequenceMatcher.get_opcodes()` 遍歷（**基於未正規化的顯示用字串**重建 opcode 或映射回原字，實作時以「正規化字元 → 原始字元索引」對照表處理）：

   | opcode | Anki HTML (`feedback_anki_html`) | Telegram (`feedback`) |
   |--------|------|------|
   | `equal` | 原文 | 原文 |
   | `replace` | `<span style="color:red">誤</span><span style="color:green">正</span>` | `<s>誤</s><b>正</b>` |
   | `delete`（多唸） | `<span style="color:red">多</span>` | `<s>多</s>` |
   | `insert`（漏唸） | `<span style="color:green">漏</span>` | `<b>漏</b>` |

   feedback 開頭附一行摘要：`相似度 XX%（對照範本第 N 條）`，再接差異標記行。**TG 端所有原文片段須經 HTML escape**（`html.escape`），避免參考答案本身含 `<` 等字元打壞 parse mode。
8. 回傳 `AudioEvaluationResult(score=..., feedback=tg_markup, feedback_anki_html=anki_markup, transcript=transcript)`。

不呼叫任何 LLM；整個函數除 STT 外零網路 IO，預期「秒回」。

#### [MODIFY] [backend/app/schemas/llm/speaking.py](../backend/app/schemas/llm/speaking.py)

```python
class AudioEvaluationResult(BaseModel):
    score: int = Field(ge=0, le=100)
    feedback: str
    transcript: str = ""
    feedback_anki_html: str | None = None  # stt_diff 專用：寫回 Anki 的紅綠標記版
```

注意：`gemini_client.py:131` 以此 Pydantic model 直接作為 `response_schema` —— 新欄位為 Optional 且 LLM schema 容許缺省，不影響現有 provider；`openai_client.py` 的手寫 `_EVALUATION_SCHEMA` 不需要動（`additionalProperties: False` + required 三欄位照舊，解析時 Pydantic 補預設值）。

#### [MODIFY] [backend/app/bot/handlers/voice.py](../backend/app/bot/handlers/voice.py)（一行）

`new_recording` 的 `"comment"` 改為 `result.feedback_anki_html or result.feedback`。

### 3.4 模式二：`stt_llm`（STT + 純文字 LLM，低成本）

#### [NEW] `backend/app/infrastructure/audio_evaluator/stt_llm_evaluator.py`

`class STTLLMEvaluator(BaseAudioEvaluator)`，`__init__` 建立 `WhisperClient` + `AsyncOpenAI(api_key=settings.AUDIO_API_KEY, base_url=settings.AUDIO_BASE_URL)`（沿用語音評分既有憑證，僅模型換成 `STT_LLM_MODEL_NAME`；`AUDIO_API_KEY` 未設定時拋 `LLMServiceError`，與 openai_client 慣例一致）。流程：

1. `user_transcript = await self._whisper.transcribe(...)`；空逐字稿 → 同 §3.3 直接回 `score=0`，**不浪費 LLM 呼叫**。
2. 渲染樣板：`engine.render(template_name, ..., user_transcript=user_transcript, disable_markdown=False)` —— 與 openai_client 的渲染呼叫唯一差別是多傳 `user_transcript`。三語卡傳入的 `template_name` 自動沿用，專屬邏輯不遺失。
3. 純文字 Chat Completions：訊息只有 `system`（渲染後樣板）+ `user`（`f"使用者的語音辨識逐字稿如下：\n{user_transcript}"`），**payload 不含任何 audio bytes**。`model` 於呼叫時讀 `settings.STT_LLM_MODEL_NAME`。
4. 結構化輸出、超時（90s）、markdown 清理、`raw_decode` 解析：**完全複製 openai_client.py 既有實作**（含 `_EVALUATION_SCHEMA`）。實作時將該段抽為模組級共用函數或直接複製皆可，以複製為先（避免本計畫夾帶重構）。
5. 回傳結果時以 STT 逐字稿**覆寫** `transcript` 欄位（LLM 拿到的就是文字，讓它自己回填只會原样複讀，直接以 STT 結果為準）。

### 3.5 樣板修改（四個檔案，同一模式）

#### [MODIFY] `templates/prompts/audio_evaluator.j2`、`templates/prompts/anki/Speaking_Trilingual_{JA,ZH,EN}.j2`

各檔在開頭聲明後插入條件區塊（以 JA 版為例，其他語言版本用對應語言撰寫）：

```jinja
{% if user_transcript %}
## 【重要】評価モード：テキスト書き起こしベース
今回は音声ファイルの代わりに、音声認識 (STT) による書き起こしテキストが提供されます。
- 以下のユーザー発話はすべて書き起こしテキストに基づいて評価すること。
- 発音・アクセント・イントネーションは評価不可能なため、評価対象から除外し、
  内容・文法・語彙・文脈適合度のみで採点すること（配点は内容 70% / 文法 30% に再配分）。
- transcript フィールドには提供された書き起こしをそのまま返すこと。
{% endif %}
```

要點：
- 未傳 `user_transcript` 時（現有三個 provider 的呼叫皆不傳），Jinja2 未定義變數在 `{% if %}` 中為 falsy，**渲染輸出與現在完全相同** → 零回歸風險。
- 各樣板中「語音空白時回 score=0」等聽力相關指示保持原樣（STT 模式在 evaluator 層已先攔截空逐字稿，不會走到 LLM）。
- 逐字稿本體不放樣板（放 user message），樣板只負責「切換評分模式」的指示——避免四個樣板重複維護逐字稿嵌入格式。

### 3.6 工廠與動態切換

#### [MODIFY] [factory.py](../backend/app/infrastructure/audio_evaluator/factory.py)

新增兩個分支（維持延遲匯入慣例），並更新結尾 `ValueError` 的可選值清單（此清單目前也漏了 `proxy`，一併補上）：

```python
if provider == "stt_diff":
    from app.infrastructure.audio_evaluator.stt_diff_evaluator import STTDiffEvaluator
    return STTDiffEvaluator()

if provider == "stt_llm":
    from app.infrastructure.audio_evaluator.stt_llm_evaluator import STTLLMEvaluator
    return STTLLMEvaluator()
```

#### [MODIFY] [callbacks_config.py](../backend/app/bot/handlers/callbacks_config.py)

`handle_setconfig_value_selection` 在 `setattr(settings, key, value)` 成功後：

```python
if key == "AUDIO_EVALUATOR_PROVIDER":
    try:
        app.state.audio_evaluator = create_audio_evaluator()
    except Exception as e:
        # 回滾 settings 並告知管理員，避免留下「設定已改但實例是舊的」的不一致狀態
        setattr(settings, key, old_value)
        await callback.message.edit_text(f"❌ 切換失敗，已回滾：{e}")
        return
```

`app` 的取得：aiogram middleware（[bot/dependencies.py](../backend/app/bot/dependencies.py)）已持有 FastAPI `app` 並逐請求注入資料，比照 `audio_evaluator` 的注入方式在 `data` 中加入 `app`（或既有等價物）供此 handler 使用——實作時以該檔現況為準，這是唯一需要小幅探索的接線點。

---

## 4. 不做的事（Scope 邊界）

- 不動 `state.action == "add_audio"` 路徑（不經過 evaluator）。
- 不重構三個現存 evaluator 的重複 JSON 解析邏輯（另立 refactor 項）。
- 不做 STT 服務的模型自動下載（部署時一次性手動安裝，見 §6）。
- 不新增 Anki 卡片欄位或模板；`feedback_anki_html` 只影響寫入 `comment` 的字串內容。
- 不處理 `stt_diff` 的多語斷詞優化（字元級 diff 已滿足日文；英文以字元級起步，效果不佳再迭代為 `split()` 詞級）。

## 5. 驗證計畫

### 自動化測試（pytest，比照現有測試基線）

| # | 測試對象 | 內容 |
|---|----------|------|
| 1 | `WhisperClient.to_whisper_language` | `ja-JP→ja`、`en-US→en`、`zh-TW→zh`、`other→None`、`None→None`、空字串→None |
| 2 | `stt_diff` 正規化 | NFKC 全半形、標點剝除、英文 lowercase |
| 3 | `stt_diff` 比對 | 完全一致→100；替換／插入／刪除各產生預期的 `<s>/<b>`（TG）與 `<span>`（Anki）標記；多 reference 取最高分 |
| 4 | `stt_diff` 防護 | 無 reference→score=0 + 提示；空逐字稿→score=0（mock WhisperClient） |
| 5 | `stt_diff` HTML escape | reference 含 `<` `&` 時 TG 輸出已轉義 |
| 6 | `stt_llm` payload | mock AsyncOpenAI，斷言 messages 不含 `input_audio`、model 為 `STT_LLM_MODEL_NAME`、system prompt 含樣板渲染結果 |
| 7 | 樣板回歸 | 四個 j2 在**不傳** `user_transcript` 時渲染輸出與修改前逐字元相同（golden test）；傳入時包含 STT 模式指示 |
| 8 | 工廠 | `stt_diff`/`stt_llm` 建立正確類別；非法值 ValueError 訊息含全部五個合法值 |
| 9 | Schema 相容 | `AudioEvaluationResult(**{"score":90,"feedback":"x","transcript":"y"})` 不因新欄位而失敗 |

### 手動驗證（Bot 操作）

1. `/setconfig` 切至 `stt_diff` → 發語音 → **不重啟服務**即生效（驗證 §3.6），秒回、TG 顯示刪除線/粗體差異、Anki 卡片背面 comment 呈現紅綠字。
2. 切至 `stt_llm` → 發語音 → 後端 log 確認呼叫模型為 `STT_LLM_MODEL_NAME`、無音訊 payload；三語卡（JA）feedback 仍為全日文（專屬樣板生效）。
3. 關閉 Speaches 服務再發語音 → 顯示 STT 服務離線的友善錯誤，狀態被清除、不留殭屍狀態。
4. 切回 `gemini_native` → 原多模態流程回歸正常（樣板 golden test 的 runtime 對照）。
5. 日文卡片實測：確認 `language=ja` 已傳入（防止韓文誤判重演）。

## 6. 部署前置清單

1. Speaches 服務常駐於 `192.168.50.171:8000`（開機自啟）。
2. 手動安裝模型一次：`POST /v1/models/Systran/faster-whisper-large-v3`（或沿用測試腳本執行）。
3. `.env` 填入 §3.1 四個 `STT_*` 變數與兩條 `MODIFY_*` 白名單。
4. **Docker 網路確認**：backend 容器需能連通區網 IP `192.168.50.171`（bridge 模式預設可達區網；若未來 compose 改用自訂網路或部署位置改變，需重驗）。
5. 首次上線先以 `stt_diff` 對一張有 References 的卡片冒煙測試，再開放 `stt_llm`。

## 7. 建議實作順序

0. **前置修復（§2.9）**：json_modifier 解析失敗改拋例外；決定測試基線來源（合併
   `claude/distracted-borg-2cfed8` 或新建 tests/）。
1. Config + 例外類別（§3.1、§3.2 例外部分）—— 無風險純新增。
2. `WhisperClient` + 單元測試（語言映射可離線測，轉錄以區網服務實測）。
3. `stt_diff` evaluator + schema 新欄位 + `voice.py` 一行改動 + 測試（此模式不依賴 LLM，可完整本地驗證）。
4. 樣板四檔加分支 + golden 回歸測試。
5. `stt_llm` evaluator + 測試。
6. 工廠分支 + `/setconfig` 重建接線 + 手動驗證全流程。
