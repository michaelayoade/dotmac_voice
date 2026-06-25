"""Voice reconciliation service: delta computation and apply."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voice import (
    ConferenceRoom,
    Extension,
    IvrMenu,
    Queue,
    RingGroup,
    SyncStatus,
    VoiceDomain,
)
from app.services.exceptions import NotFoundError, ServiceUnavailableError
from app.services.fusionpbx.client import feature_dialplan_name


@dataclass(frozen=True)
class VoiceDelta:
    """Represents differences between desired and actual extensions."""

    to_create: set[str]
    to_delete: set[str]


def compute_delta(desired_numbers: set[str], actual_numbers: set[str]) -> VoiceDelta:
    """Compute set differences for extension reconciliation.

    Args:
        desired_numbers: Extensions defined in database
        actual_numbers: Extensions present on FusionPBX

    Returns:
        VoiceDelta with to_create (missing on PBX) and to_delete (extra on PBX)
    """
    return VoiceDelta(
        to_create=desired_numbers - actual_numbers,
        to_delete=actual_numbers - desired_numbers,
    )


def reconcile_voice(db: Session, client, customer_id: str) -> SyncStatus:
    """Reconcile desired extensions vs actual FusionPBX extensions.

    Reads desired Extension rows from database, fetches actual extensions
    from FusionPBX client, computes delta, creates missing extensions, and
    deletes stale extensions so the live PBX matches desired state.
    Sets VoiceDomain.sync_status and last_reconciled_at.

    Args:
        db: SQLAlchemy session
        client: FusionpbxClient instance with list_extensions and create_extension
        customer_id: Customer identifier to reconcile

    Returns:
        SyncStatus (synced or error)

    Raises:
        NotFoundError: If no VoiceDomain exists for customer_id
    """
    # Fetch domain. with_for_update locks the row for this transaction so two
    # concurrent reconciles for the same customer serialize instead of racing on
    # the create/delete deltas (Postgres row lock; SQLite ignores it).
    domain = db.scalar(
        select(VoiceDomain)
        .where(VoiceDomain.customer_id == customer_id)
        .with_for_update()
    )
    if not domain:
        raise NotFoundError(f"No voice domain for customer {customer_id}")

    # Collect desired extensions from database (keep rows for feature reconcile).
    # Suspended (is_active=False) -> desired is empty so reconcile removes the
    # customer's FusionPBX extensions (can't register/call); models are preserved.
    desired_exts = list(
        db.scalars(select(Extension).where(Extension.voice_domain_id == domain.id))
    )
    desired = {e.number for e in desired_exts} if domain.is_active else set()

    try:
        # Fetch actual extensions from FusionPBX
        actual = {e["number"] for e in client.list_extensions(domain.fusionpbx_domain)}

        # Compute delta
        delta = compute_delta(desired, actual)

        desired_by_number = {e.number: e for e in desired_exts}

        # Create missing extensions and refresh mutable extension metadata on
        # existing rows (display name/caller ID) through the idempotent client.
        for number in sorted(desired):
            ext = desired_by_number[number]
            client.create_extension(
                domain.fusionpbx_domain,
                number,
                password="",
                display_name=ext.display_name,
            )

        # Delete stale extensions (sorted for determinism)
        for number in sorted(delta.to_delete):
            client.delete_extension(domain.fusionpbx_domain, number)
            if hasattr(client, "delete_voicemail"):
                client.delete_voicemail(domain.fusionpbx_domain, number)

        if domain.is_active:
            # Ensure a voicemail box for each voicemail-enabled extension.
            for ext in sorted(desired_exts, key=lambda e: e.number):
                if ext.voicemail_enabled:
                    client.ensure_voicemail(domain.fusionpbx_domain, ext.number)
                elif hasattr(client, "delete_voicemail"):
                    client.delete_voicemail(domain.fusionpbx_domain, ext.number)

            # Bootstrap the switch + ensure the FS-in-path internal routing dialplan
            # (idempotent). These make extensions reachable + voicemail land. (Global
            # routing is shared, so suspension enforces via extension removal above,
            # not by tearing down routing.)
            client.ensure_switch_settings()
            client.ensure_routing(
                domain.fusionpbx_domain, recording=domain.recording_enabled
            )

            # Reconcile features: apply desired models, then delete undefined (drift).
            dom_name = domain.fusionpbx_domain
            desired_dialplans: set[str] = set()
            for c in db.scalars(
                select(ConferenceRoom).where(
                    ConferenceRoom.voice_domain_id == domain.id
                )
            ):
                client.create_conference(dom_name, c.number)
                desired_dialplans.add(
                    feature_dialplan_name("conference", dom_name, c.number)
                )
            for r in db.scalars(
                select(RingGroup).where(RingGroup.voice_domain_id == domain.id)
            ):
                client.create_ring_group(
                    dom_name,
                    r.number,
                    list(r.members),
                    strategy=r.strategy,
                    timeout=r.timeout,
                )
                desired_dialplans.add(
                    feature_dialplan_name("ringgroup", dom_name, r.number)
                )
            for i in db.scalars(
                select(IvrMenu).where(IvrMenu.voice_domain_id == domain.id)
            ):
                client.create_ivr(
                    dom_name, i.number, dict(i.options), greeting=i.greeting
                )
                desired_dialplans.add(feature_dialplan_name("ivr", dom_name, i.number))
            for name in client.list_managed_dialplans(dom_name) - desired_dialplans:
                client.delete_dialplan(name)

            desired_queues: set[str] = set()
            for q in db.scalars(
                select(Queue).where(Queue.voice_domain_id == domain.id)
            ):
                client.ensure_queue(
                    dom_name,
                    q.number,
                    agents=list(q.agents),
                    name=q.name,
                    strategy=q.strategy,
                )
                desired_queues.add(q.number)
            for num in client.list_queues(dom_name) - desired_queues:
                client.delete_queue(dom_name, num)
        else:
            # Suspended customers must not retain feature entry points. Extension
            # removal blocks registrations and direct user routes; this removes
            # PBX-hosted features such as IVRs, conferences, ring groups, queues.
            for name in client.list_managed_dialplans(domain.fusionpbx_domain):
                client.delete_dialplan(name)
            for num in client.list_queues(domain.fusionpbx_domain):
                client.delete_queue(domain.fusionpbx_domain, num)

        domain.sync_status = SyncStatus.synced

    except ServiceUnavailableError:
        # Mark as error if FusionPBX is unavailable
        domain.sync_status = SyncStatus.error

    # Always update reconciliation timestamp
    domain.last_reconciled_at = datetime.now(UTC)
    db.flush()

    return domain.sync_status
