import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DialPolicy:
    allowed_destinations: tuple[str, ...] | None = None  # if set, ONLY these exact destinations allowed (caged scope, e.g. ("support",))
    allow_international: bool = False
    blocked_prefixes: tuple[str, ...] = ()               # premium-rate / blocked number prefixes; MUST be plain digit strings e.g. "234900"
    domestic_cc: str = "234"                              # Nigeria; numbers starting with this (or local) are domestic


@dataclass(frozen=True)
class DialDecision:
    allowed: bool
    reason: str          # machine code: "ok" | "not_in_allowlist" | "invalid_destination" | "blocked_prefix" | "international_blocked"
    classification: str  # "internal" | "support" | "domestic" | "international" | "invalid"


def _normalize(destination: str) -> tuple[str, str]:
    """Return (kind, digits).

    kind is 'intl' if an international escape (+ or 00) is present, else 'national'.
    digits has ALL non-digit characters removed (spaces, dashes, parens, dots, +).

    International escapes on this platform are ONLY '+' and '00'.
    A leading single '0' (e.g. '08012345678', '012345678', '011...') is the
    Nigerian national trunk prefix and is classified as 'national', not international.
    """
    s = destination.strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if has_plus:
        return ("intl", digits)        # digits = country code + subscriber number
    if digits.startswith("00"):
        return ("intl", digits[2:])    # 00 = Nigerian international access code; strip it
    return ("national", digits)


def classify_destination(
    destination: str,
    *,
    domestic_cc: str = "234",
    support_names: tuple[str, ...] = ("support",),
) -> str:
    """Classify a destination as one of: support, internal, domestic, international, invalid.

    Normalization is applied before classification so that formatting characters
    (spaces, dashes, parens, dots) cannot bypass the international check.
    """
    # Support name check (exact match on stripped raw value)
    if destination.strip() in support_names:
        return "support"

    kind, digits = _normalize(destination)

    # No usable digits → invalid
    if not digits:
        return "invalid"

    if kind == "intl":
        # International escape present: domestic only if CC matches
        return "domestic" if digits.startswith(domestic_cc) else "international"

    # National: short = internal extension, long = domestic
    return "internal" if len(digits) <= 6 else "domestic"


def check_dial(destination: str, policy: DialPolicy) -> DialDecision:
    """Check if a dial is permitted under the given policy.

    Evaluation order (security-critical; do not reorder):
    1. Scope caging (allowed_destinations) — DENY not_in_allowlist
    2. Classify destination
    3. Invalid destination — DENY invalid_destination (fail closed)
    4. Blocked prefixes (matched on normalized digits) — DENY blocked_prefix
    5. International restriction — DENY international_blocked
    6. ALLOW ok
    """
    # 1. Scope caging check FIRST (overrides everything)
    if policy.allowed_destinations is not None:
        if destination.strip() not in policy.allowed_destinations:
            classification = classify_destination(
                destination, domestic_cc=policy.domestic_cc
            )
            return DialDecision(
                allowed=False,
                reason="not_in_allowlist",
                classification=classification,
            )

    # 2. Classify the destination
    classification = classify_destination(destination, domestic_cc=policy.domestic_cc)

    # 3. Fail closed on invalid destinations
    if classification == "invalid":
        return DialDecision(
            allowed=False,
            reason="invalid_destination",
            classification=classification,
        )

    # 4. Check blocked prefixes on normalized digits
    # blocked_prefixes must be supplied as plain digit strings (e.g. "234900").
    # We use _normalize so that 00-prefixed international numbers match the same
    # digit prefix as their + equivalents (e.g. "00234900..." and "+234900..."
    # both normalize to digits starting "234900").
    _, norm_digits = _normalize(destination)
    if any(norm_digits.startswith(p) for p in policy.blocked_prefixes):
        return DialDecision(
            allowed=False,
            reason="blocked_prefix",
            classification=classification,
        )

    # 5. Check international restriction
    if classification == "international" and not policy.allow_international:
        return DialDecision(
            allowed=False,
            reason="international_blocked",
            classification=classification,
        )

    # 6. All checks passed
    return DialDecision(
        allowed=True,
        reason="ok",
        classification=classification,
    )
