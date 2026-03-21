"""Health check endpoint (PRD §3.2)."""

from starlette.responses import JSONResponse

from config import settings
from db.client import get_service_client


async def check_db_connection() -> bool:
    """Test DB connectivity via service_role client."""
    if not settings.SUPABASE_SERVICE_ROLE_KEY:
        return False
    try:
        client = get_service_client()
        client.table("trials").select("id").limit(1).execute()
        return True
    except Exception:
        return False


async def health_check(request) -> JSONResponse:
    """Return server health status with DB connectivity and tool count."""
    db_ok = await check_db_connection()
    # tool_count is injected by server.py when mounting
    tool_count = getattr(health_check, "_tool_count", 0)
    return JSONResponse({
        "status": "ok" if db_ok else "degraded",
        "tools": tool_count,
        "db": "connected" if db_ok else "disconnected",
    })
