"""EVIE MCP Tool definitions — 5 governed clinical evidence tools for HCPs."""

import json
import time

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.auth import AccessToken

from . import db
from .auth import AuthError, verify_hcp
from . import _state
from .logging import audit, auth_log, tool_log


def _error_response(message: str, code: str = "error") -> str:
    return json.dumps({"error": code, "message": message})


def _is_auth_error(exc: Exception) -> bool:
    """Check if a database/Supabase exception is caused by an expired or invalid token.

    PostgREST returns 401/403 for expired JWTs, and supabase-py surfaces these
    as exceptions with status codes or descriptive messages.  Detecting these
    lets us return ``invalid_token`` instead of a generic ``internal_error``,
    which allows Claude.ai to trigger re-authentication.
    """
    msg = str(exc).lower()
    # Use specific phrases to avoid masking unrelated DB errors.
    # Previously, broad keywords like "jwt" and "token" caused real errors
    # (e.g. PostgREST schema/relationship errors) to be misclassified as auth
    # failures, hiding the actual error message from the user.
    auth_phrases = (
        "jwt expired",
        "invalid jwt",
        "jwt claims",
        "jwt malformed",
        "role claim",
        "not authorized",
        "permission denied",
        "401 unauthorized",
        "403 forbidden",
    )
    if any(phrase in msg for phrase in auth_phrases):
        return True
    # Check HTTP status codes more carefully — only match standalone codes
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (401, 403):
        return True
    # postgrest-py APIError carries a `code` attribute (e.g. "PGRST301")
    code = getattr(exc, "code", None)
    if code and str(code).startswith("PGRST3"):
        return True
    return False


async def _authenticate(access_token: AccessToken | None):
    """Verify the HCP from the FastMCP-injected access token.

    The access token from FastMCP is an EVIE-issued token. We look up the
    corresponding Supabase token to verify the HCP profile.
    """
    if not access_token:
        auth_log.warning("Tool call with no access token", extra={"event": "auth_fail", "error_code": "no_token"})
        raise AuthError("No access token found. Please authenticate via the Evie Connector.", code="no_token")

    # Resolve the Supabase token from the EVIE token
    provider = _state.oauth_provider
    if provider:
        supabase_token = provider.get_supabase_token(access_token.token)
        if not supabase_token:
            auth_log.warning("Invalid EVIE token", extra={"event": "auth_fail", "error_code": "invalid_token"})
            raise AuthError("Invalid or expired access token.", code="invalid_token")
        try:
            hcp = await verify_hcp(supabase_token)
        except AuthError:
            raise
        except Exception as e:
            auth_log.error("HCP verification failed", extra={"event": "auth_error", "error": str(e)})
            raise AuthError("Authentication service unavailable. Please try again.", code="service_error")
        auth_log.info("HCP authenticated", extra={"event": "auth_success", "user_id": hcp.user_id})
        return hcp

    # Fallback: use the token directly (no OAuth provider configured)
    try:
        hcp = await verify_hcp(access_token.token)
    except AuthError:
        raise
    except Exception as e:
        auth_log.error("HCP verification failed", extra={"event": "auth_error", "error": str(e)})
        raise AuthError("Authentication service unavailable. Please try again.", code="service_error")
    auth_log.info("HCP authenticated (direct)", extra={"event": "auth_success", "user_id": hcp.user_id})
    return hcp


def register_tools(mcp: FastMCP) -> None:
    """Register all Evie tools on the given FastMCP server."""

    @mcp.tool(
        name="list_trials",
        annotations={
            "title": "List Available Trials",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def list_trials() -> str:
        """List clinical trials available to you based on your verification status and access tier.

        Returns a JSON array of trials you can query, including trial name, drug,
        indication, phase, and available evidence types.
        """
        t0 = time.monotonic()
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            tool_log.error("list_trials auth failed unexpectedly", extra={"event": "tool_error", "tool": "list_trials", "error": str(e), "error_type": type(e).__name__})
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            trials = db.list_trials(client)
        except Exception as e:
            tool_log.error("list_trials failed", extra={"event": "tool_error", "tool": "list_trials", "user_id": hcp.user_id, "error": str(e), "error_type": type(e).__name__})
            if _is_auth_error(e):
                auth_log.warning("Token rejected by database", extra={"event": "auth_fail", "error_code": "invalid_token", "user_id": hcp.user_id})
                return _error_response("Your session has expired. Please re-authenticate.", "invalid_token")
            return _error_response(f"Database query failed ({type(e).__name__}): {e}", "internal_error")
        audit.info("list_trials", extra={
            "event": "tool_call", "tool": "list_trials",
            "user_id": hcp.user_id, "result_count": len(trials),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        return json.dumps([t.model_dump() for t in trials], indent=2)

    @mcp.tool(
        name="get_trial_summary",
        annotations={
            "title": "Get Trial Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_trial_summary(trial_id: str) -> str:
        """Get a structured overview of a specific clinical trial.

        Returns trial metadata and primary endpoint evidence objects with full
        Context Envelopes (population constraints, guardrails, safety statements).
        Use dedicated tools for subgroup, safety, or comparator data.

        Args:
            trial_id: UUID of the trial to summarize.
        """
        t0 = time.monotonic()
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            tool_log.error("get_trial_summary auth failed unexpectedly", extra={"event": "tool_error", "tool": "get_trial_summary", "error": str(e), "error_type": type(e).__name__})
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            summary = db.get_trial_summary(client, trial_id)
        except Exception as e:
            tool_log.error("get_trial_summary failed", extra={"event": "tool_error", "tool": "get_trial_summary", "user_id": hcp.user_id, "error": str(e), "error_type": type(e).__name__})
            if _is_auth_error(e):
                auth_log.warning("Token rejected by database", extra={"event": "auth_fail", "error_code": "invalid_token", "user_id": hcp.user_id})
                return _error_response("Your session has expired. Please re-authenticate.", "invalid_token")
            return _error_response(f"Database query failed ({type(e).__name__}): {e}", "internal_error")
        if not summary:
            return _error_response("Trial not found or not accessible.", "not_found")
        audit.info("get_trial_summary", extra={
            "event": "tool_call", "tool": "get_trial_summary",
            "user_id": hcp.user_id, "trial_id": trial_id,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        return json.dumps(summary, indent=2)

    @mcp.tool(
        name="get_evidence",
        annotations={
            "title": "Search Clinical Evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_evidence(
        query: str,
        trial_id: str | None = None,
        object_class: str | None = None,
    ) -> str:
        """Search across clinical evidence using natural language.

        Returns matching evidence objects with complete Context Envelopes including
        population constraints, interpretation guardrails, and safety statements.

        Args:
            query: Natural language search — e.g. 'weight loss in patients with BMI > 35'.
            trial_id: Optional UUID to scope search to a specific trial.
            object_class: Optional filter — 'primary_endpoint', 'subgroup', 'adverse_event', or 'comparator'.
        """
        t0 = time.monotonic()
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            tool_log.error("get_evidence auth failed unexpectedly", extra={"event": "tool_error", "tool": "get_evidence", "error": str(e), "error_type": type(e).__name__})
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            results = db.search_evidence(client, query, trial_id=trial_id, object_class=object_class)
        except Exception as e:
            tool_log.error("get_evidence failed", extra={"event": "tool_error", "tool": "get_evidence", "user_id": hcp.user_id, "error": str(e), "error_type": type(e).__name__})
            if _is_auth_error(e):
                auth_log.warning("Token rejected by database", extra={"event": "auth_fail", "error_code": "invalid_token", "user_id": hcp.user_id})
                return _error_response("Your session has expired. Please re-authenticate.", "invalid_token")
            return _error_response(f"Database query failed ({type(e).__name__}): {e}", "internal_error")
        audit.info("get_evidence", extra={
            "event": "tool_call", "tool": "get_evidence",
            "user_id": hcp.user_id, "query": query,
            "trial_id": trial_id, "result_count": len(results),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        return json.dumps(
            [r.model_dump() for r in results],
            indent=2,
        )

    @mcp.tool(
        name="get_evidence_detail",
        annotations={
            "title": "Get Evidence Detail",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_evidence_detail(evidence_object_id: str) -> str:
        """Get the complete evidence object and its full Context Envelope.

        Returns all fields for a specific evidence result, including population
        constraints, endpoint definition, interpretation guardrails, safety
        statement, and source provenance.

        Args:
            evidence_object_id: UUID of the evidence object.
        """
        t0 = time.monotonic()
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            tool_log.error("get_evidence_detail auth failed unexpectedly", extra={"event": "tool_error", "tool": "get_evidence_detail", "error": str(e), "error_type": type(e).__name__})
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            detail = db.get_evidence_detail(client, evidence_object_id)
        except Exception as e:
            tool_log.error("get_evidence_detail failed", extra={"event": "tool_error", "tool": "get_evidence_detail", "user_id": hcp.user_id, "error": str(e), "error_type": type(e).__name__})
            if _is_auth_error(e):
                auth_log.warning("Token rejected by database", extra={"event": "auth_fail", "error_code": "invalid_token", "user_id": hcp.user_id})
                return _error_response("Your session has expired. Please re-authenticate.", "invalid_token")
            return _error_response(f"Database query failed ({type(e).__name__}): {e}", "internal_error")
        if not detail:
            return _error_response("Evidence object not found or not accessible.", "not_found")
        audit.info("get_evidence_detail", extra={
            "event": "tool_call", "tool": "get_evidence_detail",
            "user_id": hcp.user_id, "evidence_object_id": evidence_object_id,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        return json.dumps(detail.model_dump(), indent=2)

    @mcp.tool(
        name="debug_connection",
        annotations={
            "title": "Debug DB Connection",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def debug_connection(trial_id: str | None = None) -> str:
        """Run diagnostic checks on the database connection and data visibility.

        Tests table access, row counts, embedded selects (the pattern used by
        all evidence tools), and RLS visibility. Use this to debug why tools
        are returning errors.

        Args:
            trial_id: Optional UUID to scope diagnostics to a specific trial.
        """
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            result = db.diagnose_connection(client, trial_id=trial_id)
            result["authenticated_as"] = hcp.user_id
            result["tier_access"] = hcp.profile.max_tier_access
        except Exception as e:
            return _error_response(f"Diagnostics failed ({type(e).__name__}): {e}", "internal_error")
        return json.dumps(result, indent=2, default=str)

    @mcp.tool(
        name="get_safety_data",
        annotations={
            "title": "Get Safety Data",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_safety_data(trial_id: str) -> str:
        """Get all adverse event data for a clinical trial.

        Returns adverse event evidence objects sorted by incidence rate (highest first),
        each with a mandatory safety statement from the Context Envelope.

        Args:
            trial_id: UUID of the trial.
        """
        t0 = time.monotonic()
        try:
            access_token = get_access_token()
            hcp = await _authenticate(access_token)
        except AuthError as e:
            return _error_response(e.message, e.code)
        except Exception as e:
            tool_log.error("get_safety_data auth failed unexpectedly", extra={"event": "tool_error", "tool": "get_safety_data", "error": str(e), "error_type": type(e).__name__})
            return _error_response(f"Authentication failed: {type(e).__name__}: {e}", "auth_error")

        try:
            client = db.get_client(access_token=hcp.access_token)
            results = db.get_safety_data(client, trial_id)
        except Exception as e:
            tool_log.error("get_safety_data failed", extra={"event": "tool_error", "tool": "get_safety_data", "user_id": hcp.user_id, "error": str(e), "error_type": type(e).__name__})
            if _is_auth_error(e):
                auth_log.warning("Token rejected by database", extra={"event": "auth_fail", "error_code": "invalid_token", "user_id": hcp.user_id})
                return _error_response("Your session has expired. Please re-authenticate.", "invalid_token")
            return _error_response(f"Database query failed ({type(e).__name__}): {e}", "internal_error")
        if not results:
            return _error_response(
                "No safety data found for this trial or not accessible.",
                "not_found",
            )
        audit.info("get_safety_data", extra={
            "event": "tool_call", "tool": "get_safety_data",
            "user_id": hcp.user_id, "trial_id": trial_id,
            "result_count": len(results),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        return json.dumps(
            [r.model_dump() for r in results],
            indent=2,
        )
