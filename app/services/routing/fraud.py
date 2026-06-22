from dataclasses import dataclass


@dataclass(frozen=True)
class DialPolicy:
    allowed_destinations: tuple[str, ...] | None = None  # if set, ONLY these exact destinations allowed (caged scope, e.g. ("support",))
    allow_international: bool = False
    blocked_prefixes: tuple[str, ...] = ()               # premium-rate / blocked number prefixes (deny)
    domestic_cc: str = "234"                              # Nigeria; numbers starting with this (or local) are domestic


@dataclass(frozen=True)
class DialDecision:
    allowed: bool
    reason: str          # machine code: "ok" | "not_in_allowlist" | "international_blocked" | "blocked_prefix"
    classification: str  # "internal" | "support" | "domestic" | "international"


def classify_destination(
    destination: str,
    *,
    domestic_cc: str = "234",
    support_names: tuple[str, ...] = ("support",),
) -> str:
    """Classify a destination as one of: support, internal, domestic, international."""
    # Support name check
    if destination in support_names:
        return "support"

    # Internal extension: all digits, 6 or fewer characters
    if destination.isdigit() and len(destination) <= 6:
        return "internal"

    # Normalize and classify numeric destinations
    # Strip leading +/00 to check the country code
    normalized = destination.lstrip("+")
    if normalized.startswith("00"):
        normalized = normalized[2:]

    # Check if it starts with domestic country code
    if normalized.startswith(domestic_cc):
        # If the original had +/00, it's international format but domestic number
        if destination.startswith("+") or destination.startswith("00"):
            return "domestic"
        # Otherwise it's a domestic number
        return "domestic"

    # If it starts with + or 00, it's international (country code is not domestic)
    if destination.startswith("+") or destination.startswith("00"):
        return "international"

    # Long numeric numbers without prefix default to domestic
    if destination.isdigit() and len(destination) > 6:
        return "domestic"

    # Default to domestic
    return "domestic"


def check_dial(destination: str, policy: DialPolicy) -> DialDecision:
    """Check if a dial is permitted under the given policy."""
    # 1. Scope caging check FIRST (overrides everything)
    if policy.allowed_destinations is not None:
        if destination not in policy.allowed_destinations:
            classification = classify_destination(
                destination, domestic_cc=policy.domestic_cc
            )
            return DialDecision(
                allowed=False,
                reason="not_in_allowlist",
                classification=classification,
            )

    # 2. Classify the destination
    classification = classify_destination(
        destination, domestic_cc=policy.domestic_cc
    )

    # 3. Check blocked prefixes
    for prefix in policy.blocked_prefixes:
        if destination.startswith(prefix):
            return DialDecision(
                allowed=False,
                reason="blocked_prefix",
                classification=classification,
            )

    # 4. Check international restriction
    if classification == "international" and not policy.allow_international:
        return DialDecision(
            allowed=False,
            reason="international_blocked",
            classification=classification,
        )

    # 5. All checks passed
    return DialDecision(
        allowed=True,
        reason="ok",
        classification=classification,
    )
