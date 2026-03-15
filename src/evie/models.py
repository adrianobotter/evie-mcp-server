"""Pydantic models for EVIE Evidence Objects and Context Envelopes."""

from typing import Optional
from pydantic import BaseModel



# ─── Source Provenance ────────────────────────────────────────────────────────

class SourceProvenance(BaseModel):
    trial_name: Optional[str] = None
    doi: Optional[str] = None
    clinicaltrials_id: Optional[str] = None
    publication_date: Optional[str] = None


# ─── Context Envelope ────────────────────────────────────────────────────────

class ContextEnvelope(BaseModel):
    population_constraints: Optional[str] = None
    endpoint_definition: Optional[str] = None
    subgroup_qualifiers: Optional[str] = None
    interpretation_guardrails: str
    safety_statement: str
    methodology_qualifiers: Optional[str] = None
    source_provenance: Optional[SourceProvenance] = None


# ─── Evidence Object ─────────────────────────────────────────────────────────

class EvidenceObject(BaseModel):
    id: str
    trial_id: str
    object_class: str
    endpoint_name: Optional[str] = None
    result_value: Optional[float] = None
    unit: Optional[str] = None
    confidence_interval: Optional[list[float]] = None
    p_value: Optional[float] = None
    time_horizon: Optional[str] = None
    subgroup_definition: Optional[str] = None
    arm: Optional[str] = None
    tier: str


# ─── Combined response pair ──────────────────────────────────────────────────

class EvidenceWithEnvelope(BaseModel):
    evidence_object: EvidenceObject
    context_envelope: ContextEnvelope


# ─── Trial listing ───────────────────────────────────────────────────────────

class TrialSummary(BaseModel):
    trial_id: str
    name: str
    drug_name: Optional[str] = None
    indication: Optional[str] = None
    phase: Optional[str] = None
    available_object_classes: list[str]


# ─── HCP Profile ─────────────────────────────────────────────────────────────

class HCPProfile(BaseModel):
    id: str
    full_name: Optional[str] = None
    specialty: Optional[str] = None
    verification_status: str
    max_tier_access: str
