from __future__ import annotations

from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.branding import (
    generate_css,
    get_branding,
    sanitize_branding_css,
    save_branding,
)


def test_get_branding_returns_defaults(db_session) -> None:
    branding = get_branding(db_session)
    assert branding["display_name"]
    assert branding["primary_color"].startswith("#")
    assert branding["accent_color"].startswith("#")


def test_save_branding_persists_values(db_session) -> None:
    save_branding(
        db_session,
        {
            "display_name": "Acme Starter",
            "primary_color": "#112233",
            "accent_color": "#445566",
            "font_family_display": "Outfit",
            "font_family_body": "Inter",
        },
    )
    branding = get_branding(db_session)
    assert branding["display_name"] == "Acme Starter"
    assert branding["primary_color"] == "#112233"
    assert branding["accent_color"] == "#445566"
    setting = (
        db_session.query(DomainSetting).filter(DomainSetting.key == "ui_branding").one()
    )
    assert setting.domain == SettingDomain.branding


def test_generate_css_contains_brand_variables() -> None:
    css = generate_css(
        {
            "primary_color": "#123456",
            "accent_color": "#ABCDEF",
            "font_family_display": "Outfit",
            "font_family_body": "Plus Jakarta Sans",
            "custom_css": ".demo { color: red; }",
        }
    )
    assert "--brand-primary: #123456;" in css
    assert "--brand-accent: #ABCDEF;" in css
    assert ".demo { color: red; }" in css


def test_sanitize_branding_css_strips_dangerous_patterns() -> None:
    css = """
    .ok { color: red; }
    @import url("https://evil.example/x.css");
    .js { background: url("javascript:alert(1)"); }
    .expr { width: expression(alert(1)); }
    .legacy { behavior: url(#default#VML); }
    .data { background-image: url("data:text/html;base64,QQ=="); }
    .cdn { background-image: url("https://cdn.example.com/bg.png"); }
    .relative { background-image: url("/img/bg.png"); }
    """

    sanitized = sanitize_branding_css(css)

    assert ".ok { color: red; }" in sanitized
    assert (
        '.cdn { background-image: url("https://cdn.example.com/bg.png"); }' in sanitized
    )
    assert '.relative { background-image: url("/img/bg.png"); }' in sanitized
    assert "@import" not in sanitized
    assert "javascript:" not in sanitized.lower()
    assert "expression(" not in sanitized.lower()
    assert "behavior:" not in sanitized.lower()
    assert "data:text" not in sanitized.lower()


def test_sanitize_branding_css_rejects_angle_brackets() -> None:
    css = ".ok { color: red; }\n</style><script>alert(1)</script>"
    assert sanitize_branding_css(css) == ""


def test_save_branding_sanitizes_custom_css_before_persisting(db_session) -> None:
    save_branding(
        db_session,
        {"custom_css": ".ok { color: red; }\n</style><script>alert(1)</script>"},
    )
    branding = get_branding(db_session)
    assert branding["custom_css"] == ""


def test_generate_css_sanitizes_unsafe_custom_css() -> None:
    css = generate_css(
        {
            "primary_color": "#123456",
            "accent_color": "#ABCDEF",
            "font_family_display": "Outfit",
            "font_family_body": "Plus Jakarta Sans",
            "custom_css": """
            .ok { color: red; }
            @import url("https://evil.example/x.css");
            .bad { background-image: url("javascript:alert(1)"); }
            """,
        }
    )

    assert ".ok { color: red; }" in css
    assert "@import" not in css
    assert "javascript:" not in css.lower()
