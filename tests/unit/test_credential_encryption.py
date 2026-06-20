"""Track A tests — credential encryption and credential store."""
from __future__ import annotations

import base64
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.crypto.credentials import (
    decrypt,
    encrypt,
    generate_key_b64,
    get_encryption_key,
)


# ── Key derivation ────────────────────────────────────────────────────────────


def test_generate_key_b64_is_32_bytes():
    k = generate_key_b64()
    raw = base64.urlsafe_b64decode(k + "==")
    assert len(raw) == 32


def test_get_encryption_key_accepts_padded_base64():
    raw = os.urandom(32)
    b64 = base64.urlsafe_b64encode(raw).decode()
    key = get_encryption_key(b64)
    assert key == raw


def test_get_encryption_key_accepts_unpadded():
    raw = os.urandom(32)
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    key = get_encryption_key(b64)
    assert key == raw


def test_get_encryption_key_rejects_short_key():
    short = base64.urlsafe_b64encode(os.urandom(16)).decode()
    with pytest.raises(ValueError, match="32 bytes"):
        get_encryption_key(short)


def test_get_encryption_key_uses_first_32_bytes_of_longer_key():
    raw = os.urandom(64)
    b64 = base64.urlsafe_b64encode(raw).decode()
    key = get_encryption_key(b64)
    assert key == raw[:32]


# ── Encrypt / decrypt roundtrip ───────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    key = get_encryption_key(generate_key_b64())
    plaintext = "my-super-secret-api-key-1234567890"
    ct, iv = encrypt(plaintext, key)
    result = decrypt(ct, iv, key)
    assert result == plaintext


def test_encrypt_produces_different_ciphertext_each_call():
    key = get_encryption_key(generate_key_b64())
    plaintext = "same-api-key"
    ct1, iv1 = encrypt(plaintext, key)
    ct2, iv2 = encrypt(plaintext, key)
    # Fresh nonce → different ciphertext (even for same plaintext)
    assert ct1 != ct2 or iv1 != iv2


def test_decrypt_raises_on_wrong_key():
    from cryptography.exceptions import InvalidTag
    key1 = get_encryption_key(generate_key_b64())
    key2 = get_encryption_key(generate_key_b64())
    ct, iv = encrypt("secret", key1)
    with pytest.raises(InvalidTag):
        decrypt(ct, iv, key2)


def test_decrypt_raises_on_tampered_ciphertext():
    from cryptography.exceptions import InvalidTag
    key = get_encryption_key(generate_key_b64())
    ct, iv = encrypt("secret", key)
    tampered = ct[:-4] + "AAAA"
    with pytest.raises((InvalidTag, Exception)):
        decrypt(tampered, iv, key)


def test_encrypt_empty_string():
    key = get_encryption_key(generate_key_b64())
    ct, iv = encrypt("", key)
    assert decrypt(ct, iv, key) == ""


def test_encrypt_unicode():
    key = get_encryption_key(generate_key_b64())
    plaintext = "kéy-with-ünïcödé-日本語"
    ct, iv = encrypt(plaintext, key)
    assert decrypt(ct, iv, key) == plaintext


# ── CredentialStore ───────────────────────────────────────────────────────────


def _make_store(key_b64: str | None = None):
    from services.execution.account.credential_store import CredentialStore

    key_b64 = key_b64 or generate_key_b64()
    session_factory = MagicMock()
    return CredentialStore(session_factory, key_b64), session_factory


def test_credential_store_is_configured_when_key_set():
    store, _ = _make_store()
    assert store.is_configured() is True


def test_credential_store_not_configured_when_no_key():
    from services.execution.account.credential_store import CredentialStore
    store = CredentialStore(MagicMock(), "")
    assert store.is_configured() is False


def test_credential_store_require_key_raises_when_unconfigured():
    from services.execution.account.credential_store import CredentialStore
    store = CredentialStore(MagicMock(), "")
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
        store._require_key()


@pytest.mark.asyncio
async def test_credential_store_save_and_get_roundtrip():
    from services.execution.account.credential_store import CredentialStore
    from shared.db.models.account import ApiCredentialRef

    key_b64 = generate_key_b64()
    account_id = uuid.uuid4()
    api_key = "BINANCE_API_KEY_12345"
    api_secret = "BINANCE_SECRET_67890"

    saved_refs = {}

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # save() behavior: scalar_one_or_none → None (new), then add()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    def capture_add(ref):
        saved_refs["ref"] = ref

    mock_session.add = MagicMock(side_effect=capture_add)
    mock_session.commit = AsyncMock()
    mock_session.flush = AsyncMock()
    session_factory = MagicMock(return_value=mock_session)

    store = CredentialStore(session_factory, key_b64)
    await store.save(account_id, api_key, api_secret)

    # Verify nothing is stored as plaintext
    ref = saved_refs["ref"]
    assert api_key not in ref.encrypted_key
    assert api_secret not in ref.encrypted_secret
    assert "|" in ref.iv  # two nonces combined

    # Now verify get() decrypts correctly
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=ref))
    )

    result = await store.get(account_id)
    assert result is not None
    assert result[0] == api_key
    assert result[1] == api_secret


@pytest.mark.asyncio
async def test_credential_store_get_returns_none_when_no_record():
    from services.execution.account.credential_store import CredentialStore

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    session_factory = MagicMock(return_value=mock_session)

    store = CredentialStore(session_factory, generate_key_b64())
    result = await store.get(uuid.uuid4())
    assert result is None
