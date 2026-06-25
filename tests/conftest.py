import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import DateTime, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


# Create a test engine BEFORE any app imports
_test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# Create a mock for the app.db module that uses our test engine
class TestBase(DeclarativeBase):
    pass


_TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)


# Create TimestampMixin for test models
class TimestampMixin:
    """Mixin that adds created_at / updated_at columns to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# Create a mock db module
mock_db_module = ModuleType("app.db")
mock_db_module.Base = TestBase
mock_db_module.TimestampMixin = TimestampMixin
mock_db_module.SessionLocal = _TestSessionLocal
mock_db_module.get_engine = lambda: _test_engine


def _test_get_db():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


mock_db_module.get_db = _test_get_db

# Also mock app.config to prevent .env loading
mock_config_module = ModuleType("app.config")


class MockSettings:
    database_url = "sqlite+pysqlite:///:memory:"
    redis_url = "redis://localhost:6379/0"
    secret_key = "test-secret-key"
    db_pool_size = 5
    db_max_overflow = 10
    db_pool_timeout = 30
    db_pool_recycle = 1800
    avatar_upload_dir = "static/avatars"
    avatar_max_size_bytes = 2 * 1024 * 1024
    avatar_allowed_types = "image/jpeg,image/png,image/gif,image/webp"
    avatar_url_prefix = "/static/avatars"
    brand_name = "DotMac Voice"
    brand_tagline = "FastAPI starter"
    brand_logo_url = None
    cors_origins = ""
    storage_backend = "local"
    storage_local_dir = "/tmp/test_uploads"
    storage_url_prefix = "/static/uploads"
    s3_bucket = ""
    s3_region = ""
    s3_access_key = ""
    s3_secret_key = ""
    s3_endpoint_url = ""
    upload_max_size_bytes = 10 * 1024 * 1024
    upload_allowed_types = (
        "image/jpeg,image/png,image/gif,image/webp,application/pdf,text/plain,text/csv"
    )
    metrics_token = None
    fusionpbx_db_url = "sqlite+pysqlite:///:memory:"
    fusionpbx_api_url = "http://localhost:8080"
    fusionpbx_api_key = "test-key"
    esl_host = "localhost"
    esl_port = 8021
    esl_password = "ClueCon"
    esl_consumer_enabled = False
    edge_wss_url = "wss://sip.dotmac.io:443"
    voice_ingress_api_keys = "test-ingress-key"
    voice_ingress_allowed_ips = ""
    token_signing_key = "test-token-key"
    turn_static_auth_secret = ""
    turn_urls = ""
    stun_urls = "stun:stun.l.google.com:19302"
    turn_credential_ttl = 3600


mock_config_module.settings = MockSettings()
mock_config_module.Settings = MockSettings
mock_config_module.validate_settings = lambda s: []

# Insert mocks before any app imports
sys.modules["app.config"] = mock_config_module
sys.modules["app.db"] = mock_db_module

# Set environment variables
os.environ["JWT_SECRET"] = "test-secret"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["TOTP_ENCRYPTION_KEY"] = "QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g="
os.environ["TOTP_ISSUER"] = "StarterTemplate"

# Now import the models - they'll use our mocked db module
from app.models.person import Person
from app.models.auth import UserCredential, Session as AuthSession, SessionStatus
from app.models.rbac import Role, Permission, RolePermission, PersonRole
from app.models.audit import AuditEvent, AuditActorType
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduledTask, ScheduleType
from app.models.file_upload import FileUpload, FileUploadStatus
from app.models.notification import Notification, NotificationType
from app.models.billing import (
    Product,
    Price,
    PriceType,
    BillingScheme,
    RecurringInterval,
    Customer,
    Subscription,
    SubscriptionStatus,
    SubscriptionItem,
    Invoice,
    InvoiceStatus,
    InvoiceItem,
    PaymentMethod,
    PaymentMethodType,
    PaymentIntent,
    PaymentIntentStatus,
    UsageRecord,
    UsageAction,
    Coupon,
    CouponDuration,
    Discount,
    Entitlement,
    EntitlementValueType,
    WebhookEvent,
    WebhookEventStatus,
)
from app.models.voice import VoiceDomain, Extension, SyncStatus, Cdr, CdrRatingStatus  # noqa: F401
from app.models import webhook  # noqa: F401

# Create all tables
TestBase.metadata.create_all(_test_engine)

# Re-export Base for compatibility
Base = TestBase


@pytest.fixture(scope="session")
def engine():
    return _test_engine


@pytest.fixture()
def db_session(engine):
    """Create a database session for testing.

    Uses the same connection as the StaticPool engine to ensure
    all operations see the same data.
    """
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture()
def person(db_session):
    person = Person(
        first_name="Test",
        last_name="User",
        email=_unique_email(),
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


@pytest.fixture(autouse=True)
def auth_env():
    # Environment variables are set at module level above
    # This fixture ensures they're available for each test
    pass


# ============ FastAPI Test Client Fixtures ============


@pytest.fixture(autouse=True)
def _permissive_rate_limiter(request):
    """The module-level app's RateLimitMiddleware fails closed (503) on auth paths
    when Redis is unavailable; the test env has no Redis. Give the limiter a
    permissive fake so integration tests can reach auth endpoints. test_rate_limit.py
    exercises the limiter directly (its own app + _get_redis patches) and is skipped.
    """
    if "test_rate_limit" in request.node.nodeid:
        yield
        return
    from app.middleware.rate_limit import RateLimitMiddleware

    fake = MagicMock()
    fake.eval.return_value = [1, 1]  # (allowed, current_count) -> request allowed
    with patch.object(RateLimitMiddleware, "_ensure_redis", lambda self: fake):
        yield


@pytest.fixture()
def client(db_session):
    """Create a test client with database dependency override."""
    from app.main import app
    from app.api.deps import get_db as api_get_db

    def override_get_db():
        yield db_session

    # Override shared db dependencies
    app.dependency_overrides[api_get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _create_access_token(
    person_id: str, session_id: str, roles: list[str] = None, scopes: list[str] = None
) -> str:
    """Create a JWT access token for testing."""
    secret = os.getenv("JWT_SECRET", "test-secret")
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=15)
    payload = {
        "sub": person_id,
        "session_id": session_id,
        "roles": roles or [],
        "scopes": scopes or [],
        "typ": "access",
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture()
def auth_session(db_session, person):
    """Create an authenticated session for a person."""
    session = AuthSession(
        person_id=person.id,
        token_hash="test-token-hash",
        status=SessionStatus.active,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture()
def auth_token(db_session, person, auth_session):
    """Create a valid JWT token for authenticated requests."""
    role = db_session.scalars(select(Role).where(Role.name == "admin")).first()
    if not role:
        role = Role(name="admin", description="Administrator role")
        db_session.add(role)
        db_session.flush()
    existing = db_session.scalars(
        select(PersonRole)
        .where(PersonRole.person_id == person.id)
        .where(PersonRole.role_id == role.id)
    ).first()
    if not existing:
        db_session.add(PersonRole(person_id=person.id, role_id=role.id))
        db_session.commit()
    return _create_access_token(str(person.id), str(auth_session.id), roles=["admin"])


@pytest.fixture()
def auth_headers(auth_token):
    """Return authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture()
def admin_role(db_session):
    """Create an admin role."""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role:
        return role
    role = Role(name="admin", description="Administrator role")
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


@pytest.fixture()
def admin_person(db_session, admin_role):
    """Create a person with admin role."""
    person = Person(
        first_name="Admin",
        last_name="User",
        email=_unique_email(),
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    # Assign admin role
    person_role = PersonRole(person_id=person.id, role_id=admin_role.id)
    db_session.add(person_role)
    db_session.commit()

    return person


@pytest.fixture()
def admin_session(db_session, admin_person):
    """Create an authenticated session for admin."""
    session = AuthSession(
        person_id=admin_person.id,
        token_hash="admin-token-hash",
        status=SessionStatus.active,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture()
def admin_token(admin_person, admin_session):
    """Create a valid JWT token for admin requests."""
    return _create_access_token(
        str(admin_person.id),
        str(admin_session.id),
        roles=["admin"],
        scopes=["audit:read", "audit:*"],
    )


@pytest.fixture()
def admin_headers(admin_token):
    """Return authorization headers for admin requests."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def user_credential(db_session, person):
    """Create a user credential for testing."""
    from app.services.auth_flow import hash_password

    credential = UserCredential(
        person_id=person.id,
        username=f"testuser_{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("testpassword123"),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()
    db_session.refresh(credential)
    return credential


@pytest.fixture()
def role(db_session):
    """Create a test role."""
    role = Role(name=f"test_role_{uuid.uuid4().hex[:8]}", description="Test role")
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


@pytest.fixture()
def permission(db_session):
    """Create a test permission."""
    perm = Permission(
        key=f"test:permission:{uuid.uuid4().hex[:8]}",
        description="Test permission",
    )
    db_session.add(perm)
    db_session.commit()
    db_session.refresh(perm)
    return perm


@pytest.fixture()
def audit_event(db_session, person):
    """Create a test audit event."""
    event = AuditEvent(
        actor_id=str(person.id),
        actor_type=AuditActorType.user,
        action="test_action",
        entity_type="test_entity",
        entity_id=str(uuid.uuid4()),
        is_success=True,
        status_code=200,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


@pytest.fixture()
def domain_setting(db_session):
    """Create a test domain setting."""
    setting = DomainSetting(
        domain=SettingDomain.auth,
        key=f"test_setting_{uuid.uuid4().hex[:8]}",
        value_text="test_value",
    )
    db_session.add(setting)
    db_session.commit()
    db_session.refresh(setting)
    return setting


@pytest.fixture()
def scheduled_task(db_session):
    """Create a test scheduled task."""
    task = ScheduledTask(
        name=f"test_task_{uuid.uuid4().hex[:8]}",
        task_name="app.tasks.test_task",
        schedule_type=ScheduleType.interval,
        interval_seconds=300,
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


# ============ Billing Fixtures ============


@pytest.fixture()
def billing_product(db_session):
    """Create a test billing product."""
    product = Product(
        name=f"Product {uuid.uuid4().hex[:8]}", description="Test product"
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)
    return product


@pytest.fixture()
def billing_price(db_session, billing_product):
    """Create a test billing price."""
    price = Price(
        product_id=billing_product.id,
        currency="usd",
        unit_amount=1999,
        type=PriceType.recurring,
        billing_scheme=BillingScheme.per_unit,
        recurring_interval=RecurringInterval.month,
        recurring_interval_count=1,
        lookup_key=f"price_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(price)
    db_session.commit()
    db_session.refresh(price)
    return price


@pytest.fixture()
def billing_customer(db_session):
    """Create a test billing customer."""
    customer = Customer(
        name="Test Customer",
        email=f"customer-{uuid.uuid4().hex[:8]}@example.com",
        currency="usd",
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def billing_subscription(db_session, billing_customer):
    """Create a test billing subscription."""
    sub = Subscription(
        customer_id=billing_customer.id,
        status=SubscriptionStatus.active,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


@pytest.fixture()
def billing_subscription_item(db_session, billing_subscription, billing_price):
    """Create a test subscription item."""
    si = SubscriptionItem(
        subscription_id=billing_subscription.id,
        price_id=billing_price.id,
        quantity=1,
    )
    db_session.add(si)
    db_session.commit()
    db_session.refresh(si)
    return si


@pytest.fixture()
def billing_coupon(db_session):
    """Create a test coupon."""
    coupon = Coupon(
        name="Test Coupon",
        code=f"SAVE{uuid.uuid4().hex[:6].upper()}",
        percent_off=20,
        duration=CouponDuration.once,
    )
    db_session.add(coupon)
    db_session.commit()
    db_session.refresh(coupon)
    return coupon
