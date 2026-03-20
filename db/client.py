"""Dual-mode Supabase client (PRD §4.0).

Two access paths:
  - HCP OAuth: anon key + user JWT, RLS enforced
  - Partner/service: service_role key, bypasses RLS
"""

from __future__ import annotations

from supabase import create_client, Client

from config import settings


def get_hcp_client(supabase_jwt: str) -> Client:
    """HCP OAuth path — anon key + user JWT, RLS enforced."""
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(supabase_jwt)
    client.postgrest.session.headers["Authorization"] = f"Bearer {supabase_jwt}"
    return client


def get_service_client() -> Client:
    """Partner path — service_role key, bypasses RLS."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def get_client(caller=None) -> Client:
    """Route to correct client based on caller context.

    If caller is provided and has auth_mode 'hcp_oauth' with a supabase_jwt,
    uses the HCP path. Otherwise falls back to service client.
    """
    if caller and getattr(caller, "auth_mode", None) == "hcp_oauth":
        jwt = getattr(caller, "supabase_jwt", None)
        if jwt:
            return get_hcp_client(jwt)
    return get_service_client()
