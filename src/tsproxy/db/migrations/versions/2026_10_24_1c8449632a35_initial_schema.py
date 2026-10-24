"""Первая схема: 8 таблиц бота (задача 03, схема утверждена владельцем 2026-10-24).

Revision ID: 1c8449632a35
Revises:
Create Date: 2026-10-24 16:49:32.985057+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1c8449632a35"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("proxy_account_uuid", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_extensions", sa.Integer(), nullable=False),
        sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bot_blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trial_extensions >= 0", name=op.f("ck_clients_trial_extensions_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("proxy_account_uuid", name=op.f("uq_clients_proxy_account_uuid")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_clients_telegram_id")),
    )
    op.create_table(
        "node_outages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_uuid", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compensated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compensated_by_admin_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_outages")),
        sa.UniqueConstraint(
            "node_uuid", "started_at", name=op.f("uq_node_outages_node_uuid_started_at")
        ),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "day_before",
                "hour_before",
                "grace",
                "trial_extended",
                "farewell",
                name="notificationkind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("related_expire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_notifications_client_id_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint(
            "client_id",
            "kind",
            "related_expire_at",
            name=op.f("uq_notifications_client_id_kind_related_expire_at"),
        ),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_sent_at"), ["sent_at"], unique=False)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("tariff_id", sa.String(length=32), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "paid",
                "access_granted",
                "access_failed",
                "manual_review",
                "canceled",
                "refunded",
                name="orderstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_provider", sa.String(length=32), nullable=True),
        sa.Column("payment_id", sa.String(length=128), nullable=True),
        sa.Column("payment_url", sa.String(length=2048), nullable=True),
        sa.Column("payment_url_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("paid_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_target_expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_attempts", sa.Integer(), nullable=False),
        sa.Column("next_grant_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("days > 0", name=op.f("ck_orders_days_positive")),
        sa.CheckConstraint(
            "grant_attempts >= 0", name=op.f("ck_orders_grant_attempts_non_negative")
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_orders_price_non_negative")),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_orders_client_id_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_orders_idempotency_key")),
        sa.UniqueConstraint(
            "payment_provider", "payment_id", name=op.f("uq_orders_payment_provider_payment_id")
        ),
    )
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_orders_client_id"), ["client_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_orders_next_grant_attempt_at"), ["next_grant_attempt_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_orders_status"), ["status"], unique=False)

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "closed", name="ticketstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("assignee_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_support_tickets_client_id_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_tickets")),
        sa.UniqueConstraint("topic_id", name=op.f("uq_support_tickets_topic_id")),
    )
    with op.batch_alter_table("support_tickets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_support_tickets_client_id"), ["client_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_support_tickets_status"), ["status"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("glue_key", sa.String(length=128), nullable=True),
        sa.Column("repeat_count", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("repeat_count >= 1", name=op.f("ck_events_repeat_count_positive")),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_events_client_id_clients")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_events_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_events_client_id"), ["client_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_glue_key"), ["glue_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_occurred_at"), ["occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_order_id"), ["order_id"], unique=False)

    op.create_table(
        "refund_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unused_days", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "requested",
                "approved",
                "rejected",
                "refunded",
                name="refundstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("decided_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_refund_requests_amount_non_negative")),
        sa.CheckConstraint(
            "unused_days >= 0", name=op.f("ck_refund_requests_unused_days_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_refund_requests_client_id_clients")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_refund_requests_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_requests")),
    )
    with op.batch_alter_table("refund_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_refund_requests_client_id"), ["client_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_refund_requests_order_id"), ["order_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_refund_requests_status"), ["status"], unique=False)

    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column(
            "author",
            sa.Enum(
                "client", "support", "note", name="messageauthor", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("file_id", sa.String(length=256), nullable=True),
        sa.Column("file_kind", sa.String(length=32), nullable=True),
        sa.Column("client_message_id", sa.BigInteger(), nullable=True),
        sa.Column("group_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["support_tickets.id"],
            name=op.f("fk_ticket_messages_ticket_id_support_tickets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_messages")),
    )
    with op.batch_alter_table("ticket_messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ticket_messages_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ticket_messages_ticket_id"), ["ticket_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("ticket_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ticket_messages_ticket_id"))
        batch_op.drop_index(batch_op.f("ix_ticket_messages_created_at"))

    op.drop_table("ticket_messages")
    with op.batch_alter_table("refund_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_refund_requests_status"))
        batch_op.drop_index(batch_op.f("ix_refund_requests_order_id"))
        batch_op.drop_index(batch_op.f("ix_refund_requests_client_id"))

    op.drop_table("refund_requests")
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_events_order_id"))
        batch_op.drop_index(batch_op.f("ix_events_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_events_kind"))
        batch_op.drop_index(batch_op.f("ix_events_glue_key"))
        batch_op.drop_index(batch_op.f("ix_events_client_id"))

    op.drop_table("events")
    with op.batch_alter_table("support_tickets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_support_tickets_status"))
        batch_op.drop_index(batch_op.f("ix_support_tickets_client_id"))

    op.drop_table("support_tickets")
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_orders_status"))
        batch_op.drop_index(batch_op.f("ix_orders_next_grant_attempt_at"))
        batch_op.drop_index(batch_op.f("ix_orders_client_id"))

    op.drop_table("orders")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_sent_at"))

    op.drop_table("notifications")
    op.drop_table("node_outages")
    op.drop_table("clients")
