"""Centralized settings from environment variables (PRD §3.1)."""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """EVIE MCP Server configuration loaded from environment variables."""

    # Required — Supabase project credentials
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # Required — EVIE token validation secret (for HCP OAuth)
    EVIE_TOKEN_SECRET: str = ""

    # Server binding
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Phase 5 — Partner auth
    JWT_SECRET: str = ""
    SPONSOR_TOKEN_SECRET: str = ""

    # Logging
    LOG_LEVEL: str = "INFO"

    # OAuth redirect URIs
    EVIE_BASE_URL: str = ""

    # Required fields that must be present at startup
    _REQUIRED: list[str] = field(
        default_factory=lambda: [
            "SUPABASE_URL",
            "SUPABASE_ANON_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ],
        repr=False,
    )

    def validate(self) -> None:
        """Fail fast on missing required vars."""
        missing = [k for k in self._REQUIRED if not getattr(self, k)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )


def load_settings() -> Settings:
    """Load settings from environment variables."""
    return Settings(
        SUPABASE_URL=os.environ.get("SUPABASE_URL", ""),
        SUPABASE_ANON_KEY=os.environ.get("SUPABASE_ANON_KEY", ""),
        SUPABASE_SERVICE_ROLE_KEY=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        EVIE_TOKEN_SECRET=os.environ.get("EVIE_TOKEN_SECRET", ""),
        HOST=os.environ.get("HOST", "0.0.0.0"),
        PORT=int(os.environ.get("PORT", "8000")),
        JWT_SECRET=os.environ.get("JWT_SECRET", ""),
        SPONSOR_TOKEN_SECRET=os.environ.get("SPONSOR_TOKEN_SECRET", ""),
        LOG_LEVEL=os.environ.get("LOG_LEVEL", "INFO"),
        EVIE_BASE_URL=os.environ.get(
            "EVIE_BASE_URL",
            "https://evie-mcp-server-production.up.railway.app",
        ),
    )


# Singleton — import and use directly
settings = load_settings()
