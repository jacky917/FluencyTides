"""
SQLModel ORM 資料表模型定義（MySQL 相容版）。

本模組定義了 FluencyTides 自有資料庫的所有資料表結構。
這些資料表獨立於 Anki 本身的資料庫，專門儲存 Anki 無法高效處理的
結構化關聯資料。

MySQL 相容性鐵律（本模組嚴格遵守）：
1. 所有 str 欄位一律指定 max_length，索引欄位絕對禁止省略。
2. 約束命名由 conventions.py 中的 MetaData 統一管理。
3. DateTime 使用 sa_column + DateTime(timezone=True) + server_default=func.now()。
4. 單一自增 PK，禁止複合主鍵。
5. 禁止 SQLite 特有語法。

設計原則：
- 使用 SQLModel (table=True) 同時作為 ORM Model 與 Pydantic V2 Model。
- 所有 note_id 欄位參照 Anki 的 Note ID（int64），
  但不建立外鍵約束（因為 Anki 資料庫由 Anki 自行管理）。
- 使用 UTC 時間戳，前端/TG 自行轉換時區。

English summary:
    SQLModel ORM table definitions (MySQL-compatible) for FluencyTides' own
    database, storing structured relational data that Anki cannot handle
    efficiently. MySQL-compatibility rules strictly followed here: every
    str column declares max_length (mandatory for indexed columns),
    constraint names come from conventions.py, DateTime columns use
    sa_column + DateTime(timezone=True) + server_default=func.now(), single
    auto-increment PKs only, and no SQLite-specific syntax. note_id columns
    reference Anki note IDs (int64) without foreign keys, since the Anki
    database is managed by Anki itself. Timestamps are UTC; clients convert
    time zones.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel

# 確保 conventions 已載入（此 import 是防禦性的，database.py 也會 import）
import app.infrastructure.database.conventions  # noqa: F401


class CardRelation(SQLModel, table=True):
    """卡片關聯表。

    Card relation table.

    儲存兩張 Anki 卡片（筆記）之間的有向關係，
    支援多對多、多父節點等複雜圖譜結構。

    Stores directed relations between two Anki cards (notes), supporting
    many-to-many and multi-parent graph structures.

    Attributes:
        id (int | None): 自增主鍵（單一 PK，MySQL 相容）。
            資料庫內部唯一識別碼，所有關聯均以此 ID 為主。
            Auto-increment primary key (single PK, MySQL-compatible); the
            internal unique identifier for all relations.
        source_note_id (int | None): 起點筆記 ID（對應 Anki Note ID）。
            允許為 None (Null)，代表這是一個反向懸空節點。
            Source note ID (Anki note ID); None marks a reverse dangling
            node.
        target_note_id (int | None): 終點筆記 ID（對應 Anki Note ID）。
            允許為 None (Null)，代表目標卡片尚未在 Anki 中建立（懸空節點）。
            未來建立卡片時，可依據 target_label 補齊此 ID。
            Target note ID (Anki note ID); None means the target card does
            not exist in Anki yet (dangling node) and can be filled in later
            via target_label.
        relation_type (str): 關係類型標籤（如 synonym, antonym, parent, collocation）。
            max_length=50，已建立索引以支援按類型篩選。
            Relation-type label (e.g. synonym, antonym, parent,
            collocation); max_length=50, indexed for type filtering.
        source_label (str): 起點筆記的人類可讀標籤（如 "apple"）。
            用於圖譜渲染時避免反查 Anki，提升圖譜繪製效能。
            Human-readable label of the source note (e.g. "apple"), used to
            avoid Anki lookups during graph rendering.
        target_label (str): 終點筆記的人類可讀標籤。
            同上，且在 target_note_id 為 None 時，作為未來尋找目標卡片的唯一線索。
            Human-readable label of the target note; when target_note_id is
            None, it is the only clue for finding the target card later.
        created_at (datetime | None): 建立時間（UTC, timezone-aware）。
            使用 sa_column 確保 MySQL 與 SQLite 的時區解析一致。
            Creation timestamp (UTC, timezone-aware); sa_column keeps MySQL
            and SQLite behavior consistent.
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
    """關聯類型表。

    Relation type table.

    動態儲存使用者建立的所有關聯類型，以便前端下拉選單展示。

    Dynamically stores all relation types created by users, for display in
    frontend dropdown menus.

    Attributes:
        id (int | None): 自增主鍵。Auto-increment primary key.
        name (str): 關聯類型名稱（如 synonym, collocation, travel 等）。
            Relation type name (e.g. synonym, collocation, travel).
        created_at (datetime | None): 建立時間。Creation timestamp.
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
