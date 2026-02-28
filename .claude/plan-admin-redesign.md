# Admin Dashboard Restructuring + Toss Invest Theme Plan

## Phase 1: Admin Page Restructuring (Backend + Frontend)

### Current State
- Single monolithic `/admin` page with 4 sections stacked vertically
- System Health, All Accounts, Backtest, Users — all in one scroll

### Target State
- Admin sidebar sub-navigation with 5 separate tabs (Logs deferred to Phase 4)
- Each tab is its own focused dashboard with more detail
- Route pattern: `/admin` (overview), `/admin/accounts`, `/admin/users`, `/admin/backtest`, `/admin/trades`

---

### 1.1 Backend: New Page Routes

**File: `app/dashboard/routes.py`**

Extract shared admin page guard to avoid duplication (currently 2 copies, would become 7 without this):
```python
def _require_admin_page(request: Request):
    """Shared guard for admin SSR pages. Returns (user, redirect) tuple."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return None, RedirectResponse(url="/accounts", status_code=302)
    return user, None
```

Add new SSR page routes (all admin-only, using shared guard):
```
GET /admin              → admin_overview.html   (System Overview)
GET /admin/accounts     → admin_accounts.html   (Account Management)
GET /admin/users        → admin_users.html       (User Management)
GET /admin/backtest     → admin_backtest.html     (Backtest Lab)
GET /admin/trades       → admin_trades.html       (Trade History) [NEW]
GET /admin/backtest/{id}→ backtest_report.html    (existing, unchanged)
```

**Acceptance**: All 5 routes respond 200 for admin users, redirect to `/accounts` for non-admin.

### 1.2 Backend: New API Endpoints

**File: `app/api/admin.py`** — add (all use existing `Depends(require_admin)`):

#### `GET /api/admin/trades?limit=100&offset=0&account_id=&side=`
- **Data source**: Query `orders` table (model: `app/models/order.py` → `Order`) across ALL accounts
- Bypass `get_owned_account` ownership check; use `require_admin` instead
- SQLAlchemy query: `select(Order).order_by(Order.update_time_ms.desc())` with optional filters
- **Pagination**: `limit` (default 100, max 500) + `offset` (default 0)
- **Response schema**: `{ trades: list[OrderResponse], total: int, limit: int, offset: int }`
- Apply `@limiter.limit()` consistent with existing admin endpoints

#### `GET /api/admin/performance`
- **Data source**: Computed from existing models
- **Open lots**: Query `Lot` model where `status='open'`, sum `invested_amount` for total invested USDT
- **24h trade volume**: Query `orders` WHERE `created_at > now() - interval '24h'`, sum `quote_qty`
- **Circuit breaker**: Count accounts where `circuit_breaker_tripped=True` from `TradingAccount`
- **Buy pause**: Count accounts grouped by buy_pause state from account health
- **Response schema**:
  ```json
  {
    "total_accounts": int, "active_accounts": int,
    "open_lots_count": int, "total_invested_usdt": float,
    "trade_volume_24h": float, "trade_count_24h": int,
    "circuit_breaker_tripped": int, "circuit_breaker_total": int,
    "buy_pause_active": int, "buy_pause_paused": int
  }
  ```

#### `GET /api/admin/accounts/{id}/summary`
- Quick account summary (position, lots, PnL)
- Reuse existing `TradingEngine.get_account_health()` logic

**Acceptance**: Each endpoint returns valid JSON with correct schema. Trades pagination works with offset/limit.

### 1.3 Frontend: New Templates

Create 5 admin sub-page templates (all extend `base.html`):

| Template | Content |
|----------|---------|
| `admin_overview.html` | KPI cards from `/api/admin/performance`, account health table from `/api/admin/overview` |
| `admin_accounts.html` | Full account table with inline status, start/stop actions, CB reset, buy-pause toggle |
| `admin_users.html` | User table + create form + role management |
| `admin_backtest.html` | Backtest config form + combo builder + history table |
| `admin_trades.html` | **[NEW]** Cross-account trade history with filters + pagination + CSV export |

### 1.4 Frontend: Sidebar Navigation Update

**File: `base.html`** — Add admin sub-nav items:
```
DASHBOARD
  Accounts

ADMINISTRATION (admin only)
  Overview        /admin
  Accounts        /admin/accounts
  Users           /admin/users
  Backtest        /admin/backtest
  Trade History   /admin/trades
```

**Fix active-link detection** — Replace `startsWith` with longest-match logic:
```javascript
(function() {
  var path = window.location.pathname;
  var best = null, bestLen = 0;
  document.querySelectorAll('.sidebar-link').forEach(function(link) {
    var href = link.getAttribute('href');
    if (path === href || (href !== '/' && path.startsWith(href + '/'))) {
      if (href.length > bestLen) { best = link; bestLen = href.length; }
    } else if (path === href) {
      if (href.length > bestLen) { best = link; bestLen = href.length; }
    }
  });
  if (best) best.classList.add('active');
})();
```

**Acceptance**: Only one sidebar link is highlighted at any given time. `/admin/accounts` highlights "Accounts" under Administration, NOT the overview link.

### 1.5 Frontend: JS Refactoring

#### Function-to-Template Mapping

| Function(s) | Location | Reason |
|-------------|----------|--------|
| **Stays in `main.js`** | | |
| `apiFetch`, `escapeHtml`, `fmt`, `showToast`, `getCsrfToken` | main.js (shared utilities) | Used across all pages |
| `_renderParamsHtml`, `_toggleDeps`, `_collectParamValues` | main.js (shared helpers) | Used by both account_detail combos AND backtest |
| `resetCircuitBreaker`, `toggleBuyPause` | main.js (shared actions) | Used by both account_detail AND admin_accounts |
| `logout` | main.js | Used globally |
| **Moves to inline `<script>`** | | |
| `loadAdminOverview` | `admin_overview.html` | Overview KPIs only |
| `loadAdminAccounts` | `admin_accounts.html` | Account table + actions |
| `loadAdminUsers`, `createUser`, `changeUserRole`, `resetUserPassword`, `toggleUserActive` | `admin_users.html` | User management only |
| `loadBtLogics`, `addBtCombo`, `removeBtCombo`, `renderBtComboParams`, `toggleBtDependentParams`, `_collectBtCombos`, `startBacktest`, `_startBacktestPolling`, `loadBacktestHistory`, `deleteBacktest` | `admin_backtest.html` | Backtest-only functions |
| Module vars: `_btBuyLogics`, `_btSellLogics`, `_btComboCount`, `_backtestPollTimer` | `admin_backtest.html` | Backtest state vars |

#### main.js Auto-Init Update

Remove the `path === '/admin'` block from the `DOMContentLoaded` handler in main.js (lines ~1526-1531). Each admin sub-page handles its own initialization via `{% block scripts %}` inline script, matching the `account_detail.html` pattern.

**Acceptance**: main.js no longer calls any admin-specific functions on load. Each admin sub-page initializes only what it needs.

---

## Phase 2: New Dashboard Features

### 2.1 Admin Overview (KPI Dashboard)

Fetch data from `GET /api/admin/performance` + existing `GET /api/admin/overview`.

KPI cards:
- **Total Accounts** (active / total) — from `overview.total_accounts`, `overview.active_traders`
- **Open Lots** (count + total invested USDT) — from `performance.open_lots_count`, `performance.total_invested_usdt`
- **24h Trade Volume** (buy + sell USDT total) — from `performance.trade_volume_24h`
- **Circuit Breaker Status** (N tripped / total) — from `performance.circuit_breaker_tripped`
- **Buy Pause Summary** (active vs paused) — from `performance.buy_pause_active`, `performance.buy_pause_paused`

Below KPIs: Account health table (same as current admin overview section, with colored left borders).

**Acceptance**: Overview page loads with real KPI data. All numbers match what individual account pages show when aggregated.

### 2.2 Trade History Page (NEW)

Cross-account trade log:
- **API**: `GET /api/admin/trades` (new endpoint from 1.2)
- **Filters**: Account dropdown, Side (BUY/SELL/ALL), offset-based pagination (prev/next buttons)
- **Columns**: Time, Account, Symbol, Side, Price, Qty, Quote Amount, Combo
- **CSV Export**: Client-side, build CSV from currently loaded trades and trigger download
- **Pagination**: Show "Page X" with prev/next, 100 rows per page

**Acceptance**: Trades load from all accounts. Filters narrow results correctly. CSV download works. Pagination navigates correctly.

---

## Phase 3: Toss Invest Theme Application

### 3.1 Design Token Migration

Replace current Dash UI tokens with Toss Invest palette:

**Dark Mode (default):**
```css
--primary: #3182f6        /* Toss Blue */
--primary-hover: #2272eb
--primary-light: rgba(49,130,246,0.12)
--bg: #121319             /* Toss dark root */
--bg-secondary: #1b1d25   /* Toss dark card */
--card-bg: #1b1d25
--card-border: rgba(255,255,255,0.06)
--sidebar-bg: #191f28     /* Toss Grey 900 */
--text: #ffffff
--text-secondary: #8b95a1  /* Toss Grey 500 */
--text-muted: #6b7684      /* Toss Grey 600 */
--success: #30b06e         /* Green for success states */
--danger: #f04452          /* Toss Red */
--warning: #ffab00
```

**Light Mode:**
```css
--bg: #f2f4f6              /* Toss Grey 100 */
--card-bg: #ffffff
--card-border: #e5e8eb     /* Toss Grey 200 */
--sidebar-bg: #ffffff
--text: #333d4b            /* Toss Grey 800 */
--text-secondary: #6b7684
--text-muted: #8b95a1
```

**Hardcoded values to update:**
- `.sidebar-brand-icon` gradient: `linear-gradient(135deg, #624bff, #8b5cf6)` → use Toss Blue gradient
- Any other hardcoded `#624bff` references in CSS → replace with `var(--primary)` or Toss Blue

### 3.2 Typography Update

```css
--font-family: 'Pretendard Variable', 'Inter', -apple-system, sans-serif;
```
- Swap Pretendard to primary position (Korean-first)
- Keep Inter for Latin fallback
- Font size: 14px base → 15px (Toss convention)
- Headings: tighter letter-spacing (-0.03em)

### 3.3 Component Style Updates

- **Border radius**: 16px for cards (Toss signature generous rounding)
- **Shadows**: Ultra-subtle navy-tint (`rgba(0, 27, 55, 0.10)`)
- **Buttons**: 8px radius, 48px height (large), 600 weight
- **Tables**: Ultra-light dividers (`#f2f4f6`), no heavy borders
- **Badges**: Full pill (9999px), soft background tints
- **Cards**: More padding (20-24px), cleaner borders
- **Skeleton loaders**: Add shimmer animation for loading states

### 3.4 PnL Color Convention

Keep international convention (green=up, red=down) since the existing codebase uses it.

### 3.5 Visual Verification with Playwright

After applying the theme:
1. Launch dev server
2. Use Playwright MCP to screenshot each page (login, accounts, account detail, admin sub-pages)
3. Compare visual output against Toss Invest reference
4. Iterate on spacing, colors, typography until matching

---

## Phase 4 (Future): System Logs Page

**Deferred** — requires new `system_events` database table and event emission infrastructure.

When ready to implement:
1. Create `system_events` table: `(id, event_type, severity, account_id, combo_id, message, payload_json, created_at)`
2. Add event emission in `account_trader.py`, `buy_pause_manager.py`, `account_state_manager.py`
3. Create `GET /api/admin/logs?limit=100&offset=0&level=&event_type=` endpoint
4. Create `admin_logs.html` template with severity filters, event type filters, auto-refresh
5. Add "System Logs" link to sidebar

---

## Implementation Order

1. **Step 1**: Extract admin page guard helper + create 5 new page routes + skeleton templates
   - *Acceptance*: All routes respond 200 for admin, redirect for non-admin
2. **Step 2**: Update sidebar navigation with admin sub-menu + fix active-link detection
   - *Acceptance*: Only one link highlighted at a time; correct link on each sub-page
3. **Step 3**: Implement `admin_overview.html` — extract health table + add KPI cards + `GET /api/admin/performance`
   - *Acceptance*: KPIs show real aggregated data; health table matches current admin page
4. **Step 4**: Implement `admin_accounts.html` — extract account table + actions from current admin.html
   - *Acceptance*: Same account table with start/stop/CB-reset/buy-pause actions working
5. **Step 5**: Implement `admin_users.html` — extract user management from current admin.html
   - *Acceptance*: User CRUD (create, role change, password reset, toggle active) all working
6. **Step 6**: Implement `admin_backtest.html` — extract backtest form + combo builder + history
   - *Acceptance*: Can configure & run backtest, view history, delete runs; combo builder works
7. **Step 7**: Create `admin_trades.html` + `GET /api/admin/trades` endpoint
   - *Acceptance*: Cross-account trades load with pagination; filters work; CSV export downloads
8. **Step 8**: Clean up — remove old `admin.html`, remove dead code from main.js auto-init block
   - *Acceptance*: No 404s on any admin route; old admin.html deleted; main.js has no orphan admin calls
9. **Step 9**: Apply Toss Invest theme to style.css (design tokens + typography + components)
   - *Acceptance*: Dark/light modes both work; no hardcoded old colors remain
10. **Step 10**: Apply `/frontend-design` skill for polish + Playwright MCP verification
    - *Acceptance*: Screenshots of all pages match Toss Invest aesthetic

---

## Files Modified / Created

### Modified:
- `app/dashboard/routes.py` — Shared admin guard + 5 new page routes
- `app/api/admin.py` — 3 new API endpoints (trades, performance, account summary)
- `app/dashboard/templates/base.html` — Sidebar admin sub-nav + active-link fix
- `app/dashboard/static/css/style.css` — Toss Invest theme + hardcoded color fixes
- `app/dashboard/static/js/main.js` — Remove admin functions (moved to templates) + remove auto-init block

### Created:
- `app/dashboard/templates/admin_overview.html`
- `app/dashboard/templates/admin_accounts.html`
- `app/dashboard/templates/admin_users.html`
- `app/dashboard/templates/admin_backtest.html`
- `app/dashboard/templates/admin_trades.html`

### Deleted (Step 8, after all sub-pages confirmed working):
- `app/dashboard/templates/admin.html` (replaced by 5 sub-pages)
