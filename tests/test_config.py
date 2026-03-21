"""Tests for config.py — settings loading and validation."""

import os
from unittest.mock import patch

import pytest

from config import Settings, load_settings


class TestSettings:
    def test_validate_passes_with_all_required(self):
        s = Settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_ANON_KEY="anon-key",
            SUPABASE_SERVICE_ROLE_KEY="service-key",
        )
        s.validate()  # Should not raise

    def test_validate_fails_on_missing_url(self):
        s = Settings(
            SUPABASE_URL="",
            SUPABASE_ANON_KEY="anon-key",
            SUPABASE_SERVICE_ROLE_KEY="service-key",
        )
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            s.validate()

    def test_validate_warns_on_missing_service_role_key(self, caplog):
        """SERVICE_ROLE_KEY is recommended, not required — warns but doesn't crash."""
        import logging

        s = Settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_ANON_KEY="anon-key",
            SUPABASE_SERVICE_ROLE_KEY="",
        )
        with caplog.at_level(logging.WARNING, logger="evie.server"):
            s.validate()  # Should not raise
        assert "SUPABASE_SERVICE_ROLE_KEY" in caplog.text

    def test_validate_reports_all_missing(self):
        s = Settings()
        with pytest.raises(RuntimeError, match="SUPABASE_URL") as exc_info:
            s.validate()
        assert "SUPABASE_ANON_KEY" in str(exc_info.value)

    def test_defaults(self):
        s = Settings(
            SUPABASE_URL="url",
            SUPABASE_ANON_KEY="key",
            SUPABASE_SERVICE_ROLE_KEY="srk",
        )
        assert s.HOST == "0.0.0.0"
        assert s.PORT == 8000
        assert s.LOG_LEVEL == "INFO"
        assert s.JWT_SECRET == ""


class TestLoadSettings:
    def test_loads_from_env(self):
        env = {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_ANON_KEY": "my-anon",
            "SUPABASE_SERVICE_ROLE_KEY": "my-service",
            "EVIE_TOKEN_SECRET": "my-secret",
            "PORT": "9000",
            "LOG_LEVEL": "DEBUG",
        }
        with patch.dict(os.environ, env, clear=False):
            s = load_settings()
            assert s.SUPABASE_URL == "https://test.supabase.co"
            assert s.PORT == 9000
            assert s.LOG_LEVEL == "DEBUG"
