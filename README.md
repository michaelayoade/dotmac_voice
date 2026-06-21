# DotMac Voice

A production-ready FastAPI starter template with enterprise-grade features including authentication, RBAC, audit logging, background jobs, and full observability.

## Features

- **Authentication & Security**
  - JWT-based authentication with refresh token rotation
  - Multi-factor authentication (TOTP, SMS, Email)
  - API key management with rate limiting
  - Session management with token hashing
  - Password policies and account lockout

- **Authorization**
  - Role-based access control (RBAC)
  - Fine-grained permissions system
  - Scope-based API access

- **Audit & Compliance**
  - Comprehensive audit logging
  - Request/response tracking
  - Actor and IP address logging

- **Background Jobs**
  - Celery workers with Redis broker
  - Database-backed Beat scheduler
  - Persistent scheduled tasks

- **Observability**
  - Prometheus metrics
  - OpenTelemetry distributed tracing
  - Structured JSON logging

- **Web UI**
  - Jinja2 server-side rendering
  - Static file serving
  - Avatar upload handling

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | FastAPI 0.111.0 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| Cache/Broker | Redis 7 |
| Task Queue | Celery 5.4 |
| Auth | python-jose, passlib, pyotp |
| Tracing | OpenTelemetry |
| Metrics | Prometheus |

## Project Structure

```
├── app/
│   ├── api/              # Route handlers
│   ├── models/           # SQLAlchemy ORM models
│   ├── schemas/          # Pydantic validation schemas
│   ├── services/         # Business logic layer
│   ├── tasks/            # Celery background tasks
│   ├── main.py           # FastAPI app initialization
│   ├── config.py         # Application settings
│   ├── db.py             # Database configuration
│   ├── celery_app.py     # Celery configuration
│   └── telemetry.py      # OpenTelemetry setup
├── templates/            # Jinja2 HTML templates
├── static/               # Static assets
├── alembic/              # Database migrations
├── scripts/              # Utility scripts
├── tests/                # Test suite
├── docker-compose.yml    # Container orchestration
└── Dockerfile            # Container image
```

## Getting Started

### Prerequisites

- Python 3.11, 3.12, or 3.13
- PostgreSQL 16
- Redis 7
- [Poetry](https://python-poetry.org/) (recommended) or pip

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd dotmac_voice
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Install dependencies**
   ```bash
   # Using Poetry (recommended)
   poetry install

   # Or using pip
   pip install -r requirements.txt
   ```

### Running with Docker (Recommended)

The easiest way to run the application is with Docker Compose:

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f app

# Stop all services
docker compose down
```

Services:
- **App**: http://localhost:8007
- **PostgreSQL**: localhost:5436
- **Redis**: localhost:6381

### Running Locally

1. **Start PostgreSQL and Redis** (or use Docker for just the databases)
   ```bash
   docker compose up -d db redis
   ```

2. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

3. **Seed initial data**
   ```bash
   # Initialize RBAC roles and permissions
   python scripts/seed_rbac.py

   # Create admin user
   python scripts/seed_admin.py --username admin --password <password>

   # Sync settings
   python scripts/settings_sync.py
   ```

4. **Start the application**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
   ```

5. **Start Celery worker** (in a separate terminal)
   ```bash
   celery -A app.celery_app worker -l info
   ```

6. **Start Celery Beat scheduler** (in a separate terminal)
   ```bash
   celery -A app.celery_app beat -l info
   ```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_voice` |
| `REDIS_URL` | Redis connection string | `redis://:redis@localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | `redis://:redis@localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | Celery result backend | `redis://:redis@localhost:6379/1` |
| `JWT_SECRET` | JWT signing secret | Required |
| `SESSION_TOKEN_HASH_SECRET` | HMAC secret for refresh-session token hashes | Falls back to `API_KEY_HASH_SECRET`/`JWT_SECRET` |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_ACCESS_TTL_MINUTES` | Access token TTL | `15` |
| `JWT_REFRESH_TTL_DAYS` | Refresh token TTL | `30` |
| `TOTP_ISSUER` | TOTP issuer name | `dotmac_voice` |
| `TOTP_ENCRYPTION_KEY` | TOTP secret encryption key | Required |
| `TRUSTED_HOSTS` | Comma-separated allowed Host header values for production | Required in production |
| `OTEL_ENABLED` | Enable OpenTelemetry | `false` |
| `OTEL_SERVICE_NAME` | Service name for tracing | `dotmac_voice` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | - |

### OpenBao Integration

Secrets can be resolved from OpenBao by using the `openbao://` prefix:

```bash
JWT_SECRET=openbao://secret/data/dotmac_voice#jwt_secret
```

Configure OpenBao connection:
```bash
OPENBAO_ADDR=https://vault.example.com
OPENBAO_TOKEN=<token>
OPENBAO_NAMESPACE=<namespace>
OPENBAO_KV_VERSION=2
```

## Before Going Live

Complete this checklist before forking or deploying this starter to production:

- Set `ENVIRONMENT=production` so missing or weak core secrets fail startup.
- Generate unique `SECRET_KEY`, `JWT_SECRET`, `API_KEY_HASH_SECRET`, and `TOTP_ENCRYPTION_KEY`; do not reuse example values.
- Prefer a separate `SESSION_TOKEN_HASH_SECRET` for refresh-session HMACs; if you rotate it, expire or migrate existing sessions.
- Store secrets in OpenBao or another secret manager, and document JWT/API-key hash secret rotation.
- Set `TRUSTED_HOSTS` to the public hostnames accepted by the deployment.
- Keep `REFRESH_COOKIE_SECURE=true`, set an appropriate `REFRESH_COOKIE_DOMAIN`, and only serve auth flows over HTTPS.
- Set `TRUSTED_PROXY_CIDRS` and `FORWARDED_ALLOW_IPS` to the exact proxy/load-balancer CIDRs that may send `X-Forwarded-*` headers.
- Run `alembic upgrade head` as a release step before starting new application containers.
- Confirm PostgreSQL and Redis health checks pass before app, worker, and beat services start.
- Run exactly one Celery Beat instance per environment; multiple beat replicas will enqueue duplicate scheduled tasks.
- Treat `WebSocketManager` as single-process only. Use Redis pub/sub or another broker before scaling WebSocket workers horizontally.
- Set `METRICS_TOKEN` unless `/metrics` is exposed only on loopback or a private monitoring network.
- Review `CORS_ORIGINS`, SMTP settings, storage backend settings, and upload size/type limits for the target environment.
- Build and scan the container image, publish the generated SBOM, and block deployment on high or critical dependency/image CVEs.

## API Endpoints

### Authentication (`/auth`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/login` | User login |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Logout and revoke session |
| GET | `/auth/me` | Get current user profile |
| PUT | `/auth/me` | Update current user profile |
| POST | `/auth/password-change` | Change password |
| POST | `/auth/password-reset-request` | Request password reset |
| POST | `/auth/password-reset` | Complete password reset |
| POST | `/auth/mfa/setup` | Setup MFA |
| POST | `/auth/mfa/verify` | Verify MFA code |
| GET | `/auth/sessions` | List user sessions |
| DELETE | `/auth/sessions/{id}` | Revoke session |

### People (`/people`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/people` | Create person |
| GET | `/people` | List people |
| GET | `/people/{id}` | Get person |
| PUT | `/people/{id}` | Update person |
| DELETE | `/people/{id}` | Delete person |

### RBAC (`/roles`, `/permissions`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/roles` | Create role |
| GET | `/roles` | List roles |
| PUT | `/roles/{id}` | Update role |
| DELETE | `/roles/{id}` | Delete role |
| POST | `/permissions` | Create permission |
| GET | `/permissions` | List permissions |

### Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

## Development

### Code Style

The project follows standard Python conventions with:
- Type hints throughout
- Pydantic for data validation
- SQLAlchemy 2.0 mapped column syntax

### Adding New Endpoints

1. Create model in `app/models/`
2. Create schemas in `app/schemas/`
3. Implement service logic in `app/services/`
4. Add route handlers in `app/api/`
5. Register router in `app/main.py`

### Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_auth_flow.py
```

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/seed_admin.py` | Create admin user |
| `scripts/seed_rbac.py` | Initialize roles and permissions |
| `scripts/settings_sync.py` | Sync settings with database |
| `scripts/settings_validate.py` | Validate settings configuration |

## License

[Add your license here]
