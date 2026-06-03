"""
SQLModel MetaData 顯式約束命名規範。

此模組必須在所有 Table Model 被 import 之前載入。

為什麼需要這個設定：
    SQLite 與 MySQL 對約束（索引、唯一鍵、外鍵）的自動命名規則不同。
    若不統一命名，在使用 Alembic 從 SQLite 遷移至 MySQL 時，
    自動生成的 migration 會因為約束名稱不匹配而崩潰。
    透過顯式命名規範，確保所有約束名稱在兩個引擎間完全一致。
"""

from sqlalchemy import MetaData
from sqlmodel import SQLModel

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# 覆蓋 SQLModel 預設的 MetaData，綁定命名規範
# 此行必須在任何 table=True 的 Model 被定義之前執行
SQLModel.metadata = MetaData(naming_convention=convention)
