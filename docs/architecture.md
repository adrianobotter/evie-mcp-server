# EVIE MCP Server — Architecture

> Evidence Intelligence & Context Infrastructure
> Version 1.0 · March 2026

---

## 1. System Overview

EVIE is a two-sided clinical evidence platform connecting pharmaceutical Medical Affairs teams with verified healthcare professionals (HCPs) through governed, context-safe evidence. The platform is built as three distinct services:

| Service | Purpose |
|---------|---------|
| **Admin App** | Lovable web app. Pharma clients ingest PDFs, structure evidence, assign governance. |
| **Supabase Data Layer** | Shared database backbone. Evidence Objects, Context Envelopes, governance rules, auth. |
| **Evie MCP Server** | Hosted custom MCP tools. HCPs access governed evidence through Claude.ai Connector. |

**Core principle:** The Admin App fills the database. The MCP Server reads from it. Supabase is the contract between them.

```
┌──────────────────┐         ┌──────────────┐         ┌──────────────────┐
│                  │         │              │         │                  │
│    Admin App     │─WRITE──▶│   Supabase   │◀──READ──│  Evie MCP Server │
│   (Lovable/Web)  │         │  Data Layer  │         │   (FastMCP/Py)   │
│                  │         │              │         │                  │
└──────────────────┘         └──────────────┘         └────────┬─────────┘
                                                              │
                                                     Streamable HTTP
                                                              │
                                                     ┌────────▼─────────┐
                                                     │  Claude.ai       │
                                                     │  Connector       │
                                                     │  (HCP interface) │
                                                     └──────────────────┘
```

**This document focuses on the Evie MCP Server.** The Admin App and Supabase schema are covered in their respective workstreams.

### Supabase — Single Shared Instance

Both the Admin App and MCP Server **must** point at the same Supabase project:

| Setting | Value |
|---------|-------|
| **Project ID** | `yjtmpjuxwrggkskdffdp` |
| **URL** | `https://yjtmpjuxwrggkskdffdp.supabase.co` |
| **Admin App** | Uses `SUPABASE_URL` + service role key (bypasses RLS for writes) |
| **MCP Server** | Uses `SUPABASE_URL` + anon key (RLS enforced for HCP reads) |

> If you see two Supabase projects, something is misconfigured. Only one project should exist.

---

## 2. Evie MCP Server — Purpose & Vision

The Evie MCP Server is the **only** component of the system that HCPs interact with. It exposes a small set of carefully designed tools through the Claude.ai Connector. When an HCP activates the Evie Connector and asks a clinical question, Claude calls these tools, receives governed Evidence Objects with Context Envelopes attached, and synthesizes a response.

### Design Philosophy

- **Thin query layer** — its value is in what it _enforces_ before returning data, not in what it computes
- **No PDF/ML dependency** — it does not process PDFs or run ML models
- **Context Envelope guarantee** — every tool response includes population constraints, interpretation guardrails, and a safety statement, enforced at the tool level (not by prompt engineering)
- **Fast and lightweight** — should deploy in under 60 seconds and use minimal RAM

---

## 3. Request Flow

```
┌───────┐    Question     ┌───────────┐   Tool Call    ┌─────────────────┐
│       │────────────────▶│           │───────────────▶│                 │
│  HCP  │                 │ Claude.ai │                │  Evie MCP Server│
│       │◀────────────────│           │◀───────────────│                 │
└───────┘    Response      └───────────┘   {evidence,   └────────┬────────┘
             (synthesized               context_envelope}        │
              by Claude)                                    Query + JWT
                                                                │
                                                        ┌───────▼────────┐
                                                        │                │
                                                        │    Supabase    │
                                                        │  (RLS enforced)│
                                                        │                │
                                                        └────────────────┘
```

### Step-by-Step Data Flow

| Step | Description |
|------|-------------|
| 1 | HCP enables Evie Connector in Claude.ai. Authenticates via OAuth → Supabase creates `hcp_profiles` row. |
| 2 | HCP asks a clinical question in Claude chat. |
| 3 | Claude calls the appropriate MCP tool(s) with the HCP's JWT. |
| 4 | MCP Server validates HCP authentication and `verification_status`. |
| 5 | MCP Server queries Supabase. RLS filters results by tier + published status. |
| 6 | MCP Server attaches Context Envelope to every Evidence Object. |
| 7 | Tool returns `{evidence_object, context_envelope}` pairs. |
| 8 | Claude synthesizes a response for the HCP using the governed evidence. |

---

## 4. MCP Tool Definitions

### `list_trials()`

Returns trials available to the authenticated HCP based on their verification status and `max_tier_access`.

| Field | Value |
|-------|-------|
| **Arguments** | None |
| **Auth required** | Yes — HCP must be verified |
| **Governance filter** | Only trials with `is_published` evidence at or below HCP tier |
| **Response fields** | `trial_id`, `name`, `drug_name`, `indication`, `phase`, `available_object_classes` |
| **Response format** | JSON array — one entry per accessible trial |
| **Latency target** | < 500ms |

### `get_trial_summary(trial_id)`

Returns a structured overview of a specific trial — primary endpoints only, with Context Envelopes attached.

| Field | Value |
|-------|-------|
| **Input** | `trial_id` (uuid) |
| **Auth required** | Yes — trial must be accessible to HCP's tier |
| **Returns** | Trial metadata + primary endpoint Evidence Objects + full Context Envelopes |
| **Excludes** | Subgroup, AE, comparator objects — use dedicated tools |
| **Latency target** | < 1 second |

### `get_evidence(query, trial_id?, object_class?)`

Full-text search across Evidence Objects. The primary retrieval tool.

| Field | Value |
|-------|-------|
| **Input: `query`** | Natural language string — e.g. `"weight loss in patients with BMI > 35"` |
| **Input: `trial_id`** | Optional uuid — scopes search to a specific trial |
| **Input: `object_class`** | Optional — `primary_endpoint` \| `subgroup` \| `adverse_event` \| `comparator` |
| **Auth required** | Yes — results filtered by HCP tier |
| **Search mechanism** | Postgres FTS on stored `search_vector` + tier filter + `is_published` filter |
| **Response** | Array of `{evidence_object, context_envelope}` pairs |
| **Snippet length** | Max 500 chars per result — full object via `get_evidence_detail` |
| **Latency target** | < 2 seconds |

### `get_evidence_detail(evidence_object_id)`

Returns the complete Evidence Object and its full Context Envelope.

| Field | Value |
|-------|-------|
| **Input** | `evidence_object_id` (uuid) |
| **Auth required** | Yes — object must be accessible to HCP's tier |
| **Returns** | Full `evidence_object` row + full `context_envelope` row |
| **Latency target** | < 500ms |

### `get_safety_data(trial_id)`

Returns all adverse event Evidence Objects for a trial. Always includes the mandatory `safety_statement`.

| Field | Value |
|-------|-------|
| **Input** | `trial_id` (uuid) |
| **Auth required** | Yes — must be verified HCP, tier1 minimum |
| **Returns** | All AE objects + context_envelopes, sorted by `incidence_rate` descending |
| **Mandatory fields** | `safety_statement` always present — blocked if null |
| **Latency target** | < 1 second |

---

## 5. Tool Response Contract

Every tool that returns Evidence Objects follows this structure. No exceptions.

```json
{
  "evidence_object": {
    "id": "uuid",
    "trial_id": "uuid",
    "object_class": "primary_endpoint",
    "endpoint_name": "Body weight change from baseline",
    "result_value": -15.2,
    "unit": "%",
    "confidence_interval": [-16.1, -14.3],
    "p_value": 0.0001,
    "time_horizon": "68 weeks",
    "arm": "Semaglutide 2.4mg",
    "tier": "tier1"
  },
  "context_envelope": {
    "population_constraints": "Adults with BMI >= 30 or >= 27 with weight-related comorbidity...",
    "endpoint_definition": "Percentage change in body weight from randomization to week 68...",
    "interpretation_guardrails": "Results apply to the mITT population. Not generalizable to...",
    "safety_statement": "Common adverse events include nausea, vomiting, diarrhea...",
    "source_provenance": {
      "trial": "STEP-4",
      "doi": "10.1056/..."
    }
  }
}
```

The Context Envelope is **never** omitted. If an Evidence Object lacks a complete envelope, it cannot have `is_published = true` and will not be returned by any tool.

---

## 6. Supabase Schema Reference

The MCP Server reads from the following tables. It never writes.

### `trials`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PRIMARY KEY |
| `name` | text | e.g. "STEP-4" |
| `drug_name` | text | INN or brand |
| `indication` | text | Disease state |
| `phase` | text | Phase 2 / Phase 3 / etc. |
| `sponsor_id` | uuid | FK → `sponsors(id)` |
| `status` | text | `draft` \| `active` \| `archived` |

### `evidence_objects`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PRIMARY KEY |
| `trial_id` | uuid | FK → `trials(id)` |
| `object_class` | text | `primary_endpoint` \| `subgroup` \| `adverse_event` \| `comparator` \| `methodological` |
| `endpoint_name` | text | |
| `result_value` | numeric | |
| `unit` | text | |
| `confidence_interval_low` | numeric | |
| `confidence_interval_high` | numeric | |
| `p_value` | numeric | |
| `time_horizon` | text | e.g. "68 weeks" |
| `subgroup_definition` | text | Nullable — only for subgroup objects |
| `arm` | text | Treatment arm label |
| `tier` | text | `tier1` \| `tier2` \| `tier3` \| `tier4` |
| `is_published` | boolean | Default false |
| `search_vector` | tsvector | Generated — FTS index on `endpoint_name`, `subgroup_definition`, `arm` |

### `context_envelopes`

One-to-one with `evidence_objects`. Cannot be null if `is_published = true`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PRIMARY KEY |
| `evidence_object_id` | uuid | FK → `evidence_objects(id)`, UNIQUE |
| `source_provenance` | jsonb | `{trial_name, doi, clinicaltrials_id, publication_date}` |
| `population_constraints` | text | Inclusion/exclusion summary |
| `endpoint_definition` | text | Formal pre-specified definition |
| `subgroup_qualifiers` | text | Nullable |
| `interpretation_guardrails` | text | **Mandatory** |
| `safety_statement` | text | **Mandatory** |
| `methodology_qualifiers` | text | |

### `hcp_profiles`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PK, references `auth.users(id)` |
| `full_name` | text | |
| `specialty` | text | |
| `npi_number` | text | US NPI or equivalent |
| `verification_status` | text | `pending` \| `verified` \| `rejected` |
| `max_tier_access` | text | Default `tier1` — elevated by admin |

### `partner_access_rules`

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PRIMARY KEY |
| `sponsor_id` | uuid | FK → `sponsors(id)` |
| `partner_name` | text | |
| `allowed_tiers` | text[] | e.g. `['tier1', 'tier2']` |
| `applies_to_indications` | text[] | Nullable — null = all indications |

### Full-Text Search Index

```sql
ALTER TABLE evidence_objects ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector('english',
      coalesce(endpoint_name,'') || ' ' ||
      coalesce(subgroup_definition,'') || ' ' ||
      coalesce(arm,'')
    )
  ) STORED;

CREATE INDEX evidence_objects_fts ON evidence_objects USING GIN(search_vector);
```

---

## 7. Authentication & Governance

The MCP Server enforces governance at three layers. Relying on a single layer is insufficient for clinical evidence.

| Layer | Mechanism |
|-------|-----------|
| **Layer 1 — Connector Auth** | HCP authenticates via OAuth when activating the Evie Connector in Claude.ai. JWT token passed on every tool call. |
| **Layer 2 — Supabase RLS** | Row-Level Security policies enforce tier access and published status at the database query level. Even if Layer 1 is bypassed, RLS blocks unauthorized data. |
| **Layer 3 — Tool-level check** | Before returning any result, each tool explicitly verifies `verification_status = 'verified'`. Unverified HCPs receive a structured error, not empty results. |

### Key RLS Policies (MCP Server perspective)

| Policy | Rule |
|--------|------|
| `evidence_objects` SELECT | `is_published = true AND tier <= hcp_profiles.max_tier_access` |
| `context_envelopes` SELECT | Joins through `evidence_objects` — same tier/published rules |
| `hcp_profiles` SELECT | User can only read their own row |
| `source_documents` SELECT | **Never** — HCPs cannot access raw source document output |

---

## 8. Tech Stack

| Layer | Technology |
|-------|------------|
| **MCP Framework** | FastMCP >= 2.9.0 (native middleware, no proxy required) |
| **Language** | Python 3.11 |
| **Database Client** | supabase-py >= 2.0.0 |
| **Auth** | FastMCP OAuth provider — Supabase as identity backend |
| **Hosting** | Railway — single lightweight service, no ML dependencies |
| **Transport** | Streamable HTTP (Claude.ai Connector requirement) |
| **PDF Processing** | **None** — not a dependency of this service |

---

## 9. Claude.ai Connector Configuration

HCPs activate the Evie Connector from Claude.ai Settings → Connectors → Add Custom Connector.

| Field | Value |
|-------|-------|
| **Connector URL** | `https://evie-mcp.railway.app/mcp` |
| **Auth type** | OAuth 2.0 |
| **OAuth provider** | Supabase Auth (Evie tenant) |
| **Scopes** | `evidence:read`, `profile:read` |
| **Server Card** | `/.well-known/mcp.json` — describes available tools and auth requirements |

---

## 10. Build Sequence

The MCP Server is built in these phases:

| Phase | Deliverable |
|-------|-------------|
| 1 | Supabase schema — all tables, RLS, triggers, FTS index |
| 2 | Seed one trial manually (STEP-4) — primary endpoints as Evidence Objects with hand-authored Context Envelopes |
| 3 | MCP Server — five tools reading from seeded data, OAuth auth, hosted on Railway |
| 4 | Validate HCP experience end-to-end in Claude.ai before building Admin App |

Seeding one trial manually in Phase 2 breaks the chicken-and-egg dependency. The MCP Server experience can be validated before the Admin App exists.

---

## 11. Success Metrics

| Metric | Target |
|--------|--------|
| Evidence retrieval latency | < 2 seconds for `get_evidence` |
| Context Envelope attachment rate | **100%** — no evidence returned without envelope |
| HCP Connector activation | < 5 minutes from account creation to first query |
| Governance failure rate | **0%** — no evidence returned above HCP's permitted tier |

---

## 12. Non-Goals

The Evie MCP Server explicitly does **not**:

- Process PDFs or perform document extraction
- Generate clinical summaries or synthesize evidence — Claude does that
- Serve the Admin App — that uses Supabase directly
- Store or cache evidence — all data lives in Supabase
- Interpret label status or make regulatory claims
- Generate medical advice or clinical recommendations

---

## 13. Compliance Posture

- EVIE retrieves and structures evidence. It does not generate medical advice.
- Context Envelopes are mandatory guardrails, not optional metadata.
- Provenance (`source_provenance`) is immutable — set at creation, cannot be edited after publishing.
- All evidence access is logged with HCP identity, timestamp, and query for audit purposes.
- No raw PDF content or source document output is ever exposed to HCPs.
