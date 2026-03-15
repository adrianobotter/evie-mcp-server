"""OAuth provider that makes EVIE its own Authorization Server.

Claude.ai Connector requires RFC 8414 OAuth AS Metadata discovery, which
Supabase doesn't support. This provider wraps Supabase auth behind a
standard OAuth AS so the Connector can complete the OAuth flow:

    Claude.ai  ──OAuth──▶  EVIE MCP Server  ──Supabase Auth──▶  Supabase
"""

import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from fastmcp.server.auth import OAuthProvider
from fastmcp.server.auth.auth import ClientRegistrationOptions
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl


@dataclass
class _PendingAuth:
    """Tracks an in-progress authorization flow."""
    client_id: str
    redirect_uri: str
    code_challenge: str
    scopes: list[str] | None
    state: str | None
    supabase_state: str  # state param we send to Supabase
    created_at: float = field(default_factory=time.time)


@dataclass
class _StoredAuthCode:
    """An issued authorization code with Supabase tokens attached."""
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    scopes: list[str]
    supabase_access_token: str
    supabase_refresh_token: str
    created_at: float = field(default_factory=time.time)


@dataclass
class _StoredToken:
    """An access token issued by EVIE, wrapping a Supabase token."""
    token: str
    client_id: str
    scopes: list[str]
    supabase_access_token: str
    created_at: float = field(default_factory=time.time)
    expires_in: int = 3600


@dataclass
class _StoredRefresh:
    """A refresh token issued by EVIE."""
    token: str
    client_id: str
    scopes: list[str]
    supabase_refresh_token: str
    created_at: float = field(default_factory=time.time)


class SupabaseOAuthProvider(OAuthProvider):
    """OAuth AS that delegates authentication to Supabase.

    The EVIE server becomes its own OAuth AS (serving /.well-known/oauth-authorization-server,
    /authorize, /token, /register). The /authorize endpoint redirects to our login page.
    After the user authenticates via Supabase email/password, we issue an auth code
    and redirect back to Claude.ai.
    """

    def __init__(self, supabase_url: str, supabase_anon_key: str, base_url: str):
        super().__init__(
            base_url=AnyHttpUrl(base_url),
            required_scopes=["evidence:read"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["evidence:read"],
                default_scopes=["evidence:read"],
            ),
        )
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_anon_key = supabase_anon_key
        self._base_url_str = base_url.rstrip("/")

        # In-memory stores (sufficient for single-instance Railway deploy)
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, _PendingAuth] = {}  # keyed by supabase_state
        self._auth_codes: dict[str, _StoredAuthCode] = {}  # keyed by code
        self._tokens: dict[str, _StoredToken] = {}  # keyed by token
        self._refreshes: dict[str, _StoredRefresh] = {}  # keyed by refresh token

    # ── Client Registration (RFC 7591) ───────────────────────────────────────

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = client_info.client_id or secrets.token_hex(16)
        client_info.client_id = client_id
        client_info.client_secret = secrets.token_hex(32)
        client_info.client_id_issued_at = int(time.time())
        client_info.client_secret_expires_at = 0  # never expires
        self._clients[client_id] = client_info

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    # ── Authorization ────────────────────────────────────────────────────────

    def _cleanup_stale_entries(self) -> None:
        """Remove expired entries from in-memory stores."""
        now = time.time()
        stale = [k for k, v in self._pending.items() if now - v.created_at > 900]
        for k in stale:
            del self._pending[k]
        stale = [k for k, v in self._auth_codes.items() if now - v.created_at > 600]
        for k in stale:
            del self._auth_codes[k]
        stale = [k for k, v in self._tokens.items() if now > v.created_at + v.expires_in]
        for k in stale:
            del self._tokens[k]
        stale = [k for k, v in self._refreshes.items() if now - v.created_at > 86400 * 30]
        for k in stale:
            del self._refreshes[k]

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Redirect to EVIE's own login page for email/password auth."""
        self._cleanup_stale_entries()
        supabase_state = secrets.token_urlsafe(32)

        self._pending[supabase_state] = _PendingAuth(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            code_challenge=params.code_challenge,
            scopes=params.scopes,
            state=params.state,
            supabase_state=supabase_state,
        )

        login_params = urlencode({"state": supabase_state})
        return f"{self._base_url_str}/login?{login_params}"

    # ── Email/password login via Supabase ────────────────────────────────────

    async def handle_email_login(self, state: str, email: str, password: str) -> str:
        """Authenticate with Supabase email/password and issue an auth code.

        Returns the redirect URL to send the user back to Claude.ai.
        Raises ValueError on invalid state or failed authentication.
        """
        pending = self._pending.pop(state, None)
        if not pending:
            raise ValueError("Invalid or expired OAuth state")

        # Authenticate with Supabase
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.supabase_url}/auth/v1/token?grant_type=password",
                json={"email": email, "password": password},
                headers={
                    "apikey": self.supabase_anon_key,
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                # Put pending back so user can retry
                self._pending[state] = pending
                raise ValueError("Invalid email or password")
            token_data = resp.json()

        # Issue our own authorization code
        evie_code = secrets.token_urlsafe(48)
        scopes = pending.scopes or ["evidence:read"]
        self._auth_codes[evie_code] = _StoredAuthCode(
            code=evie_code,
            client_id=pending.client_id,
            redirect_uri=pending.redirect_uri,
            code_challenge=pending.code_challenge,
            scopes=scopes,
            supabase_access_token=token_data["access_token"],
            supabase_refresh_token=token_data.get("refresh_token", ""),
        )

        # Redirect back to Claude.ai with our authorization code
        redirect_params = {"code": evie_code}
        if pending.state:
            redirect_params["state"] = pending.state
        return f"{pending.redirect_uri}?{urlencode(redirect_params)}"

    # ── Authorization Code ───────────────────────────────────────────────────

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        stored = self._auth_codes.get(authorization_code)
        if not stored:
            return None
        if stored.client_id != client.client_id:
            return None
        # Expire after 10 minutes
        if time.time() - stored.created_at > 600:
            self._auth_codes.pop(authorization_code, None)
            return None
        # Return the SDK AuthorizationCode type the framework expects
        return AuthorizationCode(
            code=stored.code,
            client_id=stored.client_id,
            redirect_uri=AnyUrl(stored.redirect_uri),
            redirect_uri_provided_explicitly=True,
            code_challenge=stored.code_challenge,
            scopes=stored.scopes,
            expires_at=stored.created_at + 600,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Retrieve the stored code to get Supabase tokens
        stored = self._auth_codes.pop(authorization_code.code, None)
        if not stored:
            raise ValueError("Authorization code not found")

        # Issue EVIE tokens that wrap the Supabase token
        access_tok = secrets.token_urlsafe(48)
        refresh_tok = secrets.token_urlsafe(48)
        expires_in = 3600
        scopes = authorization_code.scopes or ["evidence:read"]

        self._tokens[access_tok] = _StoredToken(
            token=access_tok,
            client_id=client.client_id,
            scopes=scopes,
            supabase_access_token=stored.supabase_access_token,
            expires_in=expires_in,
        )
        self._refreshes[refresh_tok] = _StoredRefresh(
            token=refresh_tok,
            client_id=client.client_id,
            scopes=scopes,
            supabase_refresh_token=stored.supabase_refresh_token,
        )

        return OAuthToken(
            access_token=access_tok,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_tok,
        )

    # ── Token Verification ───────────────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = self._tokens.get(token)
        if not stored:
            return None
        expires_at = int(stored.created_at + stored.expires_in)
        if time.time() > expires_at:
            self._tokens.pop(token, None)
            return None
        return AccessToken(
            token=stored.token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=expires_at,
        )

    # ── Refresh Token ────────────────────────────────────────────────────────

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        stored = self._refreshes.get(refresh_token)
        if not stored or stored.client_id != client.client_id:
            return None
        return RefreshToken(
            token=stored.token,
            client_id=stored.client_id,
            scopes=stored.scopes,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Retrieve stored refresh to get Supabase token
        stored = self._refreshes.pop(refresh_token.token, None)
        if not stored:
            raise ValueError("Refresh token not found")

        # Refresh the Supabase token
        supabase_refresh = stored.supabase_refresh_token
        if not supabase_refresh:
            raise ValueError("No Supabase refresh token available")

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token",
                json={"refresh_token": supabase_refresh},
                headers={
                    "apikey": self.supabase_anon_key,
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                raise ValueError(
                    f"Supabase token refresh failed (HTTP {resp.status_code})"
                )
            data = resp.json()
            supabase_access = data["access_token"]
            supabase_refresh = data.get("refresh_token", supabase_refresh)

        # Issue new EVIE tokens
        new_access = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        expires_in = 3600
        new_scopes = scopes or stored.scopes or ["evidence:read"]

        self._tokens[new_access] = _StoredToken(
            token=new_access,
            client_id=client.client_id,
            scopes=new_scopes,
            supabase_access_token=supabase_access,
            expires_in=expires_in,
        )
        self._refreshes[new_refresh] = _StoredRefresh(
            token=new_refresh,
            client_id=client.client_id,
            scopes=new_scopes,
            supabase_refresh_token=supabase_refresh,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(new_scopes) if new_scopes else None,
            refresh_token=new_refresh,
        )

    # ── Revocation ───────────────────────────────────────────────────────────

    async def revoke_token(
        self, token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            self._tokens.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._refreshes.pop(token.token, None)

    # ── Helper: resolve Supabase token from EVIE token ───────────────────────

    def get_supabase_token(self, evie_token: str) -> str | None:
        """Look up the Supabase access token for a given EVIE access token."""
        stored = self._tokens.get(evie_token)
        return stored.supabase_access_token if stored else None
