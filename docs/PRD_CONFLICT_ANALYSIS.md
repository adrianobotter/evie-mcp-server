# PRD vs. Existing Codebase — Conflict Analysis

**Date:** 2026-03-19
**Scope:** Architecture, system design, and code-level conflicts only. New/expanded features are out of scope.

---

## Executive Summary

The PRD describes a fundamentally different system architecture than what currently exists. While they share the same tech stack (Python, FastMCP, Supabase, Railway), the PRD proposes a **machine-to-machine partner API** whereas the current system implements an **individual-user-authenticated HCP tool**. This is not an incremental evolution — it's an architectural pivot that conflicts at the authentication, authorization, database access, data model, response format, and module structure layers.

---

## 1. Authentication Architecture — INCOMPATIBLE

### Current System
- **Individual HCP authentication** via OAuth 2.0 (RFC 8414)
- Custom `SupabaseOAuthProvider` wraps Supabase Auth as an OAuth Authorization Server
- Claude.ai Connector completes full OAuth flow: `/authorize` → `/login` form → email/password → Supabase Auth → auth code → EVIE token
- Every tool call resolves to a **specific human HCP** (`auth.uid()`)
- 3-layer auth: JWT validation → Supabase RLS → HCP profile verification (`verify_hcp()`)

### PRD System
- **Machine-to-machine partner authentication** via API keys and JWTs
- No OAuth flow — no login form, no user registration
- Tier 1: **No auth at all** (anonymous access)
- Tier 2: **Partner API key** in `X-EVIE-Partner-Key` header
- Tier 3: **JWT bearer token** with `credential_type` claims (NPI-verified or sponsor auth token)
- `resolve_caller_tier()` returns a `CallerContext` with `partner_name`, not a user identity

### Conflict
These are **mutually exclusive** auth models. The current OAuth provider, login flow, `hcp_profiles` lookup, `AuthenticatedHCP` dataclass, and the entire `_authenticate()` pipeline in `tools.py` cannot serve the PRD's partner-key model. The PRD's Tier 1 (no auth) is impossible in the current system — every tool call requires a verified HCP.

**Impact:** `server.py`, `oauth.py`, `auth.py`, `tools.py` (all tool functions), `_state.py` — essentially the entire auth stack needs to be replaced or wrapped.

---

## 2. Database Access Pattern — INCOMPATIBLE

### Current System
- Uses `SUPABASE_ANON_KEY` with **per-user JWT** injected via `client.postgrest.auth()`
- **Row-Level Security (RLS)** is the primary access control mechanism
- RLS policies reference `auth.uid()` to look up `hcp_profiles.max_tier_access`
- Tier filtering happens **at the database level** — the application never sees rows the user can't access
- `sponsors` and `partner_access_rules` tables are **blocked by RLS** (`USING (false)`) for HCPs

### PRD System
- Uses `SUPABASE_SERVICE_ROLE_KEY` (admin/bypass key)
- **Application-level filtering** — the server executes parameterized queries with `WHERE eo.tier <= :caller_tier`
- No RLS dependency — service role bypasses all RLS policies
- Must read `sponsors`, `partner_access_rules`, and `audience_query_log` — all currently RLS-blocked or nonexistent

### Conflict
The PRD's `service_role_key` bypasses all RLS policies that the current system relies on for security. Switching means:

1. The `002_rls.sql` policies become dead code (or need reworking for the Admin Platform only)
2. Every query in `db.py` needs explicit `WHERE` clauses for tier filtering, audience routing, and `is_published = true` — currently guaranteed by RLS
3. The `get_client()` function signature and JWT-injection pattern becomes irrelevant
4. Security responsibility shifts entirely to application code — any missed filter is a data leak

**Impact:** `db.py` (complete rewrite), `migrations/002_rls.sql` (potentially vestigial), all query functions.

---

## 3. Database Schema — PARTIAL CONFLICT

### Missing Tables (PRD requires, don't exist)
| Table | PRD Usage |
|-------|-----------|
| `audience_query_log` | Audit logging (INSERT per tool call) — the billing/analytics data source |
| `evidence_hierarchy` | L1→L2→L3 parent-child relationships for `get_evidence_detail` |

### Missing Columns on `evidence_objects`
| Column | PRD Usage |
|--------|-----------|
| `audience_routing` | `text[]` — which audience types can see this evidence (`['hcp', 'payer']`) |
| `evidence_badge` | `text` — `green`/`amber`/`red` quality signal |
| `evidence_hierarchy_level` | `text` — `L1`/`L2`/`L3` |
| `dark_data_flag` | `boolean` — CSR supplement data marker |
| `data_source_type` | `text` — `primary_publication`/`csr_supplement`/`epar`/`rwe`/`heor_output` |
| `result_direction` | `text` — `favors_treatment`/`favors_comparator`/`neutral`/`inconclusive` |
| `comparator` | `text` — comparator arm description |
| `mlr_cleared` | `boolean` — MLR review status |

### Missing Columns on `context_envelopes`
| Column | PRD Usage |
|--------|-----------|
| `fair_balance_text` | Mandatory fair balance disclosure |
| `contraindications` | Contraindication text |
| `render_requirements` | `jsonb` — suppressibility, display time, adjacent content rules |
| `cross_trial_comparison_policy` | Policy text governing cross-trial claims |
| `mlr_review_id` | Link to MLR review record |

### Schema Enum Mismatches
| Field | Current Values | PRD Values |
|-------|---------------|------------|
| `object_class` | `primary_endpoint`, `subgroup`, `adverse_event`, `comparator`, `methodological` | `primary_endpoint`, `secondary_endpoint`, `subgroup`, `adverse_event`, `comparator`, `treatment_withdrawal`, `adherence_persistence`, `heor_output` |
| `tier` | `tier1`–`tier4` (4 tiers) | `tier1`–`tier3` (3 tiers) |

### Existing But Unused by PRD
| Element | Notes |
|---------|-------|
| `hcp_profiles` table | PRD has no concept of individual HCP profiles — auth is partner-level |
| `source_documents` table | PRD doesn't reference this table at all |
| `endpoint_definition` column on `context_envelopes` | PRD doesn't include this field |

**Impact:** `migrations/001_schema.sql` needs significant ALTER TABLE additions. Existing seed data (`003_seed_step4.sql`) may need updating. The tier system change (4→3) affects RLS policies, the `tier_rank()` function, and `hcp_profiles.max_tier_access` CHECK constraint.

---

## 4. Pydantic Models — SIGNIFICANT GAPS

### `EvidenceObject` model (`models.py`)
- **Missing fields:** `evidence_badge`, `audience_routing`, `evidence_hierarchy_level`, `dark_data_flag`, `data_source_type`, `result_direction`, `comparator`, `mlr_cleared`
- **Field mismatch:** `confidence_interval` is `list[float]` in the model but the PRD returns `confidence_interval_low` and `confidence_interval_high` as separate top-level fields in the response
- The `_row_to_evidence_object()` converter assembles CI from two DB columns into a list — the PRD wants them separate

### `ContextEnvelope` model (`models.py`)
- **Missing fields:** `fair_balance_text`, `contraindications`, `render_requirements`, `cross_trial_comparison_policy`, `mlr_review_id`
- **Extra field:** `endpoint_definition` exists in current model but not in PRD
- `SourceProvenance` model has different fields — PRD wants `document_title`, `document_type`, `url_or_doi`; current has `trial_name`, `doi`, `clinicaltrials_id`, `publication_date`

### Missing Models
- `CallerContext` — partner identity + tier + audience_type (replaces `AuthenticatedHCP`)
- No `HCPProfile` equivalent needed in PRD
- No standard response envelope model (`evidence` + `metadata` + `compliance`)

**Impact:** `models.py` needs substantial rework. Every `model_dump()` call in `tools.py` and `db.py` produces the wrong shape.

---

## 5. Response Format — INCOMPATIBLE

### Current System
- Tools return raw JSON arrays of `EvidenceWithEnvelope` objects
- Structure: `{ evidence_object: {...}, context_envelope: {...} }`
- No metadata, no compliance wrapper
- Returned as `json.dumps()` strings directly

### PRD System
- Every tool wraps responses in a standard envelope:
```json
{
  "evidence": [...],
  "metadata": { "tool", "partner", "audience_type", "tier_accessed", "evidence_count", "query_ts" },
  "compliance": { "envelope_enforcement", "badge_suppression_allowed", "cross_trial_policy_injected" }
}
```
- Evidence items are flat objects (not nested `evidence_object` + `context_envelope` pairs)
- Context envelope fields are inlined as a `context_envelope` sub-object within each evidence item
- Trial metadata is inlined as a `trial` sub-object

### Conflict
Every tool function's return value structure changes. Any downstream consumer (Claude.ai Connector, tests) expecting the current format will break.

**Impact:** All tool return statements in `tools.py`, all `model_dump()` serialization, test assertions.

---

## 6. Tool Signatures — BREAKING CHANGES

### `get_evidence`
| Aspect | Current | PRD |
|--------|---------|-----|
| Primary parameter | `query: str` (full-text search, required) | `audience_type: str` (required); `search_query` is optional |
| Audience parameter | None | `audience_type: str` (required) |
| Extra filters | `trial_id`, `object_class` | + `trial_name`, `drug_name`, `indication`, `tier`, `evidence_hierarchy_level`, `subgroup_definition`, `dark_data_only` |

### `get_trial_summary`
| Aspect | Current | PRD |
|--------|---------|-----|
| Identification | `trial_id: str` only | `trial_id` OR `trial_name` (one required) |
| Audience parameter | None | `audience_type: str` (optional) |

### `get_safety_data`
| Aspect | Current | PRD |
|--------|---------|-----|
| Input | `trial_id: str` only | `trial_id` OR `drug_name`; `audience_type` required |
| Patient handling | None | Simplifies output for `audience_type='patient'` |

### `get_evidence_detail`
| Aspect | Current | PRD |
|--------|---------|-----|
| Parameters | `evidence_object_id: str` | + `audience_type`, `include_children` |
| Hierarchy | No hierarchy traversal | Fetches L2/L3 children from `evidence_hierarchy` table |

### `list_trials`
| Aspect | Current | PRD |
|--------|---------|-----|
| Parameters | None | `drug_name`, `indication`, `sponsor` (all optional filters) |
| Response | `TrialSummary` with `available_object_classes` | Trial summaries with `available_tiers`, `evidence_count`, `sponsor_name` |

**Impact:** All tool function signatures change. Claude.ai tool schemas (auto-discovered via MCP `tools/list`) will differ, breaking any existing Connector configurations.

---

## 7. Module Structure — REORGANIZATION REQUIRED

### Current (flat)
```
src/evie/
├── server.py, tools.py, db.py, auth.py, oauth.py, models.py, logging.py, _state.py
```

### PRD (modular with subdirectories)
```
evie-mcp-server/
├── server.py
├── config.py
├── db/ (client.py, queries.py, models.py)
├── auth/ (resolver.py, jwt_validator.py, partner_keys.py)
├── tools/ (internal.py, core.py, hcp.py, payer.py)
├── compliance/ (envelope.py, badge.py, comparison_policy.py, audit.py)
├── rest/ (app.py, routes/, openapi_gen.py, auth_middleware.py)
├── transport/ (health.py)
```

### Conflict
The PRD proposes a completely different package layout. The current single-file modules (`tools.py`, `db.py`) would need to be split into subdirectory packages. Import paths throughout the codebase and test suite change. The `src/evie/` namespace might shift to project root.

Additionally, the PRD's entry point is `python server.py` (root-level), while current is `python -m src.evie.server` (package-level). This affects the Dockerfile CMD and `railway.toml` start command.

**Impact:** All import statements, Dockerfile, `railway.toml`, test imports, `__init__.py` files.

---

## 8. Compliance Layer — DOES NOT EXIST

The PRD describes a dedicated `compliance/` module with 4 sub-modules:

| Module | Current Equivalent | Gap |
|--------|-------------------|-----|
| `envelope.py` — Context Envelope injection logic | Implicit in `db.py` via JOIN | No explicit injection layer; relies on PostgREST embedded selects |
| `badge.py` — Badge enforcement + patient label mapping | **None** | No badge concept in codebase; no patient audience handling |
| `comparison_policy.py` — Cross-trial policy collection | **None** | No cross-trial comparison concept |
| `audit.py` — `audience_query_log` INSERT | Python `logging` module to stdout | No database audit trail; `audience_query_log` table doesn't exist |

The PRD treats these as **non-negotiable invariants** (Section 7). The current system enforces envelopes via RLS + embedded selects but has no badge, audience routing, comparison policy, or audit log infrastructure.

**Impact:** Entirely new module to build. Every tool's post-query processing pipeline changes.

---

## 9. Audience Routing — DOES NOT EXIST

The PRD introduces `audience_type` as a **first-class concept** affecting every query:

- `audience_routing` array on `evidence_objects` determines which audiences can see each piece of evidence
- `audience_type` is a required parameter on most tools
- Patient queries are capped at Tier 1 with lay-language badge mapping
- MSL queries access different evidence than HCP queries

The current system has **no audience concept** — all authenticated HCPs see the same evidence filtered only by tier via RLS. There is no `audience_routing` column, no `audience_type` parameter, and no patient ceiling logic.

**Impact:** Schema migration, every query function, every tool signature, new compliance enforcement logic.

---

## 10. REST Wrapper (Dual Transport) — DOES NOT EXIST

The PRD requires a **FastAPI REST wrapper** running alongside MCP on the same service to support ChatGPT Apps via OpenAPI. The current system only serves MCP over Streamable HTTP.

Key architectural requirement: REST routes must call the **same internal functions** as MCP tools — no business logic duplication. This means refactoring current tool implementations to separate the transport-facing handler from the internal query+compliance pipeline.

The PRD's `tools/internal.py` pattern (shared internal functions called by both MCP handlers and REST routes) does not exist in the current codebase, where tool logic is tightly coupled to FastMCP's `@mcp.tool()` decorator pattern.

**Impact:** New `rest/` module, tool refactoring to extract internal functions, `server.py` needs to mount both MCP and FastAPI apps, new dependency (`fastapi`).

---

## 11. Error Handling — DIFFERENT CONVENTIONS

### Current
- Returns JSON strings with `{ "error": "<code>", "message": "<text>" }` from tool functions
- Auth errors use custom `AuthError` exception with codes like `no_token`, `invalid_token`, `not_verified`
- Database errors classified via `_is_auth_error()` heuristic

### PRD
- Uses **JSON-RPC error codes** (`-32000` through `-32602`)
- Structured as `{ "error": "<message>", "code": "<TIER_INSUFFICIENT|PARTNER_UNAUTHORIZED|...>" }`
- Different error taxonomy: `TIER_INSUFFICIENT`, `PARTNER_UNAUTHORIZED`, `AUTH_INVALID`, `TRIAL_NOT_FOUND`, `SERVICE_UNAVAILABLE`, `INVALID_PARAMS`

**Impact:** Error response format changes, error codes change, `_error_response()` helper needs update, test assertions need update.

---

## 12. Health Check — MINOR CONFLICT

| Aspect | Current | PRD |
|--------|---------|-----|
| Response | `{ "status": "ok", "server": "evie_mcp" }` | `{ "status": "ok", "tools": 5, "db": "connected" }` |
| DB check | None (just returns ok) | Must verify DB connectivity |
| Tool count | Not reported | Must report tool count |

**Impact:** Small change but the health check is Railway's liveness probe — any mismatch breaks deployment monitoring.

---

## Summary: Conflict Severity Matrix

| Area | Severity | Can Evolve Incrementally? |
|------|----------|--------------------------|
| Authentication model | **Critical** | No — fundamentally different paradigm |
| Database access pattern (RLS vs service_role) | **Critical** | No — mutually exclusive approaches |
| Database schema | **High** | Yes — additive migrations possible |
| Pydantic models | **High** | Yes — fields can be added |
| Response format | **High** | Partially — could version the envelope |
| Tool signatures | **High** | No — breaking changes to all 5 tools |
| Module structure | **Medium** | Yes — can refactor incrementally |
| Compliance layer | **Medium** | Yes — new code, no conflicts |
| Audience routing | **Medium** | Yes — new concept, additive |
| REST wrapper | **Low** | Yes — purely additive |
| Error handling | **Low** | Yes — convention change |
| Health check | **Low** | Yes — trivial update |

### Bottom Line

The two **critical** conflicts (auth model and DB access pattern) make it impossible to evolve the current codebase into the PRD's target without a significant architectural rework. The current system is built around **individual HCP authentication with RLS-enforced access control**. The PRD requires **anonymous/partner-level authentication with application-enforced filtering**. These aren't just different implementations — they're different trust models.

The most pragmatic path would be to:
1. Keep the current RLS-based auth as a **parallel path** for the existing Claude Connector HCP flow
2. Add the PRD's partner-key auth as a **second auth resolver** that uses service_role
3. Extract tool internals into shared functions that both auth paths can call
4. Migrate schema additively (new columns, new tables) without breaking existing queries

But this dual-auth approach adds complexity the PRD doesn't account for.
