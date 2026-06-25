"""Tests for gunicorn configuration."""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch


class TestGunicornConfig:
    def test_config_loads(self) -> None:
        """gunicorn.conf.py can be imported without errors."""
        spec = importlib.util.spec_from_file_location(
            "gunicorn_conf", "gunicorn.conf.py"
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        assert hasattr(mod, "bind")
        assert hasattr(mod, "workers")
        assert hasattr(mod, "worker_class")
        assert hasattr(mod, "timeout")
        assert hasattr(mod, "max_requests")

    def test_default_bind(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "gunicorn_conf", "gunicorn.conf.py"
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert "8001" in mod.bind

    def test_worker_class_is_uvicorn(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "gunicorn_conf", "gunicorn.conf.py"
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert "uvicorn" in mod.worker_class

    def test_env_override_workers(self) -> None:
        with patch.dict(os.environ, {"GUNICORN_WORKERS": "4"}):
            spec = importlib.util.spec_from_file_location(
                "gunicorn_conf_custom", "gunicorn.conf.py"
            )
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            assert mod.workers == 4

    def test_preload_defaults_false(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "gunicorn_conf_preload", "gunicorn.conf.py"
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert mod.preload_app is False
