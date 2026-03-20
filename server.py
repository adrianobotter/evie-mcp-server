"""EVIE MCP Server — root entry point.

Governed pharmaceutical evidence for AI platform partners and HCPs.
Thin query layer over Supabase — no PDF processing, no ML.

Usage:
    python server.py
"""

import html
import logging
import os
import sys

from fastmcp import FastMCP
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth import _state
from auth.hcp_oauth import SupabaseOAuthProvider
from config import settings
from transport.health import health_check


# --- Logging ------------------------------------------------------------------

from src.evie.logging import setup_logging, server_log

setup_logging()


# --- Auth (Supabase as identity backend) --------------------------------------

def _create_auth() -> SupabaseOAuthProvider | None:
    """Create OAuth provider that acts as an AS, delegating to Supabase."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        return None

    provider = SupabaseOAuthProvider(
        supabase_url=settings.SUPABASE_URL,
        supabase_anon_key=settings.SUPABASE_ANON_KEY,
        base_url=settings.EVIE_BASE_URL,
    )
    _state.oauth_provider = provider
    return provider


# --- Server setup -------------------------------------------------------------

mcp = FastMCP(
    "evie-mcp-server",
    version="2.0.0",
    instructions=(
        "EVIE provides governed clinical evidence from published trials. "
        "Every evidence result includes a Context Envelope with population constraints, "
        "interpretation guardrails, and a safety statement. Always present these "
        "guardrails to the user — never omit or summarize away the safety statement. "
        "Start with list_trials to see available data, then use get_trial_summary, "
        "get_evidence, get_evidence_detail, or get_safety_data as needed."
    ),
    auth=_create_auth(),
)

# Phase 1: zero tools registered. Tools added in Phase 3+.
health_check._tool_count = 0


# --- Health check -------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def _health(request):
    return await health_check(request)


# --- Debug endpoint -----------------------------------------------------------

@mcp.custom_route("/debug/test-db", methods=["GET"])
async def debug_test_db(request):
    """Diagnostic endpoint: test Supabase connectivity and schema."""
    from supabase import create_client

    checks: dict = {}
    try:
        url = settings.SUPABASE_URL
        key = settings.SUPABASE_ANON_KEY
        if not url or not key:
            return JSONResponse({"error": "Missing Supabase config"}, status_code=500)
        checks["env_vars"] = "ok"
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        client = create_client(url, key)
        checks["create_client"] = "ok"
    except Exception as e:
        checks["create_client"] = f"FAILED: {type(e).__name__}: {e}"
        return JSONResponse(checks, status_code=500)

    for table in ("trials", "evidence_objects", "hcp_profiles"):
        try:
            result = client.table(table).select("id").limit(1).execute()
            checks[f"table_{table}"] = f"ok (rows visible to anon: {len(result.data)})"
        except Exception as e:
            checks[f"table_{table}"] = f"FAILED: {type(e).__name__}: {e}"

    checks["oauth_provider"] = "configured" if _state.oauth_provider else "NOT configured"

    status = 200 if all("FAILED" not in str(v) for v in checks.values()) else 500
    return JSONResponse(checks, status_code=status)


# --- Login page (email/password) ----------------------------------------------

_LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EVIE — Sign In</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; margin: 0; }
  .card { background: #1e293b; border-radius: 12px; padding: 2rem; width: 100%%;
          max-width: 380px; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }
  h1 { font-size: 1.25rem; margin: 0 0 0.25rem; }
  p { color: #94a3b8; font-size: 0.875rem; margin: 0 0 1.5rem; }
  label { display: block; font-size: 0.875rem; margin-bottom: 0.25rem; color: #cbd5e1; }
  input { width: 100%%; padding: 0.5rem 0.75rem; border: 1px solid #334155;
          border-radius: 6px; background: #0f172a; color: #e2e8f0;
          font-size: 0.875rem; margin-bottom: 1rem; box-sizing: border-box; }
  button { width: 100%%; padding: 0.625rem; background: #3b82f6; color: #fff;
           border: none; border-radius: 6px; font-size: 0.875rem; cursor: pointer; }
  button:hover { background: #2563eb; }
  .error { color: #f87171; font-size: 0.8rem; margin-bottom: 1rem; }
</style></head>
<body><div class="card">
  <h1>EVIE — Clinical Evidence</h1>
  <p>Sign in to connect with Claude</p>
  %s
  <form method="POST" action="/login">
    <input type="hidden" name="state" value="%s">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required>
    <button type="submit">Sign In</button>
  </form>
</div></body></html>"""


@mcp.custom_route("/login", methods=["GET"])
async def login_page(request):
    """Show the login form."""
    state = request.query_params.get("state", "")
    return HTMLResponse(_LOGIN_HTML % ("", state))


@mcp.custom_route("/login", methods=["POST"])
async def login_submit(request):
    """Handle login form submission."""
    provider = _state.oauth_provider
    if not provider:
        return JSONResponse({"error": "Auth not configured"}, status_code=500)

    form = await request.form()
    state = form.get("state", "")
    email = form.get("email", "")
    password = form.get("password", "")

    try:
        redirect_url = await provider.handle_email_login(
            state=state, email=email, password=password,
        )
        return RedirectResponse(redirect_url, status_code=303)
    except ValueError as e:
        error_html = '<div class="error">%s</div>' % html.escape(str(e))
        return HTMLResponse(_LOGIN_HTML % (error_html, state), status_code=400)


# --- Well-known MCP server card -----------------------------------------------

@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def mcp_server_card(request):
    return JSONResponse({
        "name": "EVIE — Clinical Evidence",
        "description": (
            "Access governed clinical trial evidence with mandatory context envelopes. "
            "Every result includes population constraints, interpretation guardrails, "
            "and safety statements."
        ),
        "version": "2.0.0",
        "auth": {"type": "oauth2"},
        "tools": [],  # Phase 1: no tools registered yet
    })


# --- Entry point --------------------------------------------------------------

def main():
    """Run the EVIE MCP Server with Streamable HTTP transport."""
    import uvicorn

    settings.validate()

    server_log.info(
        "Starting EVIE MCP Server v2.0.0",
        extra={"event": "server_start", "host": settings.HOST, "port": settings.PORT},
    )
    app = mcp.http_app()
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
