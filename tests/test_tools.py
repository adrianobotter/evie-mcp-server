"""Tests for EVIE MCP tool handlers — auth flow, DB calls, and error paths."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.evie.auth import AuthError, AuthenticatedHCP
from src.evie.models import (
    ContextEnvelope,
    EvidenceObject,
    EvidenceWithEnvelope,
    HCPProfile,
    TrialSummary,
)
from src.evie.tools import _authenticate, _error_response, _is_auth_error


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_hcp():
    return AuthenticatedHCP(
        user_id="user-123",
        access_token="sb-tok-123",
        profile=HCPProfile(
            id="user-123",
            full_name="Dr. Jane Smith",
            specialty="Endocrinology",
            verification_status="verified",
            max_tier_access="tier2",
        ),
    )


@pytest.fixture
def sample_trial_summary():
    return TrialSummary(
        trial_id="trial-001",
        name="STEP-4",
        drug_name="Semaglutide",
        indication="Obesity",
        phase="Phase 3",
        available_object_classes=["primary_endpoint", "adverse_event"],
    )


@pytest.fixture
def sample_evidence_with_envelope():
    return EvidenceWithEnvelope(
        evidence_object=EvidenceObject(
            id="eo-001",
            trial_id="trial-001",
            object_class="primary_endpoint",
            endpoint_name="Body weight change",
            result_value=-15.2,
            unit="%",
            tier="tier1",
        ),
        context_envelope=ContextEnvelope(
            interpretation_guardrails="mITT population only.",
            safety_statement="Nausea, vomiting, diarrhea.",
        ),
    )


async def _get_tool_fn(name):
    """Register tools on a fresh FastMCP and return the named tool's fn."""
    from fastmcp import FastMCP
    from src.evie.tools import register_tools
    mcp = FastMCP("test")
    register_tools(mcp)
    tool = await mcp.get_tool(name)
    return tool.fn


# ─── _error_response ────────────────────────────────────────────────────────


class TestErrorResponse:
    def test_formats_json(self):
        result = _error_response("Something went wrong", "test_error")
        parsed = json.loads(result)
        assert parsed["error"] == "test_error"
        assert parsed["message"] == "Something went wrong"

    def test_default_code(self):
        result = _error_response("msg")
        parsed = json.loads(result)
        assert parsed["error"] == "error"


# ─── _is_auth_error ─────────────────────────────────────────────────────


class TestIsAuthError:
    def test_jwt_expired(self):
        assert _is_auth_error(Exception("JWT expired")) is True

    def test_unauthorized_401(self):
        assert _is_auth_error(Exception("401 Unauthorized")) is True

    def test_forbidden_403(self):
        assert _is_auth_error(Exception("403 Forbidden")) is True

    def test_pgrst_code(self):
        exc = Exception("auth error")
        exc.code = "PGRST301"
        assert _is_auth_error(exc) is True

    def test_generic_error_not_auth(self):
        assert _is_auth_error(Exception("connection timeout")) is False

    def test_db_error_not_auth(self):
        assert _is_auth_error(RuntimeError("relation does not exist")) is False


# ─── _authenticate ──────────────────────────────────────────────────────────


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_no_token_raises(self):
        with pytest.raises(AuthError, match="No access token"):
            await _authenticate(None)

    @pytest.mark.asyncio
    async def test_invalid_evie_token_raises(self):
        mock_provider = MagicMock()
        mock_provider.get_supabase_token.return_value = None

        token = MagicMock()
        token.token = "bad-evie-tok"

        with patch("src.evie.tools._state") as mock_state:
            mock_state.oauth_provider = mock_provider
            with pytest.raises(AuthError, match="Invalid or expired"):
                await _authenticate(token)

    @pytest.mark.asyncio
    async def test_verify_hcp_network_error_becomes_service_error(self):
        mock_provider = MagicMock()
        mock_provider.get_supabase_token.return_value = "sb-tok"

        token = MagicMock()
        token.token = "evie-tok"

        with patch("src.evie.tools._state") as mock_state, \
             patch("src.evie.tools.verify_hcp", new_callable=AsyncMock) as mock_verify:
            mock_state.oauth_provider = mock_provider
            mock_verify.side_effect = ConnectionError("Supabase is down")
            with pytest.raises(AuthError, match="service unavailable") as exc_info:
                await _authenticate(token)
            assert exc_info.value.code == "service_error"

    @pytest.mark.asyncio
    async def test_verify_hcp_auth_error_passthrough(self):
        mock_provider = MagicMock()
        mock_provider.get_supabase_token.return_value = "sb-tok"

        token = MagicMock()
        token.token = "evie-tok"

        with patch("src.evie.tools._state") as mock_state, \
             patch("src.evie.tools.verify_hcp", new_callable=AsyncMock) as mock_verify:
            mock_state.oauth_provider = mock_provider
            mock_verify.side_effect = AuthError("Not verified", code="not_verified")
            with pytest.raises(AuthError, match="Not verified") as exc_info:
                await _authenticate(token)
            assert exc_info.value.code == "not_verified"

    @pytest.mark.asyncio
    async def test_success_with_provider(self, mock_hcp):
        mock_provider = MagicMock()
        mock_provider.get_supabase_token.return_value = "sb-tok"

        token = MagicMock()
        token.token = "evie-tok"

        with patch("src.evie.tools._state") as mock_state, \
             patch("src.evie.tools.verify_hcp", new_callable=AsyncMock) as mock_verify:
            mock_state.oauth_provider = mock_provider
            mock_verify.return_value = mock_hcp
            result = await _authenticate(token)
            assert result.user_id == "user-123"
            mock_verify.assert_awaited_once_with("sb-tok")

    @pytest.mark.asyncio
    async def test_success_direct_no_provider(self, mock_hcp):
        token = MagicMock()
        token.token = "direct-tok"

        with patch("src.evie.tools._state") as mock_state, \
             patch("src.evie.tools.verify_hcp", new_callable=AsyncMock) as mock_verify:
            mock_state.oauth_provider = None
            mock_verify.return_value = mock_hcp
            result = await _authenticate(token)
            assert result.user_id == "user-123"
            mock_verify.assert_awaited_once_with("direct-tok")


# ─── Tool handlers ──────────────────────────────────────────────────────────


class TestListTrials:
    @pytest.mark.asyncio
    async def test_returns_trials(self, mock_hcp, sample_trial_summary):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.list_trials.return_value = [sample_trial_summary]

            fn = await _get_tool_fn("list_trials")
            result = await fn()
            parsed = json.loads(result)
            assert len(parsed) == 1
            assert parsed[0]["trial_id"] == "trial-001"

    @pytest.mark.asyncio
    async def test_auth_error_returns_json(self):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth:
            mock_gat.return_value = MagicMock()
            mock_auth.side_effect = AuthError("No token", code="no_token")

            fn = await _get_tool_fn("list_trials")
            result = await fn()
            parsed = json.loads(result)
            assert parsed["error"] == "no_token"

    @pytest.mark.asyncio
    async def test_db_error_returns_internal_error(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.side_effect = ConnectionError("DB down")

            fn = await _get_tool_fn("list_trials")
            result = await fn()
            parsed = json.loads(result)
            assert parsed["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_expired_token_returns_invalid_token(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.list_trials.side_effect = Exception("JWT expired")

            fn = await _get_tool_fn("list_trials")
            result = await fn()
            parsed = json.loads(result)
            assert parsed["error"] == "invalid_token"
            assert "expired" in parsed["message"].lower()


class TestGetTrialSummary:
    @pytest.mark.asyncio
    async def test_returns_summary(self, mock_hcp):
        summary_dict = {
            "trial": {"id": "t-1", "name": "STEP-4", "drug_name": "Sema", "indication": "Obesity", "phase": "3"},
            "primary_endpoints": [],
        }
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_trial_summary.return_value = summary_dict

            fn = await _get_tool_fn("get_trial_summary")
            result = await fn(trial_id="t-1")
            parsed = json.loads(result)
            assert parsed["trial"]["name"] == "STEP-4"

    @pytest.mark.asyncio
    async def test_not_found(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_trial_summary.return_value = None

            fn = await _get_tool_fn("get_trial_summary")
            result = await fn(trial_id="nonexistent")
            parsed = json.loads(result)
            assert parsed["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_db_error(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_trial_summary.side_effect = RuntimeError("timeout")

            fn = await _get_tool_fn("get_trial_summary")
            result = await fn(trial_id="t-1")
            parsed = json.loads(result)
            assert parsed["error"] == "internal_error"


class TestGetEvidence:
    @pytest.mark.asyncio
    async def test_returns_results(self, mock_hcp, sample_evidence_with_envelope):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.search_evidence.return_value = [sample_evidence_with_envelope]

            fn = await _get_tool_fn("get_evidence")
            result = await fn(query="weight loss")
            parsed = json.loads(result)
            assert len(parsed) == 1
            assert parsed[0]["evidence_object"]["id"] == "eo-001"

    @pytest.mark.asyncio
    async def test_db_error(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.search_evidence.side_effect = RuntimeError("timeout")

            fn = await _get_tool_fn("get_evidence")
            result = await fn(query="test")
            parsed = json.loads(result)
            assert parsed["error"] == "internal_error"


class TestGetEvidenceDetail:
    @pytest.mark.asyncio
    async def test_returns_detail(self, mock_hcp, sample_evidence_with_envelope):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_evidence_detail.return_value = sample_evidence_with_envelope

            fn = await _get_tool_fn("get_evidence_detail")
            result = await fn(evidence_object_id="eo-001")
            parsed = json.loads(result)
            assert parsed["evidence_object"]["id"] == "eo-001"
            assert "safety_statement" in parsed["context_envelope"]

    @pytest.mark.asyncio
    async def test_not_found(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_evidence_detail.return_value = None

            fn = await _get_tool_fn("get_evidence_detail")
            result = await fn(evidence_object_id="nonexistent")
            parsed = json.loads(result)
            assert parsed["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_db_error(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_evidence_detail.side_effect = RuntimeError("boom")

            fn = await _get_tool_fn("get_evidence_detail")
            result = await fn(evidence_object_id="eo-001")
            parsed = json.loads(result)
            assert parsed["error"] == "internal_error"


class TestGetSafetyData:
    @pytest.mark.asyncio
    async def test_returns_safety_data(self, mock_hcp, sample_evidence_with_envelope):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_safety_data.return_value = [sample_evidence_with_envelope]

            fn = await _get_tool_fn("get_safety_data")
            result = await fn(trial_id="trial-001")
            parsed = json.loads(result)
            assert len(parsed) == 1

    @pytest.mark.asyncio
    async def test_no_data_returns_not_found(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_safety_data.return_value = []

            fn = await _get_tool_fn("get_safety_data")
            result = await fn(trial_id="trial-001")
            parsed = json.loads(result)
            assert parsed["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_db_error(self, mock_hcp):
        with patch("src.evie.tools.get_access_token") as mock_gat, \
             patch("src.evie.tools._authenticate", new_callable=AsyncMock) as mock_auth, \
             patch("src.evie.tools.db") as mock_db:
            mock_gat.return_value = MagicMock()
            mock_auth.return_value = mock_hcp
            mock_db.get_client.return_value = MagicMock()
            mock_db.get_safety_data.side_effect = RuntimeError("boom")

            fn = await _get_tool_fn("get_safety_data")
            result = await fn(trial_id="trial-001")
            parsed = json.loads(result)
            assert parsed["error"] == "internal_error"
