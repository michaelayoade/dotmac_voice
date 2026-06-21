from __future__ import annotations

import colorsys
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType

_SETTING_KEY = "ui_branding"
_SETTING_DOMAIN = SettingDomain.branding
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CSS_URL = re.compile(r"""(?is)url\(\s*(["']?)(.*?)\1\s*\)""")
_URL_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_DANGEROUS_CSS_PATTERNS = (
    re.compile(r"(?is)@import\b[^;{}]*;?"),
    re.compile(r"(?is)\bbehavior\s*:[^;{}]*;?"),
    re.compile(r"(?is)expression\s*\([^)]*\)"),
    re.compile(r"(?i)javascript\s*:"),
)


def _normalize_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip()
    if not value.startswith("#"):
        value = f"#{value}"
    return value.upper() if _HEX_COLOR.match(value) else fallback


def _brand_mark(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "ST"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _shift_lightness(hex_color: str, factor: float) -> str:
    color = hex_color.lstrip("#")
    r = int(color[0:2], 16) / 255.0
    g = int(color[2:4], 16) / 255.0
    b = int(color[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(1.0, l * factor))
    nr, ng, nb = colorsys.hls_to_rgb(h, l, s)
    return f"#{int(nr * 255):02X}{int(ng * 255):02X}{int(nb * 255):02X}"


def _sanitize_css_url(match: re.Match[str]) -> str:
    raw_url = match.group(2).strip()
    if not raw_url:
        return ""
    scheme_match = _URL_SCHEME.match(raw_url)
    if scheme_match and scheme_match.group(1).lower() not in {"http", "https"}:
        return ""
    return match.group(0)


def sanitize_branding_css(css: Any) -> str:
    if css is None:
        return ""
    sanitized = str(css).strip()
    if not sanitized or "<" in sanitized:
        return ""

    sanitized = _CSS_URL.sub(_sanitize_css_url, sanitized)
    for pattern in _DANGEROUS_CSS_PATTERNS:
        sanitized = pattern.sub("", sanitized)
    return sanitized.strip()


def _default_branding() -> dict[str, Any]:
    name = getattr(settings, "brand_name", "DotMac Voice")
    return {
        "display_name": name,
        "tagline": getattr(settings, "brand_tagline", "FastAPI starter"),
        "logo_url": getattr(settings, "brand_logo_url", None),
        "logo_dark_url": None,
        "brand_mark": _brand_mark(name),
        "primary_color": "#06B6D4",
        "accent_color": "#F97316",
        "font_family_display": "Outfit",
        "font_family_body": "Plus Jakarta Sans",
        "custom_css": "",
    }


def get_branding(db: Session) -> dict[str, Any]:
    defaults = _default_branding()
    setting = db.scalars(
        select(DomainSetting)
        .where(DomainSetting.domain == _SETTING_DOMAIN)
        .where(DomainSetting.key == _SETTING_KEY)
        .limit(1)
    ).first()
    if not setting or not isinstance(setting.value_json, dict):
        return defaults
    data = setting.value_json
    merged = {**defaults, **data}
    merged["primary_color"] = _normalize_hex(merged.get("primary_color"), "#06B6D4")
    merged["accent_color"] = _normalize_hex(merged.get("accent_color"), "#F97316")
    merged["custom_css"] = sanitize_branding_css(merged.get("custom_css"))
    return merged


def save_branding(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_branding(db)
    current.update(payload)
    current["primary_color"] = _normalize_hex(current.get("primary_color"), "#06B6D4")
    current["accent_color"] = _normalize_hex(current.get("accent_color"), "#F97316")
    current["custom_css"] = sanitize_branding_css(current.get("custom_css"))

    setting = db.scalars(
        select(DomainSetting)
        .where(DomainSetting.domain == _SETTING_DOMAIN)
        .where(DomainSetting.key == _SETTING_KEY)
        .limit(1)
    ).first()
    if not setting:
        setting = DomainSetting(
            domain=_SETTING_DOMAIN,
            key=_SETTING_KEY,
            value_type=SettingValueType.json,
            is_secret=False,
            is_active=True,
            value_json=current,
        )
        db.add(setting)
    else:
        setting.value_type = SettingValueType.json
        setting.value_text = None
        setting.value_json = current
        setting.is_active = True

    db.flush()
    return current


def google_fonts_url(branding: dict[str, Any]) -> str | None:
    families = []
    for key in ("font_family_display", "font_family_body"):
        name = (branding.get(key) or "").strip()
        if name:
            families.append(name.replace(" ", "+") + ":wght@400;500;600;700")
    if not families:
        return None
    return (
        "https://fonts.googleapis.com/css2?family="
        + "&family=".join(dict.fromkeys(families))
        + "&display=swap"
    )


def generate_css(branding: dict[str, Any]) -> str:
    primary = _normalize_hex(branding.get("primary_color"), "#06B6D4")
    accent = _normalize_hex(branding.get("accent_color"), "#F97316")
    primary_dark = _shift_lightness(primary, 0.78)
    accent_dark = _shift_lightness(accent, 0.78)
    display_font = (branding.get("font_family_display") or "Outfit").strip()
    body_font = (branding.get("font_family_body") or "Plus Jakarta Sans").strip()
    custom_css = sanitize_branding_css(branding.get("custom_css"))

    lines = [
        "/* Auto-generated starter theme */",
        ":root {",
        f"  --brand-primary: {primary};",
        f"  --brand-primary-dark: {primary_dark};",
        f"  --brand-accent: {accent};",
        f"  --brand-accent-dark: {accent_dark};",
        f'  --brand-font-display: "{display_font}", system-ui, sans-serif;',
        f'  --brand-font-body: "{body_font}", system-ui, sans-serif;',
        "}",
        "h1, h2, h3, .font-display { font-family: var(--brand-font-display); }",
        "body, .font-sans { font-family: var(--brand-font-body); }",
        ".btn-brand {",
        "  background: linear-gradient(135deg, var(--brand-primary), var(--brand-accent));",
        "  color: #fff;",
        "}",
        ".btn-brand:hover {",
        "  background: linear-gradient(135deg, var(--brand-primary-dark), var(--brand-accent-dark));",
        "}",
    ]
    if custom_css:
        lines.extend(["", "/* Custom CSS */", custom_css])
    return "\n".join(lines)
