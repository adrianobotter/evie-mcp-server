"""Authentication and HCP verification for the Evie MCP Server.

Layer 1 (JWT validation) is handled by FastMCP's JWTVerifier.
Layer 2 (RLS) is enforced by Supabase at query time.
This module handles Layer 3: HCP profile verification check.
"""

import os
from dataclasses import dataclass

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
    """Verify HCP profile and check verification status (Layer 3).

    JWT signature validation is already done by FastMCP's JWTVerifier (Layer 1).
    This function checks that the authenticated user has a verified HCP profile.
    """
    url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    client = create_client(url, anon_key)

    # Get user identity from the already-validated JWT
    user_response = client.auth.get_user(access_token)
    if not user_response or not user_response.user:
        raise AuthError("Invalid or expired access token.", code="invalid_token")

    user_id = user_response.user.id

    # Set user JWT on PostgREST so RLS sees the authenticated user
    client.postgrest.auth(access_token)
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
