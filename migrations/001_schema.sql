-- EVIE Schema — Phase 1
-- All tables, indexes, full-text search, and constraints.
-- Run against a fresh Supabase project.

-- ─── Sponsors ────────────────────────────────────────────────────────────────

CREATE TABLE sponsors (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    tier_permissions jsonb NOT NULL DEFAULT '["tier1"]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ─── Trials ──────────────────────────────────────────────────────────────────

CREATE TABLE trials (
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

CREATE INDEX trials_sponsor_id ON trials(sponsor_id);
CREATE INDEX trials_status ON trials(status);

-- ─── Evidence Objects ────────────────────────────────────────────────────────

CREATE TABLE evidence_objects (
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

CREATE INDEX evidence_objects_trial_id ON evidence_objects(trial_id);
CREATE INDEX evidence_objects_class ON evidence_objects(object_class);
CREATE INDEX evidence_objects_tier ON evidence_objects(tier);
CREATE INDEX evidence_objects_published ON evidence_objects(is_published);

-- Full-text search vector (generated column)
ALTER TABLE evidence_objects ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(endpoint_name, '') || ' ' ||
            coalesce(subgroup_definition, '') || ' ' ||
            coalesce(arm, '')
        )
    ) STORED;

CREATE INDEX evidence_objects_fts ON evidence_objects USING GIN(search_vector);

-- ─── Context Envelopes ───────────────────────────────────────────────────────

CREATE TABLE context_envelopes (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_object_id       uuid NOT NULL UNIQUE REFERENCES evidence_objects(id),
    source_provenance        jsonb,
    population_constraints   text,
    endpoint_definition      text,
    subgroup_qualifiers      text,
    interpretation_guardrails text NOT NULL,
    safety_statement         text NOT NULL,
    methodology_qualifiers   text,
    generated_at             timestamptz NOT NULL DEFAULT now(),
    generated_by             text NOT NULL DEFAULT 'cae_auto'
);

CREATE INDEX context_envelopes_eo_id ON context_envelopes(evidence_object_id);

-- ─── HCP Profiles ────────────────────────────────────────────────────────────

CREATE TABLE hcp_profiles (
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

CREATE TABLE partner_access_rules (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sponsor_id             uuid NOT NULL REFERENCES sponsors(id),
    partner_name           text NOT NULL,
    allowed_tiers          text[] NOT NULL DEFAULT ARRAY['tier1'],
    applies_to_indications text[]
);

CREATE INDEX partner_access_rules_sponsor ON partner_access_rules(sponsor_id);

-- ─── Source Documents (Admin only — never exposed to HCPs) ───────────────────

CREATE TABLE source_documents (
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

CREATE INDEX source_documents_trial_id ON source_documents(trial_id);

-- ─── Triggers ────────────────────────────────────────────────────────────────

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

CREATE TRIGGER trials_updated_at
    BEFORE UPDATE ON trials
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ─── Tier ordering helper ────────────────────────────────────────────────────
-- Used by RLS policies to compare tiers numerically

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
