"""Tests for db/client.py — dual-mode Supabase client creation."""

from unittest.mock import patch, MagicMock

from auth.resolver import CallerContext
from db.client import get_hcp_client, get_service_client, get_client


class TestGetHcpClient:
    def test_sets_auth_header(self):
        with patch("db.client.create_client") as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client

            result = get_hcp_client("user-jwt-123")
            mock_client.postgrest.auth.assert_called_once_with("user-jwt-123")
            mock_client.postgrest.session.headers.__setitem__.assert_called_once_with(
                "Authorization", "Bearer user-jwt-123"
            )
            assert result is mock_client


class TestGetServiceClient:
    def test_creates_with_service_role_key(self):
        with patch("db.client.create_client") as mock_create, \
             patch("db.client.settings") as mock_settings:
            mock_settings.SUPABASE_URL = "https://test.supabase.co"
            mock_settings.SUPABASE_SERVICE_ROLE_KEY = "service-role-key"
            mock_client = MagicMock()
            mock_create.return_value = mock_client

            result = get_service_client()
            mock_create.assert_called_once_with(
                "https://test.supabase.co", "service-role-key"
            )
            assert result is mock_client


class TestGetClient:
    def test_hcp_oauth_caller_uses_hcp_client(self):
        caller = CallerContext(
            auth_mode="hcp_oauth",
            max_tier=3,
            audience_type="hcp",
            partner_name="direct_hcp",
            supabase_jwt="user-jwt",
        )
        with patch("db.client.get_hcp_client") as mock_hcp:
            mock_hcp.return_value = MagicMock()
            result = get_client(caller)
            mock_hcp.assert_called_once_with("user-jwt")

    def test_anonymous_caller_uses_service_client(self):
        caller = CallerContext(
            auth_mode="anonymous",
            max_tier=1,
            audience_type="hcp",
            partner_name="anonymous",
        )
        with patch("db.client.get_service_client") as mock_svc:
            mock_svc.return_value = MagicMock()
            result = get_client(caller)
            mock_svc.assert_called_once()

    def test_no_caller_uses_service_client(self):
        with patch("db.client.get_service_client") as mock_svc:
            mock_svc.return_value = MagicMock()
            result = get_client(None)
            mock_svc.assert_called_once()
