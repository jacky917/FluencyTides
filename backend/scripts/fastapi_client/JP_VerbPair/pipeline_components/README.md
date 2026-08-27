# JP_VerbPair 生成管線組件 (Pipeline Components)

此模組集合負責將 `test_single_generate.py` 的繁雜生成流程進行解耦與封裝。
透過這些組件，主腳本可以單純扮演**協調者 (Coordinator)**，將髒活（包含去重檢查、HTTP API 重試、Anki 媒體延遲上傳）全部交由底層模組處理。

## 模組清單

- **`init_db.py`**: 用於初始化 MySQL 語料庫中的 `generated_sentences_log` 去重表。
- **`log_repository.py`**: 負責 `generated_sentences_log` 的基礎資料庫 CRUD 查詢與軟刪除機制。
- **`context_builder.py`**: 負責從 `scripts` 抓取對話，並合併 Visual Novel 特有的未閉合引號台詞。
- **`dedup_manager.py`**: 作為領域邏輯對外的統一窗口，統整去重檢查 (`log_repository`) 與資料準備 (`context_builder`)。
- **`backend_api_client.py`**: 封裝發往 FastAPI 後端的 HTTP 請求，內建 `tenacity` 處理因為 LLM 導致的 Timeout/502 等超時重試機制。
- **`anki_media_uploader.py`**: 解析後端回傳的 `kept_dialog`，讀取本地圖檔與音檔並上傳至 AnkiConnect，內建防重複上傳與失敗重試。

## 架構設計原則

本模組遵循 Clean Architecture 原則：
- **Infrastructure (基礎設施)**: `backend_api_client.py` 與 `anki_media_uploader.py` 處理外部 HTTP/RPC 通訊。
- **Service (領域服務)**: `dedup_manager.py` 處理與語料庫相關的商業邏輯（哪些可以生成、哪些該阻擋）。

## 資料庫結構 (generated_sentences_log)

本模組仰賴 MySQL 中的 `generated_sentences_log` 表。
若需初始化或重建該表，請單獨執行 `init_db.py`：

```bash
python scripts/fastapi_client/JP_VerbPair/pipeline_components/init_db.py
```

### 關鍵欄位設計
- `script_id` + `verb_lemma`: 複合 Unique Key，去重的判斷基準。
- `is_deleted`: 軟刪除標記。若為 `TRUE`，則 `prepare_generation` 會放行並允許重新生成。
- `delete_count`: 紀錄反覆刪除的次數，方便追蹤卡片的修改歷史。
- `master_note_id`, `context_note_id`, `cloze_note_id`: 完整記錄生成的 Anki 產物，方便未來做逆向查詢或批次清理。

## 主腳本調用範例

在客戶端腳本（如 `test_single_generate.py`）中，標準調用流程如下：

```python
from pipeline_components.dedup_manager import DedupManager
from pipeline_components.backend_api_client import BackendAPIClient
from pipeline_components.anki_media_uploader import AnkiMediaUploader

# 1. 準備資料與防重複（project 對應 generated_sentences_log 的專案隔離欄位）
from scripts.common.database.log_repository import PROJECT_JP_VERB_PAIR
dedup_manager = DedupManager(
    session, voice_dir, avatar_dir, "SabbatOfTheWitch", project=PROJECT_JP_VERB_PAIR
)
context_dialogue = await dedup_manager.prepare_generation(script_id, verb, chapter)
if not context_dialogue:
    return  # 重複，跳過

# 2. 觸發後端生成管線 (內建 Retry)
api_client = BackendAPIClient(api_url, headers)
response_data = await api_client.invoke_generation_pipeline(payload)

# 3. 延遲上傳媒體 (內建 Retry)
uploader = AnkiMediaUploader(anki_client, voice_dir, avatar_dir, "SabbatOfTheWitch")
await uploader.upload_media(response_data["kept_dialog"])

# 4. 寫入去重日誌
await dedup_manager.record_success(
    script_id, verb, chapter, 
    master_note_id, 
    response_data["context_note_id"], 
    response_data["cloze_note_id"]
)
```
