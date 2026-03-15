"""Supabase database client and query helpers for the Evie MCP Server."""

import os
from typing import Optional

from supabase import create_client, Client

from .models import (
    ContextEnvelope,
    EvidenceObject,
    EvidenceWithEnvelope,
    HCPProfile,
    SourceProvenance,
    TrialSummary,
)


def get_client(access_token: Optional[str] = None) -> Client:
    """Create a Supabase client. Uses anon key + user JWT for RLS enforcement."""
    url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    client = create_client(url, anon_key)
    if access_token:
        client.auth.set_session(access_token, "")
    return client


# ─── Row → Model converters ──────────────────────────────────────────────────


def _row_to_evidence_object(row: dict) -> EvidenceObject:
    ci = None
    if row.get("confidence_interval_low") is not None and row.get("confidence_interval_high") is not None:
        ci = [float(row["confidence_interval_low"]), float(row["confidence_interval_high"])]
    return EvidenceObject(
        id=row["id"],
        trial_id=row["trial_id"],
        object_class=row["object_class"],
        endpoint_name=row.get("endpoint_name"),
        result_value=float(row["result_value"]) if row.get("result_value") is not None else None,
        unit=row.get("unit"),
        confidence_interval=ci,
        p_value=float(row["p_value"]) if row.get("p_value") is not None else None,
        time_horizon=row.get("time_horizon"),
        subgroup_definition=row.get("subgroup_definition"),
        arm=row.get("arm"),
        tier=row["tier"],
    )


def _row_to_envelope(row: dict) -> ContextEnvelope:
    prov = row.get("source_provenance")
    source_provenance = SourceProvenance(**prov) if isinstance(prov, dict) else None
    return ContextEnvelope(
        population_constraints=row.get("population_constraints"),
        endpoint_definition=row.get("endpoint_definition"),
        subgroup_qualifiers=row.get("subgroup_qualifiers"),
        interpretation_guardrails=row["interpretation_guardrails"],
        safety_statement=row["safety_statement"],
        methodology_qualifiers=row.get("methodology_qualifiers"),
        source_provenance=source_provenance,
    )


def _pair_evidence_with_envelope(eo_row: dict, envelope_row: Optional[dict]) -> Optional[EvidenceWithEnvelope]:
    """Pair an evidence object row with its envelope. Returns None if envelope is missing."""
    if not envelope_row:
        return None
    return EvidenceWithEnvelope(
        evidence_object=_row_to_evidence_object(eo_row),
        context_envelope=_row_to_envelope(envelope_row),
    )


# ─── Query functions ─────────────────────────────────────────────────────────


def get_hcp_profile(client: Client, user_id: str) -> Optional[HCPProfile]:
    """Fetch HCP profile for the authenticated user."""
    result = client.table("hcp_profiles").select("*").eq("id", user_id).execute()
    if not result.data:
        return None
    row = result.data[0]
    return HCPProfile(
        id=row["id"],
        full_name=row.get("full_name"),
        specialty=row.get("specialty"),
        verification_status=row["verification_status"],
        max_tier_access=row["max_tier_access"],
    )


def list_trials(client: Client) -> list[TrialSummary]:
    """List all trials accessible to the authenticated HCP (RLS-filtered)."""
    result = client.table("trials").select(
        "id, name, drug_name, indication, phase, evidence_objects(object_class)"
    ).eq("status", "active").execute()

    summaries = []
    for row in result.data:
        eo_list = row.get("evidence_objects", [])
        classes = sorted(set(eo["object_class"] for eo in eo_list))
        if not classes:
            continue
        summaries.append(TrialSummary(
            trial_id=row["id"],
            name=row["name"],
            drug_name=row.get("drug_name"),
            indication=row.get("indication"),
            phase=row.get("phase"),
            available_object_classes=classes,
        ))
    return summaries


def get_trial_summary(client: Client, trial_id: str) -> Optional[dict]:
    """Get trial metadata + primary endpoint evidence objects with envelopes."""
    # Fetch trial
    trial_result = client.table("trials").select("*").eq("id", trial_id).limit(1).execute()
    if not trial_result.data:
        return None
    trial = trial_result.data[0]

    # Fetch primary endpoint evidence objects (RLS filters by tier/published)
    eo_result = (
        client.table("evidence_objects")
        .select("*, context_envelopes(*)")
        .eq("trial_id", trial_id)
        .eq("object_class", "primary_endpoint")
        .execute()
    )

    pairs = []
    for row in eo_result.data:
        envelopes = row.pop("context_envelopes", [])
        envelope_row = envelopes[0] if envelopes else None
        pair = _pair_evidence_with_envelope(row, envelope_row)
        if pair:
            pairs.append(pair)

    return {
        "trial": {
            "id": trial["id"],
            "name": trial["name"],
            "drug_name": trial.get("drug_name"),
            "indication": trial.get("indication"),
            "phase": trial.get("phase"),
        },
        "primary_endpoints": [p.model_dump() for p in pairs],
    }


def search_evidence(
    client: Client,
    query: str,
    trial_id: Optional[str] = None,
    object_class: Optional[str] = None,
) -> list[EvidenceWithEnvelope]:
    """Full-text search across evidence objects. Returns pairs with envelopes."""
    q = client.table("evidence_objects").select("*, context_envelopes(*)")
    if trial_id:
        q = q.eq("trial_id", trial_id)
    if object_class:
        q = q.eq("object_class", object_class)

    result = q.text_search("search_vector", query, options={"config": "english"}).limit(20).execute()

    pairs = []
    for row in result.data:
        envelopes = row.pop("context_envelopes", [])
        envelope_row = envelopes[0] if envelopes else None
        pair = _pair_evidence_with_envelope(row, envelope_row)
        if pair:
            pairs.append(pair)
    return pairs


def get_evidence_detail(client: Client, evidence_object_id: str) -> Optional[EvidenceWithEnvelope]:
    """Get a single evidence object with its full context envelope."""
    result = (
        client.table("evidence_objects")
        .select("*, context_envelopes(*)")
        .eq("id", evidence_object_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None

    row = result.data[0]
    envelopes = row.pop("context_envelopes", [])
    envelope_row = envelopes[0] if envelopes else None
    return _pair_evidence_with_envelope(row, envelope_row)


def get_safety_data(client: Client, trial_id: str) -> list[EvidenceWithEnvelope]:
    """Get all adverse event evidence objects for a trial, sorted by incidence (result_value desc)."""
    result = (
        client.table("evidence_objects")
        .select("*, context_envelopes(*)")
        .eq("trial_id", trial_id)
        .eq("object_class", "adverse_event")
        .order("result_value", desc=True)
        .execute()
    )

    pairs = []
    for row in result.data:
        envelopes = row.pop("context_envelopes", [])
        envelope_row = envelopes[0] if envelopes else None
        pair = _pair_evidence_with_envelope(row, envelope_row)
        if pair:
            # Enforce: safety_statement must be present
            if pair.context_envelope.safety_statement:
                pairs.append(pair)
    return pairs
