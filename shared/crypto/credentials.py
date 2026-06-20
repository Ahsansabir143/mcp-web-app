"""AES-256-GCM credential encryption.

Usage:
    key = get_encryption_key(os.environ["CREDENTIAL_ENCRYPTION_KEY"])
    enc, iv = encrypt("my-secret", key)
    plain = decrypt(enc, iv, key)

Key format: base64url-encoded 32-byte random value.
Generate with: python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_encryption_key(key_b64: str) -> bytes:
    """Decode a base64url or standard-base64 encoded key (must be >= 32 bytes)."""
    padding = "=" * (4 - len(key_b64) % 4) if len(key_b64) % 4 else ""
    raw = base64.urlsafe_b64decode(key_b64 + padding)
    if len(raw) < 32:
        raise ValueError(f"Encryption key must be at least 32 bytes, got {len(raw)}")
    return raw[:32]


def generate_key_b64() -> str:
    """Generate a new random 32-byte key as base64url string."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def encrypt(plaintext: str, key: bytes) -> tuple[str, str]:
    """Encrypt plaintext with AES-256-GCM.

    Returns (ciphertext_b64, nonce_b64). Store both; a fresh 12-byte nonce is
    generated on every call — never reuse a nonce with the same key.
    """
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return (
        base64.b64encode(ciphertext).decode("ascii"),
        base64.b64encode(nonce).decode("ascii"),
    )


def decrypt(ciphertext_b64: str, nonce_b64: str, key: bytes) -> str:
    """Decrypt AES-256-GCM ciphertext. Raises cryptography.exceptions.InvalidTag on tampering."""
    ciphertext = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
