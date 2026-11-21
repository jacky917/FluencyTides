"""SQLModel ORM table definitions (MySQL-compatible).

SQLModel ORM 資料表模型定義（MySQL 相容版）。

This module defines every table of FluencyTides' own database. These tables are independent of Anki's own
database and store structured relational data that Anki cannot handle efficiently.

本模組定義了 FluencyTides 自有資料庫的所有資料表結構。
這些資料表獨立於 Anki 本身的資料庫，專門儲存 Anki 無法高效處理的
結構化關聯資料。

MySQL-compatibility rules (strictly followed in this module):
1. Every str column declares max_length; indexed columns must never omit it.
2. Constraint names are managed centrally by the MetaData in conventions.py.
3. DateTime columns use sa_column + DateTime(timezone=True) + server_default=func.now().
4. A single auto-increment PK; composite primary keys are forbidden.
5. No SQLite-specific syntax.

MySQL 相容性鐵律（本模組嚴格遵守）：
1. 所有 str 欄位一律指定 max_length，索引欄位絕對禁止省略。
2. 約束命名由 conventions.py 中的 MetaData 統一管理。
3. DateTime 使用 sa_column + DateTime(timezone=True) + server_default=func.now()。
4. 單一自增 PK，禁止複合主鍵。
5. 禁止 SQLite 特有語法。

Design principles:
- SQLModel (table=True) serves as both the ORM model and the Pydantic V2 model.
- Every note_id column references an Anki note ID (int64)
  but has no foreign-key constraint (the Anki database is managed by Anki itself).
- Timestamps are UTC; the frontend and Telegram convert time zones themselves.

設計原則：
- 使用 SQLModel (table=True) 同時作為 ORM Model 與 Pydantic V2 Model。
- 所有 note_id 欄位參照 Anki 的 Note ID（int64），
  但不建立外鍵約束（因為 Anki 資料庫由 Anki 自行管理）。
- 使用 UTC 時間戳，前端/TG 自行轉換時區。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, UniqueConstraint, func
from sqlmodel import Field, SQLModel

# 確保 conventions 已載入（此 import 是防禦性的，database.py 也會 import）
import app.infrastructure.database.conventions  # noqa: F401


class CardRelation(SQLModel, table=True):
    """Card relation table.

    卡片關聯表。

    **No longer used (2026-11-01)**: card relations moved to the corpus ``rel_links`` (``RelationService``, C of
    D9 in docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md); this SQLite table is kept as is and not dropped, but the
    code no longer reads or writes it.

    **已停用（2026-11-01）**：卡片關聯改存語料庫的 ``rel_links``（``RelationService``，
    docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C）；這張 SQLite 表原樣保留不刪，程式不再讀寫。

    Stores directed relations between two Anki cards (notes), supporting
    complex graph structures such as many-to-many and multiple parents.

    儲存兩張 Anki 卡片（筆記）之間的有向關係，
    支援多對多、多父節點等複雜圖譜結構。

    Attributes:
        id: the auto-increment primary key (single PK, MySQL-compatible); the internal unique identifier on which
            all relations are based
            自增主鍵（單一 PK，MySQL 相容）。資料庫內部唯一識別碼，所有關聯均以此 ID 為主
        source_note_id: the source note ID (the Anki note ID); ``None`` (NULL) marks a reverse dangling node
            起點筆記 ID（對應 Anki Note ID）。允許為 ``None`` (Null)，代表這是一個反向懸空節點
        target_note_id: the target note ID (the Anki note ID); ``None`` (NULL) means the target card does not
            exist in Anki yet (a dangling node). When the card is created later, this ID can be filled in from
            ``target_label``.
            終點筆記 ID（對應 Anki Note ID）。允許為 ``None`` (Null)，代表目標卡片尚未在 Anki 中建立（懸空節點）。
            未來建立卡片時，可依據 ``target_label`` 補齊此 ID。
        relation_type: the relation-type label (for example synonym, antonym, parent, collocation);
            max_length=50, indexed to support filtering by type
            關係類型標籤（如 synonym, antonym, parent, collocation）。max_length=50，已建立索引以支援按類型篩選
        source_label: the human-readable label of the source note (for example ``"apple"``), used to avoid
            looking up Anki during graph rendering, which speeds up drawing
            起點筆記的人類可讀標籤（如 ``"apple"``）。用於圖譜渲染時避免反查 Anki，提升圖譜繪製效能
        target_label: the human-readable label of the target note. Same as above; when ``target_note_id`` is
            ``None``, it is also the only clue for finding the target card later.
            終點筆記的人類可讀標籤。同上，且在 ``target_note_id`` 為 ``None`` 時，作為未來尋找目標卡片的唯一線索。
        created_at: the creation timestamp (UTC, timezone-aware); sa_column keeps time-zone parsing consistent
            between MySQL and SQLite
            建立時間（UTC, timezone-aware）。使用 sa_column 確保 MySQL 與 SQLite 的時區解析一致
    """

    __tablename__ = "card_relations"

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="自增主鍵 ID",
    )

    source_note_id: int | None = Field(
        default=None,
        index=True,
        description="起點 Anki Note ID (允許為空，代表起點未知或尚未建立)",
    )
    target_note_id: int | None = Field(
        default=None,
        index=True,
        description="終點 Anki Note ID (允許為空，代表終點卡片尚未建立，此時依賴 target_label)",
    )

    # MySQL 中 VARCHAR 索引欄位必須指定長度，否則建索引會失敗
    relation_type: str = Field(
        max_length=50,
        index=True,
        description="關係類型：synonym, antonym, parent, collocation 等",
    )

    source_label: str = Field(
        default="",
        max_length=200,
        description="起點筆記的人類可讀標籤（如單字本身）",
    )
    target_label: str = Field(
        default="",
        max_length=200,
        description="終點筆記的人類可讀標籤",
    )

    # 使用 sa_column 確保 DateTime 帶時區且有資料庫層級的預設值
    # 為什麼不用 default_factory：
    #   default_factory 只在 Python 層面生效（ORM insert 時），
    #   但若透過原生 SQL 或 Alembic 插入資料則不會觸發。
    #   server_default=func.now() 確保資料庫引擎自動填入，
    #   在 SQLite 與 MySQL 上行為完全一致。
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )


class RelationType(SQLModel, table=True):
    """Relation type table.

    關聯類型表。

    **No longer used (2026-11-01)**: card relations moved to the corpus ``rel_types`` (``RelationService``, C of
    D9 in docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md); this SQLite table is kept as is and not dropped, but the
    code no longer reads or writes it.

    **已停用（2026-11-01）**：卡片關聯改存語料庫的 ``rel_types``（``RelationService``，
    docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C）；這張 SQLite 表原樣保留不刪，程式不再讀寫。

    Dynamically stores all relation types created by users, for display in
    frontend dropdown menus.

    動態儲存使用者建立的所有關聯類型，以便前端下拉選單展示。

    Attributes:
        id: the auto-increment primary key
            自增主鍵
        name: the relation type name (for example synonym, collocation, travel); unique
            關聯類型名稱（如 synonym, collocation, travel 等），不可重複
        created_at: the creation timestamp
            建立時間
    """

    __tablename__ = "relation_types"

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="自增主鍵 ID",
    )

    name: str = Field(
        max_length=50,
        unique=True,
        index=True,
        description="關聯類型名稱",
    )

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )


class CapabilityRouteSetting(SQLModel, table=True):
    """Capability route settings: the tool each route key uses for each capability.

    能力路由設定表：每個用途鍵在每種能力上選用的工具。

    See the capability routing plan capability_routing_FEAT_2026-10-19.md. Each row records the "provider + model +
    effort" chosen for one route key (or the global ``*``) on one capability (currently only ``llm``). The
    selectable combinations are decided by the whitelist in ``.env`` (see ``app/infrastructure/llm/endpoints.py``);
    this table only stores the choice. Every read checks the row against the whitelist again, and a row outside it
    is not used and falls back to the next layer. Deleting a row means falling back to the next layer (a route row
    falls back to the global row, the global row falls back to .env).

    見能力路由計畫 capability_routing_FEAT_2026-10-19.md。每列記錄一個用途鍵（或全域 ``*``）在一種能力（目前只有
    ``llm``）上選用的「provider + 模型 + effort」。可選的組合由 ``.env`` 的白名單決定（見
    ``app/infrastructure/llm/endpoints.py``），這裡只存選擇；讀取時逐次比對白名單，不在白名單內的
    列不使用、退回下一層。刪除一列＝退回下一層（用途列退回全域列，全域列退回 .env）。

    Note: SQLModel ``table=True`` models do not run pydantic validators on construction, so do not add validation
    here; write-time checks live in the service layer (key format, capability, whitelist, an empty effort string
    stored as NULL).

    注意：SQLModel 的 ``table=True`` 模型建構時不執行 pydantic 驗證器，不要在這裡加
    驗證——寫入前的檢查放在服務層（鍵格式、能力、白名單、effort 空字串存成 NULL）。

    Attributes:
        id: the auto-increment primary key (single PK, MySQL-compatible); the code always queries by
            ``(route_key, capability)``
            自增主鍵（單一 PK，MySQL 相容）；程式一律以 ``(route_key, capability)`` 查詢
        route_key: the route key; ``*`` is global
            用途鍵；``*`` 為全域
        capability: the capability, for example ``llm``
            能力，例如 ``llm``
        provider: who provides it; the endpoint ID for ``llm``
            由誰提供：``llm`` 為端點 ID
        model: the model name (the base name for CLI tools); ``None`` for capabilities that need no model
            模型名（CLI 類為基底名）；不需要模型的能力為 ``None``
        effort: the thinking effort; ``None`` only when the model takes no effort in the whitelist
            思考深度；只有該模型在白名單中不帶 effort 時為 ``None``
        updated_by: the last editor (``tg:<user id>`` or ``api``)
            最後修改者（``tg:<user id>`` 或 ``api``）
        created_at: the creation timestamp
            建立時間
        updated_at: the last update timestamp
            最後修改時間
    """

    __tablename__ = "capability_routes"
    # 一個用途鍵在每種能力上最多一列
    __table_args__ = (UniqueConstraint("route_key", "capability"),)

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="自增主鍵 ID",
    )

    route_key: str = Field(
        max_length=64,
        index=True,
        description="用途鍵；* 為全域",
    )

    capability: str = Field(
        max_length=32,
        description="能力，例如 llm",
    )

    provider: str = Field(
        max_length=64,
        description="由誰提供；llm 為端點 ID",
    )

    model: str | None = Field(
        default=None,
        max_length=128,
        description="模型名；不需要模型的能力為空",
    )

    effort: str | None = Field(
        default=None,
        max_length=16,
        description="思考深度；模型不使用 effort 時為空",
    )

    updated_by: str = Field(
        max_length=64,
        description="最後修改者：tg:<user id> 或 api",
    )

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )

    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    )
