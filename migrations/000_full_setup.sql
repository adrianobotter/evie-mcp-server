-- ============================================================================
-- EVIE — Full Setup for Supabase project yjtmpjuxwrggkskdffdp
-- ============================================================================
-- Run this ONCE in the Supabase SQL Editor to set up everything:
--   1. Tables, indexes, full-text search, constraints
--   2. Row-Level Security policies
--   3. Auth: auto-profile creation, admin roles, audit log
--   4. Seed data (STEP-4 trial)
--
-- Safe to run on a fresh project. Uses IF NOT EXISTS / OR REPLACE where possible.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: SCHEMA — Tables, indexes, triggers, functions
-- ============================================================================

-- ─── Sponsors ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sponsors (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    tier_permissions jsonb NOT NULL DEFAULT '["tier1"]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ─── Trials ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trials (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    drug_name   text,
    indication  text,
    phase       text,
    sponsor_id  uuid REFERENCES sponsors(id),
    status      text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active', 'archived')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trials_sponsor_id ON trials(sponsor_id);
CREATE INDEX IF NOT EXISTS trials_status ON trials(status);

-- ─── Evidence Objects ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS evidence_objects (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trial_id                 uuid NOT NULL REFERENCES trials(id),
    object_class             text NOT NULL
                             CHECK (object_class IN (
                                 'primary_endpoint', 'subgroup',
                                 'adverse_event', 'comparator', 'methodological'
                             )),
    endpoint_name            text,
    result_value             numeric,
    unit                     text,
    confidence_interval_low  numeric,
    confidence_interval_high numeric,
    p_value                  numeric,
    time_horizon             text,
    subgroup_definition      text,
    arm                      text,
    tier                     text NOT NULL DEFAULT 'tier1'
                             CHECK (tier IN ('tier1', 'tier2', 'tier3', 'tier4')),
    is_published             boolean NOT NULL DEFAULT false,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS evidence_objects_trial_id ON evidence_objects(trial_id);
CREATE INDEX IF NOT EXISTS evidence_objects_class ON evidence_objects(object_class);
CREATE INDEX IF NOT EXISTS evidence_objects_tier ON evidence_objects(tier);
CREATE INDEX IF NOT EXISTS evidence_objects_published ON evidence_objects(is_published);

-- Full-text search vector (generated column)
-- Note: ALTER TABLE ADD COLUMN IF NOT EXISTS does not support GENERATED columns,
-- so we check manually.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'evidence_objects' AND column_name = 'search_vector'
    ) THEN
        ALTER TABLE evidence_objects ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english',
                    coalesce(endpoint_name, '') || ' ' ||
                    coalesce(subgroup_definition, '') || ' ' ||
                    coalesce(arm, '')
                )
            ) STORED;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS evidence_objects_fts ON evidence_objects USING GIN(search_vector);

-- ─── Context Envelopes ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS context_envelopes (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_object_id        uuid NOT NULL UNIQUE REFERENCES evidence_objects(id),
    source_provenance         jsonb,
    population_constraints    text,
    endpoint_definition       text,
    subgroup_qualifiers       text,
    interpretation_guardrails text NOT NULL,
    safety_statement          text NOT NULL,
    methodology_qualifiers    text,
    generated_at              timestamptz NOT NULL DEFAULT now(),
    generated_by              text NOT NULL DEFAULT 'cae_auto'
);

CREATE INDEX IF NOT EXISTS context_envelopes_eo_id ON context_envelopes(evidence_object_id);

-- ─── HCP Profiles ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hcp_profiles (
    id                  uuid PRIMARY KEY REFERENCES auth.users(id),
    full_name           text,
    specialty           text,
    npi_number          text,
    verification_status text NOT NULL DEFAULT 'pending'
                        CHECK (verification_status IN ('pending', 'verified', 'rejected')),
    max_tier_access     text NOT NULL DEFAULT 'tier1'
                        CHECK (max_tier_access IN ('tier1', 'tier2', 'tier3', 'tier4')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ─── Partner Access Rules ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS partner_access_rules (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sponsor_id             uuid NOT NULL REFERENCES sponsors(id),
    partner_name           text NOT NULL,
    allowed_tiers          text[] NOT NULL DEFAULT ARRAY['tier1'],
    applies_to_indications text[]
);

CREATE INDEX IF NOT EXISTS partner_access_rules_sponsor ON partner_access_rules(sponsor_id);

-- ─── Source Documents (Admin only — never exposed to HCPs) ───────────────────

CREATE TABLE IF NOT EXISTS source_documents (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trial_id          uuid NOT NULL REFERENCES trials(id),
    url               text,
    title             text,
    docling_markdown  text,
    docling_tables    jsonb,
    processing_status text NOT NULL DEFAULT 'pending'
                      CHECK (processing_status IN ('pending', 'complete', 'failed')),
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS source_documents_trial_id ON source_documents(trial_id);

-- ─── Verification Audit Log ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS verification_audit_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hcp_id          uuid NOT NULL REFERENCES hcp_profiles(id),
    changed_by      uuid NOT NULL,
    old_status      text NOT NULL,
    new_status      text NOT NULL,
    old_tier        text,
    new_tier        text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS verification_audit_hcp ON verification_audit_log(hcp_id);
CREATE INDEX IF NOT EXISTS verification_audit_time ON verification_audit_log(created_at);

-- ============================================================================
-- PART 2: FUNCTIONS & TRIGGERS
-- ============================================================================

-- Tier ordering helper (used by RLS policies)
CREATE OR REPLACE FUNCTION tier_rank(t text)
RETURNS integer AS $$
BEGIN
    RETURN CASE t
        WHEN 'tier1' THEN 1
        WHEN 'tier2' THEN 2
        WHEN 'tier3' THEN 3
        WHEN 'tier4' THEN 4
        ELSE 0
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Admin role check
CREATE OR REPLACE FUNCTION is_admin()
RETURNS boolean AS $$
BEGIN
    RETURN coalesce(
        (SELECT raw_app_meta_data ->> 'role' FROM auth.users WHERE id = auth.uid()),
        ''
    ) = 'admin';
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- Prevent publishing evidence without a context envelope
CREATE OR REPLACE FUNCTION check_envelope_before_publish()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_published = true AND OLD.is_published IS DISTINCT FROM true THEN
        IF NOT EXISTS (
            SELECT 1 FROM context_envelopes WHERE evidence_object_id = NEW.id
        ) THEN
            RAISE EXCEPTION 'Cannot publish evidence object % — no context envelope exists', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS enforce_envelope_before_publish ON evidence_objects;
CREATE TRIGGER enforce_envelope_before_publish
    BEFORE UPDATE ON evidence_objects
    FOR EACH ROW
    EXECUTE FUNCTION check_envelope_before_publish();

-- Auto-update updated_at on trials
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trials_updated_at ON trials;
CREATE TRIGGER trials_updated_at
    BEFORE UPDATE ON trials
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Auto-create HCP profile on signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.hcp_profiles (id, full_name, verification_status, max_tier_access)
    VALUES (
        NEW.id,
        coalesce(NEW.raw_user_meta_data ->> 'full_name', ''),
        'pending',
        'tier1'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION handle_new_user();

-- Protect admin-managed fields from HCP self-service
CREATE OR REPLACE FUNCTION protect_admin_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT is_admin() THEN
        NEW.verification_status := OLD.verification_status;
        NEW.max_tier_access := OLD.max_tier_access;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS protect_hcp_admin_fields ON hcp_profiles;
CREATE TRIGGER protect_hcp_admin_fields
    BEFORE UPDATE ON hcp_profiles
    FOR EACH ROW
    EXECUTE FUNCTION protect_admin_fields();

-- Log verification status and tier changes
CREATE OR REPLACE FUNCTION log_verification_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.verification_status IS DISTINCT FROM NEW.verification_status
       OR OLD.max_tier_access IS DISTINCT FROM NEW.max_tier_access THEN
        INSERT INTO verification_audit_log (hcp_id, changed_by, old_status, new_status, old_tier, new_tier)
        VALUES (
            NEW.id,
            auth.uid(),
            OLD.verification_status,
            NEW.verification_status,
            OLD.max_tier_access,
            NEW.max_tier_access
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS log_hcp_verification_change ON hcp_profiles;
CREATE TRIGGER log_hcp_verification_change
    AFTER UPDATE ON hcp_profiles
    FOR EACH ROW
    EXECUTE FUNCTION log_verification_change();

-- ============================================================================
-- PART 3: ROW-LEVEL SECURITY
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE sponsors ENABLE ROW LEVEL SECURITY;
ALTER TABLE trials ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_envelopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE hcp_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_access_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE verification_audit_log ENABLE ROW LEVEL SECURITY;

-- Drop existing policies (safe re-run)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT policyname, tablename
        FROM pg_policies
        WHERE schemaname = 'public'
    ) LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', r.policyname, r.tablename);
    END LOOP;
END $$;

-- ─── HCP Profiles ───────────────────────────────────────────────────────────

-- HCPs read own profile
CREATE POLICY hcp_select_own ON hcp_profiles
    FOR SELECT USING (id = auth.uid());

-- HCPs update own profile (name, specialty, NPI — admin fields protected by trigger)
CREATE POLICY hcp_update_own ON hcp_profiles
    FOR UPDATE USING (id = auth.uid()) WITH CHECK (id = auth.uid());

-- Admin full access
CREATE POLICY hcp_admin_select ON hcp_profiles FOR SELECT USING (is_admin());
CREATE POLICY hcp_admin_insert ON hcp_profiles FOR INSERT WITH CHECK (is_admin());
CREATE POLICY hcp_admin_update ON hcp_profiles FOR UPDATE USING (is_admin());
CREATE POLICY hcp_admin_delete ON hcp_profiles FOR DELETE USING (is_admin());

-- ─── Evidence Objects ───────────────────────────────────────────────────────

-- HCPs see published evidence at or below their tier
CREATE POLICY evidence_hcp_select ON evidence_objects
    FOR SELECT
    USING (
        is_published = true
        AND tier_rank(tier) <= tier_rank(
            (SELECT max_tier_access FROM hcp_profiles WHERE id = auth.uid())
        )
    );

-- Admin full access
CREATE POLICY evidence_admin_all ON evidence_objects
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Context Envelopes ──────────────────────────────────────────────────────

-- HCPs see envelopes for evidence they can access
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

-- Admin full access
CREATE POLICY envelopes_admin_all ON context_envelopes
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Trials ─────────────────────────────────────────────────────────────────

-- HCPs see active trials with accessible published evidence
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

-- Admin full access
CREATE POLICY trials_admin_all ON trials
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Sponsors (admin only) ──────────────────────────────────────────────────

CREATE POLICY sponsors_deny_hcp ON sponsors FOR SELECT USING (false);
CREATE POLICY sponsors_admin_all ON sponsors
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Partner Access Rules (admin only) ──────────────────────────────────────

CREATE POLICY partner_rules_deny_hcp ON partner_access_rules FOR SELECT USING (false);
CREATE POLICY partner_rules_admin_all ON partner_access_rules
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Source Documents (admin only) ──────────────────────────────────────────

CREATE POLICY source_docs_deny_hcp ON source_documents FOR SELECT USING (false);
CREATE POLICY source_docs_admin_all ON source_documents
    FOR ALL USING (is_admin()) WITH CHECK (is_admin());

-- ─── Verification Audit Log (admin only) ────────────────────────────────────

CREATE POLICY audit_admin_select ON verification_audit_log
    FOR SELECT USING (is_admin());

-- ============================================================================
-- PART 4: SEED DATA — STEP-4 Trial (Semaglutide)
-- ============================================================================
-- Uses ON CONFLICT DO NOTHING so re-running is safe.

-- Sponsor
INSERT INTO sponsors (id, name, tier_permissions) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Novo Nordisk', '["tier1", "tier2", "tier3"]')
ON CONFLICT (id) DO NOTHING;

-- Trial
INSERT INTO trials (id, name, drug_name, indication, phase, sponsor_id, status) VALUES
    ('b0000000-0000-0000-0000-000000000001',
     'STEP-4',
     'Semaglutide 2.4 mg',
     'Obesity / Weight Management',
     'Phase 3',
     'a0000000-0000-0000-0000-000000000001',
     'active')
ON CONFLICT (id) DO NOTHING;

-- Primary Endpoint: Body weight change
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000001',
     'b0000000-0000-0000-0000-000000000001',
     'primary_endpoint',
     'Percentage change in body weight from baseline',
     -12.6, '%', -13.1, -12.1, 0.0001, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false)
ON CONFLICT (id) DO NOTHING;

-- Primary Endpoint: >=5% weight loss responder
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000002',
     'b0000000-0000-0000-0000-000000000001',
     'primary_endpoint',
     'Proportion achieving >=5% body weight loss',
     79.0, '%', 74.8, 83.2, 0.0001, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false)
ON CONFLICT (id) DO NOTHING;

-- Subgroup: BMI >=35
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon,
    subgroup_definition, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000003',
     'b0000000-0000-0000-0000-000000000001',
     'subgroup',
     'Percentage change in body weight — BMI >=35 subgroup',
     -14.1, '%', -15.2, -13.0, 0.0001, '68 weeks',
     'Baseline BMI >= 35 kg/m2', 'Semaglutide 2.4 mg', 'tier2', false)
ON CONFLICT (id) DO NOTHING;

-- Adverse Events: Nausea, Diarrhea, Vomiting
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000004',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Nausea', 44.2, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false),

    ('c0000000-0000-0000-0000-000000000005',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Diarrhea', 31.5, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false),

    ('c0000000-0000-0000-0000-000000000006',
     'b0000000-0000-0000-0000-000000000001',
     'adverse_event',
     'Vomiting', 24.8, '%', NULL, NULL, NULL, '68 weeks',
     'Semaglutide 2.4 mg', 'tier1', false)
ON CONFLICT (id) DO NOTHING;

-- Comparator: Placebo
INSERT INTO evidence_objects (id, trial_id, object_class, endpoint_name, result_value, unit,
    confidence_interval_low, confidence_interval_high, p_value, time_horizon, arm, tier, is_published)
VALUES
    ('c0000000-0000-0000-0000-000000000007',
     'b0000000-0000-0000-0000-000000000001',
     'comparator',
     'Percentage change in body weight from baseline',
     -2.4, '%', -3.2, -1.6, NULL, '68 weeks',
     'Placebo', 'tier1', false)
ON CONFLICT (id) DO NOTHING;

-- Context Envelopes (use ON CONFLICT on unique evidence_object_id)

INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000001',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults aged >=18 with BMI >=30 kg/m2 or >=27 kg/m2 with at least one weight-related comorbidity. Participants had achieved a >=5% body weight reduction during a 20-week run-in period on semaglutide before randomization.',
     'Percentage change in body weight from randomization (week 20) to week 68, assessed in the modified intention-to-treat population.',
     'Results apply to the mITT population who had already responded to semaglutide during the run-in. Not generalizable to semaglutide-naive patients or those who did not achieve initial weight loss.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea (44.2%), diarrhea (31.5%), vomiting (24.8%), and constipation (17.0%). Most events were mild to moderate in severity. Treatment discontinuation due to adverse events occurred in 2.4% of the semaglutide group.',
     'Double-blind, randomized withdrawal design. Participants receiving semaglutide 2.4 mg during a 20-week run-in were randomized 2:1 to continue semaglutide or switch to placebo for 48 weeks. mITT analysis population.')
ON CONFLICT (evidence_object_id) DO NOTHING;

INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000002',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults aged >=18 with BMI >=30 kg/m2 or >=27 kg/m2 with at least one weight-related comorbidity. Run-in responders only.',
     'Proportion of participants achieving >=5% reduction in body weight from randomization to week 68.',
     'Responder analysis in a pre-selected population of run-in responders. Absolute responder rates may differ in unselected populations.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea (44.2%), diarrhea (31.5%), vomiting (24.8%), and constipation (17.0%). Most events were mild to moderate in severity.',
     'Double-blind, randomized withdrawal design. mITT analysis. Multiple imputation used for missing data.')
ON CONFLICT (evidence_object_id) DO NOTHING;

INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, subgroup_qualifiers, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000003',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Subgroup of participants with baseline BMI >= 35 kg/m2. Further restricted to run-in responders.',
     'Percentage change in body weight from randomization to week 68 in the BMI >=35 subgroup.',
     'Pre-specified subgroup analysis. No multiplicity adjustment applied — interpret as exploratory.',
     'Subgroup result without multiplicity adjustment. Should not be used for definitive efficacy claims in this population. Sample size for subgroup not separately powered.',
     'Common adverse events in the semaglutide group included gastrointestinal events: nausea, diarrhea, vomiting. See full safety data for complete profile.',
     'Double-blind, randomized withdrawal. Subgroup defined by baseline BMI stratification.')
ON CONFLICT (evidence_object_id) DO NOTHING;

INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000004',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of nausea as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only (weeks 20-68). Nausea during the initial run-in period is not captured. Rates may differ in treatment-naive populations.',
     'Nausea was the most common adverse event. Most events were mild to moderate and occurred early in treatment. Gastrointestinal events were the primary reason for treatment discontinuation (2.4% of semaglutide group).',
     'Safety population analysis. Adverse events coded using MedDRA. Severity graded by investigator assessment.'),

    ('c0000000-0000-0000-0000-000000000005',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of diarrhea as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only. Rates in the overall STEP program may vary by trial design and population.',
     'Diarrhea was among the most common gastrointestinal adverse events. Most events were mild to moderate in severity and transient.',
     'Safety population analysis. MedDRA coding.'),

    ('c0000000-0000-0000-0000-000000000006',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Safety population — all randomized participants who received at least one dose of study medication.',
     'Incidence of vomiting as a treatment-emergent adverse event from randomization through week 68.',
     'Incidence reflects the randomized period only. Rates may differ across the STEP program trials.',
     'Vomiting was a common gastrointestinal adverse event. Most events were mild to moderate in severity.',
     'Safety population analysis. MedDRA coding.')
ON CONFLICT (evidence_object_id) DO NOTHING;

INSERT INTO context_envelopes (evidence_object_id, source_provenance, population_constraints,
    endpoint_definition, interpretation_guardrails, safety_statement, methodology_qualifiers)
VALUES
    ('c0000000-0000-0000-0000-000000000007',
     '{"trial_name": "STEP-4", "doi": "10.1001/jama.2021.23619", "clinicaltrials_id": "NCT03548935", "publication_date": "2022-01-11"}'::jsonb,
     'Adults randomized to placebo after 20-week semaglutide run-in. These participants had already lost weight on semaglutide before switching to placebo.',
     'Percentage change in body weight from randomization (week 20) to week 68 in the placebo arm.',
     'Placebo arm participants regained weight after semaglutide withdrawal. The weight regain in placebo reflects discontinuation effect, not placebo treatment of obesity.',
     'Adverse event profile in the placebo group reflected semaglutide withdrawal. Gastrointestinal event rates were lower in placebo than active treatment.',
     'Double-blind placebo comparator arm. Randomized 2:1 (semaglutide:placebo) after 20-week run-in.')
ON CONFLICT (evidence_object_id) DO NOTHING;

-- Publish all evidence (envelopes exist, trigger will validate)
UPDATE evidence_objects SET is_published = true
WHERE trial_id = 'b0000000-0000-0000-0000-000000000001'
  AND is_published = false;

COMMIT;

-- ============================================================================
-- DONE. Your Supabase project now has:
--   ✓ 8 tables (sponsors, trials, evidence_objects, context_envelopes,
--     hcp_profiles, partner_access_rules, source_documents, verification_audit_log)
--   ✓ Full-text search index
--   ✓ Row-Level Security on all tables
--   ✓ Admin role support (is_admin())
--   ✓ Auto-profile creation on signup
--   ✓ HCP self-service with protected admin fields
--   ✓ Verification audit log
--   ✓ STEP-4 seed data (7 evidence objects + 7 context envelopes)
-- ============================================================================
