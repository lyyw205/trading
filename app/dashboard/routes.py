from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


def _render(request: Request, template: str, context: dict | None = None) -> HTMLResponse:
    """Render template with auto-injected csp_nonce."""
    ctx = {"request": request}
    nonce = getattr(request.state, "csp_nonce", "")
    ctx["csp_nonce"] = nonce
    if context:
        ctx.update(context)
    templates = request.app.state.templates
    return templates.TemplateResponse(template, ctx)


def _require_login(request: Request) -> tuple[dict | None, RedirectResponse | None]:
    """Shared guard: redirect to /login if not authenticated."""
    user = getattr(request.state, "user", None)
    if not user:
        return None, RedirectResponse(url="/login", status_code=302)
    return user, None


def _require_admin_page(request: Request) -> tuple[dict | None, RedirectResponse | None]:
    """Shared guard for admin SSR pages. Returns (user, redirect) tuple."""
    user = getattr(request.state, "user", None)
    if not user:
        return None, RedirectResponse(url="/login", status_code=302)
    if user.get("role") != "admin":
        return None, RedirectResponse(url="/login", status_code=302)
    return user, None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render(request, "login.html")


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "accounts.html", {"user": user})


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
async def account_detail_page(request: Request, account_id: UUID):
    user, redirect = _require_login(request)
    if redirect:
        return redirect
    return _render(request, "account_detail.html", {"user": user, "account_id": str(account_id)})


@router.get("/admin", response_class=HTMLResponse)
async def admin_overview_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_overview.html", {"user": user})


@router.get("/admin/accounts", response_class=HTMLResponse)
async def admin_accounts_page(request: Request):
    """Deprecated — redirects to unified /accounts page."""
    return RedirectResponse(url="/accounts", status_code=302)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Deprecated — redirects to unified /accounts page."""
    return RedirectResponse(url="/accounts", status_code=302)


@router.get("/admin/lots", response_class=HTMLResponse)
async def admin_lots_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_lots.html", {"user": user})


@router.get("/admin/strategies", response_class=HTMLResponse)
async def admin_strategies_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_strategies.html", {"user": user})


@router.get("/admin/positions", response_class=HTMLResponse)
async def admin_positions_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_positions.html", {"user": user})


@router.get("/admin/earnings", response_class=HTMLResponse)
async def admin_earnings_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_earnings.html", {"user": user})


@router.get("/admin/system", response_class=HTMLResponse)
async def admin_system_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_system.html", {"user": user})


@router.get("/admin/backtest", response_class=HTMLResponse)
async def admin_backtest_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_backtest.html", {"user": user})


@router.get("/admin/trades", response_class=HTMLResponse)
async def admin_trades_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_trades.html", {"user": user})


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_logs.html", {"user": user})


@router.get("/admin/reports", response_class=HTMLResponse)
async def admin_reports_page(request: Request):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "admin_reports.html", {"user": user})


@router.get("/admin/backtest/{backtest_id}", response_class=HTMLResponse)
async def backtest_report_page(request: Request, backtest_id: UUID):
    user, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(request, "backtest_report.html", {"user": user, "backtest_id": str(backtest_id)})
