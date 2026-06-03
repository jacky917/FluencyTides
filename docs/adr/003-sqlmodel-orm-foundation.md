# ADR 003: 採用 SQLModel 與 SQLAlchemy 2.0 建立資料庫持久化基礎設施

## Status
Accepted

## Context
FluencyTides 的核心資料雖然儲存於使用者的本地端 Anki 中（如卡片內容、背誦記錄），但在實作進階功能時（例如：卡片關係的雙向與多對多連結、知識圖譜的快速查詢），直接把這些關係狀態塞在 Anki 卡片的欄位會造成維護複雜（孤兒節點）、查詢緩慢（缺乏真正的關聯式索引）等問題。

我們需要一個專屬的資料庫，用來：
1. 儲存卡片與卡片間的有向關聯 (Card Relations)，並支援高效查詢。
2. 儲存未來的個人設定、學習紀錄、API 權限等。

同時，考慮到部署靈活性，開發階段將使用 SQLite，但未來**絕對會遷移至 MySQL**，我們需要一個能夠提供高相容性且易於維護 Schema 遷移的持久化方案。

## Decision
我們決定：
1. 採用 **SQLModel** 作為主要 ORM 框架，並與 **SQLAlchemy 2.0 AsyncEngine** 結合，實作非同步的資料庫存取。
2. 採用 **Alembic** 作為資料庫遷移工具，從第一天開始追蹤所有 Schema 變更。
3. **強制執行 MySQL 相容性準則**：
   - 所有的 `str` 欄位必須明確給予 `max_length`。
   - 所有索引、主鍵、約束等必須遵循明確的命名規範 (`conventions.py`)。
   - 日期時間統一使用帶有 timezone 的 `DateTime` 與 `server_default=func.now()`。
   - 統一使用單一 `id` 自增主鍵，不使用複合主鍵。

## Rationale
- **SQLModel 的優勢**：它能將 Pydantic V2 Model 與 SQLAlchemy Mapped Class 合二為一。這不僅消除了撰寫額外 Schema 的樣板程式碼，也完美契合了本專案全端「Pydantic V2 強制執行」的準則。
- **SQLAlchemy 2.0 的優勢**：成熟的 ORM，強大的 async 支援，未來即使面對極度複雜的 Query 也可以回退到純 SQLAlchemy 語法。
- **MySQL 相容性的考量**：SQLite 在建表時不嚴格要求 VARCHAR 的長度，這常常導致轉移至 MySQL 失敗。藉由在模型宣告時實行嚴格的約定，以及透過 MetaData 控制命名規範，我們可以達到遷移「零修改」。
- **Alembic 的必要性**：當涉及協作或是部署至不同環境（尤其是跨資料庫引擎）時，僅依賴 `create_all()` 是不可靠的。Alembic 提供了一套完整追蹤欄位演進的方式。

## Consequences
- **好處**：
  - 開發體驗絕佳，因為資料庫模型與 API 資料傳輸模型 (DTO) 可輕鬆互通，且具備完整的 IDE 型別提示。
  - 保證了高度的資料庫可攜性，特別是確保未來能無痛轉移至 MySQL。
  - 將複雜的圖譜關聯交由專屬資料庫維護，讓 Anki 專心處理核心單字卡，遵循了關注點分離的架構精神。
- **壞處**：
  - 專案依賴增加（sqlmodel, aiosqlite, alembic）。
  - 開發者需要遵守更嚴謹的 SQLModel 模型定義限制（例如強制填寫 `max_length`）。
  - 需要維護 Alembic 的遷移檔案。
