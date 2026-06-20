"""CredentialStore — save and retrieve encrypted exchange API credentials.

Plaintext keys and secrets are NEVER persisted; only AES-256-GCM ciphertext.
The encryption key comes from the CREDENTIAL_ENCRYPTION_KEY env var.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.crypto.credentials import decrypt, encrypt, get_encryption_key
from shared.db.models.account import ApiCredentialRef
from shared.utils.logging import get_logger

log = get_logger("execution.account.credential_store")

_IV_SEP = "|"


class CredentialStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        encryption_key_b64: str,
    ) -> None:
        self._factory = session_factory
        self._key: bytes | None = None
        if encryption_key_b64:
            try:
                self._key = get_encryption_key(encryption_key_b64)
            except Exception as exc:
                log.error("invalid CREDENTIAL_ENCRYPTION_KEY", exc_info=exc)

    def is_configured(self) -> bool:
        return self._key is not None

    def _require_key(self) -> bytes:
        if self._key is None:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY is not configured — "
                "set it to a base64url-encoded 32-byte value"
            )
        return self._key

    async def save(self, account_id: uuid.UUID, api_key: str, api_secret: str) -> None:
        """Encrypt and upsert credentials. Plaintext is cleared from memory immediately."""
        key = self._require_key()
        enc_k, iv_k = encrypt(api_key, key)
        enc_s, iv_s = encrypt(api_secret, key)
        combined_iv = f"{iv_k}{_IV_SEP}{iv_s}"

        async with self._factory() as session:
            stmt = select(ApiCredentialRef).where(ApiCredentialRef.account_id == account_id)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.encrypted_key = enc_k
                existing.encrypted_secret = enc_s
                existing.iv = combined_iv
            else:
                session.add(ApiCredentialRef(
                    account_id=account_id,
                    credential_type="hmac",
                    encrypted_key=enc_k,
                    encrypted_secret=enc_s,
                    iv=combined_iv,
                ))
            await session.commit()
        log.info("credentials saved", account_id=str(account_id))

    async def get(self, account_id: uuid.UUID) -> tuple[str, str] | None:
        """Return (api_key, api_secret) or None if credentials not stored."""
        key = self._require_key()
        async with self._factory() as session:
            stmt = select(ApiCredentialRef).where(ApiCredentialRef.account_id == account_id)
            ref = (await session.execute(stmt)).scalar_one_or_none()
        if ref is None:
            return None
        try:
            iv_parts = ref.iv.split(_IV_SEP, 1)
            iv_k = iv_parts[0]
            iv_s = iv_parts[1] if len(iv_parts) > 1 else iv_parts[0]
            return decrypt(ref.encrypted_key, iv_k, key), decrypt(ref.encrypted_secret, iv_s, key)
        except Exception as exc:
            log.error("credential decryption failed", account_id=str(account_id), exc_info=exc)
            return None

    async def delete(self, account_id: uuid.UUID) -> None:
        async with self._factory() as session:
            stmt = select(ApiCredentialRef).where(ApiCredentialRef.account_id == account_id)
            ref = (await session.execute(stmt)).scalar_one_or_none()
            if ref:
                await session.delete(ref)
                await session.commit()
        log.info("credentials deleted", account_id=str(account_id))

    async def has_credentials(self, account_id: uuid.UUID) -> bool:
        async with self._factory() as session:
            stmt = select(ApiCredentialRef.id).where(ApiCredentialRef.account_id == account_id)
            return (await session.execute(stmt)).scalar_one_or_none() is not None
