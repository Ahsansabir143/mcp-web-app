"""One-shot admin script: import BINANCE_API_KEY / BINANCE_API_SECRET from env into
the encrypted DB credential store, then clear references from process memory.

Purpose: Railway variables are a convenient secure transport, but the authoritative
credential store is the AES-256-GCM encrypted api_credentials_ref table.  Run this
once to import, then delete BINANCE_API_KEY and BINANCE_API_SECRET from Railway vars
so the raw secrets no longer exist in env at runtime.

Required env vars (provided automatically by `railway run --service execution`):
    BINANCE_API_KEY             raw Binance API key (read-only key recommended)
    BINANCE_API_SECRET          raw Binance API secret
    DEFAULT_ACCOUNT_ID          UUID of the target ExchangeAccount row
    CREDENTIAL_ENCRYPTION_KEY   base64url-encoded 32-byte AES key
    DATABASE_URL                asyncpg connection string

Exit codes:
    0   credentials saved and verify passed
    1   configuration error (missing env var, bad UUID, bad enc key)
    2   account not found in DB
    3   DB / encryption error
    4   verify failed (decrypt mismatch)
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid


async def main() -> int:
    # ── Read env vars (never print their values) ──────────────────────────────
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    account_id_str = os.environ.get("DEFAULT_ACCOUNT_ID", "").strip()
    enc_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    db_url = os.environ.get("DATABASE_URL", "")

    missing = [n for n, v in [
        ("BINANCE_API_KEY", api_key),
        ("BINANCE_API_SECRET", api_secret),
        ("DEFAULT_ACCOUNT_ID", account_id_str),
        ("CREDENTIAL_ENCRYPTION_KEY", enc_key),
        ("DATABASE_URL", db_url),
    ] if not v]
    if missing:
        print(f"ERROR: missing env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        account_uuid = uuid.UUID(account_id_str)
    except ValueError:
        print(f"ERROR: DEFAULT_ACCOUNT_ID is not a valid UUID: {account_id_str!r}", file=sys.stderr)
        return 1

    # ── Import heavy deps after validation ────────────────────────────────────
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from services.execution.account.credential_store import CredentialStore
    from shared.db.models.account import ExchangeAccount

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # ── Verify account exists ─────────────────────────────────────────────────
    async with factory() as session:
        acct = await session.get(ExchangeAccount, account_uuid)
        if acct is None:
            print(f"ERROR: ExchangeAccount {account_uuid} not found in DB", file=sys.stderr)
            await engine.dispose()
            return 2
        print(f"Account found: label={acct.account_label!r} venue={acct.venue!r} mode={acct.trading_mode!r}")

    # ── Initialise credential store ───────────────────────────────────────────
    store = CredentialStore(factory, enc_key)
    if not store.is_configured():
        print("ERROR: CredentialStore not configured — check CREDENTIAL_ENCRYPTION_KEY", file=sys.stderr)
        await engine.dispose()
        return 1

    # ── Encrypt and persist ───────────────────────────────────────────────────
    try:
        await store.save(account_uuid, api_key, api_secret)
        print(f"SUCCESS: credentials encrypted and stored for account {account_uuid}")
    except Exception as exc:
        print(f"ERROR: save failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        await engine.dispose()
        return 3

    # ── Verify round-trip (in-memory only, no printing of values) ────────────
    retrieved = await store.get(account_uuid)
    if retrieved is None:
        print("VERIFY FAILED: could not decrypt credentials after saving", file=sys.stderr)
        await engine.dispose()
        return 4

    retrieved_key, retrieved_secret = retrieved
    if retrieved_key != api_key or retrieved_secret != api_secret:
        print("VERIFY FAILED: decrypted values do not match originals", file=sys.stderr)
        await engine.dispose()
        return 4

    print("VERIFY PASSED: decrypt round-trip confirmed")
    print(f"  api_key length  : {len(api_key)} chars")
    print(f"  api_secret length: {len(api_secret)} chars")
    print()
    print("NEXT STEP: delete BINANCE_API_KEY and BINANCE_API_SECRET from Railway vars.")
    print("  The execution service reads credentials from the encrypted DB store only.")

    # Clear from local vars before dispose (best-effort; GC handles the rest)
    api_key = api_secret = retrieved_key = retrieved_secret = ""  # noqa: F841

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
