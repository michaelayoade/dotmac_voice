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
        conn.execute(
            text(
                """
                CREATE TABLE v_dialplans (
                    dialplan_uuid TEXT PRIMARY KEY,
                    domain_uuid TEXT,
                    app_uuid TEXT,
                    dialplan_context TEXT,
                    dialplan_name TEXT,
                    dialplan_number TEXT,
                    dialplan_order INTEGER,
                    dialplan_enabled BOOLEAN,
                    dialplan_continue BOOLEAN,
                    dialplan_xml TEXT,
                    dialplan_description TEXT,
                    insert_date TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE v_default_settings (
                    default_setting_uuid TEXT PRIMARY KEY,
                    default_setting_category TEXT,
                    default_setting_subcategory TEXT,
                    default_setting_name TEXT,
                    default_setting_value TEXT,
                    default_setting_enabled BOOLEAN,
                    insert_date TEXT
                )
                """
            )
        )
        conn.execute(text(
            "CREATE TABLE v_call_center_queues (call_center_queue_uuid TEXT PRIMARY KEY, "
            "domain_uuid TEXT, queue_name TEXT, queue_extension TEXT, queue_strategy TEXT, "
            "queue_moh_sound TEXT, insert_date TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE v_call_center_agents (call_center_agent_uuid TEXT PRIMARY KEY, "
            "domain_uuid TEXT, agent_id TEXT, agent_name TEXT, agent_type TEXT, "
            "agent_contact TEXT, agent_status TEXT, insert_date TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE v_call_center_tiers (call_center_tier_uuid TEXT PRIMARY KEY, "
            "domain_uuid TEXT, call_center_queue_uuid TEXT, call_center_agent_uuid TEXT, "
            "queue_name TEXT, agent_name TEXT, tier_level INTEGER, tier_position INTEGER, "
            "insert_date TEXT)"
        ))
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


class TestCreateConference:
    def test_creates_public_dialplan(self, client, fpbx_engine):
        result = client.create_conference("a.local", "3001")
        assert result["created"] is True
        with fpbx_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT dialplan_context, dialplan_xml FROM v_dialplans "
                    "WHERE dialplan_name = :n"
                ),
                {"n": "kamailio-conference-3001"},
            ).first()
        assert row.dialplan_context == "public"
        assert 'application="conference"' in row.dialplan_xml
        assert "3001@default" in row.dialplan_xml
        assert "${network_addr}" in row.dialplan_xml

    def test_is_idempotent(self, client, reloader):
        client.create_conference("a.local", "3001")
        reloader.reset_mock()
        second = client.create_conference("a.local", "3001")
        assert second["created"] is False
        reloader.assert_not_called()


class TestCreateRingGroup:
    def test_bridges_members_simultaneously(self, client, fpbx_engine):
        result = client.create_ring_group("a.local", "2000", ["1002", "1003"])
        assert result["created"] is True
        with fpbx_engine.connect() as conn:
            xml = conn.execute(
                text("SELECT dialplan_xml FROM v_dialplans WHERE dialplan_name = :n"),
                {"n": "kamailio-ringgroup-2000"},
            ).scalar_one()
        # simultaneous = comma-joined user/ bridges routed via the dial-string unlock
        assert 'user/1002@${domain_name},user/1003@${domain_name}' in xml
        assert 'application="bridge"' in xml

    def test_is_idempotent(self, client, reloader):
        client.create_ring_group("a.local", "2000", ["1002", "1003"])
        reloader.reset_mock()
        second = client.create_ring_group("a.local", "2000", ["1002", "1003"])
        assert second["created"] is False
        reloader.assert_not_called()


class TestCreateIvr:
    def test_builds_menu(self, client, fpbx_engine):
        result = client.create_ivr("a.local", "4000", {"1": "1002", "2": "3001"})
        assert result["created"] is True
        with fpbx_engine.connect() as conn:
            xml = conn.execute(
                text("SELECT dialplan_xml FROM v_dialplans WHERE dialplan_name = :n"),
                {"n": "kamailio-ivr-4000"},
            ).scalar_one()
        assert 'application="play_and_get_digits"' in xml
        assert "^[12]$" in xml
        assert "${ivr_choice} == 1 ? 1002" in xml
        assert 'data="${ivr_target} XML public"' in xml


class TestEnsureRouting:
    def test_creates_internal_routing(self, client, fpbx_engine):
        result = client.ensure_routing("a.local")
        assert result["created"] is True
        with fpbx_engine.connect() as conn:
            xml = conn.execute(
                text("SELECT dialplan_xml FROM v_dialplans WHERE dialplan_name = :n"),
                {"n": "kamailio-internal-to-domain"},
            ).scalar_one()
        assert "sofia/external/${destination_number}@10.10.10.1:5060" in xml
        assert "app.lua voicemail" in xml  # no-answer voicemail fallback
        assert "record_session" not in xml  # recording off by default

    def test_recording_adds_record_session(self, client, fpbx_engine):
        client.ensure_routing("a.local", recording=True)
        with fpbx_engine.connect() as conn:
            xml = conn.execute(
                text("SELECT dialplan_xml FROM v_dialplans WHERE dialplan_name = :n"),
                {"n": "kamailio-internal-to-domain"},
            ).scalar_one()
        assert "execute_on_answer=record_session" in xml


class TestEnsureSwitchSettings:
    def test_sets_voicemail_dir(self, client, fpbx_engine):
        result = client.ensure_switch_settings()
        assert result["changed"] is True
        with fpbx_engine.connect() as conn:
            val = conn.execute(
                text(
                    "SELECT default_setting_value FROM v_default_settings "
                    "WHERE default_setting_subcategory='voicemail' "
                    "AND default_setting_name='dir'"
                )
            ).scalar_one()
        assert val == "/var/lib/freeswitch/storage/voicemail"

    def test_is_idempotent(self, client, reloader):
        client.ensure_switch_settings()
        reloader.reset_mock()
        second = client.ensure_switch_settings()
        assert second["changed"] is False
        reloader.assert_not_called()


class TestEnsureQueue:
    def test_provisions_queue_agents_tiers_dialplan(self, fpbx_engine):
        commander = MagicMock()
        c = FusionpbxClient(
            engine=fpbx_engine, reloader=MagicMock(), commander=commander
        )
        result = c.ensure_queue("a.local", "5000", agents=["1002", "1003"])
        assert result["created"] is True
        with fpbx_engine.connect() as conn:
            q = conn.execute(
                text(
                    "SELECT queue_name FROM v_call_center_queues "
                    "WHERE queue_extension='5000'"
                )
            ).first()
            agents = conn.execute(
                text("SELECT agent_id, agent_contact FROM v_call_center_agents")
            ).fetchall()
            tiers = conn.execute(
                text("SELECT count(*) FROM v_call_center_tiers")
            ).scalar_one()
            dp = conn.execute(
                text(
                    "SELECT dialplan_xml FROM v_dialplans "
                    "WHERE dialplan_name='kamailio-queue-5000'"
                )
            ).scalar_one()
        # GOTCHA: callcenter.conf names the queue by queue_name -> set it to the number.
        assert q.queue_name == "5000"
        assert {a.agent_id for a in agents} == {"1002", "1003"}
        assert all("user/" in a.agent_contact for a in agents)
        assert tiers == 2
        assert "callcenter" in dp and "5000@a.local" in dp
        # Runtime activation issued over ESL (DB rows alone don't load into mod_callcenter).
        cmds = " ".join(call.args[0] for call in commander.call_args_list)
        assert "queue load 5000@a.local" in cmds
        assert "tier add 5000@a.local" in cmds

    def test_is_idempotent(self, fpbx_engine):
        reloader = MagicMock()
        c = FusionpbxClient(engine=fpbx_engine, reloader=reloader, commander=MagicMock())
        c.ensure_queue("a.local", "5000", agents=["1002"])
        reloader.reset_mock()
        second = c.ensure_queue("a.local", "5000", agents=["1002"])
        assert second["created"] is False
        reloader.assert_not_called()


class TestDeletePrimitives:
    def test_delete_dialplan(self, client, fpbx_engine):
        client.create_conference("a.local", "3001")
        assert client.delete_dialplan("kamailio-conference-3001") is True
        with fpbx_engine.connect() as conn:
            n = conn.execute(
                text("SELECT count(*) FROM v_dialplans WHERE dialplan_name = :n"),
                {"n": "kamailio-conference-3001"},
            ).scalar_one()
        assert n == 0
        assert client.delete_dialplan("kamailio-conference-3001") is False  # idempotent

    def test_delete_voicemail(self, client):
        client.ensure_voicemail("a.local", "1001")
        assert client.delete_voicemail("a.local", "1001") is True
        assert client.delete_voicemail("a.local", "1001") is False

    def test_delete_queue(self, fpbx_engine):
        commander = MagicMock()
        c = FusionpbxClient(engine=fpbx_engine, reloader=MagicMock(), commander=commander)
        c.ensure_queue("a.local", "5000", agents=["1002", "1003"])
        commander.reset_mock()
        assert c.delete_queue("a.local", "5000") is True
        with fpbx_engine.connect() as conn:
            q = conn.execute(
                text("SELECT count(*) FROM v_call_center_queues WHERE queue_extension='5000'")
            ).scalar_one()
            tiers = conn.execute(text("SELECT count(*) FROM v_call_center_tiers")).scalar_one()
            dp = conn.execute(
                text("SELECT count(*) FROM v_dialplans WHERE dialplan_name='kamailio-queue-5000'")
            ).scalar_one()
        assert q == 0 and tiers == 0 and dp == 0
        cmds = " ".join(call.args[0] for call in commander.call_args_list)
        assert "queue unload 5000@a.local" in cmds
        assert c.delete_queue("a.local", "5000") is False  # idempotent
