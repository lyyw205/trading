from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.schemas.account import AccountCreate, AccountUpdate, AccountResponse, AccountListResponse
from app.services.account_service import AccountService
from app.db.account_repo import AccountRepository
from app.utils.encryption import EncryptionManager
from app.dependencies import get_current_user, get_owned_account, limiter
from app.utils.logging import audit_log

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=AccountListResponse)
@limiter.limit("120/minute")
async def list_accounts(request: Request, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_trading_session)):
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    if user.get("role") == "admin":
        repo = AccountRepository(session)
        accounts = await repo.get_active_accounts()
    else:
        accounts = await svc.get_accounts_by_owner(UUID(user["id"]))
    return AccountListResponse(accounts=[AccountResponse.model_validate(a) for a in accounts])


@router.post("", response_model=AccountResponse, status_code=201)
@limiter.limit("30/minute")
async def create_account(body: AccountCreate, request: Request, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_trading_session)):
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    account = await svc.create_account(
        owner_id=UUID(user["id"]), name=body.name,
        api_key=body.api_key, api_secret=body.api_secret,
        symbol=body.symbol, base_asset=body.base_asset, quote_asset=body.quote_asset,
        loop_interval_sec=body.loop_interval_sec, order_cooldown_sec=body.order_cooldown_sec,
    )
    await session.commit()

    audit_log("account_created", user_id=user["id"], account_id=str(account.id), name=body.name)
    return AccountResponse.model_validate(account)


@router.get("/{account_id}", response_model=AccountResponse)
@limiter.limit("120/minute")
async def get_account(request: Request, account=Depends(get_owned_account)):
    return AccountResponse.model_validate(account)


@router.put("/{account_id}", response_model=AccountResponse)
@limiter.limit("30/minute")
async def update_account(
    body: AccountUpdate,
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    encryption: EncryptionManager = request.app.state.encryption
    for field, val in body.model_dump(exclude_unset=True).items():
        if field == "api_key" and val:
            account.api_key_encrypted = encryption.encrypt(val)
        elif field == "api_secret" and val:
            account.api_secret_encrypted = encryption.encrypt(val)
        elif hasattr(account, field):
            setattr(account, field, val)
    await session.commit()

    audit_log("account_updated", user_id=user["id"], account_id=str(account.id), changed_fields=list(body.model_dump(exclude_unset=True).keys()))
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=204)
@limiter.limit("30/minute")
async def delete_account(
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    engine = request.app.state.trading_engine
    await engine.stop_account(account.id)
    await session.delete(account)
    await session.commit()

    audit_log("account_deleted", user_id=user["id"], account_id=str(account.id))


@router.post("/{account_id}/start", status_code=200)
@limiter.limit("30/minute")
async def start_account(
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
):
    engine = request.app.state.trading_engine
    await engine.start_account(account.id)

    audit_log("account_started", user_id=user["id"], account_id=str(account.id))
    return {"status": "started", "account_id": str(account.id)}


@router.post("/{account_id}/stop", status_code=200)
@limiter.limit("30/minute")
async def stop_account(
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
):
    engine = request.app.state.trading_engine
    await engine.stop_account(account.id)

    audit_log("account_stopped", user_id=user["id"], account_id=str(account.id))
    return {"status": "stopped", "account_id": str(account.id)}


@router.post("/{account_id}/buy-pause/resume", status_code=200)
@limiter.limit("30/minute")
async def resume_buying(
    request: Request,
    account=Depends(get_owned_account),
    user: dict = Depends(get_current_user),
):
    engine = request.app.state.trading_engine
    await engine.resume_buying(account.id)

    audit_log("buy_pause_resumed", user_id=user["id"], account_id=str(account.id))
    return {"status": "resumed", "account_id": str(account.id)}


@router.post("/{account_id}/reset-circuit-breaker", status_code=200)
@limiter.limit("30/minute")
async def reset_circuit_breaker(
    account_id: UUID,
    request: Request,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_trading_session),
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    # 계정 존재 확인
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    await svc.reset_circuit_breaker(account_id)
    await session.commit()
    # Restart trader
    engine = request.app.state.trading_engine
    await engine.reload_account(account_id)

    audit_log("circuit_breaker_reset", user_id=user["id"], account_id=str(account_id))
    return {"status": "reset", "account_id": str(account_id)}
