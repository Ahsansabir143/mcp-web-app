"""Account context: policy, approval level, and credential availability for a given account."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.account import ApiCredentialRef, ExchangeAccount
from shared.db.models.execution import ApprovalLevelRecord, RiskPolicy
from shared.risk.limits import RiskLimits
from shared.schemas.enums import ApprovalLevel, TradingMode
from shared.utils.logging import get_logger

log = get_logger("execution.account.context")


@dataclass(frozen=True)
class AccountContext:
    account_id: str
    user_id: str
    trading_mode: TradingMode
    approval_level: ApprovalLevel
    has_credentials: bool
    paper_only: bool
    allowed_symbols: list[str] | None = None
    denied_symbols: tuple[str, ...] = field(default_factory=tuple)
    limits: RiskLimits = field(default_factory=RiskLimits)


class AccountContextLoader:
    """Loads account policy from DB.

    All credential decryption happens in ``decrypt_credentials`` — never cached,
    never logged. Returns ``None`` when the account is not found.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        secret_key: str = "",
    ) -> None:
        self._factory = session_factory
        self._secret_key = secret_key

    async def load(self, account_id: str) -> AccountContext | None:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            return None

        async with self._factory() as session:
            account = await session.get(ExchangeAccount, account_uuid)
            if account is None:
                return None

            approval_result = await session.execute(
                select(ApprovalLevelRecord).where(
                    ApprovalLevelRecord.account_id == account_uuid
                )
            )
            approval = approval_result.scalar_one_or_none()

            policy_result = await session.execute(
                select(RiskPolicy).where(RiskPolicy.account_id == account_uuid)
            )
            policy = policy_result.scalar_one_or_none()

            cred_result = await session.execute(
                select(ApiCredentialRef).where(
                    ApiCredentialRef.account_id == account_uuid
                )
            )
            has_credentials = cred_result.scalar_one_or_none() is not None

        try:
            approval_level = ApprovalLevel(
                approval.level if approval else account.approval_level
            )
        except ValueError:
            approval_level = ApprovalLevel.L2_PAPER

        limits = RiskLimits()
        if policy:
            limits = RiskLimits(
                max_position_size_usd=Decimal(str(policy.max_position_size_usd)),
                max_daily_loss_usd=Decimal(str(policy.max_daily_loss_usd)),
                max_concurrent_positions=int(policy.max_concurrent_positions),
                symbol_cooldown_seconds=int(policy.symbol_cooldown_seconds),
            )

        paper_only = approval.paper_only if approval is not None else True
        allowed_symbols = approval.allowed_symbols if approval else None
        denied_syms = tuple(approval.denied_symbols or []) if approval else ()

        return AccountContext(
            account_id=account_id,
            user_id=str(account.user_id),
            trading_mode=TradingMode(account.trading_mode),
            approval_level=approval_level,
            has_credentials=has_credentials,
            paper_only=paper_only,
            allowed_symbols=list(allowed_symbols) if allowed_symbols else None,
            denied_symbols=denied_syms,
            limits=limits,
        )

    async def decrypt_credentials(self, account_id: str) -> tuple[str, str] | None:
        """Return ``(api_key, api_secret)`` plaintext or ``None`` if unavailable.

        The secret_key used for AES-256-GCM decryption must be provided at
        construction time. Errors are caught and logged; never raised to callers.
        """
        if not self._secret_key:
            return None
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            return None

        async with self._factory() as session:
            result = await session.execute(
                select(ApiCredentialRef).where(
                    ApiCredentialRef.account_id == account_uuid
                )
            )
            cred = result.scalar_one_or_none()
            if cred is None:
                return None

        try:
            from services.execution.credentials import decrypt_credential

            api_key = decrypt_credential(cred.encrypted_key, cred.iv, self._secret_key)
            api_secret = decrypt_credential(
                cred.encrypted_secret, cred.iv, self._secret_key
            )
            return api_key, api_secret
        except Exception as exc:
            log.error("credential decryption failed for account %s", account_id, exc_info=exc)
            return None
