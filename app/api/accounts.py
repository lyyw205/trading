from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.schemas.account import AccountCreate, AccountUpdate, AccountResponse, AccountListResponse
from app.services.account_service import AccountService
from app.db.account_repo import AccountRepository
from app.utils.encryption import EncryptionManager

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _get_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("", response_model=AccountListResponse)
async def list_accounts(request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    if user.get("role") == "admin":
        from app.db.account_repo import AccountRepository
        repo = AccountRepository(session)
        accounts = await repo.get_active_accounts()
    else:
        accounts = await svc.get_accounts_by_owner(UUID(user["id"]))
    return AccountListResponse(accounts=[AccountResponse.model_validate(a) for a in accounts])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    account = await svc.create_account(
        owner_id=UUID(user["id"]), name=body.name,
        api_key=body.api_key, api_secret=body.api_secret,
        symbol=body.symbol, base_asset=body.base_asset, quote_asset=body.quote_asset,
        loop_interval_sec=body.loop_interval_sec, order_cooldown_sec=body.order_cooldown_sec,
    )
    await session.commit()
    return AccountResponse.model_validate(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    account = await svc.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    return AccountResponse.model_validate(account)


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: UUID, body: AccountUpdate, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    account = await svc.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    # Update fields
    for field, val in body.model_dump(exclude_unset=True).items():
        if field == "api_key" and val:
            account.api_key_encrypted = encryption.encrypt(val)
        elif field == "api_secret" and val:
            account.api_secret_encrypted = encryption.encrypt(val)
        elif hasattr(account, field):
            setattr(account, field, val)
    await session.commit()
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    from app.db.account_repo import AccountRepository
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    # Stop trader first
    engine = request.app.state.trading_engine
    await engine.stop_account(account_id)
    await session.delete(account)
    await session.commit()


@router.post("/{account_id}/start", status_code=200)
async def start_account(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    engine = request.app.state.trading_engine
    await engine.start_account(account_id)
    return {"status": "started", "account_id": str(account_id)}


@router.post("/{account_id}/stop", status_code=200)
async def stop_account(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    engine = request.app.state.trading_engine
    await engine.stop_account(account_id)
    return {"status": "stopped", "account_id": str(account_id)}


@router.post("/{account_id}/reset-circuit-breaker", status_code=200)
async def reset_circuit_breaker(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    user = _get_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    encryption: EncryptionManager = request.app.state.encryption
    svc = AccountService(session, encryption)
    await svc.reset_circuit_breaker(account_id)
    await session.commit()
    # Restart trader
    engine = request.app.state.trading_engine
    await engine.reload_account(account_id)
    return {"status": "reset", "account_id": str(account_id)}
