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
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape

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
    func,
    insert,
    select,
    update,
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
    Column("dial_string", String),
    Column("insert_date", DateTime(timezone=True)),
)

v_voicemails = Table(
    "v_voicemails",
    _metadata,
    Column("voicemail_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("voicemail_id", String),
    Column("voicemail_password", String),
    Column("voicemail_enabled", Boolean),
    Column("insert_date", DateTime(timezone=True)),
)

v_voicemail_messages = Table(
    "v_voicemail_messages",
    _metadata,
    Column("voicemail_message_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("voicemail_uuid", Uuid(as_uuid=False)),
    Column("created_epoch", Integer),
    Column("caller_id_name", String),
    Column("caller_id_number", String),
    Column("message_length", Integer),
    Column("message_status", String),
)

v_dialplans = Table(
    "v_dialplans",
    _metadata,
    Column("dialplan_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("app_uuid", Uuid(as_uuid=False)),
    Column("dialplan_context", String),
    Column("dialplan_name", String),
    Column("dialplan_number", String),
    Column("dialplan_order", Integer),
    Column("dialplan_enabled", Boolean),
    Column("dialplan_continue", Boolean),
    Column("dialplan_xml", String),
    Column("dialplan_description", String),
    Column("insert_date", DateTime(timezone=True)),
)

v_default_settings = Table(
    "v_default_settings",
    _metadata,
    Column("default_setting_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("default_setting_category", String),
    Column("default_setting_subcategory", String),
    Column("default_setting_name", String),
    Column("default_setting_value", String),
    Column("default_setting_enabled", Boolean),
    Column("insert_date", DateTime(timezone=True)),
)

v_modules = Table(
    "v_modules",
    _metadata,
    Column("module_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("module_name", String),
    Column("module_enabled", Boolean),
)

v_call_center_queues = Table(
    "v_call_center_queues",
    _metadata,
    Column("call_center_queue_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("queue_name", String),
    Column("queue_extension", String),
    Column("queue_strategy", String),
    Column("queue_moh_sound", String),
    Column("insert_date", DateTime(timezone=True)),
)

v_call_center_agents = Table(
    "v_call_center_agents",
    _metadata,
    Column("call_center_agent_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("agent_id", String),
    Column("agent_name", String),
    Column("agent_type", String),
    Column("agent_contact", String),
    Column("agent_status", String),
    Column("insert_date", DateTime(timezone=True)),
)

v_call_center_tiers = Table(
    "v_call_center_tiers",
    _metadata,
    Column("call_center_tier_uuid", Uuid(as_uuid=False), primary_key=True),
    Column("domain_uuid", Uuid(as_uuid=False)),
    Column("call_center_queue_uuid", Uuid(as_uuid=False)),
    Column("call_center_agent_uuid", Uuid(as_uuid=False)),
    Column("queue_name", String),
    Column("agent_name", String),
    Column("tier_level", Integer),
    Column("tier_position", Integer),
    Column("insert_date", DateTime(timezone=True)),
)

# FusionPBX "Dialplan" app UUID (owns generic dialplan rows).
DIALPLAN_APP_UUID = "b1cd7509-5576-469a-892d-d0cfb66a4197"

# FusionPBX ships switch/voicemail/dir as "/voicemail" which breaks the voicemail
# Lua's storage path; it must be the real FreeSWITCH storage dir.
SWITCH_VOICEMAIL_DIR = "/var/lib/freeswitch/storage/voicemail"

# The FusionPBX directory dial-string that routes user/<ext> bridges to Kamailio.
# WS clients register on Kamailio (10.10.10.1), not FreeSWITCH, so the default
# sofia_contact() dial-string resolves to nothing. FreeSWITCH expands ${...} at
# runtime; stored verbatim. Unlocks ring groups / IVR / queues / transfers.
# See deploy/core/freeswitch/dialstring-unlock-and-1003.sql.
DIAL_STRING_UNLOCK = (
    "{sip_invite_domain=${domain_name},sip_h_X-Voice-Domain=${domain_name}}"
    "sofia/external/${dialed_user}@10.10.10.1:5060"
)


_DOMAIN_RE = re.compile(
    r"^(?=.{1,255}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_DIAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_*#+-]{1,64}$")
_IVR_DIGIT_RE = re.compile(r"^[0-9]$")


def _require_domain(domain_name: str) -> None:
    if not _DOMAIN_RE.fullmatch(domain_name):
        raise BadRequestError("Invalid FusionPBX domain")


def _require_dial_token(value: str, field: str = "number") -> None:
    if not _DIAL_TOKEN_RE.fullmatch(value):
        raise BadRequestError(f"Invalid {field}")


def _xml(value: str) -> str:
    return escape(value, quote=True)


def feature_dialplan_name(kind: str, domain_name: str, number: str) -> str:
    """Per-(domain, number) feature dialplan name for multi-tenant isolation.

    Two customers can both use feature number 3001 without colliding because each
    gets its own dialplan row + a ${sip_req_host} domain condition (see _domain_match).
    """
    _require_domain(domain_name)
    _require_dial_token(number)
    return f"kamailio-{kind}-{domain_name}-{number}"


def _domain_match(domain_name: str) -> str:
    """Regex anchoring a feature dialplan to one domain's calls (${sip_req_host})."""
    _require_domain(domain_name)
    return "^" + re.escape(domain_name) + "$"


def _conf_room(domain_name: str, number: str) -> str:
    """Domain-scoped conference room so rooms don't merge across customers."""
    _require_domain(domain_name)
    _require_dial_token(number)
    return f"{domain_name.replace('.', '_')}-{number}"

# Errors that mean the FusionPBX database is unreachable / a transport fault.
_UNAVAILABLE = (OperationalError, InterfaceError)

ReloadCallable = Callable[[], None]
CommandCallable = Callable[[str], None]


def _default_reloader() -> None:
    """Best-effort FreeSWITCH reloadxml using ESL settings; never raises."""
    try:
        esl.reloadxml(settings.esl_host, settings.esl_port, settings.esl_password)
    except Exception as exc:  # noqa: BLE001 - reload is best-effort; DB is source of truth
        logger.warning("FusionPBX reloadxml failed (non-fatal): %s", exc)


def _default_commander(cmd: str) -> None:
    """Best-effort ESL ``api`` command (e.g. callcenter_config); never raises."""
    try:
        esl.command(settings.esl_host, settings.esl_port, settings.esl_password, cmd)
    except Exception as exc:  # noqa: BLE001 - runtime activation is best-effort
        logger.warning("FusionPBX ESL command failed (non-fatal): %s", exc)


class FusionpbxClient:
    """Provisions FusionPBX by writing to its PostgreSQL DB + reloadxml over ESL."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        engine: Engine | None = None,
        reloader: ReloadCallable | None = None,
        commander: CommandCallable | None = None,
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
        self._commander: CommandCallable = commander or _default_commander

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
        _require_domain(name)
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
        _require_domain(domain_name)
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
        _require_domain(domain_name)
        _require_dial_token(number)
        secret = password or secrets.token_urlsafe(16)
        wrote = False
        result: dict | None = None
        try:
            with self._engine.begin() as conn:
                domain_uuid, _ = self._ensure_domain(conn, domain_name)

                existing = conn.execute(
                    select(
                        v_extensions.c.extension_uuid,
                        v_extensions.c.password,
                        v_extensions.c.effective_caller_id_name,
                        v_extensions.c.outbound_caller_id_name,
                        v_extensions.c.directory_first_name,
                    )
                    .where(v_extensions.c.domain_uuid == domain_uuid)
                    .where(v_extensions.c.extension == number)
                ).first()
                if existing is not None:
                    if (
                        existing.effective_caller_id_name != display_name
                        or existing.outbound_caller_id_name != display_name
                        or existing.directory_first_name != display_name
                    ):
                        conn.execute(
                            update(v_extensions)
                            .where(
                                v_extensions.c.extension_uuid
                                == existing.extension_uuid
                            )
                            .values(
                                effective_caller_id_name=display_name,
                                outbound_caller_id_name=display_name,
                                directory_first_name=display_name,
                            )
                        )
                        wrote = True
                    result = {
                        "number": number,
                        "extension_uuid": existing.extension_uuid,
                        "password": existing.password,
                    }
                else:
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
                            dial_string=DIAL_STRING_UNLOCK,
                            insert_date=datetime.now(UTC),
                        )
                    )
                    wrote = True
                    result = {
                        "number": number,
                        "extension_uuid": extension_uuid,
                        "password": secret,
                    }
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX extension insert failed: {exc}") from exc
        if wrote:
            self._reload()
        assert result is not None
        return result

    def ensure_voicemail(
        self,
        domain_name: str,
        number: str,
        *,
        enabled: bool = True,
        password: str = "",
    ) -> dict:
        """Idempotently ensure a voicemail box (v_voicemails) for an extension.

        The ``switch/voicemail/dir`` default-setting must be correct (see
        ``ensure_switch_settings``) for recordings to land on disk; this only
        creates the box. Mirrors deploy/core/freeswitch/dialstring-unlock-and-1003.sql.
        """
        _require_domain(domain_name)
        _require_dial_token(number)
        pwd = password or number
        wrote = False
        try:
            with self._engine.begin() as conn:
                domain_uuid, _ = self._ensure_domain(conn, domain_name)
                existing = conn.execute(
                    select(v_voicemails.c.voicemail_uuid)
                    .where(v_voicemails.c.domain_uuid == domain_uuid)
                    .where(v_voicemails.c.voicemail_id == number)
                ).first()
                if existing is not None:
                    return {"voicemail_id": number, "created": False}
                conn.execute(
                    insert(v_voicemails).values(
                        voicemail_uuid=str(uuid.uuid4()),
                        domain_uuid=domain_uuid,
                        voicemail_id=number,
                        voicemail_password=pwd,
                        voicemail_enabled=enabled,
                        insert_date=datetime.now(UTC),
                    )
                )
                wrote = True
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX voicemail insert failed: {exc}") from exc
        if wrote:
            self._reload()
        return {"voicemail_id": number, "created": True}

    def _ensure_dialplan(
        self,
        conn: Connection,
        *,
        name: str,
        number: str,
        order: int,
        xml: str,
        tag: str = "dotmac-voice:managed",
    ) -> bool:
        """Idempotently upsert a public-context dialplan row. Returns True if changed.

        ``tag`` is stored in dialplan_description so a domain's managed feature
        dialplans are listable for drift reconciliation.
        """
        existing = conn.execute(
            select(v_dialplans.c.dialplan_uuid, v_dialplans.c.dialplan_xml)
            .where(v_dialplans.c.dialplan_name == name)
            .where(v_dialplans.c.dialplan_context == "public")
        ).first()
        if existing is not None:
            if existing.dialplan_xml == xml:
                return False
            conn.execute(
                update(v_dialplans)
                .where(v_dialplans.c.dialplan_uuid == existing.dialplan_uuid)
                .values(dialplan_xml=xml, dialplan_order=order)
            )
            return True
        conn.execute(
            insert(v_dialplans).values(
                dialplan_uuid=str(uuid.uuid4()),
                domain_uuid=None,
                app_uuid=DIALPLAN_APP_UUID,
                dialplan_context="public",
                dialplan_name=name,
                dialplan_number=number,
                dialplan_order=order,
                dialplan_enabled=True,
                dialplan_continue=False,
                dialplan_xml=xml,
                dialplan_description=tag,
                insert_date=datetime.now(UTC),
            )
        )
        return True

    def create_conference(self, domain_name: str, number: str) -> dict:
        """Idempotently provision an FS-hosted conference room <number>, isolated to
        this domain (room name + ${sip_req_host} condition) so customers don't merge."""
        _require_domain(domain_name)
        _require_dial_token(number)
        name = feature_dialplan_name("conference", domain_name, number)
        room = _conf_room(domain_name, number)
        xml = (
            f'<extension name="{_xml(name)}" continue="false">\n'
            '  <condition field="${network_addr}" expression="^10\\.10\\.10\\.1$"/>\n'
            f'  <condition field="${{sip_req_host}}" expression="{_domain_match(domain_name)}"/>\n'
            f'  <condition field="destination_number" expression="^({re.escape(number)})$">\n'
            '    <action application="answer"/>\n'
            '    <action application="sleep" data="500"/>\n'
            f'    <action application="conference" data="{_xml(room)}@default"/>\n'
            "  </condition>\n"
            "</extension>"
        )
        changed = False
        try:
            with self._engine.begin() as conn:
                self._ensure_domain(conn, domain_name)
                changed = self._ensure_dialplan(
                    conn, name=name, number=number, order=55, xml=xml,
                    tag=f"dotmac-voice:feature:{domain_name}",
                )
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX conference insert failed: {exc}") from exc
        if changed:
            self._reload()
        return {"name": name, "created": changed}

    def create_ring_group(
        self,
        domain_name: str,
        number: str,
        members: list[str],
        *,
        strategy: str = "simultaneous",
        timeout: int = 30,
    ) -> dict:
        """Idempotently provision a ring group <number> bridging member extensions.

        Members bridge via ``user/<ext>@${domain_name}``, which the dial-string
        unlock routes to Kamailio -> the WS clients. ``simultaneous`` = comma-join.
        """
        _require_domain(domain_name)
        _require_dial_token(number)
        if not members:
            raise BadRequestError("Ring group requires at least one member")
        for member in members:
            _require_dial_token(member, "member")
        sep = "," if strategy == "simultaneous" else "|"
        bridge_data = sep.join(f"user/{m}@{domain_name}" for m in members)
        name = feature_dialplan_name("ringgroup", domain_name, number)
        xml = (
            f'<extension name="{_xml(name)}" continue="false">\n'
            '  <condition field="${network_addr}" expression="^10\\.10\\.10\\.1$"/>\n'
            f'  <condition field="${{sip_req_host}}" expression="{_domain_match(domain_name)}"/>\n'
            f'  <condition field="destination_number" expression="^({re.escape(number)})$">\n'
            '    <action application="set" data="hangup_after_bridge=true"/>\n'
            '    <action application="set" data="continue_on_fail=true"/>\n'
            f'    <action application="set" data="call_timeout={timeout}"/>\n'
            '    <action application="export" data="rtp_timeout_sec=30"/>\n'
            f'    <action application="bridge" data="{_xml(bridge_data)}"/>\n'
            "  </condition>\n"
            "</extension>"
        )
        changed = False
        try:
            with self._engine.begin() as conn:
                self._ensure_domain(conn, domain_name)
                changed = self._ensure_dialplan(
                    conn, name=name, number=number, order=52, xml=xml,
                    tag=f"dotmac-voice:feature:{domain_name}",
                )
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX ring group insert failed: {exc}") from exc
        if changed:
            self._reload()
        return {"name": name, "created": changed}

    def ensure_routing(
        self, domain_name: str, *, recording: bool = False, ext_pattern: str = r"1\d{3}"
    ) -> dict:
        """Idempotently ensure the FS-in-path internal-extension routing dialplan.

        WS-extension calls (from Kamailio) bridge to sofia/external (-> Kamailio ->
        WS callee) with a no-answer voicemail fallback. ``recording=True`` records
        the call (deferred via execute_on_answer so it doesn't pre-answer the leg).
        Verbatim from deploy/core/freeswitch/kamailio-internal-to-domain.xml.
        """
        _require_domain(domain_name)
        rec = ""
        if recording:
            rec_path = (
                "/var/lib/freeswitch/recordings/${sip_req_host}/"
                "${strftime(%Y-%m-%d)}/${uuid}.wav"
            )
            rec = (
                '    <action application="set" data="RECORD_STEREO=true"/>\n'
                # Expose the path as a channel variable so mod_json_cdr emits it
                # (variable_recording_file) and CDR ingest can store recording_url.
                f'    <action application="set" data="recording_file={rec_path}"/>\n'
                f'    <action application="set" data="execute_on_answer=record_session '
                f'{rec_path}"/>\n'
            )
        xml = (
            '<extension name="kamailio-internal-to-domain" continue="false">\n'
            '  <condition field="${network_addr}" expression="^10\\.10\\.10\\.1$"/>\n'
            '  <condition field="destination_number" expression="^(' + ext_pattern + ')$">\n'
            '    <action application="set" data="hangup_after_bridge=true"/>\n'
            '    <action application="set" data="continue_on_fail=true"/>\n'
            '    <action application="export" data="rtp_timeout_sec=30"/>\n'
            '    <action application="export" data="rtp_hold_timeout_sec=1800"/>\n'
            + rec
            + '    <action application="bridge" data="{sip_h_X-Voice-Domain=${sip_req_host}}'
            'sofia/external/${destination_number}@10.10.10.1:5060"/>\n'
            '    <action application="answer"/>\n'
            '    <action application="sleep" data="1000"/>\n'
            '    <action application="set" data="voicemail_action=save"/>\n'
            '    <action application="set" data="voicemail_id=${destination_number}"/>\n'
            '    <action application="set" data="voicemail_profile=default"/>\n'
            '    <action application="set" data="send_to_voicemail=true"/>\n'
            '    <action application="set" data="domain_name=${sip_req_host}"/>\n'
            '    <action application="lua" data="app.lua voicemail"/>\n'
            "  </condition>\n"
            "</extension>"
        )
        changed = False
        try:
            with self._engine.begin() as conn:
                self._ensure_domain(conn, domain_name)
                changed = self._ensure_dialplan(
                    conn, name="kamailio-internal-to-domain", number="", order=50, xml=xml,
                    tag="dotmac-voice:routing",
                )
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX routing insert failed: {exc}") from exc
        if changed:
            self._reload()
        return {"name": "kamailio-internal-to-domain", "created": changed}

    def ensure_switch_settings(self) -> dict:
        """Idempotently fix the switch/voicemail/dir default-setting so the FusionPBX
        voicemail Lua writes recordings to the real storage path. Environment bootstrap."""
        changed = False
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(
                        v_default_settings.c.default_setting_uuid,
                        v_default_settings.c.default_setting_value,
                    )
                    .where(v_default_settings.c.default_setting_category == "switch")
                    .where(v_default_settings.c.default_setting_subcategory == "voicemail")
                    .where(v_default_settings.c.default_setting_name == "dir")
                ).first()
                if row is None:
                    conn.execute(
                        insert(v_default_settings).values(
                            default_setting_uuid=str(uuid.uuid4()),
                            default_setting_category="switch",
                            default_setting_subcategory="voicemail",
                            default_setting_name="dir",
                            default_setting_value=SWITCH_VOICEMAIL_DIR,
                            default_setting_enabled=True,
                            insert_date=datetime.now(UTC),
                        )
                    )
                    changed = True
                elif row.default_setting_value != SWITCH_VOICEMAIL_DIR:
                    conn.execute(
                        update(v_default_settings)
                        .where(
                            v_default_settings.c.default_setting_uuid
                            == row.default_setting_uuid
                        )
                        .values(
                            default_setting_value=SWITCH_VOICEMAIL_DIR,
                            default_setting_enabled=True,
                        )
                    )
                    changed = True
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        if changed:
            self._reload()
        return {"changed": changed}

    def ensure_queue(
        self,
        domain_name: str,
        number: str,
        *,
        agents: list[str],
        name: str | None = None,
        strategy: str = "ring-all",
    ) -> dict:
        """Idempotently provision a call-center queue <number> with callback agents.

        Writes v_call_center_queues/agents/tiers + the public callcenter dialplan,
        then issues runtime callcenter_config over ESL (DB rows alone don't load
        into mod_callcenter). ``queue_name`` MUST equal the number -- callcenter.conf
        names the queue by ``queue_name``.

        KNOWN CAVEAT: caller<->agent media is one-way through mod_callcenter's bridge
        (rtpengine re-anchor); provisioning works, media is a tracked follow-up. See
        deploy/core/freeswitch/README.md.
        """
        _require_domain(domain_name)
        _require_dial_token(number)
        if not agents:
            raise BadRequestError("Queue requires at least one agent")
        desired_agents = list(dict.fromkeys(agents))
        for agent in desired_agents:
            _require_dial_token(agent, "agent")

        cmds: list[str] = []
        changed = False
        runtime_reload = False
        try:
            with self._engine.begin() as conn:
                domain_uuid, _ = self._ensure_domain(conn, domain_name)
                qrow = conn.execute(
                    select(
                        v_call_center_queues.c.call_center_queue_uuid,
                        v_call_center_queues.c.queue_strategy,
                    )
                    .where(v_call_center_queues.c.domain_uuid == domain_uuid)
                    .where(v_call_center_queues.c.queue_extension == number)
                ).first()
                if qrow is None:
                    queue_uuid = str(uuid.uuid4())
                    conn.execute(
                        insert(v_call_center_queues).values(
                            call_center_queue_uuid=queue_uuid,
                            domain_uuid=domain_uuid,
                            queue_name=number,
                            queue_extension=number,
                            queue_strategy=strategy,
                            queue_moh_sound="$${hold_music}",
                            insert_date=datetime.now(UTC),
                        )
                    )
                    changed = True
                    cmds.append(f"callcenter_config queue load {number}@{domain_name}")
                else:
                    queue_uuid = qrow.call_center_queue_uuid
                    if qrow.queue_strategy != strategy:
                        conn.execute(
                            update(v_call_center_queues)
                            .where(
                                v_call_center_queues.c.call_center_queue_uuid
                                == queue_uuid
                            )
                            .values(queue_strategy=strategy)
                        )
                        changed = True
                        runtime_reload = True

                existing_tiers = conn.execute(
                    select(
                        v_call_center_tiers.c.call_center_tier_uuid,
                        v_call_center_tiers.c.call_center_agent_uuid,
                        v_call_center_tiers.c.agent_name,
                    ).where(
                        v_call_center_tiers.c.call_center_queue_uuid == queue_uuid
                    )
                ).fetchall()
                desired_agent_set = set(desired_agents)
                existing_tier_agents = {t.agent_name for t in existing_tiers}
                for tier in existing_tiers:
                    if tier.agent_name in desired_agent_set:
                        continue
                    conn.execute(
                        delete(v_call_center_tiers).where(
                            v_call_center_tiers.c.call_center_tier_uuid
                            == tier.call_center_tier_uuid
                        )
                    )
                    changed = True
                    runtime_reload = True

                    remaining_refs = conn.execute(
                        select(func.count())
                        .select_from(v_call_center_tiers)
                        .where(
                            v_call_center_tiers.c.call_center_agent_uuid
                            == tier.call_center_agent_uuid
                        )
                    ).scalar_one()
                    if remaining_refs == 0:
                        conn.execute(
                            delete(v_call_center_agents).where(
                                v_call_center_agents.c.call_center_agent_uuid
                                == tier.call_center_agent_uuid
                            )
                        )

                for ext in desired_agents:
                    arow = conn.execute(
                        select(v_call_center_agents.c.call_center_agent_uuid)
                        .where(v_call_center_agents.c.domain_uuid == domain_uuid)
                        .where(v_call_center_agents.c.agent_id == ext)
                    ).first()
                    contact = f"user/{ext}@{domain_name}"
                    if arow is None:
                        agent_uuid = str(uuid.uuid4())
                        conn.execute(
                            insert(v_call_center_agents).values(
                                call_center_agent_uuid=agent_uuid,
                                domain_uuid=domain_uuid,
                                agent_id=ext,
                                agent_name=ext,
                                agent_type="callback",
                                agent_contact=contact,
                                agent_status="Available (On Demand)",
                                insert_date=datetime.now(UTC),
                            )
                        )
                        changed = True
                        cmds += [
                            f"callcenter_config agent add {agent_uuid} 'callback'",
                            f"callcenter_config agent set contact {agent_uuid} '{contact}'",
                            f"callcenter_config agent set status {agent_uuid} 'Available (On Demand)'",
                        ]
                    else:
                        agent_uuid = arow.call_center_agent_uuid
                    if ext not in existing_tier_agents:
                        conn.execute(
                            insert(v_call_center_tiers).values(
                                call_center_tier_uuid=str(uuid.uuid4()),
                                domain_uuid=domain_uuid,
                                call_center_queue_uuid=queue_uuid,
                                call_center_agent_uuid=agent_uuid,
                                queue_name=number,
                                agent_name=ext,
                                tier_level=1,
                                tier_position=1,
                            )
                        )
                        changed = True
                        cmds.append(
                            f"callcenter_config tier add {number}@{domain_name} {agent_uuid} 1 1"
                        )
                queue_dp_name = feature_dialplan_name("queue", domain_name, number)
                dp_xml = (
                    f'<extension name="{_xml(queue_dp_name)}" continue="false">\n'
                    '  <condition field="${network_addr}" expression="^10\\.10\\.10\\.1$"/>\n'
                    f'  <condition field="${{sip_req_host}}" expression="{_domain_match(domain_name)}"/>\n'
                    f'  <condition field="destination_number" expression="^({re.escape(number)})$">\n'
                    '    <action application="answer"/>\n'
                    '    <action application="sleep" data="500"/>\n'
                    f'    <action application="callcenter" data="{_xml(number)}@{_xml(domain_name)}"/>\n'
                    '    <action application="hangup"/>\n'
                    "  </condition>\n"
                    "</extension>"
                )
                if self._ensure_dialplan(
                    conn, name=queue_dp_name, number=number, order=53, xml=dp_xml,
                    tag=f"dotmac-voice:queue:{domain_name}",
                ):
                    changed = True
                if runtime_reload:
                    cmds.insert(0, f"callcenter_config queue unload {number}@{domain_name}")
                    cmds.append(f"callcenter_config queue load {number}@{domain_name}")
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX queue insert failed: {exc}") from exc
        if changed:
            self._reload()
        for cmd in cmds:
            self._commander(cmd)
        return {
            "name": feature_dialplan_name("queue", domain_name, number),
            "created": changed,
            "media_caveat": (
                "caller<->agent media one-way (mod_callcenter bridge re-anchor); "
                "tracked follow-up"
            ),
        }

    def delete_dialplan(self, name: str) -> bool:
        """Idempotently remove a managed public-context dialplan by name."""
        deleted = False
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    delete(v_dialplans)
                    .where(v_dialplans.c.dialplan_name == name)
                    .where(v_dialplans.c.dialplan_context == "public")
                )
                deleted = result.rowcount > 0
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        if deleted:
            self._reload()
        return deleted

    def delete_voicemail(self, domain_name: str, number: str) -> bool:
        """Idempotently remove a voicemail box."""
        _require_domain(domain_name)
        _require_dial_token(number)
        deleted = False
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return False
                result = conn.execute(
                    delete(v_voicemails)
                    .where(v_voicemails.c.domain_uuid == domain_uuid)
                    .where(v_voicemails.c.voicemail_id == number)
                )
                deleted = result.rowcount > 0
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        if deleted:
            self._reload()
        return deleted

    def delete_queue(self, domain_name: str, number: str) -> bool:
        """Idempotently remove a call-center queue: tiers + queue row + dialplan, and
        issue a best-effort runtime ``callcenter_config queue unload``. Agents left
        without any tier references are removed too."""
        _require_domain(domain_name)
        _require_dial_token(number)
        deleted = False
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return False
                qrow = conn.execute(
                    select(v_call_center_queues.c.call_center_queue_uuid)
                    .where(v_call_center_queues.c.domain_uuid == domain_uuid)
                    .where(v_call_center_queues.c.queue_extension == number)
                ).first()
                if qrow is not None:
                    agent_ids = [
                        row.call_center_agent_uuid
                        for row in conn.execute(
                            select(v_call_center_tiers.c.call_center_agent_uuid).where(
                                v_call_center_tiers.c.call_center_queue_uuid
                                == qrow.call_center_queue_uuid
                            )
                        ).fetchall()
                    ]
                    conn.execute(
                        delete(v_call_center_tiers).where(
                            v_call_center_tiers.c.call_center_queue_uuid
                            == qrow.call_center_queue_uuid
                        )
                    )
                    conn.execute(
                        delete(v_call_center_queues).where(
                            v_call_center_queues.c.call_center_queue_uuid
                            == qrow.call_center_queue_uuid
                        )
                    )
                    for agent_uuid in agent_ids:
                        remaining_refs = conn.execute(
                            select(func.count())
                            .select_from(v_call_center_tiers)
                            .where(
                                v_call_center_tiers.c.call_center_agent_uuid
                                == agent_uuid
                            )
                        ).scalar_one()
                        if remaining_refs == 0:
                            conn.execute(
                                delete(v_call_center_agents).where(
                                    v_call_center_agents.c.call_center_agent_uuid
                                    == agent_uuid
                                )
                            )
                    deleted = True
                dp = conn.execute(
                    delete(v_dialplans)
                    .where(
                        v_dialplans.c.dialplan_name
                        == feature_dialplan_name("queue", domain_name, number)
                    )
                    .where(v_dialplans.c.dialplan_context == "public")
                )
                deleted = deleted or dp.rowcount > 0
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        if deleted:
            self._reload()
            self._commander(f"callcenter_config queue unload {number}@{domain_name}")
        return deleted

    def list_managed_dialplans(self, domain_name: str) -> set[str]:
        """Names of per-domain FEATURE dialplans (conference/ring-group/IVR) managed
        for this domain -- for drift reconciliation. Excludes shared routing + queues."""
        _require_domain(domain_name)
        tag = f"dotmac-voice:feature:{domain_name}"
        try:
            with self._engine.begin() as conn:
                rows = conn.execute(
                    select(v_dialplans.c.dialplan_name)
                    .where(v_dialplans.c.dialplan_description == tag)
                    .where(v_dialplans.c.dialplan_context == "public")
                ).fetchall()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return {r.dialplan_name for r in rows}

    def list_queues(self, domain_name: str) -> set[str]:
        """Queue extensions managed for this domain (scoped by domain_uuid)."""
        _require_domain(domain_name)
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return set()
                rows = conn.execute(
                    select(v_call_center_queues.c.queue_extension).where(
                        v_call_center_queues.c.domain_uuid == domain_uuid
                    )
                ).fetchall()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return {r.queue_extension for r in rows}

    def resync_queues(self, domain_name: str) -> dict:
        """Re-issue runtime callcenter_config for ALL of the domain's queues/agents/
        tiers from the persisted DB, restoring in-memory mod_callcenter state after a
        FreeSWITCH restart (DB rows alone don't reload). Idempotent -- mod_callcenter
        tolerates re-load/re-add."""
        _require_domain(domain_name)
        cmds: list[str] = []
        n_queues = n_agents = 0
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return {"queues": 0, "agents": 0}
                for q in conn.execute(
                    select(v_call_center_queues.c.queue_extension).where(
                        v_call_center_queues.c.domain_uuid == domain_uuid
                    )
                ).fetchall():
                    n_queues += 1
                    cmds.append(
                        f"callcenter_config queue load {q.queue_extension}@{domain_name}"
                    )
                for a in conn.execute(
                    select(
                        v_call_center_agents.c.call_center_agent_uuid,
                        v_call_center_agents.c.agent_contact,
                    ).where(v_call_center_agents.c.domain_uuid == domain_uuid)
                ).fetchall():
                    n_agents += 1
                    cmds += [
                        f"callcenter_config agent add {a.call_center_agent_uuid} 'callback'",
                        f"callcenter_config agent set contact {a.call_center_agent_uuid} '{a.agent_contact}'",
                        f"callcenter_config agent set status {a.call_center_agent_uuid} 'Available (On Demand)'",
                    ]
                for t in conn.execute(
                    select(
                        v_call_center_tiers.c.queue_name,
                        v_call_center_tiers.c.call_center_agent_uuid,
                        v_call_center_tiers.c.tier_level,
                        v_call_center_tiers.c.tier_position,
                    ).where(v_call_center_tiers.c.domain_uuid == domain_uuid)
                ).fetchall():
                    cmds.append(
                        f"callcenter_config tier add {t.queue_name}@{domain_name} "
                        f"{t.call_center_agent_uuid} {t.tier_level} {t.tier_position}"
                    )
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        for cmd in cmds:
            self._commander(cmd)
        return {"queues": n_queues, "agents": n_agents}

    def list_voicemail_messages(self, domain_name: str, extension: str) -> list[dict]:
        """List stored voicemail messages for an extension's box, newest first.
        Metadata only (no audio payload)."""
        _require_domain(domain_name)
        _require_dial_token(extension, "extension")
        try:
            with self._engine.begin() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return []
                rows = conn.execute(
                    select(
                        v_voicemail_messages.c.voicemail_message_uuid,
                        v_voicemail_messages.c.created_epoch,
                        v_voicemail_messages.c.caller_id_name,
                        v_voicemail_messages.c.caller_id_number,
                        v_voicemail_messages.c.message_length,
                        v_voicemail_messages.c.message_status,
                    )
                    .select_from(
                        v_voicemail_messages.join(
                            v_voicemails,
                            v_voicemail_messages.c.voicemail_uuid
                            == v_voicemails.c.voicemail_uuid,
                        )
                    )
                    .where(v_voicemails.c.domain_uuid == domain_uuid)
                    .where(v_voicemails.c.voicemail_id == extension)
                    .order_by(v_voicemail_messages.c.created_epoch.desc())
                ).fetchall()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return [
            {
                "message_uuid": r.voicemail_message_uuid,
                "created_epoch": r.created_epoch,
                "caller_id_name": r.caller_id_name,
                "caller_id_number": r.caller_id_number,
                "duration_seconds": r.message_length,
                "status": r.message_status,
            }
            for r in rows
        ]

    def check_readiness(self) -> dict:
        """Report whether the FreeSWITCH modules required by this control plane are
        enabled, per FusionPBX ``v_modules`` (the same source where a missing
        mod_voicemail showed up). Matches module_name by substring so it tolerates
        the ``mod_`` prefix; verify naming against the live v_modules in production."""
        required = ("voicemail", "callcenter")
        try:
            with self._engine.begin() as conn:
                rows = conn.execute(
                    select(v_modules.c.module_name, v_modules.c.module_enabled)
                ).fetchall()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        enabled = [(r.module_name or "").lower() for r in rows if bool(r.module_enabled)]
        modules = {req: any(req in name for name in enabled) for req in required}
        return {"ready": all(modules.values()), "modules": modules}

    def get_extension_secret(self, domain_name: str, number: str) -> str | None:
        """Return an extension's SIP password (for WebRTC client registration), or
        None if the domain/extension is unknown."""
        _require_domain(domain_name)
        try:
            with self._engine.connect() as conn:
                domain_uuid = self._domain_uuid_for(conn, domain_name)
                if domain_uuid is None:
                    return None
                row = conn.execute(
                    select(v_extensions.c.password)
                    .where(v_extensions.c.domain_uuid == domain_uuid)
                    .where(v_extensions.c.extension == number)
                ).first()
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        return row.password if row else None

    def create_ivr(
        self,
        domain_name: str,
        number: str,
        options: dict[str, str],
        *,
        greeting: str = "ivr/ivr-enter_ext_pound.wav",
        timeout: int = 6000,
    ) -> dict:
        """Idempotently provision an IVR menu <number>: play greeting, collect one
        digit, transfer to the mapped target (re-enters the public context)."""
        _require_domain(domain_name)
        _require_dial_token(number)
        if not options:
            raise BadRequestError("IVR requires at least one option")
        for digit, target in options.items():
            if not _IVR_DIGIT_RE.fullmatch(digit):
                raise BadRequestError("IVR option keys must be single digits")
            _require_dial_token(target, "IVR target")
        items = sorted(options.items())
        digits = "".join(d for d, _ in items)
        regex = f"^[{digits}]$"
        # Nested cond() mapping the collected digit -> target (last entry = default).
        expr = items[-1][1]
        for digit, tgt in reversed(items[:-1]):
            expr = "${cond(${ivr_choice} == " + digit + " ? " + tgt + " : " + expr + ")}"
        name = feature_dialplan_name("ivr", domain_name, number)
        xml = (
            f'<extension name="{_xml(name)}" continue="false">\n'
            '  <condition field="${network_addr}" expression="^10\\.10\\.10\\.1$"/>\n'
            f'  <condition field="${{sip_req_host}}" expression="{_domain_match(domain_name)}"/>\n'
            f'  <condition field="destination_number" expression="^({re.escape(number)})$">\n'
            '    <action application="answer"/>\n'
            '    <action application="sleep" data="500"/>\n'
            '    <action application="play_and_get_digits" '
            f'data="1 1 3 {timeout} # {_xml(greeting)} silence_stream://250 ivr_choice {regex}"/>\n'
            f'    <action application="set" data="ivr_target={_xml(expr)}"/>\n'
            '    <action application="transfer" data="${ivr_target} XML public"/>\n'
            "  </condition>\n"
            "</extension>"
        )
        changed = False
        try:
            with self._engine.begin() as conn:
                self._ensure_domain(conn, domain_name)
                changed = self._ensure_dialplan(
                    conn, name=name, number=number, order=54, xml=xml,
                    tag=f"dotmac-voice:feature:{domain_name}",
                )
        except _UNAVAILABLE as exc:
            raise ServiceUnavailableError(f"FusionPBX DB unreachable: {exc}") from exc
        except IntegrityError as exc:
            raise BadRequestError(f"FusionPBX IVR insert failed: {exc}") from exc
        if changed:
            self._reload()
        return {"name": name, "created": changed}

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
        _require_domain(domain_name)
        _require_dial_token(number)
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
