"""Tests for the FusionPBX DB-backed provisioning client.

These exercise the client against an in-memory SQLite engine carrying minimal
``v_domains`` / ``v_extensions`` tables (TEXT booleans, matching production).
The ESL reload is always mocked so unit tests never touch a real ESL socket.
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.services.exceptions import ServiceUnavailableError
from app.services.fusionpbx.client import FusionpbxClient


@pytest.fixture()
def fpbx_engine() -> Engine:
    """In-memory SQLite engine with minimal FusionPBX tables."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE v_domains (
                    domain_uuid TEXT PRIMARY KEY,
                    domain_name TEXT,
                    domain_enabled BOOLEAN,
                    domain_description TEXT,
                    insert_date TEXT,
                    insert_user TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE v_extensions (
                    extension_uuid TEXT PRIMARY KEY,
                    domain_uuid TEXT,
                    extension TEXT,
                    password TEXT,
                    accountcode TEXT,
                    user_context TEXT,
                    effective_caller_id_name TEXT,
                    effective_caller_id_number TEXT,
                    outbound_caller_id_name TEXT,
                    outbound_caller_id_number TEXT,
                    call_timeout INTEGER,
                    enabled BOOLEAN,
                    directory_first_name TEXT,
                    description TEXT,
                    dial_string TEXT,
                    insert_date TEXT,
                    insert_user TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE v_voicemails (
                    voicemail_uuid TEXT PRIMARY KEY,
                    domain_uuid TEXT,
                    voicemail_id TEXT,
                    voicemail_password TEXT,
                    voicemail_enabled BOOLEAN,
                    insert_date TEXT
                )
                """
            )
        )
    return engine


@pytest.fixture()
def reloader() -> MagicMock:
    """Mock ESL reload callable."""
    return MagicMock()


@pytest.fixture()
def client(fpbx_engine: Engine, reloader: MagicMock) -> FusionpbxClient:
    return FusionpbxClient(engine=fpbx_engine, reloader=reloader)


class TestCreateDomain:
    def test_creates_and_returns_shape(self, client, reloader):
        result = client.create_domain("a.local")
        assert result["name"] == "a.local"
        assert result["domain_uuid"]
        reloader.assert_called_once()

    def test_is_idempotent(self, client, reloader):
        first = client.create_domain("a.local")
        reloader.reset_mock()
        second = client.create_domain("a.local")
        assert first["domain_uuid"] == second["domain_uuid"]
        # No insert happened the second time -> no reload.
        reloader.assert_not_called()

    def test_stores_boolean_enabled(self, client, fpbx_engine):
        client.create_domain("a.local")
        with fpbx_engine.connect() as conn:
            enabled = conn.execute(
                text("SELECT domain_enabled FROM v_domains WHERE domain_name = :n"),
                {"n": "a.local"},
            ).scalar_one()
        # FusionPBX domain_enabled is a real boolean column (1/True), not text.
        assert enabled == 1


class TestListDomains:
    def test_lists_domains(self, client):
        client.create_domain("a.local")
        client.create_domain("b.local")
        names = {d["name"] for d in client.list_domains()}
        assert names == {"a.local", "b.local"}
        for d in client.list_domains():
            assert d["enabled"] is True
            assert d["domain_uuid"]


class TestListExtensions:
    def test_empty_when_domain_missing(self, client):
        assert client.list_extensions("missing.local") == []

    def test_parses_extensions(self, client):
        client.create_extension("a.local", "1001", display_name="Alice")
        client.create_extension("a.local", "1002", display_name="Bob")
        rows = client.list_extensions("a.local")
        numbers = {r["number"] for r in rows}
        assert numbers == {"1001", "1002"}
        for r in rows:
            assert r["extension_uuid"]


class TestCreateExtension:
    def test_auto_ensures_domain(self, client, fpbx_engine):
        # Domain does not exist yet.
        client.create_extension("new.local", "1001")
        with fpbx_engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM v_domains WHERE domain_name = :n"),
                {"n": "new.local"},
            ).scalar_one()
        assert count == 1

    def test_generates_password_when_empty(self, client):
        result = client.create_extension("a.local", "1001", password="")
        assert result["password"]
        assert len(result["password"]) >= 16

    def test_passes_through_provided_password(self, client):
        result = client.create_extension("a.local", "1001", password="hunter2")
        assert result["password"] == "hunter2"

    def test_sets_convention_columns(self, client, fpbx_engine):
        client.create_extension("a.local", "1001", display_name="Alice")
        with fpbx_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT accountcode, user_context, enabled, call_timeout, "
                    "effective_caller_id_name, effective_caller_id_number "
                    "FROM v_extensions WHERE extension = :e"
                ),
                {"e": "1001"},
            ).one()
        assert row.accountcode == "a.local"
        assert row.user_context == "a.local"
        assert row.enabled == 1
        assert row.call_timeout == 30
        assert row.effective_caller_id_name == "Alice"
        assert row.effective_caller_id_number == "1001"

    def test_is_idempotent(self, client, reloader):
        first = client.create_extension("a.local", "1001", password="hunter2")
        reloader.reset_mock()
        second = client.create_extension("a.local", "1001", password="ignored")
        assert first["extension_uuid"] == second["extension_uuid"]
        # Existing extension returned unchanged, including original password.
        assert second["password"] == "hunter2"
        reloader.assert_not_called()

    def test_returns_shape(self, client):
        result = client.create_extension("a.local", "1001")
        assert set(result) >= {"number", "extension_uuid", "password"}
        assert result["number"] == "1001"

    def test_triggers_reload_on_write(self, client, reloader):
        client.create_extension("a.local", "1001")
        reloader.assert_called()


class TestDeleteExtension:
    def test_deletes_existing_extension(self, client, fpbx_engine, reloader):
        client.create_extension("a.local", "1001")
        reloader.reset_mock()

        assert client.delete_extension("a.local", "1001") is True

        with fpbx_engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM v_extensions WHERE extension = :e"),
                {"e": "1001"},
            ).scalar_one()
        assert count == 0
        reloader.assert_called_once()

    def test_missing_extension_is_noop(self, client, reloader):
        client.create_domain("a.local")
        reloader.reset_mock()

        assert client.delete_extension("a.local", "9999") is False
        reloader.assert_not_called()

    def test_missing_domain_is_noop(self, client, reloader):
        assert client.delete_extension("missing.local", "1001") is False
        reloader.assert_not_called()


class TestErrorMapping:
    def test_operational_error_maps_to_service_unavailable(self, reloader):
        engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
        # No tables created -> querying v_domains raises OperationalError,
        # which the client must map to ServiceUnavailableError.
        c = FusionpbxClient(engine=engine, reloader=reloader)
        with pytest.raises(ServiceUnavailableError):
            c.list_domains()
        with pytest.raises(ServiceUnavailableError):
            c.create_domain("a.local")
        reloader.assert_not_called()

    def test_unhandled_operational_error_is_operationalerror(self):
        # Sanity: confirm the underlying driver does raise OperationalError so
        # the mapping above is meaningful.
        engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
        with pytest.raises(OperationalError), engine.connect() as conn:
            conn.execute(text("SELECT * FROM v_domains"))


class TestContextManager:
    def test_context_manager_disposes_owned_engine(self):
        with FusionpbxClient("sqlite+pysqlite:///:memory:", reloader=MagicMock()) as c:
            assert isinstance(c._engine, Engine)

    def test_does_not_dispose_injected_engine(self, fpbx_engine, reloader):
        c = FusionpbxClient(engine=fpbx_engine, reloader=reloader)
        c.close()
        # Injected engine still usable after close().
        with fpbx_engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def test_requires_db_url_or_engine(self):
        with pytest.raises(ValueError):
            FusionpbxClient()


class TestReloadIsNonFatal:
    def test_reload_failure_does_not_break_write(self, fpbx_engine):
        def boom() -> None:
            raise RuntimeError("ESL down")

        c = FusionpbxClient(engine=fpbx_engine, reloader=boom)
        # Write succeeds despite the reloader raising.
        result = c.create_domain("a.local")
        assert result["name"] == "a.local"


class TestDialStringUnlock:
    """create_extension must stamp the Kamailio dial-string so FusionPBX user/<ext>
    bridges (ring groups, IVR, queues) reach WS clients registered on Kamailio."""

    def test_create_extension_sets_dial_string(self, client, fpbx_engine):
        from app.services.fusionpbx.client import DIAL_STRING_UNLOCK

        client.create_extension("a.local", "1001")
        with fpbx_engine.connect() as conn:
            ds = conn.execute(
                text("SELECT dial_string FROM v_extensions WHERE extension = :e"),
                {"e": "1001"},
            ).scalar_one()
        assert ds == DIAL_STRING_UNLOCK
        assert "sofia/external/${dialed_user}@10.10.10.1:5060" in ds


class TestEnsureVoicemail:
    def test_creates_box(self, client, fpbx_engine):
        result = client.ensure_voicemail("a.local", "1001")
        assert result == {"voicemail_id": "1001", "created": True}
        with fpbx_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT voicemail_id, voicemail_enabled FROM v_voicemails "
                    "WHERE voicemail_id = :i"
                ),
                {"i": "1001"},
            ).first()
        assert row.voicemail_id == "1001"
        assert row.voicemail_enabled == 1

    def test_is_idempotent(self, client, reloader):
        client.ensure_voicemail("a.local", "1001")
        reloader.reset_mock()
        second = client.ensure_voicemail("a.local", "1001")
        assert second["created"] is False
        reloader.assert_not_called()
