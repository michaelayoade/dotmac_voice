"""billing schema

Revision ID: 002_billing
Revises: 799a0ecebdd4
Create Date: 2026-02-16 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "002_billing"
down_revision = "799a0ecebdd4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Products
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Prices
    op.create_table(
        "prices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_amount", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("one_time", "recurring", name="pricetype"),
            nullable=False,
        ),
        sa.Column(
            "billing_scheme",
            sa.Enum("per_unit", "tiered", name="billingscheme"),
            nullable=True,
        ),
        sa.Column(
            "recurring_interval",
            sa.Enum("day", "week", "month", "year", name="recurringinterval"),
            nullable=True,
        ),
        sa.Column("recurring_interval_count", sa.Integer(), nullable=True),
        sa.Column("trial_period_days", sa.Integer(), nullable=True),
        sa.Column("tiers_json", sa.JSON(), nullable=True),
        sa.Column("lookup_key", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lookup_key", name="uq_prices_lookup_key"),
    )
    op.create_index("ix_prices_product_id", "prices", ["product_id"])

    # Customers
    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("balance", sa.Integer(), nullable=True),
        sa.Column("tax_id", sa.String(length=80), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id"),
    )

    # Subscriptions
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "incomplete",
                "trialing",
                "active",
                "past_due",
                "canceled",
                "unpaid",
                "paused",
                name="subscriptionstatus",
            ),
            nullable=True,
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True),
        sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscriptions_customer_id", "subscriptions", ["customer_id"])

    # Subscription Items
    op.create_table(
        "subscription_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("price_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["price_id"], ["prices.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id", "price_id", name="uq_subscription_items_sub_price"
        ),
    )
    op.create_index(
        "ix_subscription_items_subscription_id",
        "subscription_items",
        ["subscription_id"],
    )
    op.create_index(
        "ix_subscription_items_price_id", "subscription_items", ["price_id"]
    )

    # Invoices
    op.create_table(
        "invoices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("number", sa.String(length=80), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft", "open", "paid", "void", "uncollectible", name="invoicestatus"
            ),
            nullable=True,
        ),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("subtotal", sa.Integer(), nullable=True),
        sa.Column("tax", sa.Integer(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("amount_due", sa.Integer(), nullable=True),
        sa.Column("amount_paid", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_invoices_number"),
    )
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_index("ix_invoices_subscription_id", "invoices", ["subscription_id"])

    # Invoice Items
    op.create_table(
        "invoice_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("price_id", sa.UUID(), nullable=True),
        sa.Column("subscription_item_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("unit_amount", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["price_id"], ["prices.id"]),
        sa.ForeignKeyConstraint(["subscription_item_id"], ["subscription_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_items_invoice_id", "invoice_items", ["invoice_id"])
    op.create_index("ix_invoice_items_price_id", "invoice_items", ["price_id"])
    op.create_index(
        "ix_invoice_items_subscription_item_id",
        "invoice_items",
        ["subscription_item_id"],
    )

    # Payment Methods
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "card", "bank_account", "wallet", "other", name="paymentmethodtype"
            ),
            nullable=False,
        ),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_methods_customer_id", "payment_methods", ["customer_id"]
    )

    # Payment Intents
    op.create_table(
        "payment_intents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("payment_method_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "requires_payment_method",
                "requires_confirmation",
                "processing",
                "succeeded",
                "canceled",
                "requires_action",
                name="paymentintentstatus",
            ),
            nullable=True,
        ),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_methods.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_intents_customer_id", "payment_intents", ["customer_id"]
    )
    op.create_index("ix_payment_intents_invoice_id", "payment_intents", ["invoice_id"])
    op.create_index(
        "ix_payment_intents_payment_method_id", "payment_intents", ["payment_method_id"]
    )

    # Usage Records
    op.create_table(
        "usage_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscription_item_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.Enum("increment", "set", name="usageaction"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subscription_item_id"], ["subscription_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_usage_records_idempotency_key"),
    )
    op.create_index(
        "ix_usage_records_subscription_item_id",
        "usage_records",
        ["subscription_item_id"],
    )

    # Coupons
    op.create_table(
        "coupons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("percent_off", sa.Integer(), nullable=True),
        sa.Column("amount_off", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "duration",
            sa.Enum("once", "repeating", "forever", name="couponduration"),
            nullable=False,
        ),
        sa.Column("duration_in_months", sa.Integer(), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("times_redeemed", sa.Integer(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=True),
        sa.Column("redeem_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_coupons_code"),
    )

    # Discounts
    op.create_table(
        "discounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("coupon_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discounts_coupon_id", "discounts", ["coupon_id"])
    op.create_index("ix_discounts_customer_id", "discounts", ["customer_id"])
    op.create_index("ix_discounts_subscription_id", "discounts", ["subscription_id"])

    # Entitlements
    op.create_table(
        "entitlements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("feature_key", sa.String(length=120), nullable=False),
        sa.Column(
            "value_type",
            sa.Enum(
                "boolean", "numeric", "string", "unlimited", name="entitlementvaluetype"
            ),
            nullable=False,
        ),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_numeric", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "feature_key", name="uq_entitlements_product_feature"
        ),
    )
    op.create_index("ix_entitlements_product_id", "entitlements", ["product_id"])

    # Webhook Events
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "processed", "failed", name="webhookeventstatus"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_webhook_events_event_id"),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")

    op.drop_index("ix_entitlements_product_id", table_name="entitlements")
    op.drop_table("entitlements")

    op.drop_index("ix_discounts_subscription_id", table_name="discounts")
    op.drop_index("ix_discounts_customer_id", table_name="discounts")
    op.drop_index("ix_discounts_coupon_id", table_name="discounts")
    op.drop_table("discounts")

    op.drop_table("coupons")

    op.drop_index("ix_usage_records_subscription_item_id", table_name="usage_records")
    op.drop_table("usage_records")

    op.drop_index("ix_payment_intents_payment_method_id", table_name="payment_intents")
    op.drop_index("ix_payment_intents_invoice_id", table_name="payment_intents")
    op.drop_index("ix_payment_intents_customer_id", table_name="payment_intents")
    op.drop_table("payment_intents")

    op.drop_index("ix_payment_methods_customer_id", table_name="payment_methods")
    op.drop_table("payment_methods")

    op.drop_index("ix_invoice_items_subscription_item_id", table_name="invoice_items")
    op.drop_index("ix_invoice_items_price_id", table_name="invoice_items")
    op.drop_index("ix_invoice_items_invoice_id", table_name="invoice_items")
    op.drop_table("invoice_items")

    op.drop_index("ix_invoices_subscription_id", table_name="invoices")
    op.drop_index("ix_invoices_customer_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("ix_subscription_items_price_id", table_name="subscription_items")
    op.drop_index(
        "ix_subscription_items_subscription_id", table_name="subscription_items"
    )
    op.drop_table("subscription_items")

    op.drop_index("ix_subscriptions_customer_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_table("customers")

    op.drop_index("ix_prices_product_id", table_name="prices")
    op.drop_table("prices")

    op.drop_table("products")

    for enum_name in [
        "webhookeventstatus",
        "entitlementvaluetype",
        "couponduration",
        "usageaction",
        "paymentintentstatus",
        "paymentmethodtype",
        "invoicestatus",
        "subscriptionstatus",
        "recurringinterval",
        "billingscheme",
        "pricetype",
    ]:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
