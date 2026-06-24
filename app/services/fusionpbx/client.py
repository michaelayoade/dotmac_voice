"""FusionPBX provisioning client (direct PostgreSQL writes + ESL reloadxml).

FusionPBX exposes no REST provisioning API, so this client provisions by writing
directly to FusionPBX's own PostgreSQL database (tables ``v_domains`` and
``v_extensions``) and then triggers a FreeSWITCH ``reloadxml`` over ESL so the
change goes live. The DB is the source of truth; reload failures are non-fatal.

This talks to a SEPARATE, foreign database -- NOT this app's ORM models. The
tables are declared with SQLAlchemy Core so the client is unit-testable against
an in-memory SQLite engine. FusionPBX stores booleans as the TEXT strings
``'true'``/``'false'``, which is preserved here.
"""

import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    delete,
    insert,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.config import settings
from app.services.exceptions import BadRequestError, ServiceUnavailableError
from app.services.freeswitch import esl

logger = logging.getLogger(__name__)

# Foreign FusionPBX schema. Only the columns this client reads/writes are
# declared; the real tables have ~50 more nullable columns we ignore. Booleans
# are TEXT ('true'/'false') per FusionPBX convention.
_metadata = MetaData()

v_domains = Table(
    "v_domains",
    _metadata,
    Column("domain_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_name", String),
    Column("domain_enabled", Boolean),
    Column("domain_description", String),
    Column("insert_date", DateTime(timezone=True)),
)

v_extensions = Table(
    "v_extensions",
    _metadata,
    Column("extension_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("extension", String),
    Column("password", String),
    Column("accountcode", String),
    Column("user_context", String),
    Column("effective_caller_id_name", String),
    Column("effective_caller_id_number", String),
    Column("outbound_caller_id_name", String),
    Column("outbound_caller_id_number", String),
    Column("call_timeout", Integer),
    Column("enabled", Boolean),
    Column("directory_first_name", String),
    Column("description", String),
    Column("insert_date", DateTime(timezone=True)),
)

# Errors that mean the FusionPBX database is unreachable / a transport fault.
_UNAVAILABLE = (OperationalError, InterfaceError)

ReloadCallable = Callable[[], None]


def _default_reloader() -> None:
    """Best-effort FreeSWITCH reloadxml using ESL settings; never raises."""
    try:
        esl.reloadxml(settings.esl_host, settings.esl_port, settings.esl_password)
    except Exception as exc:  # noqa: BLE001 - reload is best-effort; DB is source of truth
        logger.warning("FusionPBX reloadxml failed (non-fatal): %s", exc)


class FusionpbxClient:
    """Provisions FusionPBX by writing to its PostgreSQL DB + reloadxml over ESL."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        engine: Engine | None = None,
        reloader: ReloadCallable | None = None,
    ) -> None:
        """Initialize the FusionPBX client.

        Args:
            db_url: SQLAlchemy URL for the FusionPBX PostgreSQL database. Ignored
                if ``engine`` is provided.
            engine: Pre-built SQLAlchemy Engine (used by tests to inject SQLite).
            reloader: Zero-arg callable invoked after a successful write to push
                the change live. Defaults to a best-effort ESL ``reloadxml``.
                Injecting a no-op keeps unit tests off a real ESL.
        """
        if engine is not None:
            self._engine = engine
            self._owns_engine = False
        else:
            if db_url is None:
                raise ValueError("FusionpbxClient requires db_url or engine")
            self._engine = create_engine(db_url)
            self._owns_engine = True
        self._reloader: ReloadCallable = reloader or _default_reloader

    def close(self) -> None:
        """Dispose the engine if this client created it."""
        if self._owns_engine:
            self._engine.dispose()

    def __enter__(self) -> "FusionpbxClient":
        """Enter context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit context manager and dispose owned resources."""
        self.close()

    def _reload(self) -> None:
        """Trigger the (injectable) reload; failures are non-fatal."""
        try:
            self._reloader()
        except Exception as exc:  # noqa: BLE001 - reload is best-effort
            logger.warning("FusionPBX reload callable failed (non-fatal): %s", exc)

    def _domain_uuid_for(self, conn: Connection, domain_name: str) -> str | None:
        """Return the domain_uuid for a domain_name, or None if it doesn't exist."""
        return conn.execute(
            select(v_domains.c.domain_uuid).where(
                v_domains.c.domain_name == domain_name
            )
        ).scalar_one_or_none()

    def _ensure_domain(self, conn: Connection, name: str) -> tuple[str, bool]:
        """Idempotently ensure a v_domains row exists; return (domain_uuid, created)."""
        existing = self._domain_uuid_for(conn, name)
        if existing is not None:
            return existing, False
        domain_uuid = str(uuid.uuid4())
        conn.execute(
            insert(v_domains).values(
                domain_uuid=domain_uuid,
                domain_name=name,
                domain_enabled=True,
                insert_date=datetime.now(UTC),
            )
        )
        return domain_uuid, True

    def list_domains(self) -> list[dict]:
        """List all FusionPBX domains.

        Returns:
            One dict per domain with ``domain_uuid``, ``name`` (=domain_name),
            and ``enabled``.

        Raises:
            ServiceUnavailableError: If the FusionPBX DB is unreachable.
        """
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    select(
                        v_domains.c.domain_uuid,
                        v_domains.c.domain_name,
                        v_domains.c.domain_enabled,
                    )
                ).all()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return [
            {
                "domain_uuid": r.domain_uuid,
                "name": r.domain_name,
                "enabled": r.domain_enabled,
            }
            for r in rows
        ]

    def create_domain(self, name: str) -> dict:
        """Idempotently create a FusionPBX domain.

        If a v_domains row with ``name`` exists it is returned; otherwise a new
        row is inserted (domain_enabled='true'). Triggers reloadxml on insert.

        Args:
            name: The domain_name.

        Returns:
            ``{"domain_uuid": ..., "name": ...}``.

        Raises:
            ServiceUnavailableError: If the FusionPBX DB is unreachable.
            BadRequestError: On an integrity violation.
        """
        try:
            with self._engine.begin() as conn:
                domain_uuid, created = self._ensure_domain(conn, name)
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX domain insert failed: {exc}") from exc
        if created:
            self._reload()
        return {"domain_uuid": domain_uuid, "name": name}

    def list_extensions(self, domain_name: str) -> list[dict]:
        """List extensions for a domain.

        Args:
            domain_name: The domain_name to resolve.

        Returns:
            ``[]`` if the domain doesn't exist; otherwise one dict per extension
            with ``number`` (=extension column) and ``extension_uuid``.

        Raises:
            ServiceUnavailableError: If the FusionPBX DB is unreachable.
        """
        try:
            with self._engine.connect() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return []
                rows = conn.execute(
                    select(
                        v_extensions.c.extension_uuid,
                        v_extensions.c.extension,
                    ).where(v_extensions.c.domain_uuid == domain_uuid)
                ).all()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return [
            {"number": r.extension, "extension_uuid": r.extension_uuid} for r in rows
        ]

    def create_extension(
        self,
        domain_name: str,
        number: str,
        password: str = "",
        display_name: str = "",
    ) -> dict:
        """Idempotently create a registerable extension, auto-ensuring the domain.

        Looks up (and creates if missing) the v_domains row for ``domain_name``,
        then inserts a v_extensions row using FusionPBX conventions
        (accountcode = user_context = domain_name; enabled='true';
        call_timeout=30). If the extension already exists it is returned as-is.
        Triggers reloadxml on a successful write.

        Args:
            domain_name: The domain_name (auto-created if missing).
            number: The extension id / phone number.
            password: SIP secret. If empty, a strong random one is generated.
            display_name: Caller-id display name.

        Returns:
            A dict including ``number``, ``extension_uuid``, and ``password``.

        Raises:
            ServiceUnavailableError: If the FusionPBX DB is unreachable.
            BadRequestError: On an integrity violation.
        """
        secret = password or secrets.token_urlsafe(16)
        wrote = False
        try:
            with self._engine.begin() as conn:
                domain_uuid, _ = self._ensure_domain(conn, domain_name)

                existing = conn.execute(
                    select(
                        v_extensions.c.extension_uuid,
                        v_extensions.c.password,
                    )
                    .where(v_extensions.c.domain_uuid == domain_uuid)
                    .where(v_extensions.c.extension == number)
                ).first()
                if existing is not None:
                    return {
                        "number": number,
                        "extension_uuid": existing.extension_uuid,
                        "password": existing.password,
                    }

                extension_uuid = str(uuid.uuid4())
                conn.execute(
                    insert(v_extensions).values(
                        extension_uuid=extension_uuid,
                        domain_uuid=domain_uuid,
                        extension=number,
                        password=secret,
                        accountcode=domain_name,
                        user_context=domain_name,
                        effective_caller_id_name=display_name,
                        effective_caller_id_number=number,
                        outbound_caller_id_name=display_name,
                        outbound_caller_id_number=number,
                        call_timeout=30,
                        enabled=True,
                        directory_first_name=display_name,
                        insert_date=datetime.now(UTC),
                    )
                )
                wrote = True
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX extension insert failed: {exc}") from exc
        if wrote:
            self._reload()
        return {
            "number": number,
            "extension_uuid": extension_uuid,
            "password": secret,
        }

    def delete_extension(self, domain_name: str, number: str) -> bool:
        """Delete an extension from a FusionPBX domain.

        Args:
            domain_name: The domain_name to resolve.
            number: The extension number to remove.

        Returns:
            True when a row was deleted, False when the domain or extension was absent.

        Raises:
            ServiceUnavailableError: If the FusionPBX DB is unreachable.
        """
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return False
                result = conn.execute(
                    delete(v_extensions).where(
                        v_extensions.c.domain_uuid == domain_uuid,
                        v_extensions.c.extension == number,
                    )
                )
                deleted = (result.rowcount or 0) > 0
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        if deleted:
            self._reload()
        return deleted
