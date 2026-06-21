"""Tests for scripts/store_credentials.py — the credential import admin script.

These tests cover _do_store(), _resolve_db_url(), and _read_credentials_from_env().
No real database or real secrets are used; CredentialStore is mocked.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.crypto.credentials import generate_key_b64


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_engine_factory(acct_exists: bool = True, save_exc=None, get_result=("KEY", "SECRET")):
    """Build fake SQLAlchemy async engine + session_factory."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    if acct_exists:
        mock_acct = MagicMock()
        mock_acct.account_label = "test"
        mock_acct.venue = "binance"
        mock_acct.trading_mode = "paper"
        mock_session.get = AsyncMock(return_value=mock_acct)
    else:
        mock_session.get = AsyncMock(return_value=None)

    factory = MagicMock(return_value=mock_session)
    return factory


def _make_store(configured: bool = True, save_exc=None, get_result=("KEY", "SECRET")):
    """Return a mock CredentialStore."""
    store = MagicMock()
    store.is_configured = MagicMock(return_value=configured)
    if save_exc:
        store.save = AsyncMock(side_effect=save_exc)
    else:
        store.save = AsyncMock()
    store.get = AsyncMock(return_value=get_result)
    return store


# ── _resolve_db_url ───────────────────────────────────────────────────────────

def test_resolve_db_url_prefers_public_url(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://a:b@pub.host:5432/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@internal.host:5432/db")
    result = _resolve_db_url()
    assert "pub.host" in result


def test_resolve_db_url_falls_back_to_database_url(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@internal.host:5432/db")
    result = _resolve_db_url()
    assert "internal.host" in result


def test_resolve_db_url_rewrites_postgres_scheme(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgres://a:b@host:5432/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = _resolve_db_url()
    assert result.startswith("postgresql+asyncpg://")


def test_resolve_db_url_rewrites_postgresql_without_driver(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://a:b@host:5432/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = _resolve_db_url()
    assert "+asyncpg" in result


def test_resolve_db_url_preserves_asyncpg_scheme(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    url = "postgresql+asyncpg://a:b@host:5432/db"
    monkeypatch.setenv("DATABASE_PUBLIC_URL", url)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _resolve_db_url() == url


def test_resolve_db_url_empty_when_no_vars(monkeypatch):
    from scripts.store_credentials import _resolve_db_url
    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _resolve_db_url() == ""


# ── _do_store — success path ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_store_returns_success_and_safe_message():
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "A" * 64
    api_secret = "S" * 64

    mock_store = _make_store(get_result=(api_key, api_secret))

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "main"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)

    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=mock_store),
    ):
        success, msg = await _do_store(
            account_id, api_key, api_secret,
            "postgresql+asyncpg://a:b@host/db", enc_key,
            verify=True,
        )

    assert success is True
    assert api_key not in msg
    assert api_secret not in msg
    assert "64" in msg
    assert "PASSED" in msg


@pytest.mark.asyncio
async def test_do_store_message_contains_account_id():
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "K" * 32
    api_secret = "S" * 32

    mock_store = _make_store(get_result=(api_key, api_secret))
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "smoke-test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=mock_store),
    ):
        success, msg = await _do_store(
            account_id, api_key, api_secret,
            "postgresql+asyncpg://a:b@host/db", enc_key,
            verify=False,
        )

    assert success is True
    assert str(account_id) in msg


# ── _do_store — error paths ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_store_returns_failure_when_account_not_found():
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.get = AsyncMock(return_value=None)
    factory = MagicMock(return_value=fake_session)

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
    ):
        success, msg = await _do_store(
            account_id, "KEY", "SECRET",
            "postgresql+asyncpg://a:b@host/db", enc_key,
        )

    assert success is False
    assert "not found" in msg
    assert "exit 2" in msg


@pytest.mark.asyncio
async def test_do_store_returns_failure_when_store_not_configured():
    from scripts.store_credentials import _do_store

    enc_key = ""
    account_id = uuid.uuid4()
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)
    unconfigured_store = _make_store(configured=False)

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=unconfigured_store),
    ):
        success, msg = await _do_store(
            account_id, "KEY", "SECRET",
            "postgresql+asyncpg://a:b@host/db", enc_key,
        )

    assert success is False
    assert "exit 1" in msg


@pytest.mark.asyncio
async def test_do_store_returns_failure_on_save_exception():
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)
    bad_store = _make_store(save_exc=RuntimeError("disk full"))

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=bad_store),
    ):
        success, msg = await _do_store(
            account_id, "KEY", "SECRET",
            "postgresql+asyncpg://a:b@host/db", enc_key,
        )

    assert success is False
    assert "exit 3" in msg
    assert "disk full" in msg


@pytest.mark.asyncio
async def test_do_store_returns_verify_failure_on_mismatch():
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "CORRECT_KEY"
    api_secret = "CORRECT_SECRET"
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)
    bad_verify_store = _make_store(get_result=("WRONG_KEY", "WRONG_SECRET"))

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=bad_verify_store),
    ):
        success, msg = await _do_store(
            account_id, api_key, api_secret,
            "postgresql+asyncpg://a:b@host/db", enc_key,
            verify=True,
        )

    assert success is False
    assert "exit 4" in msg
    assert api_key not in msg
    assert api_secret not in msg


# ── Safety: message never contains raw secrets ────────────────────────────────

@pytest.mark.asyncio
async def test_success_message_never_contains_api_key():
    """Even on success, the returned message must not contain the plaintext key."""
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "SUPER_SECRET_KEY_AAABBBCCC"
    api_secret = "SUPER_SECRET_SECRET_DDDEEEFFF"
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)
    good_store = _make_store(get_result=(api_key, api_secret))

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=good_store),
    ):
        success, msg = await _do_store(
            account_id, api_key, api_secret,
            "postgresql+asyncpg://a:b@host/db", enc_key,
            verify=True,
        )

    assert success is True
    assert api_key not in msg
    assert api_secret not in msg
    assert "SUPER_SECRET" not in msg


@pytest.mark.asyncio
async def test_failure_message_never_contains_api_key():
    """Even on failure, the returned message must not contain the plaintext key."""
    from scripts.store_credentials import _do_store

    enc_key = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "EXPOSED_IF_LEAKED_XYZ"
    api_secret = "ALSO_EXPOSED_IF_LEAKED_XYZ"
    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)
    crash_store = _make_store(save_exc=ValueError("some error"))

    with (
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=crash_store),
    ):
        success, msg = await _do_store(
            account_id, api_key, api_secret,
            "postgresql+asyncpg://a:b@host/db", enc_key,
        )

    assert success is False
    assert api_key not in msg
    assert api_secret not in msg


# ── _read_credentials_from_env ────────────────────────────────────────────────

def test_read_credentials_from_env_returns_tuple_when_both_set(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.setenv("BINANCE_API_KEY", "MYKEY123")
    monkeypatch.setenv("BINANCE_API_SECRET", "MYSECRET456")
    result = _read_credentials_from_env()
    assert result is not None
    assert result[0] == "MYKEY123"
    assert result[1] == "MYSECRET456"


def test_read_credentials_from_env_strips_whitespace(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.setenv("BINANCE_API_KEY", "  KEY  ")
    monkeypatch.setenv("BINANCE_API_SECRET", "  SECRET  ")
    result = _read_credentials_from_env()
    assert result is not None
    assert result[0] == "KEY"
    assert result[1] == "SECRET"


def test_read_credentials_from_env_returns_none_when_key_missing(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.setenv("BINANCE_API_SECRET", "SECRET")
    assert _read_credentials_from_env() is None


def test_read_credentials_from_env_returns_none_when_secret_missing(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.setenv("BINANCE_API_KEY", "KEY")
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert _read_credentials_from_env() is None


def test_read_credentials_from_env_returns_none_when_key_empty(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.setenv("BINANCE_API_KEY", "")
    monkeypatch.setenv("BINANCE_API_SECRET", "SECRET")
    assert _read_credentials_from_env() is None


def test_read_credentials_from_env_returns_none_when_secret_empty(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.setenv("BINANCE_API_KEY", "KEY")
    monkeypatch.setenv("BINANCE_API_SECRET", "")
    assert _read_credentials_from_env() is None


def test_read_credentials_from_env_returns_none_when_both_missing(monkeypatch):
    from scripts.store_credentials import _read_credentials_from_env
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert _read_credentials_from_env() is None


# ── --from-env end-to-end through main() ─────────────────────────────────────

@pytest.mark.asyncio
async def test_from_env_mode_stores_credentials_from_env(monkeypatch):
    """main() with --from-env reads credentials from env and calls _do_store."""
    import sys
    from scripts.store_credentials import main

    api_key = "ENVKEY_" + "A" * 57   # 64 chars total
    api_secret = "ENVSECRET_" + "B" * 54

    monkeypatch.setenv("BINANCE_API_KEY", api_key)
    monkeypatch.setenv("BINANCE_API_SECRET", api_secret)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", generate_key_b64())
    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql+asyncpg://a:b@host/db",
    )

    mock_engine = AsyncMock()
    mock_engine.dispose = AsyncMock()
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    mock_acct = MagicMock()
    mock_acct.account_label = "test"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    fake_session.get = AsyncMock(return_value=mock_acct)
    factory = MagicMock(return_value=fake_session)

    # store.get() must return the same values for verify to pass
    good_store = MagicMock()
    good_store.is_configured = MagicMock(return_value=True)
    good_store.save = AsyncMock()
    good_store.get = AsyncMock(return_value=(api_key, api_secret))

    saved_args: list = []

    async def capture_store(acct_id, k, s):
        saved_args.extend([acct_id, k, s])

    good_store.save = AsyncMock(side_effect=capture_store)

    with (
        patch.object(sys, "argv", [
            "store_credentials.py",
            "--account-id", "00000000-0000-0000-0000-000000000003",
            "--from-env",
            "--verify",
        ]),
        patch("scripts.store_credentials.create_async_engine", return_value=mock_engine),
        patch("scripts.store_credentials.async_sessionmaker", return_value=factory),
        patch("scripts.store_credentials.CredentialStore", return_value=good_store),
    ):
        exit_code = await main()

    assert exit_code == 0
    # The credentials passed to store.save() must be the full env-var values
    assert saved_args[1] == api_key
    assert saved_args[2] == api_secret


@pytest.mark.asyncio
async def test_from_env_mode_fails_when_env_vars_missing(monkeypatch):
    """main() --from-env exits 1 when BINANCE_API_KEY/SECRET are absent."""
    import sys
    from scripts.store_credentials import main

    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", generate_key_b64())
    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql+asyncpg://a:b@host/db",
    )

    with patch.object(sys, "argv", [
        "store_credentials.py",
        "--account-id", "00000000-0000-0000-0000-000000000003",
        "--from-env",
    ]):
        exit_code = await main()

    assert exit_code == 1
