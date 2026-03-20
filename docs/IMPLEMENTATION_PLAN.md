# EVIE MCP Server — Implementation Plan

**Version:** 0.1 (Phase 1 detailed)
**Date:** March 20, 2026
**Source PRD:** `docs/EVIE_MCP_SERVER_PRD.md` v2.0
**Branch:** `claude/evie-mcp-implementation-plan-J5G1B`

> This is a living document. Each phase will be detailed just before implementation begins. Completed phases will be marked with timestamps.

---

## Gap Analysis: Current State vs PRD Target

| Area | Current (`src/evie/`) | PRD Target (root-level packages) | Delta |
|------|----------------------|----------------------------------|-------|
| **Module structure** | Flat `src/evie/{server,tools,db,auth,oauth,models,logging}.py` | Root `server.py` + `db/`, `auth/`, `tools/`, `compliance/`, `rest/`, `transport/` packages | Full restructure |
| **Entry point** | `python -m src.evie.server` | `python server.py` | Move + rewrite |
| **Authentication** | HCP OAuth only (single path) | Dual auth: HCP OAuth (Path A) + Partner API key/JWT (Path B) | Add partner auth |
| **DB access** | `SUPABASE_ANON_KEY` + RLS only | Hybrid: anon+RLS for HCP, `SERVICE_ROLE_KEY` for partners | Add service_role client |
| **Tier model** | tier1–tier4 | tier1–tier3 | Schema change |
| **Audience routing** | Not implemented | `audience_routing` array column + `audience_type` param on all tools | Schema + app change |
| **Evidence badge** | Not implemented | `evidence_badge` column (green/amber/red) + patient lay-language mapping | Schema + app change |
| **Evidence hierarchy** | Not implemented | `evidence_hierarchy` table (L1→L2→L3 parent/child) | New table |
| **Audit logging** | Python logger only | `audience_query_log` table INSERT per tool call | New table + writes |
| **Response format** | Ad-hoc JSON | PRD envelope `{ evidence, metadata, compliance }` | Breaking change |
| **Compliance module** | Not implemented | `compliance/` package: envelope, badge, comparison_policy, audit | New package |
| **REST wrapper** | Not implemented | `rest/` package: FastAPI + OpenAPI for ChatGPT Apps | New package |
| **Seed data** | STEP-4 (7 evidence objects) | Wegovy STEP-1 pilot (5 evidence objects per PRD §10.2) | New seed script |
| **Tools** | 5 core + 1 debug | 5 core + 5 HCP + 5 payer (15 total) | 10 new tools |
| **Dependencies** | fastmcp, httpx, supabase, pydantic | Add: python-jose, uvicorn, fastapi | requirements.txt update |
| **Config** | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `PORT`, `HOST` | Add: `SERVICE_ROLE_KEY`, `JWT_SECRET`, `SPONSOR_TOKEN_SECRET`, `EVIE_TOKEN_SECRET`, `LOG_LEVEL` | .env expansion |

---

## Phase Overview

| Phase | Name | Scope | Status |
|-------|------|-------|--------|
| **1** | Scaffold & Restructure | Module structure, config, dual DB client, health endpoint, Docker, Railway | **Detailed below** |
| **2** | Schema Evolution | Clean-slate schema: 3 tiers, audience_routing, evidence_badge, evidence_hierarchy, audience_query_log, Wegovy STEP-1 seed data | Pending |
| **3** | Core Read Tools | `list_trials`, `get_trial_summary` — anonymous access, PRD response envelope | Pending |
| **4** | Evidence Retrieval | `get_evidence` with tier filtering, audience routing, envelope injection, badge enforcement | Pending |
| **5** | Dual Auth | Partner key (Tier 2), partner JWT (Tier 3), HCP OAuth (preserve), unified `CallerContext`, patient ceiling | Pending |
| **6** | Compliance Module | `envelope.py`, `badge.py`, `comparison_policy.py`, `audit.py` — all invariants from PRD §7 | Pending |
| **7** | Detail + Safety Tools | `get_evidence_detail` with hierarchy children, `get_safety_data` — completes v1.0 suite | Pending |
| **8** | Claude Connector Integration | Register on Claude.ai, OAuth config, end-to-end testing with v1.0 tools | Pending |
| **9** | REST Wrapper | FastAPI REST layer, OpenAPI spec generation, `tools/internal.py` shared functions, auth middleware | Pending |
| **10** | HCP Tool Suite | `compare_products`, `get_subgroup_evidence`, `check_stopping_rule`, `get_dosing_guidance`, `get_adherence_data` | Pending |
| **11** | Payer Tool Suite | `run_eligibility_screener`, `get_pa_criteria`, `get_budget_impact_summary`, `get_formulary_comparison`, `get_step_therapy_rules` | Pending |
| **12** | ChatGPT Apps Integration | Custom GPT creation, Actions config, OpenAPI spec upload, end-to-end testing | Pending |
| **13** | Hardening | Merged error format, input validation, rate limiting, connection pooling, structured logging | Pending |

---

## Phase 1: Scaffold & Restructure

**Goal:** Restructure the project from flat `src/evie/` into the PRD's root-level package layout. Establish dual-mode DB client, updated config, health endpoint, and deployment pipeline. No tool logic yet — just the skeleton.

**Success Criteria:**
- `python server.py` starts the FastMCP server
- `GET /health` returns `{ "status": "ok", "tools": 0, "db": "connected" }`
- Docker build succeeds
- Railway deploy config updated
- All existing tests pass (adapted to new paths) OR are cleanly replaced by new structure tests

### 1.1 Create PRD module structure

Create the following directory tree with `__init__.py` files:

```
evie-mcp-server/
├── server.py              # NEW — FastMCP entry point (replaces src/evie/server.py)
├── config.py              # NEW — Settings dataclass from env vars
├── db/
│   ├── __init__.py
│   ├── client.py          # Dual-mode Supabase client (anon+RLS vs service_role)
│   ├── queries.py         # Placeholder — populated in Phase 3
│   └── models.py          # Pydantic models (migrated from src/evie/models.py)
├── auth/
│   ├── __init__.py
│   ├── resolver.py        # resolve_caller_tier() — dual-path CallerContext resolver
│   ├── hcp_oauth.py       # HCP OAuth flow (migrated from src/evie/oauth.py + server.py login routes)
│   ├── jwt_validator.py   # Placeholder — Tier 3 partner JWT (Phase 5)
│   └── partner_keys.py    # Placeholder — Tier 2 partner API key (Phase 5)
├── tools/
│   ├── __init__.py
│   ├── internal.py        # Placeholder — shared tool internals (Phase 3+)
│   ├── core.py            # Placeholder — v1.0 tools (Phase 3)
│   ├── hcp.py             # Placeholder — v1.5 HCP tools (Phase 10)
│   └── payer.py           # Placeholder — v1.5 payer tools (Phase 11)
├── compliance/
│   ├── __init__.py
│   ├── envelope.py        # Placeholder (Phase 6)
│   ├── badge.py           # Placeholder (Phase 6)
│   ├── comparison_policy.py  # Placeholder (Phase 6)
│   └── audit.py           # Placeholder (Phase 6)
├── rest/
│   ├── __init__.py
│   ├── app.py             # Placeholder (Phase 9)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── trials.py      # Placeholder (Phase 9)
│   │   ├── evidence.py    # Placeholder (Phase 9)
│   │   ├── safety.py      # Placeholder (Phase 9)
│   │   ├── hcp.py         # Placeholder (Phase 9)
│   │   └── payer.py       # Placeholder (Phase 9)
│   ├── openapi_gen.py     # Placeholder (Phase 9)
│   └── auth_middleware.py # Placeholder (Phase 9)
├── transport/
│   ├── __init__.py
│   └── health.py          # /health endpoint
├── Dockerfile             # Updated CMD
├── requirements.txt       # Add python-jose, uvicorn, fastapi
├── railway.toml           # Updated startCommand
└── tests/                 # Updated to match new structure
```

### 1.2 Implement `config.py`

Centralized settings from environment variables (PRD §3.1):

```python
@dataclass
class Settings:
    SUPABASE_URL: str                    # Required
    SUPABASE_ANON_KEY: str               # Required
    SUPABASE_SERVICE_ROLE_KEY: str       # Required (new)
    EVIE_TOKEN_SECRET: str               # Required (new — for HCP OAuth token validation)
    HOST: str = "0.0.0.0"               # MCP_SERVER_HOST
    PORT: int = 8000                     # MCP_SERVER_PORT
    JWT_SECRET: str = ""                 # Phase 5
    SPONSOR_TOKEN_SECRET: str = ""       # Phase 5
    LOG_LEVEL: str = "INFO"
    EVIE_BASE_URL: str = ""              # For OAuth redirect URIs
```

Load from env with validation. Fail fast on missing required vars.

### 1.3 Implement `db/client.py` — Dual-mode Supabase client

Two client modes per PRD §4.0:

```python
def get_hcp_client(supabase_jwt: str) -> Client:
    """HCP OAuth path — anon key + user JWT, RLS enforced."""
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(supabase_jwt)
    return client

def get_service_client() -> Client:
    """Partner path — service_role key, bypasses RLS."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

def get_client(caller: CallerContext) -> Client:
    """Route to correct client based on auth mode."""
    if caller.auth_mode == "hcp_oauth":
        return get_hcp_client(caller.supabase_jwt)
    return get_service_client()
```

### 1.4 Implement `CallerContext` dataclass

In `auth/resolver.py` — the unified caller identity (PRD §5.2):

```python
@dataclass
class CallerContext:
    auth_mode: str          # "hcp_oauth" | "partner_key" | "partner_jwt" | "anonymous"
    max_tier: int           # 1, 2, or 3
    audience_type: str      # "hcp" | "payer" | "patient" | "msl"
    partner_name: str       # Partner identifier or "direct_hcp"
    hcp_user_id: str | None = None
    supabase_jwt: str | None = None
    npi: str | None = None
    sponsor_id: str | None = None
```

Phase 1 only implements the anonymous fallback:
```python
def resolve_caller_tier(request_context=None) -> CallerContext:
    """Phase 1: returns anonymous Tier 1. Full dual resolver in Phase 5."""
    return CallerContext(
        auth_mode="anonymous", max_tier=1,
        audience_type="hcp", partner_name="anonymous"
    )
```

### 1.5 Implement `transport/health.py`

Health check endpoint per PRD §3.2:

```python
async def health_check(request):
    # Test DB connectivity via service_role client
    db_status = await check_db_connection()
    return JSONResponse({
        "status": "ok" if db_status else "degraded",
        "tools": tool_count,
        "db": "connected" if db_status else "disconnected"
    })
```

### 1.6 Implement root `server.py`

Minimal FastMCP entry point (PRD §8.2). Phase 1 registers zero tools — just mounts health check:

```python
mcp = FastMCP(
    name="evie-mcp-server",
    version="2.0.0",
    description="EVIE Evidence Intelligence Engine — governed pharmaceutical evidence for AI platform partners and HCPs"
)

# Phase 1: health check only. Tools registered in Phase 3+.
# mcp.mount health endpoint
```

### 1.7 Migrate HCP OAuth

Move `src/evie/oauth.py` → `auth/hcp_oauth.py` and `src/evie/_state.py` → `auth/_state.py`. Update imports. The OAuth flow stays functionally identical — just relocated.

Move login route HTML from `src/evie/server.py` into `auth/hcp_oauth.py`.

### 1.8 Migrate models

Move `src/evie/models.py` → `db/models.py`. Add new PRD fields as optional for forward compatibility:
- `evidence_badge: str | None`
- `audience_routing: list[str] | None`
- `evidence_hierarchy_level: str | None`
- `dark_data_flag: bool | None`
- `fair_balance_text: str | None`
- `cross_trial_comparison_policy: str | None`
- `render_requirements: dict | None`

### 1.9 Update deployment files

**`requirements.txt`:**
```
fastmcp>=3.1.0
httpx>=0.27.0
supabase>=2.28.0
pydantic>=2.0.0
python-jose>=3.3.0
uvicorn>=0.30.0
fastapi>=0.115.0
```

**`Dockerfile`:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "server.py"]
```

**`railway.toml`:**
```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "python server.py"
healthcheckPath = "/health"
healthcheckTimeout = 10
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**`.env.example`:** Add `SUPABASE_SERVICE_ROLE_KEY`, `EVIE_TOKEN_SECRET`, `JWT_SECRET`, `SPONSOR_TOKEN_SECRET`, `LOG_LEVEL`.

### 1.10 Update tests

- Rename/restructure test imports to match new module paths
- Add `test_config.py` — settings loading and validation
- Add `test_db_client.py` — dual-mode client creation
- Add `test_health.py` — health endpoint response format
- Existing `test_auth.py`, `test_models.py`, `test_oauth.py` — update import paths

### 1.11 Preserve legacy code

Keep `src/evie/` intact during Phase 1 as a reference. Remove after Phase 3 confirms all functionality is migrated. Add to `.gitignore` or mark as deprecated.

### Phase 1 Deliverables Checklist

- [ ] Directory structure created with all `__init__.py` files
- [ ] `config.py` — Settings loads from env, fails on missing required vars
- [ ] `db/client.py` — `get_hcp_client()`, `get_service_client()`, `get_client(caller)`
- [ ] `db/models.py` — Migrated + new PRD fields added
- [ ] `auth/resolver.py` — `CallerContext` dataclass + anonymous-only `resolve_caller_tier()`
- [ ] `auth/hcp_oauth.py` — OAuth provider migrated from `src/evie/oauth.py`
- [ ] `transport/health.py` — `/health` endpoint with DB check
- [ ] `server.py` — FastMCP entry point, mounts health check
- [ ] `requirements.txt` — Updated with new deps
- [ ] `Dockerfile` — Updated `CMD`
- [ ] `railway.toml` — Updated `startCommand`
- [ ] `.env.example` — Updated with all env vars
- [ ] Tests pass for new module structure
- [ ] `python server.py` starts and `/health` returns valid JSON

---

## Phase 2: Schema Evolution
*Details to be added before Phase 2 implementation begins.*

---

## Phase 3: Core Read Tools
*Details to be added before Phase 3 implementation begins.*

---

## Phase 4: Evidence Retrieval
*Details to be added before Phase 4 implementation begins.*

---

## Phase 5: Dual Auth
*Details to be added before Phase 5 implementation begins.*

---

## Phase 6: Compliance Module
*Details to be added before Phase 6 implementation begins.*

---

## Phase 7: Detail + Safety Tools
*Details to be added before Phase 7 implementation begins.*

---

## Phase 8: Claude Connector Integration
*Details to be added before Phase 8 implementation begins.*

---

## Phase 9: REST Wrapper
*Details to be added before Phase 9 implementation begins.*

---

## Phase 10: HCP Tool Suite
*Details to be added before Phase 10 implementation begins.*

---

## Phase 11: Payer Tool Suite
*Details to be added before Phase 11 implementation begins.*

---

## Phase 12: ChatGPT Apps Integration
*Details to be added before Phase 12 implementation begins.*

---

## Phase 13: Hardening
*Details to be added before Phase 13 implementation begins.*
