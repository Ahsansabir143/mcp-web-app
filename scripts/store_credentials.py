"""Admin script: interactively encrypt and store Binance API credentials.

Prompts for API key and secret via hidden input (getpass) — they are never
logged, echoed, or stored as plaintext.

Usage (recommended — Railway injects CREDENTIAL_ENCRYPTION_KEY automatically):

    # In your local terminal, set the public DB URL once:
    $env:DATABASE_PUBLIC_URL = "postgresql+asyncpg://...@yamanote.proxy.rlwy.net:15859/railway"

    # Then run via railway run so CREDENTIAL_ENCRYPTION_KEY is injected:
    railway run --service execution python scripts/store_credentials.py \\
        --account-id 00000000-0000-0000-0000-000000000003 --verify

Environment variables (in priority order):
    DATABASE_PUBLIC_URL         asyncpg URL to public Postgres TCP proxy (for local runs)
    DATABASE_URL                asyncpg URL; used if DATABASE_PUBLIC_URL not set
    CREDENTIAL_ENCRYPTION_KEY   base64url-encoded 32-byte AES key (injected by railway run)

Exit codes:
    0   credentials saved and verified
    1   configuration / argument error
    2   account not found in DB
    3   DB or encryption error
    4   verify failed
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid
from pathlib import Path

# ── Local-execution path fix ──────────────────────────────────────────────────
# When run directly (not via `pip install -e .`) ensure the project root is on
# sys.path so that `shared` and `services` are importable.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from services.execution.account.credential_store import CredentialStore  # noqa: E402
from shared.db.models.account import ExchangeAccount  # noqa: E402


# ── Testable core (no I/O) ────────────────────────────────────────────────────

async def _do_store(
    account_id: uuid.UUID,
    api_key: str,
    api_secret: str,
    db_url: str,
    enc_key: str,
    *,
    verify: bool = True,
) -> tuple[bool, str]:
    """Encrypt and persist credentials.  Returns (success, message).

    ``message`` is safe to print — it contains lengths, not values.
    """
    engine = create_async_engine(db_url, echo=False)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    try:
        # Verify account exists
        async with factory() as session:
            acct = await session.get(ExchangeAccount, account_id)
            if acct is None:
                return False, f"ERROR [exit 2]: account {account_id} not found in DB"
            account_info = (
                f"label={acct.account_label!r} venue={acct.venue!r} "
                f"mode={acct.trading_mode!r}"
            )

        # Initialise store
        store = CredentialStore(factory, enc_key)
        if not store.is_configured():
            return (
                False,
                "ERROR [exit 1]: CredentialStore not configured — "
                "check CREDENTIAL_ENCRYPTION_KEY",
            )

        # Encrypt and save
        try:
            await store.save(account_id, api_key, api_secret)
        except Exception as exc:
            return False, f"ERROR [exit 3]: save failed: {type(exc).__name__}: {exc}"

        lines = [
            f"Account      : {account_id}  ({account_info})",
            f"Key length   : {len(api_key)} chars",
            f"Secret length: {len(api_secret)} chars",
            "Stored       : encrypted (AES-256-GCM, per-field nonce)",
        ]

        # Verify round-trip
        if verify:
            retrieved = await store.get(account_id)
            if retrieved is None:
                return False, "VERIFY FAILED [exit 4]: could not decrypt after saving"
            rk, rs = retrieved
            if rk != api_key or rs != api_secret:
                return (
                    False,
                    "VERIFY FAILED [exit 4]: decrypted values do not match originals",
                )
            lines.append("Verify       : PASSED (decrypt round-trip confirmed)")

        return True, "\n".join(lines)

    finally:
        await engine.dispose()


def _resolve_db_url() -> str:
    """Pick the best available database URL for local execution.

    Prefers DATABASE_PUBLIC_URL over DATABASE_URL, and rewrites postgres://
    to postgresql+asyncpg:// if needed.
    """
    raw = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL", "")
    if not raw:
        return ""
    # Normalise driver
    if raw.startswith("postgres://"):
        raw = "postgresql+asyncpg://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
        raw = "postgresql+asyncpg://" + raw[len("postgresql://"):]
    return raw


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactively store encrypted Binance API credentials.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--account-id",
        required=True,
        help="ExchangeAccount UUID (e.g. 00000000-0000-0000-0000-000000000003)",
    )
    parser.add_argument(
        "--db-url",
        default="",
        help=(
            "Override database URL (asyncpg). "
            "Defaults to DATABASE_PUBLIC_URL or DATABASE_URL env var. "
            "Use the public TCP proxy URL for local execution."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="After saving, decrypt and verify the round-trip (default: on)",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip the verify step",
    )
    args = parser.parse_args()

    # Validate account UUID
    try:
        account_uuid = uuid.UUID(args.account_id)
    except ValueError:
        print(f"ERROR: '{args.account_id}' is not a valid UUID", file=sys.stderr)
        return 1

    # Resolve DB URL
    db_url = args.db_url.strip() or _resolve_db_url()
    if not db_url:
        print(
            "ERROR: No database URL found.\n"
            "Set DATABASE_PUBLIC_URL env var to the public TCP proxy URL, or\n"
            "pass --db-url directly.\n"
            "Example: postgresql+asyncpg://postgres:PASSWORD@yamanote.proxy.rlwy.net:15859/railway",
            file=sys.stderr,
        )
        return 1

    if ".railway.internal" in db_url:
        print(
            "WARNING: DATABASE_URL points to the internal Railway host "
            "(postgres.railway.internal).\n"
            "This is only reachable inside Railway's network. "
            "For local execution, set DATABASE_PUBLIC_URL or pass --db-url "
            "with the public TCP proxy URL.",
            file=sys.stderr,
        )

    # Validate encryption key
    enc_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not enc_key:
        print(
            "ERROR: CREDENTIAL_ENCRYPTION_KEY is not set.\n"
            "Run via: railway run --service execution python scripts/store_credentials.py ...\n"
            "(Railway injects this variable automatically.)",
            file=sys.stderr,
        )
        return 1

    # ── Prompt for credentials (hidden input) ─────────────────────────────────
    print(
        "\n"
        "Enter your Binance API credentials below.\n"
        "Input is hidden and will NOT be logged or echoed.\n"
        "(These should be READ-ONLY keys with no trading permissions.)\n"
    )
    try:
        api_key = getpass.getpass("  Binance API Key    : ")
        api_secret = getpass.getpass("  Binance API Secret : ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return 1

    if not api_key or not api_secret:
        print("ERROR: API key and secret must not be empty.", file=sys.stderr)
        return 1

    print("\nEncrypting and storing credentials …\n")

    success, message = await _do_store(
        account_uuid, api_key, api_secret, db_url, enc_key, verify=args.verify
    )

    # Clear local references to plaintext (best-effort before GC)
    api_key = api_secret = ""  # noqa: F841

    if success:
        print("SUCCESS")
        print("─" * 50)
        print(message)
        print("─" * 50)
        print(
            "\nNext steps:\n"
            "  1. Remove BINANCE_API_KEY / BINANCE_API_SECRET from Railway vars if present\n"
            "     (the DB credential store is now the authoritative source).\n"
            "  2. Set ACCOUNT_STREAM_ENABLED=true on the execution service.\n"
            "  3. Redeploy execution and check logs for stream connectivity."
        )
        return 0
    else:
        print(f"\nFAILED\n{message}", file=sys.stderr)
        exit_code = 3
        for tag in ("[exit 1]", "[exit 2]", "[exit 3]", "[exit 4]"):
            if tag in message:
                exit_code = int(tag[6])
                break
        return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
