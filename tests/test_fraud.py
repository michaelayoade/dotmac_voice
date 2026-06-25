from app.services.routing.fraud import (
    DialPolicy,
    _normalize,
    check_dial,
    classify_destination,
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

    def test_blocked_prefix_ordering_comes_before_international_check(self):
        """Blocked prefix check (step 4) happens AFTER classification but BEFORE international check (step 5).
        With the new ordering: invalid_destination > blocked_prefix > international_blocked.
        A US number with a digits-based blocked prefix (e.g. '14155') is denied 'blocked_prefix'
        when the prefix matches the normalized digits, exercising the prefix step explicitly."""
        policy = DialPolicy(
            blocked_prefixes=("14155",),  # digit prefix matching normalized US number
            allow_international=True,  # international IS allowed — so if prefix wins, reason="blocked_prefix"
        )
        result = check_dial("+14155550123", policy)
        # Blocked prefix (on digits) fires BEFORE international check
        assert result.allowed is False
        assert result.reason == "blocked_prefix"

    def test_international_blocked_when_prefix_does_not_match(self):
        """International block fires when prefix does not match."""
        policy = DialPolicy(
            blocked_prefixes=("9999",),  # does not match +1-415
            allow_international=False,
        )
        result = check_dial("+14155550123", policy)
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


class TestNormalize:
    """Test the _normalize helper directly."""

    def test_plus_prefix_is_intl(self):
        assert _normalize("+442079460000") == ("intl", "442079460000")

    def test_plus_prefix_strips_non_digits(self):
        assert _normalize("+44 207 946 0000") == ("intl", "442079460000")

    def test_00_prefix_is_intl_strips_00(self):
        assert _normalize("0044 1234 5678") == ("intl", "4412345678")

    def test_00_nigerian_is_intl_but_digits_have_234(self):
        # 00234... → intl kind, digits=2348012345678
        assert _normalize("00234 801 234 5678") == ("intl", "2348012345678")

    def test_national_trunk_0_is_national(self):
        # Single leading 0 = national trunk prefix, NOT international
        assert _normalize("08012345678") == ("national", "08012345678")

    def test_lagos_01_is_national(self):
        # Lagos landline 01... is national, not international
        assert _normalize("012345678") == ("national", "012345678")

    def test_011_is_national(self):
        # 011... is a valid Nigerian national number, NOT international access
        assert _normalize("011 1234567") == ("national", "0111234567")

    def test_empty_string_gives_empty_digits(self):
        kind, digits = _normalize("")
        assert digits == ""

    def test_spaces_only_gives_empty_digits(self):
        kind, digits = _normalize("   ")
        assert digits == ""


class TestClassifyDestinationFormattingBypasses:
    """Bypass tests: formatted international numbers must NOT slip through as domestic."""

    def test_formatted_uk_with_spaces_is_international(self):
        """+44 207 946 0000 must classify as international, not domestic."""
        result = classify_destination("+44 207 946 0000")
        assert result == "international"

    def test_formatted_us_with_dashes_is_international(self):
        """+1-415-555-0123 must classify as international, not domestic."""
        result = classify_destination("+1-415-555-0123")
        assert result == "international"

    def test_formatted_with_parens_is_international(self):
        """(44) 207 946 0000 — parens around CC, international."""
        # Note: no + but digits start with 44 (not 234), and no 00 prefix
        # This is ambiguous raw input; we expect domestic default without intl escape.
        # The spec says intl escape is ONLY + or 00. So (44) without + or 00 = national.
        # This case is "domestic" by design (no intl escape marker).
        result = classify_destination("(44) 207 946 0000")
        # Should be domestic (no intl escape) — but must NOT be "international"
        # The important bypass to close is + and 00 forms
        assert result in ("domestic", "internal")  # not "international" by bypass

    def test_formatted_00_prefix_uk_is_international(self):
        """0044 1234 5678 — 00 escape with non-Nigerian CC = international."""
        result = classify_destination("0044 1234 5678")
        assert result == "international"

    def test_formatted_00_prefix_nigerian_is_domestic(self):
        """00234 801 234 5678 — 00 escape with Nigerian CC = domestic."""
        result = classify_destination("00234 801 234 5678")
        assert result == "domestic"

    def test_plus_nigerian_formatted_is_domestic(self):
        """+2348012345678 is domestic (Nigerian CC)."""
        result = classify_destination("+2348012345678")
        assert result == "domestic"


class TestNigerianNationalNotMisclassified:
    """Nigerian national numbers must never be misclassified as international."""

    def test_08_prefix_is_domestic(self):
        """08012345678 — national trunk 0 → domestic."""
        result = classify_destination("08012345678")
        assert result == "domestic"

    def test_lagos_01_prefix_is_domestic(self):
        """012345678 — Lagos 01 landline → domestic, NOT international."""
        result = classify_destination("012345678")
        assert result == "domestic"

    def test_011_prefix_is_domestic(self):
        """011 1234567 — valid Nigerian national number → domestic, NOT international."""
        result = classify_destination("011 1234567")
        assert result == "domestic"


class TestEmptyAndInvalidDestination:
    """Empty or blank destinations must be denied as invalid."""

    def test_empty_string_is_invalid(self):
        """Empty destination → classification 'invalid'."""
        result = classify_destination("")
        assert result == "invalid"

    def test_whitespace_only_is_invalid(self):
        """Whitespace-only destination → classification 'invalid'."""
        result = classify_destination("   ")
        assert result == "invalid"

    def test_check_dial_empty_denied_invalid_destination(self):
        """check_dial on empty string → denied, reason=invalid_destination."""
        policy = DialPolicy()
        result = check_dial("", policy)
        assert result.allowed is False
        assert result.reason == "invalid_destination"
        assert result.classification == "invalid"

    def test_check_dial_whitespace_denied_invalid_destination(self):
        """check_dial on whitespace-only → denied, reason=invalid_destination."""
        policy = DialPolicy()
        result = check_dial("   ", policy)
        assert result.allowed is False
        assert result.reason == "invalid_destination"
        assert result.classification == "invalid"


class TestBlockedPrefixFormattingBypass:
    """blocked_prefixes must match normalized digits so reformatting cannot defeat them."""

    def test_plus_formatted_intl_nigerian_premium_blocked(self):
        """+234 900 111 2222 — formatted, must be blocked by prefix '234900'."""
        policy = DialPolicy(blocked_prefixes=("234900",), allow_international=True)
        result = check_dial("+234 900 111 2222", policy)
        assert result.allowed is False
        assert result.reason == "blocked_prefix"

    def test_00_formatted_nigerian_premium_blocked(self):
        """00234900... — formatted, must be blocked by prefix '234900'."""
        policy = DialPolicy(blocked_prefixes=("234900",), allow_international=True)
        result = check_dial("00234 900 111 2222", policy)
        assert result.allowed is False
        assert result.reason == "blocked_prefix"

    def test_plain_digits_nigerian_premium_blocked(self):
        """234900... — plain digits, must be blocked by prefix '234900'."""
        policy = DialPolicy(blocked_prefixes=("234900",), allow_international=True)
        result = check_dial("2349001112222", policy)
        assert result.allowed is False
        assert result.reason == "blocked_prefix"

    def test_formatted_uk_international_blocked_by_prefix(self):
        """+44 207 946 0000 blocked by digit prefix '44207'."""
        policy = DialPolicy(blocked_prefixes=("44207",), allow_international=True)
        result = check_dial("+44 207 946 0000", policy)
        assert result.allowed is False
        assert result.reason == "blocked_prefix"


class TestCagingWithFormattedNumbers:
    """Caging must still be checked first even for formatted numbers."""

    def test_caged_to_support_denies_formatted_international(self):
        """Caged policy denies '+44 207 946 0000' (not in allowlist)."""
        policy = DialPolicy(allowed_destinations=("support",))
        result = check_dial("+44 207 946 0000", policy)
        assert result.allowed is False
        assert result.reason == "not_in_allowlist"

    def test_caged_to_support_allows_support(self):
        """Caged policy still allows 'support'."""
        policy = DialPolicy(allowed_destinations=("support",))
        result = check_dial("support", policy)
        assert result.allowed is True
        assert result.reason == "ok"

    def test_caged_to_support_denies_any_number(self):
        """Caged policy denies any PSTN number regardless of format."""
        policy = DialPolicy(allowed_destinations=("support",))
        assert check_dial("+44 207 946 0000", policy).reason == "not_in_allowlist"
        assert check_dial("+1-415-555-0123", policy).reason == "not_in_allowlist"
        assert check_dial("08012345678", policy).reason == "not_in_allowlist"
