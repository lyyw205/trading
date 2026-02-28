from __future__ import annotations

from typing import Tuple, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


def _require_admin_page(request: Request) -> Tuple[Optional[dict], Optional[RedirectResponse]]:
    """Shared guard for admin SSR pages. Returns (user, redirect) tuple."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return None, RedirectResponse(url="/accounts", status_code=302)
    return user, None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    user = getattr(request.state, "user", None)
    templates = request.app.state.templates
    return templates.TemplateResponse("accounts.html", {"request": request, "user": user})


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
async def account_detail_page(request: Request, account_id: str):
    user = getattr(request.state, "user", None)
    templates = request.app.state.templates
    return templates.TemplateResponse("account_detail.html", {
        "request": request,
        "user": user,
        "account_id": account_id,
    })


@router.get("/admin", response_class=HTMLResponse)
async def admin_overview_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("admin_overview.html", {"request": request, "user": user})


@router.get("/admin/accounts", response_class=HTMLResponse)
async def admin_accounts_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("admin_accounts.html", {"request": request, "user": user})


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("admin_users.html", {"request": request, "user": user})


@router.get("/admin/backtest", response_class=HTMLResponse)
async def admin_backtest_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("admin_backtest.html", {"request": request, "user": user})


@router.get("/admin/trades", response_class=HTMLResponse)
async def admin_trades_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("admin_trades.html", {"request": request, "user": user})


@router.get("/admin/backtest/{backtest_id}", response_class=HTMLResponse)
async def backtest_report_page(request: Request, backtest_id: str):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    templates = request.app.state.templates
    return templates.TemplateResponse("backtest_report.html", {
        "request": request,
        "user": user,
        "backtest_id": backtest_id,
    })
