from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.account_repo import AccountRepository
from app.models.account import TradingAccount
from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)


class AccountService:
    """계정 CRUD + API 키 암호화 관리"""

    def __init__(self, session: AsyncSession, encryption: EncryptionManager):
        self._repo = AccountRepository(session)
        self._session = session
        self._encryption = encryption

    async def create_account(
        self, *, owner_id: UUID, name: str, api_key: str, api_secret: str,
        symbol: str = "ETHUSDT", base_asset: str = "ETH", quote_asset: str = "USDT",
        loop_interval_sec: int = 60, order_cooldown_sec: int = 7,
    ) -> TradingAccount:
        account = TradingAccount(
            owner_id=owner_id,
            name=name,
            api_key_encrypted=self._encryption.encrypt(api_key),
            api_secret_encrypted=self._encryption.encrypt(api_secret),
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            loop_interval_sec=loop_interval_sec,
            order_cooldown_sec=order_cooldown_sec,
        )
        return await self._repo.create(account)

    async def get_account(self, account_id: UUID) -> TradingAccount | None:
        return await self._repo.get_by_id(account_id)

    async def get_accounts_by_owner(self, owner_id: UUID) -> list[TradingAccount]:
        return await self._repo.get_by_owner(owner_id)

    async def get_active_accounts(self) -> list[TradingAccount]:
        return await self._repo.get_active_accounts()

    def decrypt_api_key(self, account: TradingAccount) -> str:
        return self._encryption.decrypt(account.api_key_encrypted)

    def decrypt_api_secret(self, account: TradingAccount) -> str:
        return self._encryption.decrypt(account.api_secret_encrypted)

    async def update_api_keys(self, account_id: UUID, api_key: str, api_secret: str) -> None:
        account = await self._repo.get_by_id(account_id)
        if account:
            account.api_key_encrypted = self._encryption.encrypt(api_key)
            account.api_secret_encrypted = self._encryption.encrypt(api_secret)
            await self._session.flush()

    async def reset_circuit_breaker(self, account_id: UUID) -> None:
        await self._repo.reset_circuit_breaker(account_id)
