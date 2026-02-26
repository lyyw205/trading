from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.models.trading_combo import TradingCombo
from app.models.lot import Lot
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
from app.schemas.strategy import (
    BuyLogicInfo, SellLogicInfo, ComboCreate, ComboUpdate, ComboResponse,
)
from app.dependencies import get_owned_account, get_current_user, limiter
from app.utils.logging import audit_log

router = APIRouter(prefix="/api", tags=["combos"])


# --- Logic listing ---

@router.get("/buy-logics", response_model=list[BuyLogicInfo])
@limiter.limit("120/minute")
async def list_buy_logics(request: Request):
    return BuyLogicRegistry.list_all()


@router.get("/sell-logics", response_model=list[SellLogicInfo])
@limiter.limit("120/minute")
async def list_sell_logics(request: Request):
    return SellLogicRegistry.list_all()


# --- Combo CRUD ---

@router.get("/accounts/{account_id}/combos", response_model=list[ComboResponse])
@limiter.limit("120/minute")
async def list_combos(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    stmt = select(TradingCombo).where(
        TradingCombo.account_id == account.id,
    ).order_by(TradingCombo.created_at)
    result = await session.execute(stmt)
    return [ComboResponse.model_validate(c) for c in result.scalars().all()]


@router.post("/accounts/{account_id}/combos", response_model=ComboResponse, status_code=201)
@limiter.limit("30/minute")
async def create_combo(
    body: ComboCreate,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    # Validate logic names
    try:
        BuyLogicRegistry.get(body.buy_logic_name)
    except KeyError:
        raise HTTPException(status_code=422, detail=f"Unknown buy logic: {body.buy_logic_name}")
    try:
        SellLogicRegistry.get(body.sell_logic_name)
    except KeyError:
        raise HTTPException(status_code=422, detail=f"Unknown sell logic: {body.sell_logic_name}")

    # Validate reference_combo_id
    if body.reference_combo_id:
        ref = await session.get(TradingCombo, body.reference_combo_id)
        if not ref or ref.account_id != account.id:
            raise HTTPException(status_code=422, detail="Invalid reference_combo_id")

    combo = TradingCombo(
        account_id=account.id,
        name=body.name,
        buy_logic_name=body.buy_logic_name,
        buy_params=body.buy_params,
        sell_logic_name=body.sell_logic_name,
        sell_params=body.sell_params,
        reference_combo_id=body.reference_combo_id,
    )
    session.add(combo)
    await session.commit()
    await session.refresh(combo)

    audit_log(
        "combo_created",
        user_id=user["id"],
        account_id=str(account.id),
        combo_id=str(combo.id),
        combo_name=combo.name,
    )
    return ComboResponse.model_validate(combo)


@router.put("/accounts/{account_id}/combos/{combo_id}", response_model=ComboResponse)
@limiter.limit("30/minute")
async def update_combo(
    combo_id: UUID,
    body: ComboUpdate,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    combo = await session.get(TradingCombo, combo_id)
    if not combo or combo.account_id != account.id:
        raise HTTPException(status_code=404, detail="Combo not found")

    if body.name is not None:
        combo.name = body.name
    if body.buy_params is not None:
        buy_logic = BuyLogicRegistry.create_instance(combo.buy_logic_name)
        combo.buy_params = buy_logic.validate_params(body.buy_params)
    if body.sell_params is not None:
        sell_logic = SellLogicRegistry.create_instance(combo.sell_logic_name)
        combo.sell_params = sell_logic.validate_params(body.sell_params)
    if body.reference_combo_id is not None:
        if body.reference_combo_id == combo_id:
            raise HTTPException(status_code=422, detail="Cannot reference self")
        ref = await session.get(TradingCombo, body.reference_combo_id)
        if not ref or ref.account_id != account.id:
            raise HTTPException(status_code=422, detail="Invalid reference_combo_id")
        combo.reference_combo_id = body.reference_combo_id

    await session.commit()
    await session.refresh(combo)

    audit_log(
        "combo_updated",
        user_id=user["id"],
        account_id=str(account.id),
        combo_id=str(combo.id),
    )
    return ComboResponse.model_validate(combo)


@router.delete("/accounts/{account_id}/combos/{combo_id}")
@limiter.limit("30/minute")
async def delete_combo(
    combo_id: UUID,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    combo = await session.get(TradingCombo, combo_id)
    if not combo or combo.account_id != account.id:
        raise HTTPException(status_code=404, detail="Combo not found")

    # Guard: check for OPEN lots
    stmt = select(Lot).where(
        Lot.account_id == account.id,
        Lot.combo_id == combo_id,
        Lot.status == "OPEN",
    ).limit(1)
    result = await session.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Cannot delete combo with open lots. Disable it or close all lots first.",
        )

    await session.delete(combo)
    await session.commit()

    audit_log(
        "combo_deleted",
        user_id=user["id"],
        account_id=str(account.id),
        combo_id=str(combo_id),
    )
    return {"status": "deleted"}


@router.post("/accounts/{account_id}/combos/{combo_id}/enable")
@limiter.limit("30/minute")
async def enable_combo(
    combo_id: UUID,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    combo = await session.get(TradingCombo, combo_id)
    if not combo or combo.account_id != account.id:
        raise HTTPException(status_code=404, detail="Combo not found")

    combo.is_enabled = True
    await session.commit()

    audit_log("combo_enabled", user_id=user["id"], account_id=str(account.id), combo_id=str(combo_id))
    return {"status": "enabled"}


@router.post("/accounts/{account_id}/combos/{combo_id}/disable")
@limiter.limit("30/minute")
async def disable_combo(
    combo_id: UUID,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    combo = await session.get(TradingCombo, combo_id)
    if not combo or combo.account_id != account.id:
        raise HTTPException(status_code=404, detail="Combo not found")

    combo.is_enabled = False
    await session.commit()

    audit_log("combo_disabled", user_id=user["id"], account_id=str(account.id), combo_id=str(combo_id))
    return {"status": "disabled"}
