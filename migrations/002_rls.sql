-- EVIE RLS Policies — Phase 1
-- Enforces governance at the database level.
-- Run after 001_schema.sql.

-- Enable RLS on all tables
ALTER TABLE sponsors ENABLE ROW LEVEL SECURITY;
ALTER TABLE trials ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_envelopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE hcp_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_access_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_documents ENABLE ROW LEVEL SECURITY;

-- ─── HCP Profiles ────────────────────────────────────────────────────────────
-- Users can only read their own profile

CREATE POLICY hcp_select_own ON hcp_profiles
    FOR SELECT
    USING (id = auth.uid());

-- ─── Evidence Objects (HCP read) ─────────────────────────────────────────────
-- HCPs see only published evidence at or below their tier

CREATE POLICY evidence_hcp_select ON evidence_objects
    FOR SELECT
    USING (
        is_published = true
        AND tier_rank(tier) <= tier_rank(
            (SELECT max_tier_access FROM hcp_profiles WHERE id = auth.uid())
        )
    );

-- ─── Context Envelopes (HCP read) ───────────────────────────────────────────
-- Follows evidence_objects access — if you can see the evidence, you see the envelope

CREATE POLICY envelope_hcp_select ON context_envelopes
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM evidence_objects eo
            WHERE eo.id = context_envelopes.evidence_object_id
              AND eo.is_published = true
              AND tier_rank(eo.tier) <= tier_rank(
                  (SELECT max_tier_access FROM hcp_profiles WHERE id = auth.uid())
              )
        )
    );

-- ─── Trials (HCP read) ──────────────────────────────────────────────────────
-- HCPs can see trials that have at least one published evidence object they can access

CREATE POLICY trials_hcp_select ON trials
    FOR SELECT
    USING (
        status = 'active'
        AND EXISTS (
            SELECT 1 FROM evidence_objects eo
            WHERE eo.trial_id = trials.id
              AND eo.is_published = true
              AND tier_rank(eo.tier) <= tier_rank(
                  (SELECT max_tier_access FROM hcp_profiles WHERE id = auth.uid())
              )
        )
    );

-- ─── Sponsors (no HCP access) ───────────────────────────────────────────────

CREATE POLICY sponsors_deny_hcp ON sponsors
    FOR SELECT
    USING (false);

-- ─── Partner Access Rules (no HCP access) ────────────────────────────────────

CREATE POLICY partner_rules_deny_hcp ON partner_access_rules
    FOR SELECT
    USING (false);

-- ─── Source Documents (never exposed to HCPs) ───────────────────────────────

CREATE POLICY source_docs_deny_hcp ON source_documents
    FOR SELECT
    USING (false);

-- ─── Service role bypass ─────────────────────────────────────────────────────
-- Note: Supabase service_role key bypasses RLS by default.
-- The Admin App uses service_role. The MCP Server uses anon key + RLS.
