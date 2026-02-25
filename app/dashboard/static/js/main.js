/**
 * main.js - Core JavaScript for Crypto Multi-Trader Dashboard
 */

/* ============================================================
   Core Helpers
   ============================================================ */

/**
 * Wrapper around fetch that includes credentials and handles 401 redirects.
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<Response>}
 */
async function apiFetch(url, options = {}) {
  const defaults = { credentials: 'include' };
  const merged = { ...defaults, ...options };
  // Merge headers
  if (options.headers) {
    merged.headers = { ...options.headers };
  }

  const response = await fetch(url, merged);

  if (response.status === 401) {
    // Not authenticated - redirect to login
    window.location.href = '/login';
    throw new Error('Redirecting to login');
  }

  return response;
}

/**
 * Read CSRF token from meta tag or cookie.
 * @returns {string}
 */
function getCsrfToken() {
  // Try meta tag first
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;

  // Fall back to cookie
  const name = 'csrftoken';
  const cookies = document.cookie.split(';');
  for (const c of cookies) {
    const [k, v] = c.trim().split('=');
    if (k === name) return decodeURIComponent(v);
  }
  return '';
}

/**
 * Logout: POST to /api/auth/logout then redirect to /login.
 */
async function logout() {
  try {
    await apiFetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
  } catch (_) {
    // ignore errors, redirect anyway
  }
  window.location.href = '/login';
}

/* ============================================================
   Accounts Page
   ============================================================ */

/**
 * Fetch and render account cards on the /accounts page.
 */
async function loadAccounts() {
  const grid = document.getElementById('accounts-grid');
  if (!grid) return;

  try {
    const resp = await apiFetch('/api/accounts');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const accounts = await resp.json();

    if (!accounts.length) {
      grid.innerHTML = '<p class="loading-spinner">No accounts found. Add your first account.</p>';
      return;
    }

    grid.innerHTML = accounts.map(acct => {
      const isActive = acct.is_active;
      const badgeClass = isActive ? 'badge-success' : 'badge-danger';
      const badgeText = isActive ? 'Active' : 'Inactive';
      const cbTripped = acct.circuit_breaker_tripped;

      return `
        <div class="account-card" onclick="window.location.href='/accounts/${acct.id}'">
          <div class="account-card-label">${escapeHtml(acct.label || acct.id)}</div>
          <div class="account-card-symbol">${escapeHtml(acct.symbol || '')}</div>
          <div class="account-card-footer">
            <span class="status-badge ${badgeClass}">${badgeText}</span>
            ${cbTripped ? '<span class="status-badge badge-danger">CB Tripped</span>' : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    if (grid) grid.innerHTML = '<p class="error-text">Failed to load accounts: ' + escapeHtml(e.message) + '</p>';
  }
}

/* ============================================================
   Account Detail / Dashboard Page
   ============================================================ */

/**
 * Orchestrate loading all dashboard sections for an account.
 * @param {string} accountId
 */
async function loadDashboard(accountId) {
  await Promise.allSettled([
    loadPriceChart(accountId, 'price-chart'),
    loadAssetStatus(accountId),
    loadLots(accountId),
    loadTuneValues(accountId),
    loadCircuitBreaker(accountId),
  ]);
}

/**
 * Load price candles and trade event markers using LightweightCharts.
 * @param {string} accountId
 * @param {string} containerId
 */
async function loadPriceChart(accountId, containerId) {
  const container = document.getElementById(containerId);
  if (!container || typeof LightweightCharts === 'undefined') return;

  container.innerHTML = '';

  const chart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: '#1e293b' },
      textColor: '#e2e8f0',
    },
    grid: {
      vertLines: { color: '#334155' },
      horzLines: { color: '#334155' },
    },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: '#334155' },
    timeScale: { borderColor: '#334155', timeVisible: true, secondsVisible: false },
    width: container.clientWidth,
    height: container.clientHeight || 400,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#16a34a',
    downColor: '#dc2626',
    borderUpColor: '#16a34a',
    borderDownColor: '#dc2626',
    wickUpColor: '#16a34a',
    wickDownColor: '#dc2626',
  });

  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/price_candles');
    if (resp.ok) {
      const candles = await resp.json();
      candleSeries.setData(candles);
      chart.timeScale().fitContent();
    }
  } catch (e) {
    console.error('Failed to load candles', e);
  }

  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/trade_events');
    if (resp.ok) {
      const events = await resp.json();
      const markers = events
        .filter(e => e.time)
        .map(e => ({
          time: e.time,
          position: e.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: e.side === 'buy' ? '#16a34a' : '#dc2626',
          shape: e.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: e.side === 'buy' ? ('B ' + (e.price || '')) : ('S ' + (e.price || '')),
        }));
      if (markers.length) candleSeries.setMarkers(markers);
    }
  } catch (e) {
    console.error('Failed to load trade events', e);
  }

  // Responsive resize
  const resizeObs = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth });
  });
  resizeObs.observe(container);
}

/**
 * Fetch and render asset status panel.
 * @param {string} accountId
 */
async function loadAssetStatus(accountId) {
  const el = document.getElementById('asset-status');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/asset_status');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    el.innerHTML = `
      <div class="asset-card">
        <div class="asset-label">BTC Balance</div>
        <div class="asset-value">${fmt(data.btc_balance, 6)}</div>
      </div>
      <div class="asset-card">
        <div class="asset-label">USDT Balance</div>
        <div class="asset-value">${fmt(data.usdt_balance, 2)}</div>
      </div>
      <div class="asset-card">
        <div class="asset-label">Reserve Pool</div>
        <div class="asset-value">${fmt(data.reserve_pool_usdt, 2)} USDT</div>
        <div class="asset-sub">${data.reserve_pool_pct != null ? data.reserve_pool_pct + '%' : ''}</div>
      </div>
      <div class="asset-card">
        <div class="asset-label">Total Invested</div>
        <div class="asset-value">${fmt(data.total_invested_usdt, 2)} USDT</div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load asset status</p>';
  }
}

/* ============================================================
   LOT Table
   ============================================================ */

/** @type {Array} */
let _allLots = [];
let _currentLotFilter = 'all';

/**
 * Fetch lots and render lot table.
 * @param {string} accountId
 */
async function loadLots(accountId) {
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/lots');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    _allLots = await resp.json();
    _renderLots(_currentLotFilter);
  } catch (e) {
    const tbody = document.getElementById('lots-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="table-empty error-text">Failed to load lots: ' + escapeHtml(e.message) + '</td></tr>';
  }
}

/**
 * Filter and re-render the lot table.
 * @param {string} filter - 'all' | 'lot_stacking' | 'trend_buy'
 */
function filterLots(filter) {
  _currentLotFilter = filter;
  _renderLots(filter);
}

function _renderLots(filter) {
  // Update active tab UI
  document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === filter);
  });

  const filtered = filter === 'all' ? _allLots : _allLots.filter(l => l.strategy === filter);
  const tbody = document.getElementById('lots-tbody');
  if (!tbody) return;

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No lots found</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map((lot, i) => {
    const pnl = lot.pnl_pct;
    const pnlClass = pnl == null ? '' : (pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    return `<tr>
      <td>${i + 1}</td>
      <td><span class="strategy-badge">${escapeHtml(lot.strategy || '-')}</span></td>
      <td>${fmt(lot.buy_price, 2)}</td>
      <td>${fmt(lot.qty, 6)}</td>
      <td>${fmt(lot.cost_usdt, 2)}</td>
      <td>${fmt(lot.current_price, 2)}</td>
      <td class="${pnlClass}">${pnl != null ? pnl.toFixed(2) + '%' : '-'}</td>
      <td><span class="order-status">${escapeHtml(lot.sell_order_status || '-')}</span></td>
    </tr>`;
  }).join('');
}

/* ============================================================
   Tune Controls
   ============================================================ */

/**
 * Populate tune form with current values from API.
 * @param {string} accountId
 */
async function loadTuneValues(accountId) {
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/tune');
    if (!resp.ok) return;
    const data = await resp.json();
    const ls = data.lot_stacking || {};
    const tb = data.trend_buy || {};

    _setVal('ls-drop-pct', ls.drop_pct);
    _setVal('ls-tp-pct', ls.tp_pct);
    _setVal('ls-buy-usdt', ls.buy_usdt);
    _setVal('ls-prebuy-pct', ls.prebuy_pct);
    _setVal('ls-cancel-rebound-pct', ls.cancel_rebound_pct);
    _setVal('ls-recenter-pct', ls.recenter_pct);
    _setVal('ls-recenter-ema-n', ls.recenter_ema_n);
    const reEl = document.getElementById('ls-recenter-enabled');
    if (reEl && ls.recenter_enabled != null) reEl.value = ls.recenter_enabled ? 'true' : 'false';

    _setVal('tb-drop-pct', tb.drop_pct);
    _setVal('tb-tp-pct', tb.tp_pct);
    _setVal('tb-buy-usdt', tb.buy_usdt);
    _setVal('tb-step-pct', tb.step_pct);
    _setVal('tb-step-count', tb.step_count);
    _setVal('tb-above-base-pct', tb.above_base_pct);
  } catch (e) {
    console.error('Failed to load tune values', e);
  }
}

/**
 * Submit tune values for a strategy.
 * @param {string} accountId
 * @param {string} strategy - 'lot_stacking' | 'trend_buy'
 */
async function saveTuneValues(accountId, strategy) {
  let payload;
  if (strategy === 'lot_stacking') {
    payload = {
      strategy: 'lot_stacking',
      drop_pct: _getFloat('ls-drop-pct'),
      tp_pct: _getFloat('ls-tp-pct'),
      buy_usdt: _getFloat('ls-buy-usdt'),
      prebuy_pct: _getFloat('ls-prebuy-pct'),
      cancel_rebound_pct: _getFloat('ls-cancel-rebound-pct'),
      recenter_pct: _getFloat('ls-recenter-pct'),
      recenter_ema_n: _getInt('ls-recenter-ema-n'),
      recenter_enabled: (document.getElementById('ls-recenter-enabled') || {}).value === 'true',
    };
  } else {
    payload = {
      strategy: 'trend_buy',
      drop_pct: _getFloat('tb-drop-pct'),
      tp_pct: _getFloat('tb-tp-pct'),
      buy_usdt: _getFloat('tb-buy-usdt'),
      step_pct: _getFloat('tb-step-pct'),
      step_count: _getInt('tb-step-count'),
      above_base_pct: _getFloat('tb-above-base-pct'),
    };
  }

  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/tune', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      showToast('Settings saved', 'success');
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Save failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Circuit Breaker
   ============================================================ */

/**
 * Load circuit breaker status and render.
 * @param {string} accountId
 */
async function loadCircuitBreaker(accountId) {
  const el = document.getElementById('circuit-breaker-panel');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const tripped = !!data.circuit_breaker_tripped;
    el.innerHTML = `
      <div class="cb-status ${tripped ? 'cb-tripped' : 'cb-ok'}">
        <span class="cb-indicator"></span>
        <span class="cb-label">${tripped ? 'TRIPPED - Trading Halted' : 'Normal - Trading Active'}</span>
      </div>
      ${tripped ? `<button class="btn btn-danger" onclick="resetCircuitBreaker('${accountId}')">Reset Circuit Breaker</button>` : ''}
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load circuit breaker status</p>';
  }
}

/**
 * POST reset circuit breaker for an account.
 * @param {string} accountId
 */
async function resetCircuitBreaker(accountId) {
  if (!confirm('Reset circuit breaker? This will resume trading.')) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/reset-circuit-breaker', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('Circuit breaker reset', 'success');
      loadCircuitBreaker(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Reset failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Admin Page
   ============================================================ */

/**
 * Load admin overview (system health summary).
 */
async function loadAdminOverview() {
  const el = document.getElementById('admin-health');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/admin/overview');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    el.innerHTML = Object.entries(data).map(([key, val]) => `
      <div class="health-card">
        <div class="health-card-label">${escapeHtml(key.replace(/_/g, ' '))}</div>
        <div class="health-card-value">${escapeHtml(String(val))}</div>
      </div>
    `).join('');
  } catch (e) {
    if (el) el.innerHTML = '<p class="error-text">Failed to load system health</p>';
  }
}

/**
 * Load all accounts for admin table.
 */
async function loadAdminAccounts() {
  const tbody = document.getElementById('admin-accounts-tbody');
  if (!tbody) return;
  try {
    const resp = await apiFetch('/api/admin/accounts');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const accounts = await resp.json();
    if (!accounts.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No accounts</td></tr>';
      return;
    }
    tbody.innerHTML = accounts.map(acct => {
      const cbTripped = acct.circuit_breaker_tripped;
      return `<tr>
        <td><a href="/accounts/${acct.id}">${escapeHtml(acct.label || acct.id)}</a></td>
        <td>${escapeHtml(acct.symbol || '')}</td>
        <td>${escapeHtml(acct.owner_email || acct.user_id || '-')}</td>
        <td><span class="status-badge ${acct.is_active ? 'badge-success' : 'badge-neutral'}">${acct.is_active ? 'Yes' : 'No'}</span></td>
        <td>${escapeHtml(acct.health_status || '-')}</td>
        <td><span class="status-badge ${cbTripped ? 'badge-danger' : 'badge-success'}">${cbTripped ? 'Tripped' : 'OK'}</span></td>
        <td>
          ${cbTripped ? `<button class="btn btn-danger btn-sm" onclick="resetCircuitBreaker('${acct.id}')">Reset CB</button>` : ''}
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty error-text">Failed to load accounts</td></tr>';
  }
}

/**
 * Load users for admin table.
 */
async function loadAdminUsers() {
  const tbody = document.getElementById('admin-users-tbody');
  if (!tbody) return;
  try {
    const resp = await apiFetch('/api/admin/users');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const users = await resp.json();
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="table-empty">No users</td></tr>';
      return;
    }
    tbody.innerHTML = users.map(u => `
      <tr>
        <td>${escapeHtml(u.email || '-')}</td>
        <td>
          <select class="form-input" style="width:auto;" onchange="changeUserRole('${u.id}', this.value)">
            <option value="user" ${u.role === 'user' ? 'selected' : ''}>User</option>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option>
          </select>
        </td>
        <td>
          <span class="status-badge ${u.role === 'admin' ? 'badge-warning' : 'badge-neutral'}">${escapeHtml(u.role || 'user')}</span>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="3" class="table-empty error-text">Failed to load users</td></tr>';
  }
}

/**
 * Change a user's role.
 * @param {string} userId
 * @param {string} role
 */
async function changeUserRole(userId, role) {
  try {
    const resp = await apiFetch('/api/admin/users/' + userId + '/role', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ role }),
    });
    if (resp.ok) {
      showToast('Role updated', 'success');
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Update failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Backtest
   ============================================================ */

let _backtestPollTimer = null;

/**
 * Show/hide tune panels based on strategy checkbox state.
 * @param {string} which - 'lot' or 'trend'
 */
function toggleBtTunePanel(which) {
  if (which === 'lot') {
    const panel = document.getElementById('bt-tune-lot');
    if (panel) panel.style.display = document.getElementById('bt-strat-lot').checked ? '' : 'none';
  } else if (which === 'trend') {
    const panel = document.getElementById('bt-tune-trend');
    if (panel) panel.style.display = document.getElementById('bt-strat-trend').checked ? '' : 'none';
  }
}

/**
 * Collect strategy params from tune panels.
 * @returns {Object} e.g. { "lot_stacking": { ... }, "trend_buy": { ... } }
 */
function _collectBtStrategyParams() {
  const params = {};

  if (document.getElementById('bt-strat-lot').checked) {
    params.lot_stacking = {};
    const fields = {
      'bt-ls-drop-pct': 'drop_pct',
      'bt-ls-tp-pct': 'tp_pct',
      'bt-ls-buy-usdt': 'buy_usdt',
      'bt-ls-prebuy-pct': 'prebuy_pct',
      'bt-ls-cancel-rebound-pct': 'cancel_rebound_pct',
      'bt-ls-recenter-pct': 'recenter_pct',
      'bt-ls-recenter-ema-n': 'recenter_ema_n',
    };
    for (const [elId, key] of Object.entries(fields)) {
      const el = document.getElementById(elId);
      if (el && el.value !== '') params.lot_stacking[key] = parseFloat(el.value);
    }
    const reEl = document.getElementById('bt-ls-recenter-enabled');
    if (reEl) params.lot_stacking.recenter_enabled = reEl.value === 'true';
  }

  if (document.getElementById('bt-strat-trend').checked) {
    params.trend_buy = {};
    const fields = {
      'bt-tb-drop-pct': 'drop_pct',
      'bt-tb-tp-pct': 'tp_pct',
      'bt-tb-buy-usdt': 'buy_usdt',
      'bt-tb-enable-pct': 'enable_pct',
      'bt-tb-recenter-pct': 'recenter_pct',
      'bt-tb-step-pct': 'step_pct',
    };
    for (const [elId, key] of Object.entries(fields)) {
      const el = document.getElementById(elId);
      if (el && el.value !== '') params.trend_buy[key] = parseFloat(el.value);
    }
  }

  return params;
}

/**
 * Start a new backtest run.
 */
async function startBacktest() {
  const symbol = document.getElementById('bt-symbol').value;
  const initialUsdt = parseFloat(document.getElementById('bt-initial-usdt').value) || 10000;
  const startDate = document.getElementById('bt-start-date').value;
  const endDate = document.getElementById('bt-end-date').value;

  if (!startDate || !endDate) {
    showToast('Please select start and end dates', 'error');
    return;
  }

  const startTsMs = new Date(startDate).getTime();
  const endTsMs = new Date(endDate + 'T23:59:59').getTime();

  if (startTsMs >= endTsMs) {
    showToast('Start date must be before end date', 'error');
    return;
  }

  const strategies = [];
  if (document.getElementById('bt-strat-lot').checked) strategies.push('lot_stacking');
  if (document.getElementById('bt-strat-trend').checked) strategies.push('trend_buy');

  if (!strategies.length) {
    showToast('Select at least one strategy', 'error');
    return;
  }

  const strategyParams = _collectBtStrategyParams();

  const btn = document.getElementById('bt-run-btn');
  btn.disabled = true;
  btn.textContent = 'Starting...';

  try {
    const resp = await apiFetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({
        symbol,
        start_ts_ms: startTsMs,
        end_ts_ms: endTsMs,
        initial_usdt: initialUsdt,
        strategies,
        strategy_params: strategyParams,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to start backtest');
    }

    const data = await resp.json();
    showToast('Backtest started', 'success');

    // Start polling
    _startBacktestPolling(data.id);
    loadBacktestHistory();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
  }
}

/**
 * Poll backtest status until completed/failed.
 * @param {string} runId
 */
function _startBacktestPolling(runId) {
  if (_backtestPollTimer) clearInterval(_backtestPollTimer);

  _backtestPollTimer = setInterval(async () => {
    try {
      const resp = await apiFetch('/api/backtest/' + runId + '/status');
      if (!resp.ok) return;
      const data = await resp.json();

      if (data.status === 'COMPLETED' || data.status === 'FAILED') {
        clearInterval(_backtestPollTimer);
        _backtestPollTimer = null;
        loadBacktestHistory();
        if (data.status === 'COMPLETED') {
          showToast('Backtest completed!', 'success');
        } else {
          showToast('Backtest failed: ' + (data.error_message || 'Unknown error'), 'error');
        }
      }
    } catch (e) {
      // ignore polling errors
    }
  }, 2000);
}

/**
 * Load backtest history table.
 */
async function loadBacktestHistory() {
  const tbody = document.getElementById('backtest-history-tbody');
  if (!tbody) return;

  try {
    const resp = await apiFetch('/api/backtest/list');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const runs = await resp.json();

    if (!runs.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No backtests yet</td></tr>';
      return;
    }

    tbody.innerHTML = runs.map(r => {
      const created = new Date(r.created_at).toLocaleString();
      const startD = new Date(r.start_ts_ms).toLocaleDateString();
      const endD = new Date(r.end_ts_ms).toLocaleDateString();
      const strats = r.strategies.join(', ');
      const pnlClass = r.pnl_pct != null ? (r.pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative') : '';
      const pnlText = r.pnl_pct != null ? r.pnl_pct.toFixed(2) + '%' : '-';

      let statusBadge = '';
      if (r.status === 'COMPLETED') statusBadge = '<span class="status-badge badge-success">Completed</span>';
      else if (r.status === 'RUNNING') statusBadge = '<span class="status-badge badge-warning">Running</span>';
      else if (r.status === 'PENDING') statusBadge = '<span class="status-badge badge-neutral">Pending</span>';
      else statusBadge = '<span class="status-badge badge-danger">Failed</span>';

      let actions = '';
      if (r.status === 'COMPLETED') {
        actions = `<a href="/admin/backtest/${r.id}" class="btn btn-outline btn-sm">View Report</a>`;
      }
      if (r.status !== 'RUNNING' && r.status !== 'PENDING') {
        actions += ` <button class="btn btn-danger btn-sm" onclick="deleteBacktest('${r.id}')">Delete</button>`;
      }

      return `<tr>
        <td>${escapeHtml(created)}</td>
        <td>${escapeHtml(r.symbol)}</td>
        <td>${escapeHtml(strats)}</td>
        <td>${escapeHtml(startD)} ~ ${escapeHtml(endD)}</td>
        <td class="${pnlClass}">${pnlText}</td>
        <td>${statusBadge}</td>
        <td>${actions}</td>
      </tr>`;
    }).join('');

    // Auto-poll if any are still running/pending
    const hasActive = runs.some(r => r.status === 'RUNNING' || r.status === 'PENDING');
    if (hasActive && !_backtestPollTimer) {
      const activeRun = runs.find(r => r.status === 'RUNNING' || r.status === 'PENDING');
      if (activeRun) _startBacktestPolling(activeRun.id);
    }
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty error-text">Failed to load history</td></tr>';
  }
}

/**
 * Delete a backtest run.
 * @param {string} runId
 */
async function deleteBacktest(runId) {
  if (!confirm('Delete this backtest?')) return;
  try {
    const resp = await apiFetch('/api/backtest/' + runId, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('Backtest deleted', 'success');
      loadBacktestHistory();
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Delete failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Utility Functions
   ============================================================ */

/** Format a number with fixed decimal places, returns '-' for null/undefined */
function fmt(val, decimals) {
  if (val == null) return '-';
  return Number(val).toFixed(decimals);
}

/** Escape HTML to prevent XSS */
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Show a toast notification */
function showToast(msg, type) {
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + (type || 'success');
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 2700);
}

function _setVal(id, val) {
  const el = document.getElementById(id);
  if (el && val != null) el.value = val;
}
function _getFloat(id) {
  const el = document.getElementById(id);
  if (!el || el.value === '') return null;
  return parseFloat(el.value);
}
function _getInt(id) {
  const el = document.getElementById(id);
  if (!el || el.value === '') return null;
  return parseInt(el.value, 10);
}

/* ============================================================
   Auto-initialize on page load
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;

  if (path === '/accounts') {
    loadAccounts();
  } else if (path.startsWith('/accounts/')) {
    // Dashboard page initializes via inline script in account_detail.html
  } else if (path === '/admin') {
    loadAdminOverview();
    loadAdminAccounts();
    loadAdminUsers();
    loadBacktestHistory();
  }
});
