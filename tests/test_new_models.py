"""Tests for db/models.py — new PRD v2.0 fields."""

from db.models import EvidenceObject, EvidenceWithEnvelope, ContextEnvelope


class TestNewPRDFields:
    def test_evidence_object_new_fields_default_none(self):
        eo = EvidenceObject(
            id="1", trial_id="t1", object_class="primary_endpoint", tier="tier1"
        )
        assert eo.evidence_badge is None
        assert eo.audience_routing is None
        assert eo.evidence_hierarchy_level is None
        assert eo.dark_data_flag is None
        assert eo.fair_balance_text is None
        assert eo.cross_trial_comparison_policy is None
        assert eo.render_requirements is None

    def test_evidence_object_with_new_fields(self):
        eo = EvidenceObject(
            id="1",
            trial_id="t1",
            object_class="primary_endpoint",
            tier="tier1",
            evidence_badge="green",
            audience_routing=["hcp", "payer"],
            evidence_hierarchy_level="L1",
            dark_data_flag=False,
            fair_balance_text="Fair balance text here",
            cross_trial_comparison_policy="no_cross_trial",
            render_requirements={"chart_type": "bar"},
        )
        assert eo.evidence_badge == "green"
        assert eo.audience_routing == ["hcp", "payer"]
        assert eo.render_requirements["chart_type"] == "bar"

    def test_serialization_includes_new_fields(self):
        eo = EvidenceObject(
            id="1",
            trial_id="t1",
            object_class="primary_endpoint",
            tier="tier1",
            evidence_badge="amber",
        )
        d = eo.model_dump()
        assert "evidence_badge" in d
        assert d["evidence_badge"] == "amber"
        assert "audience_routing" in d
        assert d["audience_routing"] is None
