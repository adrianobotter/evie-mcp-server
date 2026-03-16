"""Integration tests for Supabase RLS policies using the test HCP user.

These tests hit the live Supabase instance to verify that Row-Level Security
policies correctly filter data for an authenticated HCP user (tier4/verified).

Run with: pytest tests/test_rls_integration.py -v -m integration
"""

import os
import pytest
from supabase import create_client

# Point env at the real Supabase instance for integration tests
SUPABASE_URL = "https://yjtmpjuxwrggkskdffdp.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlqdG1wanV4d3JnZ2tza2RmZmRwIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzM0MTMxNDQsImV4cCI6MjA4ODk4OTE0NH0."
    "nI67zUHkxux_vB1oV0FR6o8OFcQ2PyCCwIayRzY_qzc"
)
TEST_EMAIL = "test-hcp@evie.local"
TEST_PASSWORD = "evie-test-2026!"

# Known seed data IDs (from migrations/003_seed_step4.sql)
STEP4_TRIAL_ID = "b0000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def access_token():
    """Sign in as the test HCP user and return a valid JWT."""
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    result = client.auth.sign_in_with_password(
        {"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert result.session is not None, "Failed to sign in test user"
    return result.session.access_token


@pytest.fixture(scope="module")
def rls_client(access_token):
    """Create a Supabase client authenticated with the test user's JWT."""
    os.environ["SUPABASE_URL"] = SUPABASE_URL
    os.environ["SUPABASE_ANON_KEY"] = SUPABASE_ANON_KEY
    from src.evie.db import get_client

    return get_client(access_token=access_token)


# ─── Direct table access tests ──────────────────────────────────────────────


class TestTrialsRLS:
    def test_active_trial_visible(self, rls_client):
        """Tier4 HCP should see the STEP-4 trial (active with published evidence)."""
        result = rls_client.table("trials").select("*").execute()
        assert len(result.data) >= 1
        trial_ids = [r["id"] for r in result.data]
        assert STEP4_TRIAL_ID in trial_ids

    def test_trial_has_expected_fields(self, rls_client):
        result = (
            rls_client.table("trials")
            .select("id, name, drug_name, indication, phase, status")
            .eq("id", STEP4_TRIAL_ID)
            .execute()
        )
        assert len(result.data) == 1
        trial = result.data[0]
        assert trial["name"] == "STEP-4"
        assert trial["drug_name"] == "Semaglutide 2.4 mg"
        assert trial["status"] == "active"


class TestEvidenceObjectsRLS:
    def test_published_evidence_visible(self, rls_client):
        """Tier4 HCP should see all published evidence objects."""
        result = (
            rls_client.table("evidence_objects")
            .select("id, object_class, tier, is_published")
            .eq("trial_id", STEP4_TRIAL_ID)
            .execute()
        )
        # Seed data has 7 evidence objects, all published
        assert len(result.data) >= 7
        for row in result.data:
            assert row["is_published"] is True

    def test_evidence_tiers_accessible(self, rls_client):
        """Tier4 user should see evidence at all tier levels."""
        result = (
            rls_client.table("evidence_objects")
            .select("tier")
            .eq("trial_id", STEP4_TRIAL_ID)
            .execute()
        )
        tiers = set(r["tier"] for r in result.data)
        assert "tier1" in tiers
        assert "tier2" in tiers  # subgroup evidence is tier2


class TestContextEnvelopesRLS:
    def test_envelopes_accessible(self, rls_client):
        """Envelopes should be visible for accessible evidence objects."""
        result = (
            rls_client.table("context_envelopes")
            .select("id, evidence_object_id")
            .execute()
        )
        assert len(result.data) >= 7  # one envelope per evidence object

    def test_envelope_has_required_fields(self, rls_client):
        result = (
            rls_client.table("context_envelopes")
            .select("interpretation_guardrails, safety_statement")
            .limit(1)
            .execute()
        )
        assert len(result.data) == 1
        row = result.data[0]
        assert row["interpretation_guardrails"] is not None
        assert row["safety_statement"] is not None


class TestHCPProfileRLS:
    def test_own_profile_visible(self, rls_client):
        """HCP should see only their own profile."""
        result = (
            rls_client.table("hcp_profiles")
            .select("id, verification_status, max_tier_access")
            .execute()
        )
        assert len(result.data) == 1
        profile = result.data[0]
        assert profile["verification_status"] == "verified"
        assert profile["max_tier_access"] == "tier4"


class TestDeniedTablesRLS:
    def test_sponsors_denied(self, rls_client):
        """HCPs should not see any sponsor data."""
        result = rls_client.table("sponsors").select("id").execute()
        assert len(result.data) == 0

    def test_source_documents_denied(self, rls_client):
        """HCPs should not see source documents."""
        result = rls_client.table("source_documents").select("id").execute()
        assert len(result.data) == 0


# ─── DB helper function tests (using real RLS) ─────────────────────────────


class TestDBFunctionsWithRLS:
    def test_list_trials(self, rls_client):
        """list_trials() should return STEP-4 with correct object classes."""
        from src.evie.db import list_trials

        trials = list_trials(rls_client)
        assert len(trials) >= 1
        step4 = next((t for t in trials if t.trial_id == STEP4_TRIAL_ID), None)
        assert step4 is not None
        assert step4.name == "STEP-4"
        assert "primary_endpoint" in step4.available_object_classes
        assert "adverse_event" in step4.available_object_classes

    def test_get_trial_summary(self, rls_client):
        """get_trial_summary() should return trial + primary endpoints with envelopes."""
        from src.evie.db import get_trial_summary

        summary = get_trial_summary(rls_client, STEP4_TRIAL_ID)
        assert summary is not None
        assert summary["trial"]["name"] == "STEP-4"
        assert len(summary["primary_endpoints"]) >= 1
        for ep in summary["primary_endpoints"]:
            assert "evidence_object" in ep
            assert "context_envelope" in ep

    def test_search_evidence(self, rls_client):
        """search_evidence() should find results for 'weight' query."""
        from src.evie.db import search_evidence

        results = search_evidence(rls_client, "weight")
        assert len(results) >= 1
        for r in results:
            assert r.context_envelope is not None

    def test_get_safety_data(self, rls_client):
        """get_safety_data() should return adverse events with safety statements."""
        from src.evie.db import get_safety_data

        results = get_safety_data(rls_client, STEP4_TRIAL_ID)
        assert len(results) >= 3  # nausea, diarrhea, vomiting
        for r in results:
            assert r.evidence_object.object_class == "adverse_event"
            assert r.context_envelope.safety_statement
