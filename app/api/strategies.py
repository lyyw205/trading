from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.models.strategy_config import StrategyConfig
from app.strategies.registry import StrategyRegistry
from app.schemas.strategy import StrategyInfo, StrategyConfigResponse, StrategyParamsUpdate

router = APIRouter(prefix="/api", tags=["strategies"])


@router.get("/strategies", response_model=list[StrategyInfo])
async def list_strategies():
    """List all available strategies with params schema"""
    return StrategyRegistry.list_all()


@router.get("/accounts/{account_id}/strategies", response_model=list[StrategyConfigResponse])
async def get_account_strategies(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    stmt = select(StrategyConfig).where(StrategyConfig.account_id == account_id)
    result = await session.execute(stmt)
    configs = list(result.scalars().all())
    return [StrategyConfigResponse(
        strategy_name=c.strategy_name,
        is_enabled=c.is_enabled,
        params=c.params or {},
    ) for c in configs]


@router.put("/accounts/{account_id}/strategies/{strategy_name}")
async def update_strategy_params(account_id: UUID, strategy_name: str, body: StrategyParamsUpdate, request: Request, session: AsyncSession = Depends(get_trading_session)):
    stmt = select(StrategyConfig).where(
        StrategyConfig.account_id == account_id,
        StrategyConfig.strategy_name == strategy_name,
    )
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        # Create new config
        strategy_cls = StrategyRegistry.get(strategy_name)
        instance = strategy_cls()
        merged = instance.validate_params(body.params)
        config = StrategyConfig(account_id=account_id, strategy_name=strategy_name, params=merged, is_enabled=True)
        session.add(config)
    else:
        strategy_cls = StrategyRegistry.get(strategy_name)
        instance = strategy_cls()
        config.params = instance.validate_params(body.params)
    await session.commit()
    return {"status": "updated", "strategy_name": strategy_name, "params": config.params}


@router.post("/accounts/{account_id}/strategies/{strategy_name}/enable")
async def enable_strategy(account_id: UUID, strategy_name: str, session: AsyncSession = Depends(get_trading_session)):
    stmt = select(StrategyConfig).where(StrategyConfig.account_id == account_id, StrategyConfig.strategy_name == strategy_name)
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        config = StrategyConfig(account_id=account_id, strategy_name=strategy_name, is_enabled=True, params={})
        session.add(config)
    else:
        config.is_enabled = True
    await session.commit()
    return {"status": "enabled"}


@router.post("/accounts/{account_id}/strategies/{strategy_name}/disable")
async def disable_strategy(account_id: UUID, strategy_name: str, session: AsyncSession = Depends(get_trading_session)):
    stmt = select(StrategyConfig).where(StrategyConfig.account_id == account_id, StrategyConfig.strategy_name == strategy_name)
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if config:
        config.is_enabled = False
        await session.commit()
    return {"status": "disabled"}
