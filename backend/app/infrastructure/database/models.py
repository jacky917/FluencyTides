"""SQLModel ORM table definitions of the website's settings, stored in the MySQL corpus database.

網站設定的 SQLModel ORM 資料表模型，存在 MySQL 語料庫。

The only table is ``cfg_capability_routes`` (:class:`CapabilityRouteSetting`). Its DDL lives in
``scripts/database/corpus_rebuild/schema.sql`` (the single source; created at startup by
:func:`app.infrastructure.database.corpus_schema.ensure_site_tables`); this model only describes the columns the
website reads and writes, and ``SQLModel.metadata.create_all`` is used only by tests on SQLite. The website's old
SQLite tables ``card_relations`` and ``relation_types`` are no longer defined here (card relations moved to the
corpus ``rel_links`` on 2026-11-01; the ``fluencytides.db`` file is kept untouched as a backup).

唯一的表是 ``cfg_capability_routes``（:class:`CapabilityRouteSetting`）。建表 SQL 在
``scripts/database/corpus_rebuild/schema.sql``（唯一來源；啟動時由
:func:`app.infrastructure.database.corpus_schema.ensure_site_tables` 建立）；這裡的模型只描述網站讀寫的欄位，
``SQLModel.metadata.create_all`` 只在測試用 SQLite 時使用。網站舊的 SQLite 表 ``card_relations``、
``relation_types`` 已不在這裡定義（卡片關聯 2026-11-01 改存語料庫的 ``rel_links``；``fluencytides.db`` 檔案
原樣保留當備份）。

Rules for the columns (so the model matches ``schema.sql`` on MySQL and still works on SQLite in tests):
1. Every str column declares max_length; indexed columns must never omit it.
2. Constraint names are managed centrally by the MetaData in conventions.py.
3. DateTime columns use sa_column + DateTime() (MySQL DATETIME has no zone), written as UTC by the website
   (default/onupdate=utc_now); server_default=func.now() only covers rows inserted by hand.
4. A single auto-increment PK; composite primary keys are forbidden.

欄位規則（讓模型和 MySQL 上的 ``schema.sql`` 一致，測試用 SQLite 時也能用）：
1. 所有 str 欄位一律指定 max_length，索引欄位絕對禁止省略。
2. 約束命名由 conventions.py 中的 MetaData 統一管理。
3. DateTime 使用 sa_column + DateTime()（MySQL DATETIME 不存時區），由網站寫入 UTC（default/onupdate=utc_now）；
   server_default=func.now() 只給手動 INSERT 的列用。
4. 單一自增 PK，禁止複合主鍵。
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, UniqueConstraint, func
from sqlmodel import Field, SQLModel

# 確保 conventions 已載入，約束名稱才會照命名規則產生
import app.infrastructure.database.conventions  # noqa: F401
from app.infrastructure.database.corpus_tables import CFG_CAPABILITY_ROUTES


def utc_now() -> datetime:
    """Return the current UTC time without zone info, the form MySQL ``DATETIME`` stores.

    回傳目前的 UTC 時間（不帶時區資訊），也就是 MySQL ``DATETIME`` 存的形式。

    MySQL's ``CURRENT_TIMESTAMP`` follows the server's time zone, so the website writes the timestamps itself
    instead of relying on the column default.

    MySQL 的 ``CURRENT_TIMESTAMP`` 依伺服器時區，所以時間戳由網站自己寫入，不靠欄位預設值。
    """
    return datetime.now(UTC).replace(tzinfo=None)


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
        created_at: the creation time, in UTC without zone info
            建立時間，UTC、不帶時區資訊
        updated_at: the last update time, in UTC without zone info
            最後修改時間，UTC、不帶時區資訊
    """

    __tablename__ = CFG_CAPABILITY_ROUTES
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

    # 時間由網站寫入 UTC（utc_now）；server_default 只給手動 INSERT 的列用，那時是 MySQL 伺服器的時區
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(),
            default=utc_now,
            server_default=func.now(),
            nullable=False,
        ),
    )

    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(),
            default=utc_now,
            onupdate=utc_now,
            server_default=func.now(),
            nullable=False,
        ),
    )
