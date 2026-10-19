"""llm_routes → capability_routes（能力路由計畫 R1）

把 LLM 用途設定搬到能力路由的新表：每列多一個 ``capability``（既有的列為 ``llm``），
``endpoint`` 改名為 ``provider``，``model`` 可為空，唯一鍵改為 ``(route_key, capability)``。

每次啟動都會先跑 ``create_all``（見 README），所以部署後執行本遷移時，``capability_routes``
多半已經被建好、而且是空的。每一步都先檢查再做：表已存在就不建，已搬過的列不重搬，
``llm_routes`` 不存在（從沒建過，或已搬完刪除）就跳過搬移。重複執行結果相同。

Moves the LLM route settings into ``capability_routes``. Every step is
guarded because startup's ``create_all`` usually created the (empty) new
table before this migration runs; running it twice is harmless.

Revision ID: c3a9e5d1f7b2
Revises: a01287171364
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c3a9e5d1f7b2'
down_revision: Union[str, None] = 'a01287171364'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    """目前資料庫中的資料表名稱。Table names currently in the database."""
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if 'capability_routes' not in tables:
        op.create_table('capability_routes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('route_key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('capability', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('model', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column('effort', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_capability_routes')),
        sa.UniqueConstraint('route_key', 'capability', name=op.f('uq_capability_routes_route_key'))
        )
        with op.batch_alter_table('capability_routes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_capability_routes_route_key'), ['route_key'], unique=False)

    if 'llm_routes' in tables:
        # 只搬還沒搬過的列（同一個用途鍵已有 llm 列就保留新表的值）
        op.execute(
            "INSERT INTO capability_routes "
            "(route_key, capability, provider, model, effort, updated_by, created_at, updated_at) "
            "SELECT l.route_key, 'llm', l.endpoint, l.model, l.effort, l.updated_by, l.created_at, l.updated_at "
            "FROM llm_routes l "
            "WHERE NOT EXISTS (SELECT 1 FROM capability_routes c "
            "WHERE c.route_key = l.route_key AND c.capability = 'llm')"
        )
        op.drop_table('llm_routes')


def downgrade() -> None:
    tables = _tables()
    if 'llm_routes' not in tables:
        op.create_table('llm_routes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('route_key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('endpoint', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('model', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('effort', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_routes'))
        )
        with op.batch_alter_table('llm_routes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_llm_routes_route_key'), ['route_key'], unique=True)

    if 'capability_routes' in tables:
        # 舊表只能存 LLM：其他能力的列在降級時捨棄
        op.execute(
            "INSERT INTO llm_routes (route_key, endpoint, model, effort, updated_by, created_at, updated_at) "
            "SELECT c.route_key, c.provider, c.model, c.effort, c.updated_by, c.created_at, c.updated_at "
            "FROM capability_routes c "
            "WHERE c.capability = 'llm' AND c.model IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM llm_routes l WHERE l.route_key = c.route_key)"
        )
        op.drop_table('capability_routes')
