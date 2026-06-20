"""Track A tests — connectivity checker: response parsing and incident dispatch."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── check_account_connectivity ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connectivity_returns_connected_on_200():
    from services.execution.account.connectivity import check_account_connectivity
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("api_key", "api_secret")

    assert result["status"] == "connected"
    assert result["code"] == 200
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_connectivity_auth_error_on_invalid_key_code():
    from services.execution.account.connectivity import check_account_connectivity

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = MagicMock(return_value={"code": -2014, "msg": "API-key format invalid."})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("bad_key", "secret")

    assert result["status"] == "auth_error"
    assert result["code"] == -2014


@pytest.mark.asyncio
async def test_connectivity_ip_restricted():
    from services.execution.account.connectivity import check_account_connectivity

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json = MagicMock(return_value={"code": -2015, "msg": "This IP is not allowed."})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("key", "secret")

    assert result["status"] == "ip_restricted"
    assert result["code"] == -2015


@pytest.mark.asyncio
async def test_connectivity_timestamp_error():
    from services.execution.account.connectivity import check_account_connectivity

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = MagicMock(return_value={"code": -1021, "msg": "Timestamp for this request is outside."})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("key", "secret")

    assert result["status"] == "timestamp_error"
    assert result["code"] == -1021


@pytest.mark.asyncio
async def test_connectivity_perm_error():
    from services.execution.account.connectivity import check_account_connectivity

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = MagicMock(return_value={"code": -2012, "msg": "Unauth for this action."})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("key", "secret")

    assert result["status"] == "perm_error"


@pytest.mark.asyncio
async def test_connectivity_network_error_on_timeout():
    import httpx
    from services.execution.account.connectivity import check_account_connectivity

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        result = await check_account_connectivity("key", "secret")

    assert result["status"] == "network_error"
    assert result["message"] == "timeout"


@pytest.mark.asyncio
async def test_check_and_persist_logs_incident_on_auth_error():
    from services.execution.account.connectivity import check_and_persist

    account_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    session_factory = MagicMock(return_value=mock_session)

    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = MagicMock(return_value={"code": -2014, "msg": "bad key"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_and_persist(
            account_id=account_id,
            api_key="bad",
            api_secret="bad",
            session_factory=session_factory,
            incident_logger=incident_logger,
        )

    assert result["status"] == "auth_error"
    incident_logger.log_incident.assert_awaited_once()
    call_kwargs = incident_logger.log_incident.call_args.kwargs
    assert call_kwargs["incident_type"] == "auth_key_invalid"
    assert call_kwargs["severity"] == "error"
    assert str(account_id) in call_kwargs["context"]["account_id"]


@pytest.mark.asyncio
async def test_check_and_persist_no_incident_on_connected():
    from services.execution.account.connectivity import check_and_persist

    account_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    session_factory = MagicMock(return_value=mock_session)

    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await check_and_persist(
            account_id=account_id,
            api_key="good",
            api_secret="good",
            session_factory=session_factory,
            incident_logger=incident_logger,
        )

    assert result["status"] == "connected"
    incident_logger.log_incident.assert_not_awaited()
