"""Tests for custom Jinja2 template filters."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.templates import (
    _format_currency,
    _format_date,
    _format_datetime,
    _format_number,
    _nl2br,
    _sanitize_html,
    _timeago,
)


class TestSanitizeHtml:
    def test_strips_tags(self) -> None:
        assert _sanitize_html("<b>bold</b>") == "bold"

    def test_strips_script_tags(self) -> None:
        result = _sanitize_html('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "alert" in result  # text is preserved but escaped

    def test_escapes_ampersand_in_plain_text(self) -> None:
        result = _sanitize_html("A & B")
        assert "&amp;" in result

    def test_escapes_angle_brackets_in_entities(self) -> None:
        result = _sanitize_html("&lt;script&gt;")
        assert "&amp;lt;" in result  # Already escaped entities get double-escaped

    def test_none_returns_empty(self) -> None:
        assert _sanitize_html(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert _sanitize_html("") == ""

    def test_nested_tags(self) -> None:
        result = _sanitize_html("<div><p>hello</p></div>")
        assert result == "hello"


class TestNl2br:
    def test_newlines_to_br(self) -> None:
        result = _nl2br("line1\nline2")
        assert "<br>" in result
        assert "line1" in result
        assert "line2" in result

    def test_escapes_html(self) -> None:
        result = _nl2br("<script>bad</script>\nnormal")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_none_returns_empty(self) -> None:
        assert _nl2br(None) == ""


class TestFormatDate:
    def test_date_default_format(self) -> None:
        d = date(2026, 2, 12)
        assert _format_date(d) == "12 Feb 2026"

    def test_datetime_default_format(self) -> None:
        dt = datetime(2026, 2, 12, 14, 30, 0)
        assert _format_date(dt) == "12 Feb 2026"

    def test_custom_format(self) -> None:
        d = date(2026, 2, 12)
        assert _format_date(d, "%Y-%m-%d") == "2026-02-12"

    def test_none_returns_empty(self) -> None:
        assert _format_date(None) == ""


class TestFormatDatetime:
    def test_default_format(self) -> None:
        dt = datetime(2026, 2, 12, 14, 30)
        assert _format_datetime(dt) == "12 Feb 2026 14:30"

    def test_none_returns_empty(self) -> None:
        assert _format_datetime(None) == ""


class TestFormatCurrency:
    def test_basic(self) -> None:
        assert _format_currency(1234.56) == "$1,234.56"

    def test_custom_symbol(self) -> None:
        assert _format_currency(1234.56, symbol="€") == "€1,234.56"

    def test_zero_decimals(self) -> None:
        assert _format_currency(1234, decimals=0) == "$1,234"

    def test_large_number(self) -> None:
        assert _format_currency(1000000) == "$1,000,000.00"

    def test_none_returns_empty(self) -> None:
        assert _format_currency(None) == ""

    def test_negative(self) -> None:
        assert _format_currency(-500.50) == "$-500.50"


class TestFormatNumber:
    def test_basic(self) -> None:
        assert _format_number(12345.678) == "12,345.68"

    def test_zero_decimals(self) -> None:
        assert _format_number(12345, decimals=0) == "12,345"

    def test_none_returns_empty(self) -> None:
        assert _format_number(None) == ""


class TestTimeago:
    def test_just_now(self) -> None:
        now = datetime.now(UTC)
        assert _timeago(now) == "just now"

    def test_minutes(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC) - timedelta(minutes=5)
        assert _timeago(past) == "5m ago"

    def test_hours(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC) - timedelta(hours=3)
        assert _timeago(past) == "3h ago"

    def test_days(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC) - timedelta(days=7)
        assert _timeago(past) == "7d ago"

    def test_months(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC) - timedelta(days=60)
        assert _timeago(past) == "2mo ago"

    def test_years(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC) - timedelta(days=400)
        assert _timeago(past) == "1y ago"

    def test_none_returns_empty(self) -> None:
        assert _timeago(None) == ""

    def test_naive_datetime_treated_as_utc(self) -> None:
        from datetime import timedelta

        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        result = _timeago(past)
        assert "h ago" in result
