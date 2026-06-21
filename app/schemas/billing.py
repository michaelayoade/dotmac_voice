from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ── Product ──────────────────────────────────────────────


class ProductBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ── Price ────────────────────────────────────────────────


class PriceBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    unit_amount: int = Field(ge=0)
    type: Literal["one_time", "recurring"]
    billing_scheme: Literal["per_unit", "tiered"] = "per_unit"
    recurring_interval: Literal["day", "week", "month", "year"] | None = None
    recurring_interval_count: int = Field(default=1, ge=1)
    trial_period_days: int | None = Field(default=None, ge=0)
    tiers_json: dict | None = None
    lookup_key: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PriceCreate(PriceBase):
    pass


class PriceUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    unit_amount: int | None = Field(default=None, ge=0)
    type: Literal["one_time", "recurring"] | None = None
    billing_scheme: Literal["per_unit", "tiered"] | None = None
    recurring_interval: Literal["day", "week", "month", "year"] | None = None
    recurring_interval_count: int | None = Field(default=None, ge=1)
    trial_period_days: int | None = Field(default=None, ge=0)
    tiers_json: dict | None = None
    lookup_key: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PriceRead(PriceBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    type: str  # type: ignore[assignment]
    billing_scheme: str  # type: ignore[assignment]
    recurring_interval: str | None = None  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Customer ─────────────────────────────────────────────


class CustomerBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    person_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(max_length=255)
    currency: str = Field(default="usd", max_length=3)
    balance: int = 0
    tax_id: str | None = Field(default=None, max_length=80)
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    person_id: UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    balance: int | None = None
    tax_id: str | None = Field(default=None, max_length=80)
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class CustomerRead(CustomerBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ── Subscription ─────────────────────────────────────────


class SubscriptionBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    customer_id: UUID
    status: Literal[
        "incomplete", "trialing", "active", "past_due", "canceled", "unpaid", "paused"
    ] = "incomplete"
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    cancel_at_period_end: bool = False
    cancel_at: datetime | None = None
    canceled_at: datetime | None = None
    ended_at: datetime | None = None
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: (
        Literal[
            "incomplete",
            "trialing",
            "active",
            "past_due",
            "canceled",
            "unpaid",
            "paused",
        ]
        | None
    ) = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    cancel_at_period_end: bool | None = None
    cancel_at: datetime | None = None
    canceled_at: datetime | None = None
    ended_at: datetime | None = None
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class SubscriptionRead(SubscriptionBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    status: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Subscription Item ────────────────────────────────────


class SubscriptionItemBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subscription_id: UUID
    price_id: UUID
    quantity: int = Field(default=1, ge=1)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class SubscriptionItemCreate(SubscriptionItemBase):
    pass


class SubscriptionItemUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    quantity: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class SubscriptionItemRead(SubscriptionItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ── Invoice ──────────────────────────────────────────────


class InvoiceBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    customer_id: UUID
    subscription_id: UUID | None = None
    number: str | None = Field(default=None, max_length=80)
    status: Literal["draft", "open", "paid", "void", "uncollectible"] = "draft"
    currency: str = Field(default="usd", min_length=3, max_length=3)
    subtotal: int = Field(default=0, ge=0)
    tax: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    amount_due: int = Field(default=0, ge=0)
    amount_paid: int = Field(default=0, ge=0)
    due_date: datetime | None = None
    paid_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str | None = Field(default=None, max_length=80)
    status: Literal["draft", "open", "paid", "void", "uncollectible"] | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: int | None = Field(default=None, ge=0)
    tax: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    amount_due: int | None = Field(default=None, ge=0)
    amount_paid: int | None = Field(default=None, ge=0)
    due_date: datetime | None = None
    paid_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    external_id: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class InvoiceRead(InvoiceBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    status: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Invoice Item ─────────────────────────────────────────


class InvoiceItemBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invoice_id: UUID
    price_id: UUID | None = None
    subscription_item_id: UUID | None = None
    description: str | None = None
    quantity: int = Field(default=1, ge=1)
    unit_amount: int = Field(default=0, ge=0)
    amount: int = Field(default=0, ge=0)
    period_start: datetime | None = None
    period_end: datetime | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    description: str | None = None
    quantity: int | None = Field(default=None, ge=1)
    unit_amount: int | None = Field(default=None, ge=0)
    amount: int | None = Field(default=None, ge=0)
    period_start: datetime | None = None
    period_end: datetime | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class InvoiceItemRead(InvoiceItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ── Payment Method ───────────────────────────────────────


class PaymentMethodBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    customer_id: UUID
    type: Literal["card", "bank_account", "wallet", "other"]
    details: dict | None = None
    is_default: bool = False
    is_active: bool = True
    external_id: str | None = Field(default=None, max_length=255)


class PaymentMethodCreate(PaymentMethodBase):
    pass


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["card", "bank_account", "wallet", "other"] | None = None
    details: dict | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    external_id: str | None = Field(default=None, max_length=255)


class PaymentMethodRead(PaymentMethodBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    type: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Payment Intent ───────────────────────────────────────


class PaymentIntentBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    customer_id: UUID
    invoice_id: UUID | None = None
    payment_method_id: UUID | None = None
    amount: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3)
    status: Literal[
        "requires_payment_method",
        "requires_confirmation",
        "processing",
        "succeeded",
        "canceled",
        "requires_action",
    ] = "requires_payment_method"
    failure_code: str | None = Field(default=None, max_length=80)
    failure_message: str | None = None
    external_id: str | None = Field(default=None, max_length=255)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PaymentIntentCreate(PaymentIntentBase):
    pass


class PaymentIntentUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: (
        Literal[
            "requires_payment_method",
            "requires_confirmation",
            "processing",
            "succeeded",
            "canceled",
            "requires_action",
        ]
        | None
    ) = None
    payment_method_id: UUID | None = None
    failure_code: str | None = Field(default=None, max_length=80)
    failure_message: str | None = None
    external_id: str | None = Field(default=None, max_length=255)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class PaymentIntentRead(PaymentIntentBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    status: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Usage Record ─────────────────────────────────────────


class UsageRecordBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subscription_item_id: UUID
    quantity: int = Field(ge=0)
    action: Literal["increment", "set"] = "increment"
    recorded_at: datetime | None = None
    idempotency_key: str = Field(max_length=255)


class UsageRecordCreate(UsageRecordBase):
    pass


class UsageRecordRead(UsageRecordBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    action: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Coupon ───────────────────────────────────────────────


class CouponBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=80)
    percent_off: int | None = Field(default=None, ge=0, le=100)
    amount_off: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    duration: Literal["once", "repeating", "forever"]
    duration_in_months: int | None = Field(default=None, ge=1)
    max_redemptions: int | None = Field(default=None, ge=0)
    times_redeemed: int = Field(default=0, ge=0)
    valid: bool = True
    redeem_by: datetime | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class CouponCreate(CouponBase):
    pass


class CouponUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str | None = Field(default=None, max_length=255)
    percent_off: int | None = Field(default=None, ge=0, le=100)
    amount_off: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    duration: Literal["once", "repeating", "forever"] | None = None
    duration_in_months: int | None = Field(default=None, ge=1)
    max_redemptions: int | None = Field(default=None, ge=0)
    times_redeemed: int | None = Field(default=None, ge=0)
    valid: bool | None = None
    redeem_by: datetime | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class CouponRead(CouponBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    duration: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Discount ─────────────────────────────────────────────


class DiscountBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    coupon_id: UUID
    customer_id: UUID | None = None
    subscription_id: UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    external_id: str | None = Field(default=None, max_length=255)


class DiscountCreate(DiscountBase):
    pass


class DiscountRead(DiscountBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ── Entitlement ──────────────────────────────────────────


class EntitlementBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_id: UUID
    feature_key: str = Field(min_length=1, max_length=120)
    value_type: Literal["boolean", "numeric", "string", "unlimited"]
    value_text: str | None = None
    value_numeric: int | None = Field(default=None, ge=0)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class EntitlementCreate(EntitlementBase):
    pass


class EntitlementUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    feature_key: str | None = Field(default=None, max_length=120)
    value_type: Literal["boolean", "numeric", "string", "unlimited"] | None = None
    value_text: str | None = None
    value_numeric: int | None = Field(default=None, ge=0)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class EntitlementRead(EntitlementBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    value_type: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime


# ── Webhook Event ────────────────────────────────────────


class WebhookEventBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    provider: str = Field(max_length=80)
    event_type: str = Field(max_length=120)
    event_id: str = Field(max_length=255)
    payload: dict | None = None
    status: Literal["pending", "processed", "failed"] = "pending"
    error_message: str | None = None
    processed_at: datetime | None = None


class WebhookEventCreate(WebhookEventBase):
    pass


class WebhookEventUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: Literal["pending", "processed", "failed"] | None = None
    error_message: str | None = None
    processed_at: datetime | None = None


class WebhookEventRead(WebhookEventBase):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, use_enum_values=True
    )
    id: UUID
    status: str  # type: ignore[assignment]
    created_at: datetime
    updated_at: datetime
