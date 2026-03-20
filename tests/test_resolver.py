"""Tests for auth/resolver.py — CallerContext and resolve_caller_tier."""

from auth.resolver import CallerContext, resolve_caller_tier


class TestCallerContext:
    def test_anonymous_defaults(self):
        ctx = CallerContext(
            auth_mode="anonymous",
            max_tier=1,
            audience_type="hcp",
            partner_name="anonymous",
        )
        assert ctx.hcp_user_id is None
        assert ctx.supabase_jwt is None
        assert ctx.npi is None
        assert ctx.sponsor_id is None

    def test_hcp_oauth_context(self):
        ctx = CallerContext(
            auth_mode="hcp_oauth",
            max_tier=3,
            audience_type="hcp",
            partner_name="direct_hcp",
            hcp_user_id="user-123",
            supabase_jwt="jwt-abc",
            npi="1234567890",
        )
        assert ctx.max_tier == 3
        assert ctx.supabase_jwt == "jwt-abc"


class TestResolveCallerTier:
    def test_phase1_returns_anonymous(self):
        ctx = resolve_caller_tier()
        assert ctx.auth_mode == "anonymous"
        assert ctx.max_tier == 1
        assert ctx.audience_type == "hcp"
        assert ctx.partner_name == "anonymous"

    def test_ignores_request_context(self):
        ctx = resolve_caller_tier(request_context={"some": "data"})
        assert ctx.auth_mode == "anonymous"
