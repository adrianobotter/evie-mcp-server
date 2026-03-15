-- EVIE Auth Migration — Phase 2
-- Auto-profile creation, admin role, HCP self-service, admin CRUD.
-- Run after 001_schema.sql and 002_rls.sql.

-- ─── Admin role helper ─────────────────────────────────────────────────────
-- Admin users have {"role": "admin"} in auth.users.raw_app_meta_data.
-- Set via Supabase dashboard or:
--   UPDATE auth.users SET raw_app_meta_data =
--     raw_app_meta_data || '{"role": "admin"}'::jsonb
--   WHERE id = '<admin-user-id>';

CREATE OR REPLACE FUNCTION is_admin()
RETURNS boolean AS $$
BEGIN
    RETURN coalesce(
        (SELECT raw_app_meta_data ->> 'role' FROM auth.users WHERE id = auth.uid()),
        ''
    ) = 'admin';
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- ─── Auto-create HCP profile on signup ─────────────────────────────────────
-- When a new user signs up via Supabase Auth, create a pending HCP profile.
-- This ensures every authenticated user has a profile row for RLS to reference.

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

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION handle_new_user();

-- ─── HCP self-service policies ─────────────────────────────────────────────
-- HCPs can update their own name, specialty, and NPI (not verification or tier).

CREATE POLICY hcp_update_own ON hcp_profiles
    FOR UPDATE
    USING (id = auth.uid())
    WITH CHECK (id = auth.uid());

-- Prevent HCPs from changing their own verification_status or max_tier_access.
-- This trigger rejects any attempt to modify admin-managed columns.

CREATE OR REPLACE FUNCTION protect_admin_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT is_admin() THEN
        -- Revert admin-managed fields to their original values
        NEW.verification_status := OLD.verification_status;
        NEW.max_tier_access := OLD.max_tier_access;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER protect_hcp_admin_fields
    BEFORE UPDATE ON hcp_profiles
    FOR EACH ROW
    EXECUTE FUNCTION protect_admin_fields();

-- ─── Admin policies ────────────────────────────────────────────────────────
-- Admins can read, update, and delete any HCP profile.

CREATE POLICY hcp_admin_select ON hcp_profiles
    FOR SELECT
    USING (is_admin());

CREATE POLICY hcp_admin_update ON hcp_profiles
    FOR UPDATE
    USING (is_admin());

CREATE POLICY hcp_admin_delete ON hcp_profiles
    FOR DELETE
    USING (is_admin());

CREATE POLICY hcp_admin_insert ON hcp_profiles
    FOR INSERT
    WITH CHECK (is_admin());

-- ─── Admin policies for other tables ───────────────────────────────────────
-- Admins need full CRUD on sponsors, trials, evidence, envelopes, etc.

-- Sponsors
CREATE POLICY sponsors_admin_all ON sponsors
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- Trials
CREATE POLICY trials_admin_all ON trials
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- Evidence Objects
CREATE POLICY evidence_admin_all ON evidence_objects
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- Context Envelopes
CREATE POLICY envelopes_admin_all ON context_envelopes
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- Partner Access Rules
CREATE POLICY partner_rules_admin_all ON partner_access_rules
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- Source Documents
CREATE POLICY source_docs_admin_all ON source_documents
    FOR ALL
    USING (is_admin())
    WITH CHECK (is_admin());

-- ─── Audit: track verification changes ─────────────────────────────────────

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

CREATE INDEX verification_audit_hcp ON verification_audit_log(hcp_id);
CREATE INDEX verification_audit_time ON verification_audit_log(created_at);

ALTER TABLE verification_audit_log ENABLE ROW LEVEL SECURITY;

-- Only admins can read the audit log
CREATE POLICY audit_admin_select ON verification_audit_log
    FOR SELECT
    USING (is_admin());

-- Log changes to verification_status or max_tier_access

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

CREATE TRIGGER log_hcp_verification_change
    AFTER UPDATE ON hcp_profiles
    FOR EACH ROW
    EXECUTE FUNCTION log_verification_change();
