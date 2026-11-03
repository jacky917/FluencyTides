# JP_VerbPair 生成管線組件 (Pipeline Components)

此模組集合負責將建卡腳本（`JP_VerbPair/generate_child_cards.py`、`JP_CoreVerb/generate_child_cards.py`、
`JP_VerbPair/mark_skipped_es_data.py`）的繁雜生成流程進行解耦與封裝。
透過這些組件，主腳本可以單純扮演**協調者 (Coordinator)**，將髒活（包含去重檢查、HTTP API 重試、Anki 媒體延遲上傳）全部交由底層模組處理。

## 模組清單

- **`init_db.py`**（`scripts/common/database/init_db.py`）: 依 `schema.sql` 建立語料庫的 `card_gen_log` 去重表與 `nlp_line_readings`。
- **`log_repository.py`**（`scripts/common/database/log_repository.py`）: 負責 `card_gen_log` 的 CRUD 查詢與軟刪除機制，並在同一個交易裡維護卡片關聯 `rel_links`。
- **`context_builder.py`**: 負責從語料庫的 `game_lines`（經 `scripts/common/database/corpus_repository.py`）抓取前後文，並合併 Visual Novel 特有的未閉合引號台詞；目標台詞不在語料庫、或語音檔不存在時記 WARNING。
- **`dedup_manager.py`**: 作為領域邏輯對外的統一窗口，統整去重檢查 (`log_repository`) 與資料準備 (`context_builder`)。
- **`backend_api_client.py`**: 封裝發往 FastAPI 後端的 HTTP 請求，內建 `tenacity` 處理因為 LLM 導致的 Timeout/502 等超時重試機制。
- **`anki_media_uploader.py`**: 解析後端回傳的 `kept_dialog`，讀取本地圖檔與音檔並上傳至 AnkiConnect，內建防重複上傳與失敗重試。

## 架構設計原則

本模組遵循 Clean Architecture 原則：
- **Infrastructure (基礎設施)**: `backend_api_client.py` 與 `anki_media_uploader.py` 處理外部 HTTP/RPC 通訊。
- **Service (領域服務)**: `dedup_manager.py` 處理與語料庫相關的商業邏輯（哪些可以生成、哪些該阻擋）。

## 資料庫結構 (card_gen_log)

本模組仰賴語料庫 `fluencytides_corpus` 的 `card_gen_log` 表（原 `generated_sentences_log`；欄位見
`backend/scripts/database/corpus_rebuild/schema.sql`）。與舊表的對照：`script_id` → `line_id`、
`verb_lemma` → `term_key`（`ja-JP:<原形>`）、`project` → `card_type`、`source` → `game_id`，
`chapter` 已拿掉（由台詞的 `chapter_label` 查）。`log_repository` 的方法參數仍叫 `script_id` / `verb_lemma` /
`project`，`term_key` 與 `game_id` 由它組出與查出。若需建立該表，請單獨執行 `init_db.py`：

```bash
python scripts/common/database/init_db.py
```

### 關鍵欄位設計
- `line_id` + `term_key` + `card_type`: 複合 Unique Key（`uk_line_term_type`），去重的判斷基準；不同卡片類型各自去重。
- `is_deleted`: 軟刪除標記。若為 `TRUE`，則 `prepare_generation` 會放行並允許重新生成。
- `delete_count`: 紀錄反覆刪除的次數，方便追蹤卡片的修改歷史。
- `failure_count`: 生成失敗次數；大於 0 時 `prepare_generation` 跳過該句。
- `master_note_id`, `context_note_id`, `cloze_note_id`: 完整記錄生成的 Anki 產物，方便未來做逆向查詢或批次清理（子卡 id 可為 NULL）。

## 主腳本調用範例

在建卡腳本（如 `JP_VerbPair/generate_child_cards.py`）中，標準調用流程如下：

```python
from scripts.common.database.corpus_repository import get_game_profile, require_media_dirs
from scripts.common.database.log_repository import PROJECT_JP_VERB_PAIR
from scripts.fastapi_client.JP_VerbPair.pipeline_components.anki_media_uploader import AnkiMediaUploader
from scripts.fastapi_client.JP_VerbPair.pipeline_components.backend_api_client import BackendAPIClient
from scripts.fastapi_client.JP_VerbPair.pipeline_components.dedup_manager import DedupManager

# 0. 遊戲設定從 game_profiles 讀；語音或頭像資料夾不存在時直接報錯
game = await get_game_profile(session, "SabbatOfTheWitch")
voice_dir, avatar_dir = require_media_dirs(game)

# 1. 準備資料與防重複（project 對應 card_gen_log.card_type）
dedup_manager = DedupManager(
    session, voice_dir, avatar_dir, game.game_id,
    context_prev=settings.JP_VERB_PAIR_CONTEXT_PREV,
    context_next=settings.JP_VERB_PAIR_CONTEXT_NEXT,
    project=PROJECT_JP_VERB_PAIR,
)
context_dialogue = await dedup_manager.prepare_generation(script_id, verb_lemma, dialogue=dialogue)
if not context_dialogue:
    return  # None：重複或失敗達上限；[]：台詞不在語料庫（已記 WARNING）

# 2. 觸發後端生成管線 (內建 Retry)
api_client = BackendAPIClient(api_url, headers)
response_data = await api_client.invoke_generation_pipeline(payload)

# 3. 延遲上傳媒體 (內建 Retry)
uploader = AnkiMediaUploader(anki_client, voice_dir, avatar_dir, game.game_id)
await uploader.upload_media(response_data["kept_dialog"])

# 4. 寫入去重日誌（同一個交易寫入 rel_links）
await dedup_manager.record_success(
    script_id, verb_lemma,
    master_note_id,
    response_data["context_note_id"],
    response_data["cloze_note_id"],
    llm_model=llm_model_label,
)
```
