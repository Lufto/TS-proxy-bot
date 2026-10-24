"""Убрать `clients.proxy_account_uuid`: аккаунт клиента находится по имени `tg_<id>`.

Колонку не заполнял и не читал ни один модуль; в Remnawave 3.x у аккаунта числовой id.
Удаление одобрено владельцем 2026-10-25.

Revision ID: 5b7e2d9a4c10
Revises: 1c8449632a35
Create Date: 2026-10-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b7e2d9a4c10"
down_revision: str | Sequence[str] | None = "1c8449632a35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as batch_op:
        batch_op.drop_constraint(op.f("uq_clients_proxy_account_uuid"), type_="unique")
        batch_op.drop_column("proxy_account_uuid")


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch_op:
        batch_op.add_column(sa.Column("proxy_account_uuid", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            op.f("uq_clients_proxy_account_uuid"), ["proxy_account_uuid"]
        )
