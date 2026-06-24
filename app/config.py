import logging
import os
from dataclasses import dataclass
from ipaddress import ip_network

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigWarning:
    message: str
    critical: bool = False


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_voice",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    secret_key: str = os.getenv("SECRET_KEY", "")
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    db_statement_timeout_ms: int = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))

    # Avatar settings
    avatar_upload_dir: str = os.getenv("AVATAR_UPLOAD_DIR", "static/avatars")
    avatar_max_size_bytes: int = int(
        os.getenv("AVATAR_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    )  # 2MB
    avatar_allowed_types: str = os.getenv(
        "AVATAR_ALLOWED_TYPES", "image/jpeg,image/png,image/gif,image/webp"
    )
    avatar_url_prefix: str = os.getenv("AVATAR_URL_PREFIX", "/static/avatars")

    # Branding
    brand_name: str = os.getenv("BRAND_NAME", "DotMac Voice")
    brand_tagline: str = os.getenv("BRAND_TAGLINE", "FastAPI starter")
    brand_logo_url: str | None = os.getenv("BRAND_LOGO_URL") or None
    branding_upload_dir: str = os.getenv("BRANDING_UPLOAD_DIR", "static/branding")
    branding_max_size_bytes: int = int(
        os.getenv("BRANDING_MAX_SIZE_BYTES", str(5 * 1024 * 1024))
    )  # 5MB
    branding_allowed_types: str = os.getenv(
        "BRANDING_ALLOWED_TYPES",
        "image/jpeg,image/png,image/gif,image/webp,image/svg+xml,image/x-icon,image/vnd.microsoft.icon",
    )
    branding_url_prefix: str = os.getenv("BRANDING_URL_PREFIX", "/static/branding")

    # Storage
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local")  # "local" or "s3"
    storage_local_dir: str = os.getenv("STORAGE_LOCAL_DIR", "static/uploads")
    storage_url_prefix: str = os.getenv("STORAGE_URL_PREFIX", "/static/uploads")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "")

    # File uploads
    upload_max_size_bytes: int = int(
        os.getenv("UPLOAD_MAX_SIZE_BYTES", str(10 * 1024 * 1024))
    )  # 10MB
    upload_allowed_types: str = os.getenv(
        "UPLOAD_ALLOWED_TYPES",
        "image/jpeg,image/png,image/gif,image/webp,application/pdf,text/plain,text/csv",
    )

    # CORS
    cors_origins: str = os.getenv("CORS_ORIGINS", "")  # Comma-separated origins
    trusted_hosts: str = os.getenv("TRUSTED_HOSTS", "")

    # Metrics
    metrics_token: str | None = os.getenv("METRICS_TOKEN") or None

    # Static assets
    static_cache_control: str = os.getenv(
        "STATIC_CACHE_CONTROL", "public, max-age=300, must-revalidate"
    )

    # Voice settings
    # FusionPBX has no REST provisioning API; we write directly to its PostgreSQL DB.
    fusionpbx_db_url: str = os.getenv(
        "FUSIONPBX_DB_URL",
        "postgresql+psycopg://fusionpbx:fusionpbx@localhost:5432/fusionpbx",
    )
    # Deprecated: kept for backwards-compat, no longer used by FusionpbxClient.
    fusionpbx_api_url: str = os.getenv("FUSIONPBX_API_URL", "http://localhost:8080")
    fusionpbx_api_key: str = os.getenv("FUSIONPBX_API_KEY", "")
    esl_host: str = os.getenv("ESL_HOST", "localhost")
    esl_port: int = int(os.getenv("ESL_PORT", "8021"))
    esl_password: str = os.getenv("ESL_PASSWORD", "ClueCon")
    # Start the background ESL->webhook consumer at app startup (disable in tests).
    esl_consumer_enabled: bool = os.getenv("ESL_CONSUMER_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    edge_wss_url: str = os.getenv("EDGE_WSS_URL", "wss://sip.dotmac.io:443")
    voice_ingress_api_keys: str = os.getenv("VOICE_INGRESS_API_KEYS", "")
    voice_ingress_allowed_ips: str = os.getenv("VOICE_INGRESS_ALLOWED_IPS", "")
    token_signing_key: str = os.getenv("TOKEN_SIGNING_KEY", "dev-token-key")


def validate_settings(s: Settings) -> list[ConfigWarning]:
    """Validate required settings at startup."""
    warnings: list[ConfigWarning] = []
    environment = os.getenv("ENVIRONMENT", "dev").lower()
    production = environment in {"prod", "production"}
    jwt_secret = os.getenv("JWT_SECRET", "")
    totp_key = os.getenv("TOTP_ENCRYPTION_KEY", "")

    if not jwt_secret:
        warnings.append(
            ConfigWarning(
                "JWT_SECRET is not set - authentication will not work",
                critical=production,
            )
        )
    elif len(jwt_secret) < 32 and not jwt_secret.startswith("openbao://"):
        warnings.append(
            ConfigWarning(
                "JWT_SECRET is shorter than 32 characters - consider a stronger secret",
                critical=production,
            )
        )

    if not totp_key:
        warnings.append(
            ConfigWarning(
                "TOTP_ENCRYPTION_KEY is not set - MFA will not work",
                critical=production,
            )
        )

    if not s.secret_key:
        warnings.append(
            ConfigWarning(
                "SECRET_KEY is not set - CSRF and session security weakened",
                critical=production,
            )
        )

    if (
        "localhost" in s.database_url
        and os.getenv("ENVIRONMENT", "dev") == "production"
    ):
        warnings.append(
            ConfigWarning(
                "DATABASE_URL points to localhost in production", critical=True
            )
        )

    if production and not s.trusted_hosts:
        warnings.append(
            ConfigWarning(
                "TRUSTED_HOSTS is not set - Host header validation is disabled",
                critical=True,
            )
        )

    if "*" in [origin.strip() for origin in s.cors_origins.split(",")]:
        warnings.append(
            ConfigWarning(
                "CORS_ORIGINS contains * while credentials are enabled",
                critical=production,
            )
        )

    if s.storage_backend not in {"local", "s3"}:
        warnings.append(
            ConfigWarning(
                "STORAGE_BACKEND must be either 'local' or 's3'",
                critical=True,
            )
        )

    if s.storage_backend == "s3":
        missing = [
            name
            for name, value in {
                "S3_BUCKET": s.s3_bucket,
                "S3_REGION": s.s3_region,
                "S3_ACCESS_KEY": s.s3_access_key,
                "S3_SECRET_KEY": s.s3_secret_key,
            }.items()
            if not value
        ]
        if missing:
            warnings.append(
                ConfigWarning(
                    f"STORAGE_BACKEND=s3 is missing: {', '.join(missing)}",
                    critical=production,
                )
            )

    if production and s.token_signing_key == "dev-token-key":  # noqa: S105
        warnings.append(
            ConfigWarning(
                "TOKEN_SIGNING_KEY uses the development default",
                critical=True,
            )
        )

    if production and s.esl_password == "ClueCon":  # noqa: S105
        warnings.append(
            ConfigWarning(
                "ESL_PASSWORD uses the FreeSWITCH default (ClueCon)",
                critical=True,
            )
        )

    for cidr in os.getenv("TRUSTED_PROXY_CIDRS", "").split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            ip_network(cidr, strict=False)
        except ValueError:
            warnings.append(
                ConfigWarning(
                    f"TRUSTED_PROXY_CIDRS contains invalid CIDR: {cidr}",
                    critical=production,
                )
            )

    for directive in s.static_cache_control.split(","):
        directive = directive.strip()
        if not directive:
            warnings.append(
                ConfigWarning(
                    "STATIC_CACHE_CONTROL contains an empty directive",
                    critical=production,
                )
            )
            continue
        if directive.lower().startswith("max-age="):
            value = directive.split("=", 1)[1]
            if not value.isdigit():
                warnings.append(
                    ConfigWarning(
                        "STATIC_CACHE_CONTROL max-age must be an integer",
                        critical=production,
                    )
                )

    return warnings


settings = Settings()
