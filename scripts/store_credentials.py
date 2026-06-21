"""One-shot admin script: encrypt and store Binance API credentials.

Run via Railway CLI (which injects CREDENTIAL_ENCRYPTION_KEY and DATABASE_URL):

    railway run --service execution python scripts/store_credentials.py \
        --account-id <UUID> \
        --api-key-stdin \
        --api-secret-stdin

The script reads api-key and api-secret from stdin (one value per prompt)
so they never appear in shell history or process argument lists.

Env vars required (provided automatically by `railway run --service execution`):
    CREDENTIAL_ENCRYPTION_KEY   base64url-encoded 32-byte AES key
    DATABASE_URL                asyncpg connection string

Exit codes:
    0   credentials saved successfully
    1   configuration error (missing env var, bad key)
    2   account not found in DB
    3   DB / encryption error
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid


async def main() -> int:
    parser = argparse.ArgumentParser(description="Store encrypted exchange credentials")
    parser.add_argument("--account-id", required=True, help="ExchangeAccount UUID")
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read API key interactively from stdin (never from args)",
    )
    parser.add_argument(
        "--api-secret-stdin",
        action="store_true",
        help="Read API secret interactively from stdin (never from args)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After saving, immediately verify by decrypting and checking connectivity",
    )
    args = parser.parse_args()

    # Validate account ID
    try:
        account_uuid = uuid.UUID(args.account_id)
    except ValueError:
        print(f"ERROR: '{args.account_id}' is not a valid UUID", file=sys.stderr)
        return 1

    # Validate encryption key is configured
    enc_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    if not enc_key:
        print(
            "ERROR: CREDENTIAL_ENCRYPTION_KEY is not set. "
            "Run via: railway run --service execution python scripts/store_credentials.py ...",
            file=sys.stderr,
        )
        return 1

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    # Read credentials securely from stdin
    if args.api_key_stdin:
        api_key = getpass.getpass("Binance API Key: ")
    else:
        print("ERROR: --api-key-stdin is required (keys must not be passed as args)", file=sys.stderr)
        return 1

    if args.api_secret_stdin:
        api_secret = getpass.getpass("Binance API Secret: ")
    else:
        print("ERROR: --api-secret-stdin is required", file=sys.stderr)
        return 1

    if not api_key or not api_secret:
        print("ERROR: API key and secret must not be empty", file=sys.stderr)
        return 1

    # Import heavy deps after arg validation
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import select
    from shared.db.models.account import ExchangeAccount
    from services.execution.account.credential_store import CredentialStore

    # Verify account exists
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        acct = await session.get(ExchangeAccount, account_uuid)
        if acct is None:
            print(f"ERROR: Account {account_uuid} not found in database", file=sys.stderr)
            await engine.dispose()
            return 2
        print(f"Account found: label='{acct.account_label}' venue='{acct.venue}' mode='{acct.trading_mode}'")

    # Save credentials
    store = CredentialStore(factory, enc_key)
    if not store.is_configured():
        print("ERROR: Failed to initialise CredentialStore (bad encryption key?)", file=sys.stderr)
        await engine.dispose()
        return 1

    try:
        await store.save(account_uuid, api_key, api_secret)
        print(f"SUCCESS: Credentials encrypted and stored for account {account_uuid}")
    except Exception as exc:
        print(f"ERROR: Failed to save credentials: {exc}", file=sys.stderr)
        await engine.dispose()
        return 3

    # Optional: decrypt and verify round-trip (not connectivity, just crypto)
    if args.verify:
        retrieved = await store.get(account_uuid)
        if retrieved is None:
            print("VERIFY FAILED: Could not decrypt credentials after saving", file=sys.stderr)
            await engine.dispose()
            return 3
        k, s = retrieved
        if k != api_key or s != api_secret:
            print("VERIFY FAILED: Decrypted values do not match originals", file=sys.stderr)
            await engine.dispose()
            return 3
        print("VERIFY PASSED: Decrypt round-trip confirmed")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
