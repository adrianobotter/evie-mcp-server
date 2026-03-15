"""Tests for EVIE Pydantic models and tier logic."""

from src.evie.models import (
    ContextEnvelope,
    EvidenceObject,
    EvidenceWithEnvelope,
    HCPProfile,
    SourceProvenance,
    TrialSummary,
)


class TestEvidenceObject:
    def test_minimal_evidence(self):
        eo = EvidenceObject(id="1", trial_id="t1", object_class="adverse_event", tier="tier1")
        assert eo.endpoint_name is None
        assert eo.result_value is None
        assert eo.confidence_interval is None

    def test_full_evidence(self):
        eo = EvidenceObject(
            id="1",
            trial_id="t1",
            object_class="primary_endpoint",
            endpoint_name="Weight change",
            result_value=-15.2,
            unit="%",
            confidence_interval=[-16.1, -14.3],
            p_value=0.0001,
            time_horizon="68 weeks",
            arm="Semaglutide 2.4mg",
            tier="tier1",
        )
        assert eo.result_value == -15.2
        assert len(eo.confidence_interval) == 2


class TestContextEnvelope:
    def test_requires_mandatory_fields(self):
        env = ContextEnvelope(
            interpretation_guardrails="Results apply to mITT.",
            safety_statement="Nausea, vomiting reported.",
        )
        assert env.population_constraints is None
        assert env.safety_statement == "Nausea, vomiting reported."

    def test_with_provenance(self):
        prov = SourceProvenance(trial_name="STEP-4", doi="10.1056/test")
        env = ContextEnvelope(
            interpretation_guardrails="guardrails",
            safety_statement="safety",
            source_provenance=prov,
        )
        assert env.source_provenance.trial_name == "STEP-4"


class TestEvidenceWithEnvelope:
    def test_pairing(self):
        eo = EvidenceObject(id="1", trial_id="t1", object_class="primary_endpoint", tier="tier1")
        env = ContextEnvelope(
            interpretation_guardrails="guardrails",
            safety_statement="safety",
        )
        pair = EvidenceWithEnvelope(evidence_object=eo, context_envelope=env)
        assert pair.evidence_object.id == "1"
        assert pair.context_envelope.safety_statement == "safety"


class TestTrialSummary:
    def test_serialization(self):
        ts = TrialSummary(
            trial_id="t1",
            name="STEP-4",
            drug_name="Semaglutide",
            indication="Obesity",
            phase="Phase 3",
            available_object_classes=["primary_endpoint", "adverse_event"],
        )
        d = ts.model_dump()
        assert d["name"] == "STEP-4"
        assert len(d["available_object_classes"]) == 2


class TestHCPProfile:
    def test_verified_profile(self):
        p = HCPProfile(
            id="u1",
            full_name="Dr. Smith",
            verification_status="verified",
            max_tier_access="tier2",
        )
        assert p.verification_status == "verified"
