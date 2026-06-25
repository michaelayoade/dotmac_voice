"""Gunicorn configuration for production deployment.

Usage:
    gunicorn -c gunicorn.conf.py app.main:app
"""

from __future__ import annotations

import multiprocessing
import os

# ── Server socket ────────────────────────────────────────
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8001")

# ── Worker processes ─────────────────────────────────────
# Rule of thumb: 2-4 workers per CPU core for I/O-bound apps
workers = int(os.getenv("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class = "uvicorn.workers.UvicornWorker"
worker_tmp_dir = "/dev/shm"  # noqa: S108 - RAM-backed heartbeat dir in containers

# ── Timeouts ─────────────────────────────────────────────
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# ── Request limits ───────────────────────────────────────
# Restart workers after N requests to prevent memory leaks
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "50"))

# ── Preloading ───────────────────────────────────────────
# Set True in production for faster worker startup and shared memory
# Note: code changes require full restart (not just HUP signal)
preload_app = os.getenv("GUNICORN_PRELOAD", "false").lower() == "true"

# ── Logging ──────────────────────────────────────────────
accesslog = os.getenv("GUNICORN_ACCESSLOG", "-")  # stdout
errorlog = os.getenv("GUNICORN_ERRORLOG", "-")  # stderr
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ── Process naming ───────────────────────────────────────
proc_name = "dotmac_voice"

# ── Proxy headers ────────────────────────────────────────
# Uvicorn reads FORWARDED_ALLOW_IPS to decide which proxies may set
# X-Forwarded-* values used by request.url.scheme and HSTS checks.
raw_env = [
    f"FORWARDED_ALLOW_IPS={os.getenv('FORWARDED_ALLOW_IPS', '127.0.0.1')}",
]
