from __future__ import annotations

from abc import ABC, abstractmethod

from shared.schemas.execution import ExecutionRequest, RiskDecision


class RiskEngineBase(ABC):
    """Abstract interface every risk engine must implement.

    Concrete implementation lives in the execution service.
    """

    @abstractmethod
    async def evaluate(self, request: ExecutionRequest) -> RiskDecision: ...

    @abstractmethod
    async def is_kill_switch_active(self, account_id: str) -> bool: ...

    @abstractmethod
    async def is_user_paused(self, account_id: str) -> bool: ...

    @abstractmethod
    async def is_symbol_paused(self, account_id: str, symbol: str) -> bool: ...
