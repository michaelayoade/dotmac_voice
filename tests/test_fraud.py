import pytest
from app.services.routing.fraud import (
    DialPolicy,
    DialDecision,
    classify_destination,
    check_dial,
)


class TestClassifyDestination:
    """Test classification logic for dial destinations."""

    def test_classify_support_name(self):
        """Support name destinations are classified as 'support'."""
        result = classify_destination("support")
        assert result == "support"

    def test_classify_support_with_custom_names(self):
        """Custom support names can be specified."""
        result = classify_destination("helpdesk", support_names=("helpdesk", "support"))
        assert result == "support"

    def test_classify_internal_extension(self):
        """Numeric destinations 6 digits or less are 'internal' extensions."""
        assert classify_destination("1001") == "internal"
        assert classify_destination("2020") == "internal"
        assert classify_destination("123456") == "internal"

    def test_classify_not_internal_if_too_long(self):
        """All-numeric but longer than 6 digits is not internal."""
        assert classify_destination("1234567") != "internal"

    def test_classify_domestic_nigerian(self):
        """Nigerian numbers starting with 0 are domestic."""
        result = classify_destination("08012345678")
        assert result == "domestic"

    def test_classify_domestic_with_cc_prefix(self):
        """Numbers prefixed with +234 are domestic."""
        result = classify_destination("+2348012345678")
        assert result == "domestic"

    def test_classify_domestic_with_00_prefix(self):
        """Numbers prefixed with 00234 are domestic."""
        result = classify_destination("002348012345678")
        assert result == "domestic"

    def test_classify_international_us(self):
        """US numbers are international."""
        result = classify_destination("+14155550123")
        assert result == "international"

    def test_classify_international_uk(self):
        """UK numbers are international."""
        result = classify_destination("+441234567890")
        assert result == "international"

    def test_classify_international_with_00_prefix(self):
        """Numbers with 00 and non-Nigerian CC are international."""
        result = classify_destination("00441234567890")
        assert result == "international"

    def test_default_long_number_is_domestic(self):
        """Long numbers without prefix default to domestic."""
        result = classify_destination("2348012345678")
        assert result == "domestic"

    def test_custom_domestic_cc(self):
        """Can specify a custom domestic country code."""
        result = classify_destination("+254712345678", domestic_cc="254")
        assert result == "domestic"
        result_intl = classify_destination("+14155550123", domestic_cc="254")
        assert result_intl == "international"


class TestCheckDial:
    """Test dial permission checking with DialPolicy."""

    def test_caged_scope_blocks_pstn_allows_support(self):
        """Caged scope: support token cannot dial PSTN, can dial support."""
        # Support can dial support
        policy = DialPolicy(allowed_destinations=("support",))
        result = check_dial("support", policy)
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.classification == "support"

        # Support cannot dial PSTN
        result = check_dial("2348012345678", policy)
        assert result.allowed is False
        assert result.reason == "not_in_allowlist"
        assert result.classification == "domestic"

    def test_caged_scope_checked_first(self):
        """Caged scope (not_in_allowlist) checked BEFORE prefix/international."""
        # International is normally blocked, but caged scope blocks first
        policy = DialPolicy(
            allowed_destinations=("support",),
            allow_international=True,  # even if international allowed
            blocked_prefixes=("999",),  # even if prefix not blocked
        )
        result = check_dial("+14155550123", policy)
        assert result.allowed is False
        assert result.reason == "not_in_allowlist"

    def test_international_blocked_by_default(self):
        """International calls blocked by default."""
        policy = DialPolicy()
        result = check_dial("+14155550123", policy)
        assert result.allowed is False
        assert result.reason == "international_blocked"
        assert result.classification == "international"

    def test_international_allowed_when_permitted(self):
        """International calls allowed when policy permits."""
        policy = DialPolicy(allow_international=True)
        result = check_dial("+14155550123", policy)
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.classification == "international"

    def test_domestic_allowed_by_default(self):
        """Domestic calls allowed by default."""
        policy = DialPolicy()
        result = check_dial("08012345678", policy)
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.classification == "domestic"

    def test_internal_extension_allowed_by_default(self):
        """Internal extensions allowed by default."""
        policy = DialPolicy()
        result = check_dial("1001", policy)
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.classification == "internal"

    def test_support_allowed_by_default(self):
        """Support destination allowed by default."""
        policy = DialPolicy()
        result = check_dial("support", policy)
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.classification == "support"

    def test_blocked_prefix_prevents_dial(self):
        """Numbers matching blocked prefixes are denied."""
        policy = DialPolicy(blocked_prefixes=("234900", "234911"))
        result = check_dial("2349001112222", policy)
        assert result.allowed is False
        assert result.reason == "blocked_prefix"
        assert result.classification == "domestic"

    def test_blocked_prefix_partial_match(self):
        """Blocked prefix checked as prefix, not exact match."""
        policy = DialPolicy(blocked_prefixes=("234900",))
        # Should match
        assert check_dial("2349001111111", policy).allowed is False
        # Should not match (prefix is 234901, not 234900)
        assert check_dial("2349011111111", policy).allowed is True

    def test_multiple_blocked_prefixes(self):
        """Multiple blocked prefixes are checked."""
        policy = DialPolicy(blocked_prefixes=("234900", "234911", "234909"))
        assert check_dial("2349001111", policy).allowed is False
        assert check_dial("2349111111", policy).allowed is False
        assert check_dial("2349091111", policy).allowed is False
        # Different prefix allowed
        assert check_dial("2348011111", policy).allowed is True

    def test_blocked_prefix_does_not_override_international_block(self):
        """Blocked prefix check happens after international check."""
        policy = DialPolicy(
            blocked_prefixes=("1415",),  # US number, but also blocked
            allow_international=False,
        )
        result = check_dial("+14155550123", policy)
        # Should fail on international_blocked first, not blocked_prefix
        assert result.allowed is False
        assert result.reason == "international_blocked"

    def test_full_scenario_support_agent(self):
        """Support agent can only dial support queue."""
        policy = DialPolicy(
            allowed_destinations=("support",),
            allow_international=False,
            blocked_prefixes=(),
        )
        # Can dial support
        assert check_dial("support", policy).allowed is True
        # Cannot dial domestic
        assert check_dial("08012345678", policy).allowed is False
        # Cannot dial international
        assert check_dial("+14155550123", policy).allowed is False
        # Cannot dial internal
        assert check_dial("1001", policy).allowed is False

    def test_full_scenario_domestic_only_customer(self):
        """Domestic-only customer cannot dial international or blocked prefixes."""
        policy = DialPolicy(
            allowed_destinations=None,  # No caging
            allow_international=False,
            blocked_prefixes=("234900", "234911"),  # Premium rate
        )
        # Can dial domestic
        assert check_dial("08012345678", policy).allowed is True
        # Can dial internal
        assert check_dial("1001", policy).allowed is True
        # Cannot dial blocked prefix
        assert check_dial("2349001111", policy).allowed is False
        # Cannot dial international
        assert check_dial("+14155550123", policy).allowed is False

    def test_full_scenario_international_enabled_customer(self):
        """Customer with international and no restrictions."""
        policy = DialPolicy(
            allowed_destinations=None,
            allow_international=True,
            blocked_prefixes=("234900",),  # Still block premium-rate
        )
        # Can dial domestic
        assert check_dial("08012345678", policy).allowed is True
        # Can dial international
        assert check_dial("+14155550123", policy).allowed is True
        # Cannot dial blocked prefix
        assert check_dial("2349001111", policy).allowed is False
        # Can dial internal
        assert check_dial("1001", policy).allowed is True
