"""Authentication and HCP verification for the Evie MCP Server."""

import os
from dataclasses import dataclass
from typing import Optional

from supabase import create_client

from .models import HCPProfile


@dataclass
class AuthenticatedHCP:
    """Resolved HCP identity after authentication and verification."""
    user_id: str
    access_token: str
    profile: HCPProfile


class AuthError(Exception):
    """Raised when authentication or verification fails."""
    def __init__(self, message: str, code: str = "auth_error"):
        self.message = message
        self.code = code
        super().__init__(message)


async def verify_hcp(access_token: str) -> AuthenticatedHCP:
    """Verify an HCP's JWT and check their verification status.

    Layer 1: Validates the JWT with Supabase Auth.
    Layer 3: Checks verification_status = 'verified' in hcp_profiles.

    (Layer 2 — RLS — is enforced at the query level by using anon key + JWT.)
    """
    url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    client = create_client(url, anon_key)

    # Validate JWT and get user identity
    user_response = client.auth.get_user(access_token)
    if not user_response or not user_response.user:
        raise AuthError("Invalid or expired access token.", code="invalid_token")

    user_id = user_response.user.id

    # Fetch HCP profile — use service client to read profile regardless of RLS
    # (hcp_profiles RLS restricts to own row, which requires the session to be set)
    client.auth.set_session(access_token, "")
    result = client.table("hcp_profiles").select("*").eq("id", user_id).execute()

    if not result.data:
        raise AuthError(
            "No HCP profile found. Please complete registration.",
            code="no_profile",
        )

    row = result.data[0]
    profile = HCPProfile(
        id=row["id"],
        full_name=row.get("full_name"),
        specialty=row.get("specialty"),
        verification_status=row["verification_status"],
        max_tier_access=row["max_tier_access"],
    )

    # Layer 3: Tool-level verification check
    if profile.verification_status != "verified":
        raise AuthError(
            f"HCP verification status is '{profile.verification_status}'. "
            "Only verified HCPs can access clinical evidence.",
            code="not_verified",
        )

    return AuthenticatedHCP(
        user_id=user_id,
        access_token=access_token,
        profile=profile,
    )


def get_supabase_oauth_config() -> dict:
    """Return OAuth configuration for the Supabase identity provider."""
    return {
        "authorization_url": f"{os.environ['SUPABASE_URL']}/auth/v1/authorize",
        "token_url": f"{os.environ['SUPABASE_URL']}/auth/v1/token?grant_type=authorization_code",
        "client_id": os.environ.get("SUPABASE_OAUTH_CLIENT_ID", ""),
        "client_secret": os.environ.get("SUPABASE_OAUTH_CLIENT_SECRET", ""),
        "scopes": ["evidence:read", "profile:read"],
    }
