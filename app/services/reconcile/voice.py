"""Voice reconciliation service: delta computation and apply."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voice import Extension, SyncStatus, VoiceDomain
from app.services.exceptions import NotFoundError, ServiceUnavailableError


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
    # Fetch domain
    domain = db.scalar(
        select(VoiceDomain).where(VoiceDomain.customer_id == customer_id)
    )
    if not domain:
        raise NotFoundError(f"No voice domain for customer {customer_id}")

    # Collect desired extensions from database (keep rows for feature reconcile)
    desired_exts = list(
        db.scalars(select(Extension).where(Extension.voice_domain_id == domain.id))
    )
    desired = {e.number for e in desired_exts}

    try:
        # Fetch actual extensions from FusionPBX
        actual = {e["number"] for e in client.list_extensions(domain.fusionpbx_domain)}

        # Compute delta
        delta = compute_delta(desired, actual)

        # Create missing extensions (sorted for determinism)
        for number in sorted(delta.to_create):
            client.create_extension(
                domain.fusionpbx_domain, number, password="", display_name=""
            )

        # Delete stale extensions (sorted for determinism)
        for number in sorted(delta.to_delete):
            client.delete_extension(domain.fusionpbx_domain, number)

        # Ensure a voicemail box for each voicemail-enabled extension.
        for ext in sorted(desired_exts, key=lambda e: e.number):
            if ext.voicemail_enabled:
                client.ensure_voicemail(domain.fusionpbx_domain, ext.number)

        domain.sync_status = SyncStatus.synced

    except ServiceUnavailableError:
        # Mark as error if FusionPBX is unavailable
        domain.sync_status = SyncStatus.error

    # Always update reconciliation timestamp
    domain.last_reconciled_at = datetime.now(UTC)
    db.flush()

    return domain.sync_status
