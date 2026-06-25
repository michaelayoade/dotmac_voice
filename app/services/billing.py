import logging
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.billing import (
    Coupon,
    Customer,
    Discount,
    Entitlement,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentMethodType,
    Price,
    PriceType,
    Product,
    Subscription,
    SubscriptionItem,
    SubscriptionStatus,
    UsageRecord,
    WebhookEvent,
    WebhookEventStatus,
)
from app.schemas.billing import (
    CouponCreate,
    CouponUpdate,
    CustomerCreate,
    CustomerUpdate,
    DiscountCreate,
    EntitlementCreate,
    EntitlementUpdate,
    InvoiceCreate,
    InvoiceItemCreate,
    InvoiceItemUpdate,
    InvoiceUpdate,
    PaymentIntentCreate,
    PaymentIntentUpdate,
    PaymentMethodCreate,
    PaymentMethodUpdate,
    PriceCreate,
    PriceUpdate,
    ProductCreate,
    ProductUpdate,
    SubscriptionCreate,
    SubscriptionItemCreate,
    SubscriptionItemUpdate,
    SubscriptionUpdate,
    UsageRecordCreate,
    WebhookEventCreate,
    WebhookEventUpdate,
)
from app.services.common import coerce_uuid
from app.services.exceptions import NotFoundError
from app.services.query_utils import apply_ordering, apply_pagination, validate_enum
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _require(item: Any | None, message: str) -> Any:
    if not item:
        raise NotFoundError(message)
    return item


def _get(db: Session, model: type, item_id: str, message: str) -> Any:
    return _require(db.get(model, coerce_uuid(item_id)), message)


def _require_exists(db: Session, model: type, item_id: str, message: str) -> None:
    _get(db, model, str(item_id), message)


def _persist(db: Session, item: Any) -> Any:
    db.add(item)
    db.flush()
    db.refresh(item)
    return item


def _flush(db: Session, item: Any) -> Any:
    db.flush()
    db.refresh(item)
    return item


def _list(
    db: Session,
    query: Select,
    order_by: str,
    order_dir: str,
    allowed_columns: dict[str, Any],
    limit: int,
    offset: int,
) -> tuple[list[Any], int]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    ordered = apply_ordering(query, order_by, order_dir, allowed_columns)
    items = list(db.scalars(apply_pagination(ordered, limit, offset)).all())
    return items, total or 0


# ── Products ─────────────────────────────────────────────


class Products(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProductCreate) -> Product:
        item = Product(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Product: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Product:
        return _get(db, Product, item_id, "Product not found")

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Product], int]:
        query = select(Product)
        if is_active is not None:
            query = query.where(Product.is_active == is_active)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Product.created_at, "name": Product.name},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: ProductUpdate) -> Product:
        item = _get(db, Product, item_id, "Product not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Product.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Product, item_id, "Product not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", Product.__name__, item.id)


# ── Prices ───────────────────────────────────────────────


class Prices(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PriceCreate) -> Price:
        _require_exists(db, Product, str(payload.product_id), "Product not found")
        item = Price(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Price: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Price:
        return _get(db, Price, item_id, "Price not found")

    @staticmethod
    def list(
        db: Session,
        product_id: str | None,
        type: str | None,
        currency: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Price], int]:
        query = select(Price)
        if product_id:
            query = query.where(Price.product_id == coerce_uuid(product_id))
        if type:
            query = query.where(Price.type == validate_enum(type, PriceType, "type"))
        if currency:
            query = query.where(Price.currency == currency)
        if is_active is not None:
            query = query.where(Price.is_active == is_active)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Price.created_at, "unit_amount": Price.unit_amount},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: PriceUpdate) -> Price:
        item = _get(db, Price, item_id, "Price not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Price.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Price, item_id, "Price not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", Price.__name__, item.id)


# ── Customers ────────────────────────────────────────────


class Customers(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: CustomerCreate) -> Customer:
        item = Customer(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Customer: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Customer:
        return _get(db, Customer, item_id, "Customer not found")

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        email: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Customer], int]:
        query = select(Customer)
        if person_id:
            query = query.where(Customer.person_id == coerce_uuid(person_id))
        if email:
            query = query.where(Customer.email.ilike(f"%{email}%"))
        if is_active is not None:
            query = query.where(Customer.is_active == is_active)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Customer.created_at, "name": Customer.name},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: CustomerUpdate) -> Customer:
        item = _get(db, Customer, item_id, "Customer not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Customer.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Customer, item_id, "Customer not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", Customer.__name__, item.id)


# ── Subscriptions ────────────────────────────────────────


class Subscriptions(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SubscriptionCreate) -> Subscription:
        _require_exists(db, Customer, str(payload.customer_id), "Customer not found")
        item = Subscription(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Subscription: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Subscription:
        return _get(db, Subscription, item_id, "Subscription not found")

    @staticmethod
    def list(
        db: Session,
        customer_id: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Subscription], int]:
        query = select(Subscription)
        if customer_id:
            query = query.where(Subscription.customer_id == coerce_uuid(customer_id))
        if status:
            query = query.where(
                Subscription.status
                == validate_enum(status, SubscriptionStatus, "status")
            )
        if is_active is not None:
            query = query.where(Subscription.is_active == is_active)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Subscription.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: SubscriptionUpdate) -> Subscription:
        item = _get(db, Subscription, item_id, "Subscription not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Subscription.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Subscription, item_id, "Subscription not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", Subscription.__name__, item.id)


# ── Subscription Items ───────────────────────────────────


class SubscriptionItems(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SubscriptionItemCreate) -> SubscriptionItem:
        _require_exists(
            db, Subscription, str(payload.subscription_id), "Subscription not found"
        )
        _require_exists(db, Price, str(payload.price_id), "Price not found")
        item = SubscriptionItem(**payload.model_dump())
        _persist(db, item)
        logger.info("Created SubscriptionItem: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> SubscriptionItem:
        return _get(db, SubscriptionItem, item_id, "Subscription item not found")

    @staticmethod
    def list(
        db: Session,
        subscription_id: str | None,
        price_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[SubscriptionItem], int]:
        query = select(SubscriptionItem).options(selectinload(SubscriptionItem.price))
        if subscription_id:
            query = query.where(
                SubscriptionItem.subscription_id == coerce_uuid(subscription_id)
            )
        if price_id:
            query = query.where(SubscriptionItem.price_id == coerce_uuid(price_id))
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": SubscriptionItem.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(
        db: Session, item_id: str, payload: SubscriptionItemUpdate
    ) -> SubscriptionItem:
        item = _get(db, SubscriptionItem, item_id, "Subscription item not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", SubscriptionItem.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, SubscriptionItem, item_id, "Subscription item not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", SubscriptionItem.__name__, item.id)


# ── Invoices ─────────────────────────────────────────────


class Invoices(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InvoiceCreate) -> Invoice:
        _require_exists(db, Customer, str(payload.customer_id), "Customer not found")
        if payload.subscription_id:
            _require_exists(
                db, Subscription, str(payload.subscription_id), "Subscription not found"
            )
        item = Invoice(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Invoice: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Invoice:
        return _get(db, Invoice, item_id, "Invoice not found")

    @staticmethod
    def list(
        db: Session,
        customer_id: str | None,
        subscription_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Invoice], int]:
        query = select(Invoice).options(selectinload(Invoice.customer))
        if customer_id:
            query = query.where(Invoice.customer_id == coerce_uuid(customer_id))
        if subscription_id:
            query = query.where(Invoice.subscription_id == coerce_uuid(subscription_id))
        if status:
            query = query.where(
                Invoice.status == validate_enum(status, InvoiceStatus, "status")
            )
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Invoice.created_at, "total": Invoice.total},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: InvoiceUpdate) -> Invoice:
        item = _get(db, Invoice, item_id, "Invoice not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Invoice.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Invoice, item_id, "Invoice not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", Invoice.__name__, item.id)


# ── Invoice Items ────────────────────────────────────────


class InvoiceItems(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InvoiceItemCreate) -> InvoiceItem:
        _require_exists(db, Invoice, str(payload.invoice_id), "Invoice not found")
        if payload.price_id:
            _require_exists(db, Price, str(payload.price_id), "Price not found")
        if payload.subscription_item_id:
            _require_exists(
                db,
                SubscriptionItem,
                str(payload.subscription_item_id),
                "Subscription item not found",
            )
        item = InvoiceItem(**payload.model_dump())
        _persist(db, item)
        logger.info("Created InvoiceItem: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> InvoiceItem:
        return _get(db, InvoiceItem, item_id, "Invoice item not found")

    @staticmethod
    def list(
        db: Session,
        invoice_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[InvoiceItem], int]:
        query = select(InvoiceItem)
        if invoice_id:
            query = query.where(InvoiceItem.invoice_id == coerce_uuid(invoice_id))
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": InvoiceItem.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: InvoiceItemUpdate) -> InvoiceItem:
        item = _get(db, InvoiceItem, item_id, "Invoice item not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", InvoiceItem.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, InvoiceItem, item_id, "Invoice item not found")
        db.delete(item)
        db.flush()
        logger.info("Deleted %s: %s", InvoiceItem.__name__, item_id)


# ── Payment Methods ──────────────────────────────────────


class PaymentMethods(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PaymentMethodCreate) -> PaymentMethod:
        _require_exists(db, Customer, str(payload.customer_id), "Customer not found")
        item = PaymentMethod(**payload.model_dump())
        _persist(db, item)
        logger.info("Created PaymentMethod: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> PaymentMethod:
        return _get(db, PaymentMethod, item_id, "Payment method not found")

    @staticmethod
    def list(
        db: Session,
        customer_id: str | None,
        type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[PaymentMethod], int]:
        query = select(PaymentMethod)
        if customer_id:
            query = query.where(PaymentMethod.customer_id == coerce_uuid(customer_id))
        if type:
            query = query.where(
                PaymentMethod.type == validate_enum(type, PaymentMethodType, "type")
            )
        if is_active is not None:
            query = query.where(PaymentMethod.is_active == is_active)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": PaymentMethod.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(
        db: Session, item_id: str, payload: PaymentMethodUpdate
    ) -> PaymentMethod:
        item = _get(db, PaymentMethod, item_id, "Payment method not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", PaymentMethod.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, PaymentMethod, item_id, "Payment method not found")
        item.is_active = False
        _flush(db, item)
        logger.info("Soft-deleted %s: %s", PaymentMethod.__name__, item.id)


# ── Payment Intents ──────────────────────────────────────


class PaymentIntents(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PaymentIntentCreate) -> PaymentIntent:
        _require_exists(db, Customer, str(payload.customer_id), "Customer not found")
        if payload.invoice_id:
            _require_exists(db, Invoice, str(payload.invoice_id), "Invoice not found")
        if payload.payment_method_id:
            _require_exists(
                db,
                PaymentMethod,
                str(payload.payment_method_id),
                "Payment method not found",
            )
        item = PaymentIntent(**payload.model_dump())
        _persist(db, item)
        logger.info("Created PaymentIntent: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> PaymentIntent:
        return _get(db, PaymentIntent, item_id, "Payment intent not found")

    @staticmethod
    def list(
        db: Session,
        customer_id: str | None,
        invoice_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[PaymentIntent], int]:
        query = select(PaymentIntent)
        if customer_id:
            query = query.where(PaymentIntent.customer_id == coerce_uuid(customer_id))
        if invoice_id:
            query = query.where(PaymentIntent.invoice_id == coerce_uuid(invoice_id))
        if status:
            query = query.where(
                PaymentIntent.status
                == validate_enum(status, PaymentIntentStatus, "status")
            )
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": PaymentIntent.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(
        db: Session, item_id: str, payload: PaymentIntentUpdate
    ) -> PaymentIntent:
        item = _get(db, PaymentIntent, item_id, "Payment intent not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", PaymentIntent.__name__, item.id)
        return item


# ── Usage Records ────────────────────────────────────────


class UsageRecords(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: UsageRecordCreate) -> UsageRecord:
        _require_exists(
            db,
            SubscriptionItem,
            str(payload.subscription_item_id),
            "Subscription item not found",
        )
        item = UsageRecord(**payload.model_dump())
        _persist(db, item)
        logger.info("Created UsageRecord: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> UsageRecord:
        return _get(db, UsageRecord, item_id, "Usage record not found")

    @staticmethod
    def list(
        db: Session,
        subscription_item_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[UsageRecord], int]:
        query = select(UsageRecord)
        if subscription_item_id:
            query = query.where(
                UsageRecord.subscription_item_id == coerce_uuid(subscription_item_id)
            )
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {
                "created_at": UsageRecord.created_at,
                "recorded_at": UsageRecord.recorded_at,
            },
            limit,
            offset,
        )


# ── Coupons ──────────────────────────────────────────────


class Coupons(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: CouponCreate) -> Coupon:
        item = Coupon(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Coupon: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Coupon:
        return _get(db, Coupon, item_id, "Coupon not found")

    @staticmethod
    def list(
        db: Session,
        valid: bool | None,
        code: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Coupon], int]:
        query = select(Coupon)
        if valid is not None:
            query = query.where(Coupon.valid == valid)
        if code:
            query = query.where(Coupon.code == code)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Coupon.created_at, "name": Coupon.name},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: CouponUpdate) -> Coupon:
        item = _get(db, Coupon, item_id, "Coupon not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Coupon.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Coupon, item_id, "Coupon not found")
        item.valid = False
        _flush(db, item)
        logger.info("Soft-deleted Coupon: %s", item.id)


# ── Discounts ────────────────────────────────────────────


class Discounts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: DiscountCreate) -> Discount:
        _require_exists(db, Coupon, str(payload.coupon_id), "Coupon not found")
        if payload.customer_id:
            _require_exists(
                db, Customer, str(payload.customer_id), "Customer not found"
            )
        if payload.subscription_id:
            _require_exists(
                db, Subscription, str(payload.subscription_id), "Subscription not found"
            )
        item = Discount(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Discount: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Discount:
        return _get(db, Discount, item_id, "Discount not found")

    @staticmethod
    def list(
        db: Session,
        customer_id: str | None,
        subscription_id: str | None,
        coupon_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Discount], int]:
        query = select(Discount)
        if customer_id:
            query = query.where(Discount.customer_id == coerce_uuid(customer_id))
        if subscription_id:
            query = query.where(
                Discount.subscription_id == coerce_uuid(subscription_id)
            )
        if coupon_id:
            query = query.where(Discount.coupon_id == coerce_uuid(coupon_id))
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Discount.created_at},
            limit,
            offset,
        )

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Discount, item_id, "Discount not found")
        db.delete(item)
        db.flush()
        logger.info("Deleted %s: %s", Discount.__name__, item_id)


# ── Entitlements ─────────────────────────────────────────


class Entitlements(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: EntitlementCreate) -> Entitlement:
        _require_exists(db, Product, str(payload.product_id), "Product not found")
        item = Entitlement(**payload.model_dump())
        _persist(db, item)
        logger.info("Created Entitlement: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> Entitlement:
        return _get(db, Entitlement, item_id, "Entitlement not found")

    @staticmethod
    def list(
        db: Session,
        product_id: str | None,
        feature_key: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Entitlement], int]:
        query = select(Entitlement)
        if product_id:
            query = query.where(Entitlement.product_id == coerce_uuid(product_id))
        if feature_key:
            query = query.where(Entitlement.feature_key == feature_key)
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": Entitlement.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: EntitlementUpdate) -> Entitlement:
        item = _get(db, Entitlement, item_id, "Entitlement not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", Entitlement.__name__, item.id)
        return item

    @staticmethod
    def delete(db: Session, item_id: str) -> None:
        item = _get(db, Entitlement, item_id, "Entitlement not found")
        db.delete(item)
        db.flush()
        logger.info("Deleted %s: %s", Entitlement.__name__, item_id)


# ── Webhook Events ───────────────────────────────────────


class WebhookEvents(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WebhookEventCreate) -> WebhookEvent:
        item = WebhookEvent(**payload.model_dump())
        _persist(db, item)
        logger.info("Created WebhookEvent: %s", item.id)
        return item

    @staticmethod
    def get(db: Session, item_id: str) -> WebhookEvent:
        return _get(db, WebhookEvent, item_id, "Webhook event not found")

    @staticmethod
    def list(
        db: Session,
        provider: str | None,
        event_type: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> tuple[list[WebhookEvent], int]:
        query = select(WebhookEvent)
        if provider:
            query = query.where(WebhookEvent.provider == provider)
        if event_type:
            query = query.where(WebhookEvent.event_type == event_type)
        if status:
            query = query.where(
                WebhookEvent.status
                == validate_enum(status, WebhookEventStatus, "status")
            )
        return _list(
            db,
            query,
            order_by,
            order_dir,
            {"created_at": WebhookEvent.created_at},
            limit,
            offset,
        )

    @staticmethod
    def update(db: Session, item_id: str, payload: WebhookEventUpdate) -> WebhookEvent:
        item = _get(db, WebhookEvent, item_id, "Webhook event not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        _flush(db, item)
        logger.info("Updated %s: %s", WebhookEvent.__name__, item.id)
        return item


# ── Singletons ───────────────────────────────────────────

products = Products()
prices = Prices()
customers = Customers()
subscriptions = Subscriptions()
subscription_items = SubscriptionItems()
invoices = Invoices()
invoice_items = InvoiceItems()
payment_methods = PaymentMethods()
payment_intents = PaymentIntents()
usage_records = UsageRecords()
coupons = Coupons()
discounts = Discounts()
entitlements = Entitlements()
webhook_events = WebhookEvents()
