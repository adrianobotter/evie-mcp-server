"""Unified caller identity resolver (PRD §5.2).

Phase 1: returns anonymous Tier 1 only.
Phase 5: full dual-path resolver (HCP OAuth + Partner key/JWT).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CallerContext:
    """Unified caller identity passed through all tool calls."""

    auth_mode: str  # "hcp_oauth" | "partner_key" | "partner_jwt" | "anonymous"
    max_tier: int  # 1, 2, or 3
    audience_type: str  # "hcp" | "payer" | "patient" | "msl"
    partner_name: str  # Partner identifier or "direct_hcp"
    hcp_user_id: str | None = None
    supabase_jwt: str | None = None
    npi: str | None = None
    sponsor_id: str | None = None


def resolve_caller_tier(request_context=None) -> CallerContext:
    """Phase 1: returns anonymous Tier 1. Full dual resolver in Phase 5."""
    return CallerContext(
        auth_mode="anonymous",
        max_tier=1,
        audience_type="hcp",
        partner_name="anonymous",
    )
