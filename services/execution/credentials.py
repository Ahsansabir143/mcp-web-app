from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_key(secret_key: str) -> bytes:
    return hashlib.sha256(secret_key.encode()).digest()


def encrypt_credential(plaintext: str, secret_key: str) -> tuple[str, str]:
    """Encrypt plaintext with AES-256-GCM.

    Returns (ciphertext_b64, iv_b64).  Plaintext is never stored.
    """
    key = _derive_key(secret_key)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.b64encode(ct).decode("ascii"), base64.b64encode(iv).decode("ascii")


def decrypt_credential(ciphertext_b64: str, iv_b64: str, secret_key: str) -> str:
    """Decrypt AES-256-GCM ciphertext.

    Raises ValueError when the tag check fails (tampering or wrong key).
    """
    key = _derive_key(secret_key)
    try:
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ciphertext_b64)
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ct, None).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Credential decryption failed: {exc}") from exc
