"""Tests for transport/health.py — health endpoint response format."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from transport.health import check_db_connection, health_check


class TestCheckDbConnection:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        with patch("transport.health.get_service_client") as mock_get:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "1"}]
            mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = mock_result
            mock_get.return_value = mock_client

            result = await check_db_connection()
            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        with patch("transport.health.get_service_client") as mock_get:
            mock_get.side_effect = Exception("Connection refused")

            result = await check_db_connection()
            assert result is False


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_ok_response(self):
        with patch("transport.health.check_db_connection", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True
            health_check._tool_count = 0

            request = MagicMock()
            response = await health_check(request)
            body = response.body.decode()

            import json
            data = json.loads(body)
            assert data["status"] == "ok"
            assert data["tools"] == 0
            assert data["db"] == "connected"

    @pytest.mark.asyncio
    async def test_degraded_response(self):
        with patch("transport.health.check_db_connection", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = False
            health_check._tool_count = 0

            request = MagicMock()
            response = await health_check(request)

            import json
            data = json.loads(response.body.decode())
            assert data["status"] == "degraded"
            assert data["db"] == "disconnected"
