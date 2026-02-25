from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


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
async def admin_page(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/accounts", status_code=302)
    templates = request.app.state.templates
    return templates.TemplateResponse("admin.html", {"request": request, "user": user})


@router.get("/admin/backtest/{backtest_id}", response_class=HTMLResponse)
async def backtest_report_page(request: Request, backtest_id: str):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/accounts", status_code=302)
    templates = request.app.state.templates
    return templates.TemplateResponse("backtest_report.html", {
        "request": request,
        "user": user,
        "backtest_id": backtest_id,
    })
