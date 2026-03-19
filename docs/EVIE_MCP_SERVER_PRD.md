# EVIE MCP Server — Implementation PRD

**Version:** 1.0  
**Date:** March 19, 2026  
**Build Platform:** Python (FastMCP) deployed on Railway  
**Database:** Supabase PostgreSQL (project `yjtmpjuxwrggkskdffdp`) — read from the same DB that the Admin Platform writes to  
**Reference:** EVIE PRD v2.1 (north star), §7 MCP Server Specification, §8 Evidence Schema Architecture  
**Status:** Ready for Implementation

---

## 1. What We're Building

The EVIE MCP Server is **Side B** of EVIE's two-sided architecture. It is a standalone Python service that exposes governed pharmaceutical evidence as queryable MCP tool endpoints. AI platform partners — including Claude (via MCP Connectors), ChatGPT (via Custom GPTs/Actions), OpenEvidence, Epic CDS, and Doximity — connect to this server to ground their AI responses in structured, compliance-wrapped clinical evidence.

Side A (the Lovable Admin Platform) writes evidence into Supabase. Side B (this server) reads from the same database and delivers it via MCP protocol. They share no code — only data.

### 1.1 What This Server Does

1. **Receives** MCP tool calls from AI platform partners via JSON-RPC 2.0 over Streamable HTTP
2. **Validates** the caller's access tier (Tier 1 open, Tier 2 partner agreement, Tier 3 NPI/sponsor token)
3. **Routes** queries by `audience_type` (hcp / payer / patient / msl) to return audience-appropriate evidence
4. **Retrieves** published, enveloped evidence objects from Supabase with structured query filters
5. **Injects** Context Envelope fields (interpretation_guardrails, safety_statement, fair_balance_text, evidence_badge, cross_trial_comparison_policy) into every response
6. **Logs** every query to `audience_query_log` for sponsor analytics

### 1.2 What This Server Does NOT Do

- Write or modify evidence objects (that's the Admin Platform's job)
- Render UI (EVIE is infrastructure — platforms render)
- Execute HEOR models (EVIE hosts structured outputs, not live computation)
- Store or transmit PHI/PII
- Perform AI inference (evidence is pre-structured; no LLM calls at query time)

### 1.3 Who Calls This Server

AI platform partners integrate the EVIE MCP server as a tool provider. The calling AI system sends MCP tool calls and receives structured evidence JSON in response. The server is NOT called by humans directly — it is machine-to-machine.

```
AI Platform Partner (e.g., Claude via MCP Connector, ChatGPT via Custom GPT Actions, OpenEvidence)
  → Their LLM decides to call an EVIE tool
    → MCP tool_call: get_evidence(trial="wegovy", audience_type="hcp", tier="tier2")
      → EVIE MCP Server (this service)
        → Supabase query (evidence_objects + context_envelopes)
        → Structured JSON response with envelope fields injected
      ← Response to AI platform
    ← AI platform grounds its response in EVIE evidence
  ← HCP sees governed, badge-labeled evidence in their clinical tool
```

---

## 2. Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3.11+ | |
| MCP Framework | FastMCP (`mcp[cli]`) | MCP SDK for Python; Streamable HTTP transport |
| Database Client | `supabase-py` or `asyncpg` | Reads from Supabase PostgreSQL |
| HTTP Transport | Streamable HTTP (built into FastMCP) | JSON-RPC 2.0 over HTTP |
| Hosting | Railway | Always-on deployment; custom domain |
| Auth | JWT validation (Tier 3) + partner API key (Tier 2) | No auth for Tier 1 |
| Health Check | `/health` HTTP endpoint | Railway health check |

### 2.1 Dependencies

```
mcp[cli]>=1.0.0
supabase>=2.0.0
httpx>=0.27.0
pydantic>=2.0.0
python-jose>=3.3.0    # JWT validation for Tier 3
uvicorn>=0.30.0       # ASGI server
fastapi>=0.115.0      # REST wrapper for ChatGPT Apps (OpenAPI)
```

---

## 3. Server Configuration

### 3.1 Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `SUPABASE_URL` | Supabase project URL | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | Admin DB access (read-only usage) | Yes |
| `MCP_SERVER_PORT` | HTTP port | Yes (default: 8000) |
| `MCP_SERVER_HOST` | Bind address | Yes (default: 0.0.0.0) |
| `JWT_SECRET` | Shared secret for NPI-verified JWT validation (Tier 3 HCP) | Yes (for v1.5) |
| `SPONSOR_TOKEN_SECRET` | Shared secret for sponsor auth token validation (Tier 3 payer/MSL) | Yes (for v1.5) |
| `LOG_LEVEL` | Logging verbosity | No (default: INFO) |
| `RAILWAY_ENVIRONMENT` | Railway environment identifier | Auto-injected |

### 3.2 Production Deployment

| Property | Value |
|----------|-------|
| Base URL | `evie-mcp-server-production.up.railway.app/mcp` |
| Custom Domain (planned) | `evie-mcp.publicishealth.com/mcp` |
| Health Check | `GET /health` → `{ "status": "ok", "tools": 5, "db": "connected" }` |
| Railway Config | Always-on; auto-deploy from main branch; 512MB RAM minimum |
| Transport | Streamable HTTP — FastMCP `mcp.run(transport="streamable-http")` |

### 3.3 Docker Configuration

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "server.py"]
```

---

## 4. Database Access Pattern

The MCP server is a **read-only consumer** of the Supabase database. It reads from 5 tables and writes to 1 (audit log).

### 4.1 Tables Read

| Table | Usage | Key Queries |
|-------|-------|-------------|
| `evidence_objects` | Core evidence retrieval | Filter by `trial_id`, `object_class`, `tier`, `audience_routing`, `is_published = true`, full-text `search_vector` |
| `context_envelopes` | Governance data injected into every response | JOIN on `evidence_object_id` (1:1) |
| `trials` | Trial metadata for `list_trials` and `get_trial_summary` | Filter by `status = 'active'` |
| `sponsors` | Sponsor metadata | JOIN via `trials.sponsor_id` |
| `partner_access_rules` | Tier validation per partner/indication | Filter by `partner_name` and `applies_to_indications` |
| `evidence_hierarchy` | L1→L2→L3 relationships | Filter by `parent_evidence_id` or `child_evidence_id` |

### 4.2 Table Written

| Table | Usage |
|-------|-------|
| `audience_query_log` | INSERT one row per MCP tool call with `partner_name`, `audience_type`, `tool_called`, `trial_id`, `evidence_objects_returned`, `tier_max_accessed` |

### 4.3 Critical Query: Evidence Retrieval

This is the core query executed by `get_evidence`, `get_evidence_detail`, `get_safety_data`, and most other tools:

```sql
SELECT
  eo.*,
  ce.interpretation_guardrails,
  ce.safety_statement,
  ce.fair_balance_text,
  ce.contraindications,
  ce.render_requirements,
  ce.cross_trial_comparison_policy,
  ce.population_constraints,
  ce.subgroup_qualifiers,
  ce.methodology_qualifiers,
  ce.source_provenance,
  ce.mlr_review_id,
  t.name AS trial_name,
  t.drug_name,
  t.indication,
  t.phase,
  s.name AS sponsor_name
FROM evidence_objects eo
JOIN context_envelopes ce ON ce.evidence_object_id = eo.id
JOIN trials t ON t.id = eo.trial_id
JOIN sponsors s ON s.id = t.sponsor_id
WHERE eo.is_published = true
  AND eo.tier <= :caller_tier           -- tier_rank() comparison
  AND :audience_type = ANY(eo.audience_routing)
  -- Additional filters per tool:
  AND eo.trial_id = :trial_id           -- if specified
  AND eo.object_class = :object_class   -- if specified
  AND eo.subgroup_definition ILIKE :subgroup  -- if specified
  AND eo.search_vector @@ plainto_tsquery('english', :search_query)  -- if specified
ORDER BY eo.evidence_hierarchy_level ASC, eo.created_at DESC;
```

**Invariants enforced by this query:**
- Only `is_published = true` evidence is returned (enveloped by definition — DB trigger guarantees it)
- Tier gating: caller can only see evidence at or below their access tier
- Audience routing: only evidence tagged for the caller's audience type is returned
- Context Envelope fields are ALWAYS joined — no evidence without governance

---

## 5. Authentication & Access Control

### 5.1 Tier Model

| Tier | Access Level | Auth Mechanism | Who |
|------|-------------|----------------|-----|
| Tier 1 | Open | None — any MCP client | Any AI platform |
| Tier 2 | Partner agreement | Partner API key in `X-EVIE-Partner-Key` header or MCP metadata | Registered AI platform partners |
| Tier 3 | NPI-verified or sponsor token | JWT bearer token in `Authorization` header | NPI-verified HCPs (via platform) or sponsor auth token holders |

### 5.2 Auth Flow

```python
def resolve_caller_tier(request_context) -> CallerContext:
    """
    Determine the maximum tier the caller can access.
    Returns CallerContext with partner_name, audience_type, max_tier.
    """
    # 1. Check for Tier 3 JWT (NPI or sponsor token)
    auth_header = request_context.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        claims = validate_jwt(token)  # Raises on invalid
        if claims.get("credential_type") == "npi_verified":
            return CallerContext(
                partner_name=claims["partner_name"],
                audience_type=claims.get("audience_type", "hcp"),
                max_tier=3,
                npi=claims.get("npi")
            )
        elif claims.get("credential_type") == "sponsor_auth_token":
            return CallerContext(
                partner_name=claims["partner_name"],
                audience_type=claims.get("audience_type", "payer"),
                max_tier=3,
                sponsor_id=claims.get("sponsor_id")
            )

    # 2. Check for Tier 2 partner key
    partner_key = request_context.get("x-evie-partner-key")
    if partner_key:
        partner = validate_partner_key(partner_key)  # Looks up partner_access_rules
        if partner:
            return CallerContext(
                partner_name=partner.partner_name,
                audience_type=request_context.get("audience_type", "hcp"),
                max_tier=2
            )

    # 3. Default: Tier 1 (open access)
    return CallerContext(
        partner_name="anonymous",
        audience_type=request_context.get("audience_type", "hcp"),
        max_tier=1
    )
```

### 5.3 Partner Access Validation

For Tier 2+ queries, after resolving caller tier, also check `partner_access_rules`:

```python
def validate_partner_access(caller: CallerContext, trial_id: str) -> bool:
    """
    Check if this partner has permission for this trial's indication at their tier.
    """
    rules = db.query(partner_access_rules).filter(
        partner_name=caller.partner_name,
        sponsor_id=trial.sponsor_id
    )
    if not rules:
        return caller.max_tier <= 1  # No rules = Tier 1 only
    for rule in rules:
        if caller.max_tier <= max(tier_rank(t) for t in rule.allowed_tiers):
            if rule.applies_to_indications is None or trial.indication in rule.applies_to_indications:
                return True
    return False
```

---

## 6. MCP Tool Specifications

### 6.0 Response Envelope (All Tools)

Every tool response wraps evidence in a standard envelope:

```json
{
  "evidence": [ ... ],
  "metadata": {
    "tool": "get_evidence",
    "partner": "openevidence",
    "audience_type": "hcp",
    "tier_accessed": "tier2",
    "evidence_count": 3,
    "query_ts": "2026-03-19T14:22:00Z"
  },
  "compliance": {
    "envelope_enforcement": "all evidence objects include mandatory Context Envelope",
    "badge_suppression_allowed": false,
    "cross_trial_policy_injected": true
  }
}
```

Each evidence item in the `evidence` array includes:

```json
{
  "evidence_id": "uuid",
  "object_class": "primary_endpoint",
  "evidence_hierarchy_level": "L2",
  "evidence_badge": "green",
  "audience_routing": ["hcp", "payer"],
  "endpoint_name": "Body Weight Change",
  "result_value": -14.9,
  "unit": "%",
  "confidence_interval_low": -15.7,
  "confidence_interval_high": -14.2,
  "p_value": 0.0001,
  "time_horizon": "68 weeks",
  "arm": "semaglutide 2.4mg QW + lifestyle intervention",
  "comparator": "placebo + lifestyle intervention",
  "result_direction": "favors_treatment",
  "subgroup_definition": null,
  "dark_data_flag": false,
  "data_source_type": "primary_publication",
  "mlr_cleared": true,
  "trial": {
    "name": "STEP 1",
    "drug_name": "Semaglutide 2.4mg",
    "indication": "Obesity",
    "phase": "Phase III",
    "sponsor": "Novo Nordisk"
  },
  "context_envelope": {
    "interpretation_guardrails": "The -14.9% weight reduction represents...",
    "safety_statement": "Wegovy is contraindicated in patients...",
    "fair_balance_text": "WEGOVY® (semaglutide) injection 2.4 mg is indicated...",
    "contraindications": "Personal or family history of MTC; MEN 2...",
    "population_constraints": "Adults with BMI ≥30 kg/m² or BMI ≥27 kg/m²...",
    "methodology_qualifiers": "Randomized, double-blind, placebo-controlled...",
    "render_requirements": {
      "suppressible": false,
      "min_display_time_ms": null,
      "required_adjacent_content": "Black box warning must be accessible..."
    },
    "cross_trial_comparison_policy": "Direct numeric comparison of STEP 1 weight loss outcomes with SURMOUNT-1...",
    "source_provenance": {
      "document_title": "Once-Weekly Semaglutide in Adults with Overweight or Obesity (STEP 1)",
      "document_type": "publication",
      "url_or_doi": "10.1056/NEJMoa2032183"
    }
  }
}
```

### 6.1 v1.0 Core Tools

---

#### `list_trials`

**Purpose:** List all active trials with basic metadata and available evidence tiers.  
**Auth:** None (Tier 1)  
**Audience:** All

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | No | Filter by drug name (case-insensitive partial match) |
| `indication` | string | No | Filter by indication (case-insensitive partial match) |
| `sponsor` | string | No | Filter by sponsor name |

**Query:**
```sql
SELECT t.id, t.name, t.drug_name, t.indication, t.phase, s.name AS sponsor_name,
  (SELECT array_agg(DISTINCT eo.tier) FROM evidence_objects eo WHERE eo.trial_id = t.id AND eo.is_published = true) AS available_tiers,
  (SELECT count(*) FROM evidence_objects eo WHERE eo.trial_id = t.id AND eo.is_published = true) AS evidence_count
FROM trials t
JOIN sponsors s ON s.id = t.sponsor_id
WHERE t.status = 'active'
ORDER BY t.name;
```

**Response:** Array of trial summaries (no evidence objects, no envelopes).

---

#### `get_trial_summary`

**Purpose:** Summary of a single trial: design, primary endpoint headline (L1), key safety signal, evidence badge.  
**Auth:** None (Tier 1)  
**Audience:** All

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `trial_id` | string | Yes* | Trial UUID |
| `trial_name` | string | Yes* | Trial name (alternative to trial_id) |
| `audience_type` | string | No | `hcp` / `payer` / `patient` (default: `hcp`) |

*One of `trial_id` or `trial_name` required.

**Logic:**
1. Look up trial by ID or name
2. Fetch L1 evidence objects (headline results) WHERE `evidence_hierarchy_level = 'L1'` AND `is_published = true` AND `tier = 'tier1'` AND `audience_type` in `audience_routing`
3. Include one safety highlight (first `adverse_event` object)
4. Return trial metadata + L1 evidence + safety summary + badges

---

#### `get_evidence`

**Purpose:** The primary evidence retrieval tool. Returns evidence objects filtered by trial, class, audience, tier, with full Context Envelopes.  
**Auth:** Tier 1–2 open; Tier 3 requires JWT/token  
**Audience:** All

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `trial_id` | string | No | Trial UUID |
| `trial_name` | string | No | Trial name (alternative) |
| `drug_name` | string | No | Filter by drug name |
| `indication` | string | No | Filter by indication |
| `object_class` | string | No | Filter by object_class enum value |
| `audience_type` | string | Yes | `hcp` / `payer` / `patient` / `msl` |
| `tier` | string | No | Maximum tier to return (default: caller's max_tier) |
| `evidence_hierarchy_level` | string | No | `L1` / `L2` / `L3` (default: all available) |
| `subgroup_definition` | string | No | Filter by subgroup (ILIKE match) |
| `search_query` | string | No | Full-text search across endpoint_name, subgroup, arm |
| `dark_data_only` | boolean | No | If true, only return dark_data_flag=true evidence |

**Logic:**
1. Resolve caller tier via auth
2. Cap `tier` parameter at caller's `max_tier`
3. Execute core evidence query (§4.3) with all applicable filters
4. For `audience_type = 'patient'`: map evidence_badge to plain-language labels (`green` → "strong evidence", `amber` → "moderate evidence", `red` → "limited evidence") and limit to Tier 1 only regardless of auth
5. Log to `audience_query_log`
6. Return evidence array with envelopes

---

#### `get_evidence_detail`

**Purpose:** Retrieve L2/L3 detail for a specific evidence object by ID. Returns the full statistical detail and hierarchy children.  
**Auth:** Tier 2 open; Tier 3 requires JWT/token  
**Audience:** HCP / MSL

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `evidence_id` | string | Yes | Evidence object UUID |
| `audience_type` | string | No | Default: `hcp` |
| `include_children` | boolean | No | If true, also return L2/L3 children from `evidence_hierarchy` table (default: true) |

**Logic:**
1. Resolve caller tier
2. Fetch the specific evidence object + envelope
3. Validate `audience_type` is in the object's `audience_routing`
4. Validate caller tier >= object's tier
5. If `include_children`: query `evidence_hierarchy` WHERE `parent_evidence_id = :evidence_id`, fetch child evidence objects recursively
6. Log + return

---

#### `get_safety_data`

**Purpose:** Retrieve adverse event profile, discontinuation rates, and safety statement for a product.  
**Auth:** Tier 1–2  
**Audience:** All

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `trial_id` | string | No | Trial UUID |
| `drug_name` | string | No | Drug name (alternative) |
| `audience_type` | string | Yes | `hcp` / `payer` / `patient` |

**Logic:**
1. Fetch evidence objects WHERE `object_class = 'adverse_event'` AND `is_published = true` AND `audience_type` in `audience_routing`
2. Include the product's black box warning text (from trial/sponsor metadata or highest-tier envelope `contraindications`)
3. For `audience_type = 'patient'`: simplify to most_common side effects + when_to_call_doctor only
4. Log + return

---

### 6.2 v1.5 HCP Tools

---

#### `compare_products`

**Purpose:** Compare two or more products on a specified endpoint. Injects cross-trial comparison policy.  
**Auth:** Tier 2  
**Audience:** HCP

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `products` | string[] | Yes | Array of drug_name or evie_product_id values (2+ required) |
| `endpoint_name` | string | Yes | Endpoint to compare (e.g., "Body Weight Change") |
| `subgroup_definition` | string | No | Subgroup filter (e.g., "BMI ≥ 35") |
| `audience_type` | string | No | Default: `hcp` |

**Logic:**
1. For each product: query evidence objects matching endpoint_name (+ subgroup if specified)
2. Collect all `cross_trial_comparison_policy` values from the matched envelopes
3. Build comparison response with evidence per product + badges
4. **Inject merged cross-trial policy** as a top-level `comparison_policy` field in the response — this tells the calling AI what comparisons are and are not supported
5. Log + return

**Critical compliance behavior:** The `comparison_policy` field is **not optional** in the response. If any product has a cross-trial policy, it MUST be included. This is the primary mechanism preventing AI platforms from generating unsupported cross-trial claims.

---

#### `get_subgroup_evidence`

**Purpose:** Retrieve subgroup-specific evidence for a defined patient population.  
**Auth:** Tier 2 (published subgroups); Tier 3 (CSR supplemental / dark data)  
**Audience:** HCP / MSL

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name or evie_product_id |
| `subgroup_definition` | string | Yes | Population definition (e.g., "CKD stage 3", "BMI ≥ 35", "sex=female") |
| `endpoint_name` | string | No | Specific endpoint (default: all endpoints for this subgroup) |
| `audience_type` | string | No | Default: `hcp` |

**Logic:**
1. Query `evidence_objects` WHERE `object_class IN ('primary_endpoint', 'secondary_endpoint', 'subgroup')` AND `subgroup_definition ILIKE '%' || :subgroup || '%'`
2. Include `dark_data_flag` in response — callers need to know if this is CSR supplement data
3. If dark data nodes are returned at Tier 3, verify caller has Tier 3 auth
4. Log + return with subgroup_qualifiers from envelope

---

#### `check_stopping_rule`

**Purpose:** Retrieve clinical criteria for treatment discontinuation.  
**Auth:** Tier 2  
**Audience:** HCP

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name or evie_product_id |
| `stopping_rule_type` | string | No | e.g., "weight_loss_threshold", "safety_discontinuation" |

**Logic:**
1. Query evidence objects WHERE `object_class = 'treatment_withdrawal'` for the product
2. Also query any evidence objects tagged with stopping-rule-related endpoint_names (e.g., "< 5% weight loss at week 16")
3. Return with evidence_badge and interpretation_guardrails

---

#### `get_dosing_guidance`

**Purpose:** Retrieve dose escalation schedule, renal adjustment, titration protocols.  
**Auth:** Tier 1–2  
**Audience:** HCP

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name or evie_product_id |
| `patient_characteristic` | string | No | e.g., "renal_impairment", "hepatic_impairment", "elderly" |

**Logic:** Query evidence objects with dosing-related data. This tool may also reference structured dosing data stored in the evidence data store schema's formulation/titration fields.

---

#### `get_adherence_data`

**Purpose:** Retrieve real-world persistence rates (PDC), adherence data, patient-reported outcomes.  
**Auth:** Tier 2  
**Audience:** HCP / Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name or evie_product_id |
| `population` | string | No | e.g., "commercial_insurance", "medicare" |

**Logic:** Query `evidence_objects` WHERE `object_class = 'adherence_persistence'`. Include data_gap_flag information if result_value is null (DQ-tagged gaps are evidence — absence of data is a clinical signal).

---

### 6.3 v1.5 Payer Tools

---

#### `run_eligibility_screener`

**Purpose:** WF_01: Check payer-defined eligibility criteria for a drug/indication.  
**Auth:** Tier 3 (sponsor token)  
**Audience:** Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name |
| `diagnosis_code` | string | Yes | ICD-10 or indication |
| `bmi` | number | No | Patient BMI |
| `comorbidities` | string[] | No | List of comorbidities |

**Logic:** Match input parameters against PA criteria evidence nodes. Return eligibility determination with evidence_badge per criterion.

---

#### `get_pa_criteria`

**Purpose:** WF_02–WF_04: Retrieve PA criteria and step therapy requirements.  
**Auth:** Tier 2–3  
**Audience:** HCP / Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name |
| `plan_type` | string | No | e.g., "commercial", "medicare_part_d", "medicaid" |
| `include_appeal_guidance` | boolean | No | If true, include appeal evidence (WF_04) |

**Logic:** Query evidence objects with PA-related data. Return criteria with evidence_basis, evidence_badge, and documentation requirements.

---

#### `get_budget_impact_summary`

**Purpose:** WF_05–WF_07: Retrieve structured HEOR budget impact analysis output.  
**Auth:** Tier 3 (sponsor token)  
**Audience:** Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name |
| `membership_size` | integer | No | Plan membership for BIA calculation |
| `time_horizon` | string | No | e.g., "1_year", "3_year", "5_year" |

**Logic:** Query `evidence_objects` WHERE `object_class = 'heor_output'`. Return BIA results with model assumptions, key drivers, and evidence_badge (typically `amber`).

---

#### `get_formulary_comparison`

**Purpose:** WF_08–WF_10: Compare formulary tier placement and access restrictions across plans for a product class.  
**Auth:** Tier 2  
**Audience:** Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_class` | string | Yes | e.g., "glp1_ra", "sglt2" |
| `plan_types` | string[] | No | Filter by plan types |
| `endpoint_name` | string | No | For clinical evidence comparison (WF_09) |

**Logic:** Query comparative evidence across products in the drug class. Inject cross_trial_comparison_policy. Return with evidence_badge per comparison point.

---

#### `get_step_therapy_rules`

**Purpose:** WF_11–WF_13: Retrieve step therapy sequencing and documented exceptions.  
**Auth:** Tier 2–3  
**Audience:** Payer

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `drug_name` | string | Yes | Drug name |
| `exception_type` | string | No | Filter for specific exception category |
| `clinical_justification` | string | No | For WF_12 exception review — match against evidence basis |

**Logic:** Return step therapy sequence with evidence_badge per step, exception criteria, and supporting evidence for exception requests.

---

## 7. Compliance Enforcement Layer

These behaviors are **non-negotiable architectural constraints** — not configurable per partner.

### 7.1 Invariant: No Evidence Without Envelope

Every evidence object returned by any tool MUST include its Context Envelope. The database enforces this via the `enforce_envelope_before_publish` trigger (only published + enveloped evidence can have `is_published = true`), and the MCP server enforces it via the JOIN in the core query. There is no code path that returns evidence without an envelope.

### 7.2 Invariant: Badge Always Present

The `evidence_badge` field (`green` / `amber` / `red`) is included in every evidence object in every response. The `render_requirements.suppressible` field is set to `false` for safety_statement and fair_balance_text — this tells platform partners they MUST display these fields. The MCP server transmits this; enforcement at the rendering layer is the platform partner's responsibility.

### 7.3 Invariant: Cross-Trial Policy Injection

When `compare_products` or any multi-product tool returns evidence for multiple products, ALL applicable `cross_trial_comparison_policy` values are collected and injected as a top-level `comparison_policy` field. This field tells the calling AI what cross-trial comparisons are prohibited or require qualification.

### 7.4 Invariant: Audience Filtering

Evidence objects are only returned if the `audience_type` parameter matches a value in the object's `audience_routing` array. A payer query never sees HCP-only evidence. A patient query never sees Tier 2+ evidence.

### 7.5 Invariant: Patient Ceiling

For `audience_type = 'patient'`: regardless of caller auth, the maximum tier is Tier 1. Evidence_badge is mapped to lay-language labels. No dark data is returned. The `escalate_to_physician` signal is returned for queries that exceed plain-language scope.

### 7.6 Invariant: Audit Logging

Every tool call results in an INSERT to `audience_query_log`. No exceptions. This is the data source for sponsor reporting and the billing model.

---

## 8. Server Architecture

### 8.1 Module Structure

```
evie-mcp-server/
├── server.py                    # FastMCP entry point + tool registration
├── config.py                    # Environment variables, constants
├── db/
│   ├── __init__.py
│   ├── client.py                # Supabase client initialization
│   ├── queries.py               # Parameterized SQL queries
│   └── models.py                # Pydantic models for DB rows
├── auth/
│   ├── __init__.py
│   ├── resolver.py              # resolve_caller_tier() — shared by MCP + REST
│   ├── jwt_validator.py         # JWT validation for Tier 3
│   └── partner_keys.py          # Partner API key validation
├── tools/
│   ├── __init__.py
│   ├── internal.py              # Shared internal functions (called by MCP tools + REST routes)
│   ├── core.py                  # v1.0 MCP tools: list_trials, get_trial_summary, get_evidence, get_evidence_detail, get_safety_data
│   ├── hcp.py                   # v1.5 HCP MCP tools: compare_products, get_subgroup_evidence, check_stopping_rule, get_dosing_guidance, get_adherence_data
│   └── payer.py                 # v1.5 Payer MCP tools: run_eligibility_screener, get_pa_criteria, get_budget_impact_summary, get_formulary_comparison, get_step_therapy_rules
├── compliance/
│   ├── __init__.py
│   ├── envelope.py              # Context Envelope injection logic
│   ├── badge.py                 # Badge enforcement + patient label mapping
│   ├── comparison_policy.py     # Cross-trial policy collection + injection
│   └── audit.py                 # audience_query_log INSERT
├── rest/                        # REST wrapper for ChatGPT Apps (OpenAPI)
│   ├── __init__.py
│   ├── app.py                   # FastAPI app mounted alongside MCP
│   ├── routes/
│   │   ├── trials.py            # GET /api/v1/trials, GET /api/v1/trials/:id/summary
│   │   ├── evidence.py          # POST /api/v1/evidence, GET /api/v1/evidence/:id
│   │   ├── safety.py            # POST /api/v1/safety
│   │   ├── hcp.py               # compare, subgroup, stopping, dosing, adherence
│   │   └── payer.py             # eligibility, pa, budget, formulary, step therapy
│   ├── openapi_gen.py           # Auto-generate OpenAPI 3.0 spec from MCP tool schemas
│   └── auth_middleware.py       # API key → CallerContext mapping for REST callers
├── transport/
│   ├── __init__.py
│   └── health.py                # /health endpoint
├── Dockerfile
├── requirements.txt
├── railway.toml
└── README.md
```

### 8.2 Server Entry Point

```python
# server.py
from mcp.server.fastmcp import FastMCP
from tools.core import register_core_tools
from tools.hcp import register_hcp_tools
from tools.payer import register_payer_tools
from transport.health import health_app
from config import settings

mcp = FastMCP(
    name="evie-mcp-server",
    version="1.0.0",
    description="EVIE Evidence Intelligence Engine — governed pharmaceutical evidence for AI platform partners"
)

# Register all tools
register_core_tools(mcp)
register_hcp_tools(mcp)
register_payer_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=settings.HOST, port=settings.PORT)
```

### 8.3 Tool Registration Pattern

```python
# tools/core.py
from mcp.server.fastmcp import FastMCP
from db.queries import query_evidence, query_trials
from auth.resolver import resolve_caller_tier
from compliance.envelope import inject_envelope
from compliance.audit import log_query
from compliance.badge import enforce_badge

def register_core_tools(mcp: FastMCP):

    @mcp.tool()
    async def list_trials(
        drug_name: str | None = None,
        indication: str | None = None,
        sponsor: str | None = None
    ) -> dict:
        """List active clinical trials with drug name, indication, sponsor, and available evidence tiers."""
        trials = await query_trials(drug_name=drug_name, indication=indication, sponsor=sponsor)
        await log_query(partner="anonymous", audience_type="hcp", tool="list_trials", evidence_count=len(trials))
        return {"trials": trials}

    @mcp.tool()
    async def get_evidence(
        audience_type: str,
        trial_id: str | None = None,
        trial_name: str | None = None,
        drug_name: str | None = None,
        indication: str | None = None,
        object_class: str | None = None,
        tier: str | None = None,
        evidence_hierarchy_level: str | None = None,
        subgroup_definition: str | None = None,
        search_query: str | None = None,
        dark_data_only: bool = False
    ) -> dict:
        """Retrieve governed evidence objects by trial, class, audience, and tier. Every result includes its Context Envelope with interpretation guardrails, safety statement, and fair balance text."""
        caller = await resolve_caller_tier()
        effective_tier = min(tier_rank(tier or "tier3"), caller.max_tier)

        # Patient ceiling
        if audience_type == "patient":
            effective_tier = min(effective_tier, 1)

        evidence = await query_evidence(
            audience_type=audience_type,
            max_tier=effective_tier,
            trial_id=trial_id, trial_name=trial_name, drug_name=drug_name,
            indication=indication, object_class=object_class,
            hierarchy_level=evidence_hierarchy_level,
            subgroup=subgroup_definition, search=search_query,
            dark_data_only=dark_data_only
        )

        # Enforce badges + map for patient
        evidence = enforce_badge(evidence, audience_type)

        await log_query(
            partner=caller.partner_name,
            audience_type=audience_type,
            tool="get_evidence",
            trial_id=trial_id,
            evidence_count=len(evidence),
            tier_max=effective_tier
        )

        return {
            "evidence": evidence,
            "metadata": { ... },
            "compliance": { ... }
        }
```

---

## 9. Error Handling

| Scenario | HTTP / MCP Error | Response |
|----------|------------------|----------|
| Tier insufficient | JSON-RPC error -32001 | `{ "error": "Evidence tier requires NPI verification or sponsor auth token.", "code": "TIER_INSUFFICIENT" }` |
| Partner not authorized | JSON-RPC error -32002 | `{ "error": "Partner not authorized for this indication/tier.", "code": "PARTNER_UNAUTHORIZED" }` |
| Invalid JWT | JSON-RPC error -32003 | `{ "error": "Invalid or expired authentication token.", "code": "AUTH_INVALID" }` |
| Trial not found | JSON-RPC error -32004 | `{ "error": "Trial not found.", "code": "TRIAL_NOT_FOUND" }` |
| No evidence matches | Success (empty array) | `{ "evidence": [], "metadata": { "evidence_count": 0 } }` — empty results are NOT errors |
| Database connection failed | JSON-RPC error -32000 | `{ "error": "Service temporarily unavailable.", "code": "SERVICE_UNAVAILABLE" }` |
| Invalid audience_type | JSON-RPC error -32602 | `{ "error": "Invalid audience_type. Must be: hcp, payer, patient, msl", "code": "INVALID_PARAMS" }` |
| Invalid object_class | JSON-RPC error -32602 | `{ "error": "Invalid object_class.", "code": "INVALID_PARAMS" }` |

---

## 10. Testing Strategy

### 10.1 Critical Test Scenarios

| # | Scenario | Expected Result | Priority |
|---|----------|-----------------|----------|
| 1 | `list_trials` — no auth | Returns all active trials with evidence counts | P0 |
| 2 | `get_evidence` — Tier 1, HCP audience | Returns only Tier 1, published, HCP-routed evidence with envelopes | P0 |
| 3 | `get_evidence` — Tier 2 with partner key | Returns Tier 1+2 evidence | P0 |
| 4 | `get_evidence` — Tier 3 without JWT | Returns only Tier 1 evidence (tier falls back to max available) | P0 |
| 5 | `get_evidence` — patient audience | Returns Tier 1 only, badges mapped to lay-language, no dark data | P0 |
| 6 | Context Envelope always present | Every evidence object in every response has envelope fields | P0 |
| 7 | Evidence badge always present | Every evidence object has `evidence_badge` field, never null | P0 |
| 8 | `compare_products` — cross-trial policy injected | Response includes `comparison_policy` from all relevant envelopes | P1 |
| 9 | `get_subgroup_evidence` — dark data at Tier 3 | Dark data (dark_data_flag=true) returned only with Tier 3 auth | P1 |
| 10 | `get_evidence_detail` — hierarchy children | Returns parent + L2/L3 children from evidence_hierarchy | P1 |
| 11 | Audience routing enforcement | HCP-only evidence not returned for payer queries | P1 |
| 12 | `audience_query_log` populated | Every tool call creates a log entry | P1 |
| 13 | Partner access rules respected | Partner with `allowed_tiers = {tier1}` cannot access Tier 2 evidence | P1 |
| 14 | Full-text search | `search_query` parameter matches endpoint_name, subgroup, arm | P2 |
| 15 | Health check | `GET /health` returns status + tool count + DB connected | P2 |

### 10.2 Test Data Requirement

Seed the Supabase database with the Wegovy GLP-1 pilot data (5 evidence objects from the Wegovy reference instance in the Evidence Data Store schema):

1. Primary endpoint: -14.9% weight loss (STEP 1) — green, Tier 1, L2
2. Treatment withdrawal: +6.9% regain (STEP 4) — green, Tier 1, L2
3. Subgroup: -16.0% female (CSR supplement) — amber, Tier 3, L3, **dark_data_flag=true**
4. Adherence/persistence: 12-month persistence (RWE) — amber, Tier 2, L2, DQ-01 data gap
5. HEOR output: $175K/QALY ICER — amber, Tier 2, L2

Each must have a complete Context Envelope.

---

## 11. Deployment & Operations

### 11.1 Railway Configuration

```toml
# railway.toml
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

### 11.2 Monitoring

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Health check status | Railway `/health` | Down > 1 minute |
| MCP query latency (p95) | Application logs | > 2 seconds |
| Error rate (JSON-RPC errors) | Application logs | > 5% of queries |
| `audience_query_log` insert rate | Supabase | 0 inserts over 24h (indicates logging failure) |
| DB connection pool exhaustion | Supabase logs | > 80% pool utilization |

### 11.3 Performance Targets

| Metric | Target |
|--------|--------|
| `list_trials` latency | < 200ms |
| `get_evidence` latency (typical) | < 500ms |
| `get_evidence` latency (full-text search) | < 1s |
| `compare_products` latency (2 products) | < 1s |
| Cold start (Railway) | < 5s |
| Concurrent MCP connections | 50+ |

---

## 12. Platform Partner Integrations

Claude Connector and ChatGPT Apps are the **priority integration targets** — they are the fastest path to validating the MCP server because the EVIE team controls both integrations end-to-end with no enterprise sales cycle.

### 12.1 Claude Connector (Anthropic) — Priority 1

| Property | Value |
|----------|-------|
| Integration Type | MCP Connector — Claude.ai natively supports MCP server connections |
| Transport | Streamable HTTP (native MCP — zero translation layer) |
| Server URL | `evie-mcp-server-production.up.railway.app/mcp` |
| Auth (Tier 1–2) | No auth required — Claude users query Tier 1–2 evidence immediately |
| Auth (Tier 3) | JWT bearer token injected via Claude Connector config |
| Tool Discovery | Automatic via MCP `tools/list` — Claude discovers all EVIE tools at connection time |

**Why Claude Connector is the ideal first integration:**
- Native MCP — the EVIE MCP server IS a Claude Connector endpoint, as-is, with zero adapter code
- Context Envelope fields (`interpretation_guardrails`, `safety_statement`, `fair_balance_text`) land directly in Claude's context window, constraining response generation by design
- Evidence badges and cross-trial comparison policies become part of Claude's reasoning chain — compliance works at the inference layer, not just the rendering layer
- The existing Railway deployment already works as a Claude Connector

**Integration steps:**
1. Register EVIE MCP server URL as a Claude Connector (direct URL or Connectors marketplace)
2. Configure metadata: name "EVIE Evidence Intelligence", description, icon
3. Test all 5 v1.0 core tools via Claude.ai chat
4. Validate Context Envelopes constrain Claude's clinical responses
5. Validate patient ceiling (patient queries → Tier 1 only, lay-language badges)
6. Validate `audience_query_log` entries appear for Claude queries

**Claude Connector test prompts:**
```
"What is the weight loss data for Wegovy?"
→ Should trigger: get_evidence(drug_name="wegovy", audience_type="hcp")
→ Should return: STEP 1 primary endpoint with full envelope

"Compare Wegovy and Mounjaro for weight loss"
→ Should trigger: compare_products(products=["wegovy","mounjaro"], endpoint_name="Body Weight Change")
→ Should return: comparison with cross_trial_comparison_policy injected

"What happens if a patient stops taking Wegovy?"
→ Should trigger: get_evidence(drug_name="wegovy", object_class="treatment_withdrawal", audience_type="hcp")
→ Should return: STEP 4 withdrawal data with interpretation guardrails

"In simple terms, what is Wegovy and what are the side effects?"
→ Should trigger: get_evidence(..., audience_type="patient") + get_safety_data(..., audience_type="patient")
→ Should return: Tier 1 only, lay-language badges, simplified safety profile
```

### 12.2 ChatGPT Apps (OpenAI) — Priority 2

| Property | Value |
|----------|-------|
| Integration Type | Custom GPT with Actions (OpenAPI 3.0 spec) |
| Transport | REST (OpenAPI) via wrapper — until OpenAI ships native MCP support |
| Server URL | `evie-mcp-server-production.up.railway.app/api/v1/` (REST wrapper) |
| Auth | API key in Actions config → maps to EVIE Tier 2 partner key |
| Tool Discovery | Defined in OpenAPI spec uploaded to Custom GPT configuration |

**REST Wrapper Architecture:**

ChatGPT Actions require OpenAPI REST endpoints. The EVIE MCP server needs a thin REST translation layer that maps REST calls to internal MCP tool invocations.

```
ChatGPT Custom GPT
  → POST /api/v1/get_evidence { body: { trial_name, audience_type, ... } }
    → REST Wrapper (FastAPI, same Railway service)
      → Internal MCP tool call: get_evidence(...)
      → Same query pipeline, same compliance enforcement
    ← JSON response (identical to MCP response)
  ← ChatGPT uses response in generation
```

**REST wrapper endpoints** (one per EVIE tool):

| Method | Endpoint | Maps to MCP Tool |
|--------|----------|-----------------|
| GET | `/api/v1/trials` | `list_trials` |
| GET | `/api/v1/trials/:id/summary` | `get_trial_summary` |
| POST | `/api/v1/evidence` | `get_evidence` |
| GET | `/api/v1/evidence/:id` | `get_evidence_detail` |
| POST | `/api/v1/safety` | `get_safety_data` |
| POST | `/api/v1/compare` | `compare_products` |
| POST | `/api/v1/subgroup` | `get_subgroup_evidence` |
| POST | `/api/v1/stopping-rule` | `check_stopping_rule` |
| POST | `/api/v1/dosing` | `get_dosing_guidance` |
| POST | `/api/v1/adherence` | `get_adherence_data` |
| POST | `/api/v1/eligibility` | `run_eligibility_screener` |
| POST | `/api/v1/pa-criteria` | `get_pa_criteria` |
| POST | `/api/v1/budget-impact` | `get_budget_impact_summary` |
| POST | `/api/v1/formulary` | `get_formulary_comparison` |
| POST | `/api/v1/step-therapy` | `get_step_therapy_rules` |
| GET | `/api/v1/openapi.json` | Auto-generated OpenAPI 3.0 spec |

**Custom GPT configuration:**
- Name: "EVIE Clinical Evidence Assistant"
- Description: "Access governed pharmaceutical evidence from clinical trials, EPARs, and CSR supplements. Grounded in sponsor-verified, MLR-compliant data with evidence quality badges (green/amber/red)."
- System prompt: includes default `audience_type=hcp`, instruction to always surface `evidence_badge` and `safety_statement`, instruction to respect `cross_trial_comparison_policy`
- Actions: OpenAPI spec auto-served from `/api/v1/openapi.json`
- Auth: API key (maps to EVIE partner key for Tier 2)

**Integration steps:**
1. Build REST wrapper (FastAPI) as a second transport on the same Railway service
2. Auto-generate OpenAPI 3.0 spec from tool parameter schemas
3. Create Custom GPT in ChatGPT with Actions pointing to `/api/v1/` endpoints
4. Upload OpenAPI spec and configure API key authentication
5. Write system prompt with evidence governance instructions
6. Test all tools via ChatGPT interface
7. Validate envelope fields appear in ChatGPT responses

### 12.3 REST Wrapper Implementation

Add to the server module structure:

```
evie-mcp-server/
├── ...existing modules...
├── rest/
│   ├── __init__.py
│   ├── app.py               # FastAPI app with REST endpoints
│   ├── routes/
│   │   ├── trials.py        # GET /api/v1/trials, GET /api/v1/trials/:id/summary
│   │   ├── evidence.py      # POST /api/v1/evidence, GET /api/v1/evidence/:id
│   │   ├── safety.py        # POST /api/v1/safety
│   │   ├── hcp.py           # compare, subgroup, stopping, dosing, adherence
│   │   └── payer.py         # eligibility, pa, budget, formulary, step therapy
│   ├── openapi_gen.py       # Auto-generate OpenAPI spec from MCP tool schemas
│   └── auth_middleware.py   # API key → CallerContext mapping
```

**Key implementation rule:** The REST wrapper calls the SAME internal functions as the MCP tools. There is ONE query pipeline, ONE compliance layer, ONE audit logger. The wrapper is purely a transport translation — it does not duplicate any business logic.

```python
# rest/app.py
from fastapi import FastAPI, Depends
from tools.core import get_evidence_internal
from auth.resolver import resolve_caller_from_api_key

rest_app = FastAPI(title="EVIE Evidence API", version="1.0.0")

@rest_app.post("/api/v1/evidence")
async def rest_get_evidence(request: EvidenceRequest, caller = Depends(resolve_caller_from_api_key)):
    # Same internal function as MCP get_evidence tool
    return await get_evidence_internal(
        caller=caller,
        audience_type=request.audience_type,
        trial_name=request.trial_name,
        drug_name=request.drug_name,
        object_class=request.object_class,
        # ... all params
    )
```

### 12.4 Platform Partner Tier Matrix

| Partner | Type | Transport | Auth | Default Audience | Tier | Status |
|---------|------|-----------|------|------------------|------|--------|
| **Claude Connector** | MCP native | Streamable HTTP | None (T1–2) / JWT (T3) | hcp | 1–3 | **Alpha-ready** |
| **ChatGPT Apps** | REST wrapper | OpenAPI HTTP | API key (T2) / OAuth (T3) | hcp | 1–3 | **Requires REST wrapper (Phase 10)** |
| OpenEvidence | MCP native | Streamable HTTP | Partner key (T2) / JWT (T3) | hcp | 1–3 | Target: Beta |
| Epic CDS | MCP or FHIR bridge | Streamable HTTP | Partner key + SMART-on-FHIR | hcp | 1–2 | Target: Beta |
| Doximity | MCP native | Streamable HTTP | Partner key (T2) / NPI JWT (T3) | hcp | 1–3 | Target: Beta |
| UpToDate AI | MCP native | Streamable HTTP | Partner key (T2) | hcp | 1–2 | Target: GA |
| Alexa Health | MCP native | Streamable HTTP | None (T1) | patient | 1 | Future |

---

## 13. Build Order

| Phase | What to Build | Success Criteria |
|-------|---------------|------------------|
| **1. Scaffold** | FastMCP server, Supabase client, `/health` endpoint, Docker, Railway deploy | Health check passes on Railway |
| **2. Core Read** | `list_trials`, `get_trial_summary` — read-only, no auth | Tools return data from Supabase |
| **3. Evidence Retrieval** | `get_evidence` with tier filtering, audience routing, envelope injection, badge enforcement | Tier 1 evidence returned with full envelopes |
| **4. Auth** | Partner key validation (Tier 2), JWT validation (Tier 3), patient ceiling | Tier 2/3 access control works |
| **5. Audit** | `audience_query_log` INSERT on every tool call | Log entries appear in Supabase |
| **6. Detail + Safety** | `get_evidence_detail` with hierarchy children, `get_safety_data` | Complete v1.0 tool suite |
| **7. Claude Connector** | Register on Claude.ai; test all 5 v1.0 tools via Claude chat; validate envelopes in Claude responses | Claude users can query EVIE evidence with governance |
| **8. HCP Suite** | `compare_products`, `get_subgroup_evidence`, `check_stopping_rule`, `get_dosing_guidance`, `get_adherence_data` | HCP tools with cross-trial policy injection |
| **9. Payer Suite** | `run_eligibility_screener`, `get_pa_criteria`, `get_budget_impact_summary`, `get_formulary_comparison`, `get_step_therapy_rules` | Payer tools operational |
| **10. ChatGPT REST Wrapper** | FastAPI REST wrapper, OpenAPI spec generation, Custom GPT creation + Actions config | ChatGPT users can query EVIE evidence |
| **11. Harden** | Error handling, input validation, rate limiting, connection pooling, logging | Production-ready |

---

## 14. Acceptance Criteria

The MCP server is complete when:

1. ✅ `GET /health` returns `{ "status": "ok" }` on Railway
2. ✅ All 5 v1.0 core tools return correct evidence from Supabase
3. ✅ Every evidence response includes a Context Envelope — no exceptions
4. ✅ Every evidence response includes `evidence_badge` — no exceptions
5. ✅ Tier 1 queries work without any auth
6. ✅ Tier 2 queries require and validate partner API key
7. ✅ Tier 3 queries require and validate JWT (NPI or sponsor token)
8. ✅ Patient audience queries are capped at Tier 1 with lay-language badges
9. ✅ Audience routing filters evidence correctly (HCP evidence not in payer responses)
10. ✅ `compare_products` injects cross-trial comparison policy from all relevant envelopes
11. ✅ Dark data (dark_data_flag=true) only returned at Tier 3 with proper auth
12. ✅ `audience_query_log` populated for every tool call
13. ✅ MCP query latency < 2 seconds (p95) for `get_evidence`
14. ✅ **Claude Connector:** Claude.ai can connect to EVIE MCP server and call all tools; Context Envelopes appear in Claude's clinical responses; evidence badges referenced in Claude reasoning
15. ✅ **ChatGPT Apps:** Custom GPT can call all EVIE tools via REST wrapper; OpenAPI spec auto-generated; envelope fields appear in ChatGPT responses
16. ✅ REST wrapper (`/api/v1/`) returns identical results to MCP transport for all tools

---

## Appendix: Enum Quick Reference

**audience_type:** `hcp` | `payer` | `patient` | `msl`

**object_class:** `primary_endpoint` | `secondary_endpoint` | `subgroup` | `adverse_event` | `comparator` | `treatment_withdrawal` | `adherence_persistence` | `heor_output`

**evidence_hierarchy_level:** `L1` | `L2` | `L3`

**evidence_badge:** `green` | `amber` | `red`

**tier:** `tier1` | `tier2` | `tier3`

**result_direction:** `favors_treatment` | `favors_comparator` | `neutral` | `inconclusive`

**data_source_type:** `primary_publication` | `csr_supplement` | `epar` | `rwe` | `heor_output`

**patient badge mapping:** `green` → "strong evidence" | `amber` → "moderate evidence" | `red` → "limited evidence"

**JSON-RPC error codes:** `-32000` (service unavailable) | `-32001` (tier insufficient) | `-32002` (partner unauthorized) | `-32003` (auth invalid) | `-32004` (not found) | `-32602` (invalid params)
