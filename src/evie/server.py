"""
EVIE MCP Server
Governed clinical evidence for HCPs via Claude.ai Connector.

Thin query layer over Supabase — no PDF processing, no ML, no Docling.
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl

from .tools import register_tools


# ─── Auth (Supabase as identity backend) ─────────────────────────────────────

def _create_auth() -> RemoteAuthProvider | None:
    """Create auth provider that delegates to Supabase for OAuth.

    JWTVerifier validates Supabase JWTs at the FastMCP layer (Layer 1).
    RemoteAuthProvider tells Claude.ai Connector where to redirect for OAuth.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    if not supabase_url:
        return None

    token_verifier = JWTVerifier(
        jwks_uri=f"{supabase_url}/auth/v1/.well-known/jwks.json",
        issuer=f"{supabase_url}/auth/v1",
        audience="authenticated",
        required_scopes=["evidence:read"],
    )

    return RemoteAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(f"{supabase_url}/auth/v1")],
        base_url=os.environ.get(
            "EVIE_BASE_URL", "https://evie-mcp.railway.app"
        ),
    )


# ─── Server setup ─────────────────────────────────────────────────────────────

mcp = FastMCP(
    "evie",
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

# Register all 5 evidence tools
register_tools(mcp)


# ─── Health check ─────────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request) -> dict:
    return {"status": "ok", "server": "evie_mcp"}


# ─── Well-known MCP server card ──────────────────────────────────────────────

@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def mcp_server_card(request) -> dict:
    return {
        "name": "EVIE — Clinical Evidence",
        "description": (
            "Access governed clinical trial evidence with mandatory context envelopes. "
            "Every result includes population constraints, interpretation guardrails, "
            "and safety statements."
        ),
        "auth": {"type": "oauth2"},
        "tools": [
            {
                "name": "list_trials",
                "description": "List clinical trials available to you",
            },
            {
                "name": "get_trial_summary",
                "description": "Get primary endpoint overview for a trial",
            },
            {
                "name": "get_evidence",
                "description": "Search clinical evidence with natural language",
            },
            {
                "name": "get_evidence_detail",
                "description": "Get full evidence object with context envelope",
            },
            {
                "name": "get_safety_data",
                "description": "Get adverse event data for a trial",
            },
        ],
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    """Run the Evie MCP Server with Streamable HTTP transport."""
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    # Validate required env vars
    required = ["SUPABASE_URL", "SUPABASE_ANON_KEY"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    app = mcp.http_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
