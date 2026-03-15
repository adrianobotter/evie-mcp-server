"""Tests for EVIE OAuth provider — in-memory token lifecycle."""

import time
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
import pytest_asyncio

from mcp.server.auth.provider import RefreshToken
from mcp.shared.auth import OAuthClientInformationFull
from src.evie.oauth import SupabaseOAuthProvider, _PendingAuth, _StoredAuthCode, _StoredToken, _StoredRefresh


@pytest.fixture
def provider():
    return SupabaseOAuthProvider(
        supabase_url="https://test.supabase.co",
        supabase_anon_key="test-key",
        base_url="https://evie.example.com",
    )


@pytest.fixture
def client_info():
    return OAuthClientInformationFull(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uris=["https://claude.ai/callback"],
    )


class TestClientRegistration:
    @pytest.mark.asyncio
    async def test_register_and_retrieve(self, provider):
        info = OAuthClientInformationFull(
            redirect_uris=["https://claude.ai/callback"],
        )
        await provider.register_client(info)
        assert info.client_id is not None
        assert info.client_secret is not None

        retrieved = await provider.get_client(info.client_id)
        assert retrieved is not None
        assert retrieved.client_id == info.client_id

    @pytest.mark.asyncio
    async def test_unknown_client_returns_none(self, provider):
        result = await provider.get_client("nonexistent")
        assert result is None


class TestTokenLifecycle:
    @pytest.mark.asyncio
    async def test_load_valid_token(self, provider):
        provider._tokens["tok-1"] = _StoredToken(
            token="tok-1",
            client_id="c1",
            scopes=["evidence:read"],
            supabase_access_token="sb-tok",
        )
        result = await provider.load_access_token("tok-1")
        assert result is not None
        assert result.token == "tok-1"
        assert result.client_id == "c1"

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self, provider):
        provider._tokens["tok-expired"] = _StoredToken(
            token="tok-expired",
            client_id="c1",
            scopes=["evidence:read"],
            supabase_access_token="sb-tok",
            created_at=time.time() - 7200,  # 2 hours ago
            expires_in=3600,  # 1 hour expiry
        )
        result = await provider.load_access_token("tok-expired")
        assert result is None
        assert "tok-expired" not in provider._tokens

    @pytest.mark.asyncio
    async def test_unknown_token_returns_none(self, provider):
        result = await provider.load_access_token("nonexistent")
        assert result is None


class TestSupabaseTokenMapping:
    def test_get_supabase_token(self, provider):
        provider._tokens["evie-tok"] = _StoredToken(
            token="evie-tok",
            client_id="c1",
            scopes=["evidence:read"],
            supabase_access_token="supabase-jwt-123",
        )
        assert provider.get_supabase_token("evie-tok") == "supabase-jwt-123"

    def test_unknown_evie_token(self, provider):
        assert provider.get_supabase_token("nope") is None


class TestAuthCodeLifecycle:
    @pytest.mark.asyncio
    async def test_load_valid_auth_code(self, provider, client_info):
        provider._auth_codes["code-1"] = _StoredAuthCode(
            code="code-1",
            client_id="test-client",
            redirect_uri="https://claude.ai/callback",
            code_challenge="challenge",
            scopes=["evidence:read"],
            supabase_access_token="sb-at",
            supabase_refresh_token="sb-rt",
        )
        result = await provider.load_authorization_code(client_info, "code-1")
        assert result is not None
        assert result.code == "code-1"

    @pytest.mark.asyncio
    async def test_wrong_client_returns_none(self, provider, client_info):
        provider._auth_codes["code-2"] = _StoredAuthCode(
            code="code-2",
            client_id="different-client",
            redirect_uri="https://claude.ai/callback",
            code_challenge="challenge",
            scopes=["evidence:read"],
            supabase_access_token="sb-at",
            supabase_refresh_token="sb-rt",
        )
        result = await provider.load_authorization_code(client_info, "code-2")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_code_returns_none(self, provider, client_info):
        provider._auth_codes["code-old"] = _StoredAuthCode(
            code="code-old",
            client_id="test-client",
            redirect_uri="https://claude.ai/callback",
            code_challenge="challenge",
            scopes=["evidence:read"],
            supabase_access_token="sb-at",
            supabase_refresh_token="sb-rt",
            created_at=time.time() - 700,  # >10 min ago
        )
        result = await provider.load_authorization_code(client_info, "code-old")
        assert result is None


class TestExchangeRefreshToken:
    @pytest.mark.asyncio
    async def test_refresh_failure_raises(self, provider, client_info):
        """If Supabase returns non-200 on refresh, ValueError should be raised."""
        provider._refreshes["rt-1"] = _StoredRefresh(
            token="rt-1",
            client_id="test-client",
            scopes=["evidence:read"],
            supabase_refresh_token="sb-refresh-tok",
        )
        refresh_token = RefreshToken(token="rt-1", client_id="test-client", scopes=["evidence:read"])

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("src.evie.oauth.httpx.AsyncClient") as mock_http_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_cls.return_value = mock_http

            with pytest.raises(ValueError, match="Supabase token refresh failed"):
                await provider.exchange_refresh_token(client_info, refresh_token, scopes=["evidence:read"])

    @pytest.mark.asyncio
    async def test_missing_supabase_refresh_raises(self, provider, client_info):
        """If no Supabase refresh token is stored, ValueError should be raised."""
        provider._refreshes["rt-2"] = _StoredRefresh(
            token="rt-2",
            client_id="test-client",
            scopes=["evidence:read"],
            supabase_refresh_token="",
        )
        refresh_token = RefreshToken(token="rt-2", client_id="test-client", scopes=["evidence:read"])

        with pytest.raises(ValueError, match="No Supabase refresh token"):
            await provider.exchange_refresh_token(client_info, refresh_token, scopes=["evidence:read"])

    @pytest.mark.asyncio
    async def test_successful_refresh_issues_new_tokens(self, provider, client_info):
        provider._refreshes["rt-3"] = _StoredRefresh(
            token="rt-3",
            client_id="test-client",
            scopes=["evidence:read"],
            supabase_refresh_token="sb-refresh-tok",
        )
        refresh_token = RefreshToken(token="rt-3", client_id="test-client", scopes=["evidence:read"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-sb-access",
            "refresh_token": "new-sb-refresh",
        }

        with patch("src.evie.oauth.httpx.AsyncClient") as mock_http_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_cls.return_value = mock_http

            result = await provider.exchange_refresh_token(client_info, refresh_token, scopes=["evidence:read"])

        assert result.access_token is not None
        assert result.refresh_token is not None
        assert result.token_type == "Bearer"
        # Old refresh consumed
        assert "rt-3" not in provider._refreshes
        # New tokens stored
        assert provider.get_supabase_token(result.access_token) == "new-sb-access"

    @pytest.mark.asyncio
    async def test_unknown_refresh_token_raises(self, provider, client_info):
        refresh_token = RefreshToken(token="nonexistent", client_id="test-client", scopes=["evidence:read"])

        with pytest.raises(ValueError, match="Refresh token not found"):
            await provider.exchange_refresh_token(client_info, refresh_token, scopes=["evidence:read"])


class TestCleanupStaleEntries:
    def test_cleans_expired_pending(self, provider):
        provider._pending["old"] = _PendingAuth(
            client_id="c1", redirect_uri="https://x.com/cb",
            code_challenge="ch", scopes=None, state=None,
            supabase_state="old", created_at=time.time() - 1000,
        )
        provider._pending["fresh"] = _PendingAuth(
            client_id="c1", redirect_uri="https://x.com/cb",
            code_challenge="ch", scopes=None, state=None,
            supabase_state="fresh",
        )
        provider._cleanup_stale_entries()
        assert "old" not in provider._pending
        assert "fresh" in provider._pending

    def test_cleans_expired_tokens(self, provider):
        provider._tokens["expired"] = _StoredToken(
            token="expired", client_id="c1", scopes=["evidence:read"],
            supabase_access_token="sb", created_at=time.time() - 7200, expires_in=3600,
        )
        provider._tokens["valid"] = _StoredToken(
            token="valid", client_id="c1", scopes=["evidence:read"],
            supabase_access_token="sb",
        )
        provider._cleanup_stale_entries()
        assert "expired" not in provider._tokens
        assert "valid" in provider._tokens

    def test_cleans_expired_auth_codes(self, provider):
        provider._auth_codes["old-code"] = _StoredAuthCode(
            code="old-code", client_id="c1", redirect_uri="https://x.com/cb",
            code_challenge="ch", scopes=["evidence:read"],
            supabase_access_token="sb", supabase_refresh_token="sr",
            created_at=time.time() - 700,
        )
        provider._cleanup_stale_entries()
        assert "old-code" not in provider._auth_codes


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_access_token(self, provider):
        provider._tokens["tok-revoke"] = _StoredToken(
            token="tok-revoke",
            client_id="c1",
            scopes=["evidence:read"],
            supabase_access_token="sb-tok",
        )
        from fastmcp.server.auth import AccessToken
        at = AccessToken(token="tok-revoke", client_id="c1", scopes=["evidence:read"], expires_at=9999999999)
        await provider.revoke_token(at)
        assert "tok-revoke" not in provider._tokens
