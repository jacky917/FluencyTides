# FIX：本機 `.env` 的 `AUDIO_API_KEY` 讓兩個 LLM 端點測試失敗

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-25 |
| **性質** | 測試隔離（只改測試，不改產品程式） |
| **狀態** | 🚧 實作完成，PR 審查中 |
| **嚴重度** | 🟢 CI 一直都會過；只有本機跑全部測試時有 2 個失敗 |
| **影響範圍** | `backend/tests/conftest.py` 的 autouse fixture `_no_local_llm_endpoints` |
| **分支** | `fix/audio-endpoint-test-isolation`（從 `origin/main`） |

---

## 1. 症狀

本機跑 `python -m pytest tests -q` 一直有 2 個失敗，CI 上卻是通過的：

- `tests/test_llm_endpoints.py::test_list_endpoints_only_selectable_ones`
  - 預期清單只有 `claude-code` 和 `relay`，實際前面多了 `audio`。
- `tests/test_models_menu.py::test_malformed_buttons_are_stale_and_do_nothing[effort-token-mismatch]`
  - 這顆按鈕應該被當成過期、什麼都不做。實際上它指到 audio 端點的 `gemini-3.1-flash-lite`，而且真的存了一筆設定。
  - 這筆寫在測試用的暫存 SQLite，正式的路由設定沒有被改到。

## 2. 原因

1. 內建的 `audio` 端點由 `AUDIO_*` 設定組成（`app/infrastructure/llm/endpoints.py` 的 `_audio_endpoint()`）。只要有填 `AUDIO_API_KEY`，它就可選（`listed=True`），會出現在端點清單和 `/setconfig` 的模型選單裡。
2. 本機 `backend/.env` 有填 `AUDIO_API_KEY`，錄音評分要用到它，這樣設是對的。
3. 這兩個測試比內建 audio 端點早寫：
   - `fdb98ab`（10/16）、`5dd79c6`（10/18）加了這兩個測試。
   - `d70805b`（10/19）才加了內建 audio 端點。

   所以測試沒有把 `AUDIO_*` 關掉，讀到的是本機的真實設定。
4. `conftest.py` 原本就有 autouse 的 `_no_local_llm_endpoints`，用來「不讀開發機 .env 的自訂端點」。但它只清了 `LLM_ENDPOINTS` 和兩份 CLI 白名單，沒有涵蓋 audio 端點。
5. CI 沒有 `.env`，`AUDIO_API_KEY` 是預設值 `None`，所以 CI 上會過。

## 3. 修法

擴充 `_no_local_llm_endpoints`：所有 `AUDIO_*`、`STT_*` 設定一律還原成 `Settings` 宣告的預設值（從 `Settings.model_fields` 讀），和 CI 的狀態相同。

- 範圍和原本的用意一樣，就是「LLM 端點不受本機 .env 影響」。所以沿用這個 fixture，不另外開新的。
- 要用到這些設定的測試，原本就會自己 patch（例如 `test_recording_eval_routing.py` 的 `AUDIO_API_KEY="google-key"`）。fixture 的 patch 在外層，測試的 patch 在內層，所以會覆蓋 fixture 的值。
- 一起還原 `STT_*`：錄音評分的 `stt_diff`／`stt_llm` 也會讀這組設定，本機 `.env` 有填 `STT_SERVER_URL` 時，同樣會讓測試結果和 CI 不一樣。
- 產品程式不用改。

### 沒有採用的做法

| 做法 | 不採用的原因 |
|---|---|
| 只在兩個失敗的測試裡 patch `AUDIO_API_KEY` | 之後新寫的端點或選單測試還是會踩到同樣的問題 |
| 測試時不載入 `.env` | 很多測試依賴 `Settings` 的其他欄位，改動範圍太大 |

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `backend/tests/conftest.py` | `_no_local_llm_endpoints` 加上把 `AUDIO_*`／`STT_*` 還原成預設值；docstring 改成英文在前的雙語，並說明原因 |

## 5. 驗收

- [x] 本機有 `.env` 時，`pytest tests -q` 沒有失敗（修正前固定有 2 個失敗）。
- [x] `ruff check app scripts tests` 通過。

## 6. 實作紀錄（2026-11-25）

跑法：

```powershell
cd backend
```

```powershell
python -m pytest tests -q
```

```powershell
python -m ruff check app scripts tests
```

結果：全部測試 2620 passed、8 skipped、0 failed，ruff 通過。修正前是 2618 passed、2 failed。
