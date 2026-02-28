/**
 * main.js - Core JavaScript for Crypto Multi-Trader Dashboard
 * 전략 tune 패널과 lot 필터 탭은 tunable_params 메타데이터 기반 동적 렌더링
 */

/* ============================================================
   Theme Toggle
   ============================================================ */

function getTheme() {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

function toggleTheme() {
  const html = document.documentElement;
  html.classList.add('theme-transition');
  const next = getTheme() === 'dark' ? 'light' : 'dark';
  if (next === 'light') {
    html.setAttribute('data-theme', 'light');
  } else {
    html.removeAttribute('data-theme');
  }
  localStorage.setItem('theme', next);
  // Remove transition class after animation completes
  setTimeout(() => html.classList.remove('theme-transition'), 350);
}

function getChartTheme() {
  const isLight = getTheme() === 'light';
  return {
    layout: {
      background: { color: isLight ? '#FFFFFF' : '#1e293b' },
      textColor: isLight ? '#4E5968' : '#e2e8f0',
    },
    grid: {
      vertLines: { color: isLight ? '#F0F1F3' : '#334155' },
      horzLines: { color: isLight ? '#F0F1F3' : '#334155' },
    },
    rightPriceScale: { borderColor: isLight ? '#E5E7EB' : '#334155' },
    timeScale: { borderColor: isLight ? '#E5E7EB' : '#334155', timeVisible: true, secondsVisible: false },
  };
}

/* ============================================================
   Core Helpers
   ============================================================ */

async function apiFetch(url, options = {}) {
  const defaults = { credentials: 'include' };
  const merged = { ...defaults, ...options };
  if (options.headers) {
    merged.headers = { ...options.headers };
  }
  const response = await fetch(url, merged);
  if (response.status === 401) {
    window.location.href = '/login';
    throw new Error('Redirecting to login');
  }
  return response;
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
  const cookies = document.cookie.split(';');
  for (const c of cookies) {
    const [k, v] = c.trim().split('=');
    if (k === 'csrftoken') return decodeURIComponent(v);
  }
  return '';
}

async function logout() {
  try {
    await apiFetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
  } catch (_) {}
  window.location.href = '/login';
}

async function handleLogin() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errorEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');

  if (!email || !password) {
    errorEl.textContent = 'Please enter email and password.';
    errorEl.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Signing in...';
  errorEl.style.display = 'none';

  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify({ email, password }),
    });
    if (resp.ok) {
      window.location.href = '/accounts';
      return;
    }
    const err = await resp.json().catch(() => ({}));
    errorEl.textContent = err.detail || 'Login failed. Please check your credentials.';
    errorEl.style.display = 'block';
  } catch (e) {
    errorEl.textContent = 'Network error. Please try again.';
    errorEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sign In';
  }
}

/* ============================================================
   Accounts Page
   ============================================================ */

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
      const buyPauseBadge = acct.buy_pause_state === 'PAUSED'
        ? '<span class="status-badge badge-buy-paused">Buy Paused</span>'
        : acct.buy_pause_state === 'THROTTLED'
        ? '<span class="status-badge badge-buy-throttled">Buy Throttled</span>'
        : '';
      const symbolUpper = (acct.symbol || '').toUpperCase();
      const isBTC = symbolUpper.includes('BTC');
      const symbolIcon = isBTC
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.5 8H14a2 2 0 0 1 0 4h-4.5m0-4v8m0-4H14a2 2 0 0 1 0 4H9.5m2-10v2m0 8v2"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';
      return `
        <div class="account-card" onclick="window.location.href='/accounts/${acct.id}'">
          <div class="account-card-top">
            <div class="account-card-icon">${symbolIcon}</div>
            <div>
              <div class="account-card-label">${escapeHtml(acct.label || acct.id)}</div>
              <div class="account-card-symbol">${escapeHtml(acct.symbol || '')}</div>
            </div>
          </div>
          <div class="account-card-footer">
            <span class="status-badge ${badgeClass}">${badgeText}</span>
            ${cbTripped ? '<span class="status-badge badge-danger">CB Tripped</span>' : ''}
            ${buyPauseBadge}
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
 * 계정 대시보드 전체 로드 (account_detail.html에서 호출)
 */
async function loadAccountDashboard(accountId) {
  // 헤더 설정
  try {
    const resp = await apiFetch('/api/accounts/' + accountId);
    if (resp.ok) {
      const acct = await resp.json();
      const label = document.getElementById('account-label');
      if (label) label.textContent = acct.name || accountId;
      const badge = document.getElementById('account-status-badge');
      if (badge) {
        badge.textContent = acct.is_active ? 'Active' : 'Inactive';
        badge.className = 'status-badge ' + (acct.is_active ? 'badge-success' : 'badge-danger');
      }
      const sym = document.getElementById('chart-symbol');
      if (sym) sym.textContent = acct.symbol || '';
    }
  } catch (e) {
    console.error('Failed to load account info', e);
  }

  // 모든 섹션 병렬 로드
  await Promise.allSettled([
    loadPriceChart(accountId, 'price-chart'),
    loadAssetStatus(accountId),
    loadCombosAndLots(accountId),
    loadCircuitBreaker(accountId),
    loadBuyPauseStatus(accountId),
  ]);
}

async function loadPriceChart(accountId, containerId) {
  const container = document.getElementById(containerId);
  if (!container || typeof LightweightCharts === 'undefined') return;
  container.innerHTML = '';

  const theme = getChartTheme();
  const chart = LightweightCharts.createChart(container, {
    ...theme,
    crosshair: { mode: 1 },
    width: container.clientWidth,
    height: container.clientHeight || 400,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#16a34a', downColor: '#dc2626',
    borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
  });

  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/price_candles');
    if (resp.ok) {
      const candles = await resp.json();
      candleSeries.setData(candles);
      chart.timeScale().fitContent();
    }
  } catch (e) { console.error('Failed to load candles', e); }

  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/trade_events');
    if (resp.ok) {
      const events = await resp.json();
      const markers = events.filter(e => e.time).map(e => ({
        time: e.time,
        position: e.side === 'buy' ? 'belowBar' : 'aboveBar',
        color: e.side === 'buy' ? '#16a34a' : '#dc2626',
        shape: e.side === 'buy' ? 'arrowUp' : 'arrowDown',
        text: e.side === 'buy' ? ('B ' + (e.price || '')) : ('S ' + (e.price || '')),
      }));
      if (markers.length) candleSeries.setMarkers(markers);
    }
  } catch (e) { console.error('Failed to load trade events', e); }

  const resizeObs = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth });
  });
  resizeObs.observe(container);
}

async function loadAssetStatus(accountId) {
  const el = document.getElementById('asset-status');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/asset_status');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    el.innerHTML = `
      <div class="asset-card">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-warning">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.5 8H14a2 2 0 0 1 0 4h-4.5m0-4v8m0-4H14a2 2 0 0 1 0 4H9.5m2-10v2m0 8v2"/></svg>
          </div>
          <div>
            <div class="asset-label">BTC Balance</div>
            <div class="asset-value">${fmt(data.btc_balance, 6)}</div>
          </div>
        </div>
      </div>
      <div class="asset-card">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-success">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
          </div>
          <div>
            <div class="asset-label">USDT Balance</div>
            <div class="asset-value">${fmt(data.usdt_balance, 2)}</div>
          </div>
        </div>
      </div>
      <div class="asset-card">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-info">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </div>
          <div>
            <div class="asset-label">Reserve Pool</div>
            <div class="asset-value">${fmt(data.reserve_pool_usdt, 2)} USDT</div>
            <div class="asset-sub">${data.reserve_pool_pct != null ? data.reserve_pool_pct + '%' : ''}</div>
          </div>
        </div>
      </div>
      <div class="asset-card earnings-card ${(data.pending_earnings_usdt || 0) > 0 ? 'has-earnings' : ''}">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-orange">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>
          </div>
          <div>
            <div class="asset-label">적립금 (Pending)</div>
            <div class="asset-value">${fmt(data.pending_earnings_usdt || 0, 2)} USDT</div>
          </div>
        </div>
        <button class="btn btn-sm btn-approve" style="margin-top:0.5rem;"
                onclick="openEarningsModal('${accountId}')"
                ${(data.pending_earnings_usdt || 0) <= 0 ? 'disabled' : ''}>
          Reserve 추가
        </button>
      </div>
      <div class="asset-card">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>
          </div>
          <div>
            <div class="asset-label">Total Invested</div>
            <div class="asset-value">${fmt(data.total_invested_usdt, 2)} USDT</div>
          </div>
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load asset status</p>';
  }
}

/* ============================================================
   Earnings Approval Modal
   ============================================================ */

let _earningsAccountId = null;
let _earningsTotal = 0;

function openEarningsModal(accountId) {
  _earningsAccountId = accountId;
  let modal = document.getElementById('earnings-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'earnings-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal-content">
        <h3>적립금 → Reserve Pool</h3>
        <div class="earnings-total">
          적립금: <strong id="earnings-total-display">0</strong> USDT
        </div>
        <div class="earnings-slider-wrap">
          <label>Reserve 비율: <span id="earnings-pct-label">100</span>%</label>
          <input type="range" id="earnings-slider" min="0" max="100" value="100"
                 oninput="updateEarningsPreview(this.value)">
        </div>
        <div class="earnings-quick-buttons">
          <button class="btn btn-sm" onclick="updateEarningsPreview(100)">100% Reserve</button>
          <button class="btn btn-sm" onclick="updateEarningsPreview(50)">50 / 50</button>
          <button class="btn btn-sm" onclick="updateEarningsPreview(0)">100% 유동</button>
        </div>
        <div id="earnings-preview" class="earnings-preview"></div>
        <p class="book-value-notice">
          * Reserve BTC 수량은 현재가 기준 장부상 환산값이며, 실제 거래가와 차이가 있을 수 있습니다.
        </p>
        <div class="modal-actions">
          <button class="btn btn-primary" onclick="submitEarningsApproval()">확인</button>
          <button class="btn" onclick="closeEarningsModal()">취소</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }
  // Fetch latest pending earnings
  apiFetch('/api/dashboard/' + accountId + '/pending_earnings')
    .then(r => r.json())
    .then(data => {
      _earningsTotal = data.pending_earnings_usdt || 0;
      document.getElementById('earnings-total-display').textContent = fmt(_earningsTotal, 2);
      updateEarningsPreview(100);
    });
  modal.style.display = 'flex';
}

function closeEarningsModal() {
  const modal = document.getElementById('earnings-modal');
  if (modal) modal.style.display = 'none';
}

function updateEarningsPreview(pct) {
  pct = parseFloat(pct);
  const slider = document.getElementById('earnings-slider');
  if (slider) slider.value = pct;
  document.getElementById('earnings-pct-label').textContent = pct;

  const toReserve = _earningsTotal * (pct / 100);
  const toLiquid = _earningsTotal - toReserve;
  const preview = document.getElementById('earnings-preview');
  if (preview) {
    preview.innerHTML = `
      <div>Reserve에 추가: <strong>${fmt(toReserve, 2)} USDT</strong></div>
      <div>유동 전환: <strong>${fmt(toLiquid, 2)} USDT</strong></div>
    `;
  }
}

async function submitEarningsApproval() {
  const pct = parseFloat(document.getElementById('earnings-slider').value);
  try {
    const resp = await apiFetch('/api/dashboard/' + _earningsAccountId + '/approve_earnings', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify({ reserve_pct: pct }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const result = await resp.json();
    alert('Reserve에 ' + fmt(result.to_reserve_usdt, 2) + ' USDT 추가 완료\n유동 전환: ' + fmt(result.to_liquid_usdt, 2) + ' USDT');
    closeEarningsModal();
    loadAssetStatus(_earningsAccountId);
  } catch (e) {
    alert('승인 실패: ' + e.message);
  }
}

/* ============================================================
   LOT Table (동적 필터 탭)
   ============================================================ */

let _allLots = [];
let _currentLotFilter = 'all';

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

function filterLots(filter) {
  _currentLotFilter = filter;
  _renderLots(filter);
}

function _renderLots(filter) {
  document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === filter);
  });
  const filtered = filter === 'all' ? _allLots : _allLots.filter(l => l.combo_id === filter);
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
   Shared Combo Param Utilities (계정 + 백테스트 공용)
   ============================================================ */

/**
 * 파라미터 입력 HTML 생성 (공용)
 * @param {Object} opts
 * @param {Object} opts.tunableParams - 파라미터 메타데이터
 * @param {Object} opts.defaults - 기본값
 * @param {Object} opts.current - 현재값 (편집 시)
 * @param {string} opts.side - 'buy' | 'sell'
 * @param {Function} opts.inputId - (key) => input element id
 * @param {Function} opts.dataAttrs - (key) => data-* 속성 문자열
 * @param {Function} opts.onToggle - () => onchange 핸들러 문자열
 * @param {boolean} [opts.showUnit=true] - 단위 표시 여부
 * @param {string} [opts.extraHtml=''] - 추가 HTML (예: 계산기 버튼)
 */
function _renderParamsHtml(opts) {
  const { tunableParams, defaults, current, side, inputId, dataAttrs, onToggle, showUnit = true, extraHtml = '' } = opts;

  let html = '<div class="tune-grid">';
  for (const [key, pm] of Object.entries(tunableParams)) {
    const val = current[key] ?? defaults[key] ?? '';
    const title = pm.title || key;
    const unit = (showUnit && pm.unit) ? ` <span class="form-hint">(${escapeHtml(pm.unit)})</span>` : '';
    const id = inputId(key);
    const attrs = dataAttrs(key);

    // visible_when: 조건부 표시
    let groupAttrs = '';
    let groupStyle = '';
    if (pm.visible_when) {
      const depKey = Object.keys(pm.visible_when)[0];
      const depVals = Array.isArray(pm.visible_when[depKey]) ? pm.visible_when[depKey] : [pm.visible_when[depKey]];
      const curDepVal = String(current[depKey] ?? defaults[depKey] ?? '');
      const visible = depVals.includes(curDepVal);
      groupAttrs = ` data-depends-on="${escapeHtml(depKey)}" data-depends-values="${depVals.map(v => escapeHtml(String(v))).join(',')}"`;
      groupStyle = visible ? '' : ' style="display:none"';
    }

    html += `<div class="form-group"${groupAttrs}${groupStyle}>`;
    html += `<label class="form-label">${escapeHtml(title)}${unit}</label>`;

    if (pm.type === 'bool') {
      const isTrue = val === true || val === 'true';
      html += `<select id="${id}" class="form-input" ${attrs} data-param="${key}" data-type="bool">
        <option value="true" ${isTrue ? 'selected' : ''}>Yes</option>
        <option value="false" ${!isTrue ? 'selected' : ''}>No</option>
      </select>`;
    } else if (pm.type === 'select') {
      const onchangeAttr = pm.visible_when ? '' : ` onchange="${onToggle()}"`;
      html += `<select id="${id}" class="form-input" ${attrs} data-param="${key}" data-type="select"${onchangeAttr}>`;
      for (const opt of (pm.options || [])) {
        const ov = typeof opt === 'object' ? opt.value : opt;
        const ol = typeof opt === 'object' ? opt.label : opt;
        html += `<option value="${escapeHtml(String(ov))}" ${val === ov ? 'selected' : ''}>${escapeHtml(String(ol))}</option>`;
      }
      html += '</select>';
    } else {
      const step = pm.step || (pm.type === 'int' ? 1 : 0.001);
      html += `<input type="number" step="${step}" id="${id}" class="form-input" value="${val}" ${attrs} data-param="${key}" data-type="${pm.type || 'float'}">`;
    }
    html += '</div>';
  }
  html += '</div>';
  html += extraHtml;
  return html;
}

/**
 * 의존 파라미터 표시/숨김 토글 (공용)
 */
function _toggleDeps(container, key, val) {
  if (!container) return;
  container.querySelectorAll(`[data-depends-on="${key}"]`).forEach(group => {
    const allowed = group.dataset.dependsValues.split(',');
    group.style.display = allowed.includes(val) ? '' : 'none';
  });
}

/**
 * 입력값 수집 (공용)
 * @param {string} selector - CSS 셀렉터
 * @returns {Object} 파라미터 객체
 */
function _collectParamValues(selector) {
  const params = {};
  document.querySelectorAll(selector).forEach(input => {
    const key = input.dataset.param;
    const type = input.dataset.type;
    if (type === 'bool') params[key] = input.value === 'true';
    else if (type === 'int') { if (input.value !== '') params[key] = parseInt(input.value, 10); }
    else if (type === 'select') params[key] = input.value;
    else { if (input.value !== '') params[key] = parseFloat(input.value); }
  });
  return params;
}

/* ============================================================
   Trading Combos (CRUD + 동적 파라미터 렌더링)
   ============================================================ */

let _combos = [];
let _buyLogics = [];
let _sellLogics = [];

async function loadCombosAndLots(accountId) {
  try {
    const [combosResp, buyResp, sellResp] = await Promise.all([
      apiFetch('/api/accounts/' + accountId + '/combos'),
      apiFetch('/api/buy-logics'),
      apiFetch('/api/sell-logics'),
    ]);
    if (combosResp.ok) _combos = await combosResp.json();
    if (buyResp.ok) _buyLogics = await buyResp.json();
    if (sellResp.ok) _sellLogics = await sellResp.json();

    _renderCombosPanel(accountId);
    _buildComboLotFilterTabs();
    await loadLots(accountId);
  } catch (e) {
    const el = document.getElementById('combos-panel');
    if (el) el.innerHTML = '<p class="error-text">Failed to load combos: ' + escapeHtml(e.message) + '</p>';
  }
}

function _renderCombosPanel(accountId) {
  const container = document.getElementById('combos-panel');
  if (!container) return;

  if (!_combos.length) {
    container.innerHTML = '<p style="color:#94a3b8;">No combos configured. Click "+ New Combo" to create one.</p>';
    return;
  }

  container.innerHTML = _combos.map(combo => {
    const buyMeta = _buyLogics.find(b => b.name === combo.buy_logic_name) || {};
    const sellMeta = _sellLogics.find(s => s.name === combo.sell_logic_name) || {};
    const enabledClass = combo.is_enabled ? 'badge-success' : 'badge-neutral';
    const enabledText = combo.is_enabled ? 'ON' : 'OFF';

    let paramsHtml = '';
    const bpKeys = Object.keys(combo.buy_params || {}).filter(k => !k.startsWith('_'));
    const spKeys = Object.keys(combo.sell_params || {});
    if (bpKeys.length) paramsHtml += bpKeys.map(k => '<span>' + escapeHtml(k) + ': <strong>' + escapeHtml(String(combo.buy_params[k])) + '</strong></span>').join('');
    if (spKeys.length) paramsHtml += spKeys.map(k => '<span>' + escapeHtml(k) + ': <strong>' + escapeHtml(String(combo.sell_params[k])) + '</strong></span>').join('');

    return `<div class="combo-card">
      <div class="combo-card-header">
        <div class="combo-card-name">
          ${escapeHtml(combo.name)}
          <span class="status-badge ${enabledClass}">${enabledText}</span>
        </div>
        <div class="combo-card-actions">
          <button class="btn btn-outline btn-sm" onclick="editCombo('${combo.id}')">Edit</button>
          <button class="btn btn-outline btn-sm" onclick="toggleCombo('${accountId}','${combo.id}',${combo.is_enabled})">${combo.is_enabled ? 'Disable' : 'Enable'}</button>
          <button class="btn btn-danger btn-sm" onclick="deleteCombo('${accountId}','${combo.id}')">Delete</button>
        </div>
      </div>
      <div class="combo-card-body">
        <div class="combo-card-logic">
          <span class="combo-logic-pill combo-logic-buy"><span class="combo-logic-dot"></span> Buy: ${escapeHtml(buyMeta.display_name || combo.buy_logic_name)}</span>
          <span class="combo-logic-pill combo-logic-sell"><span class="combo-logic-dot"></span> Sell: ${escapeHtml(sellMeta.display_name || combo.sell_logic_name)}</span>
        </div>
        ${paramsHtml ? '<div class="combo-card-params">' + paramsHtml + '</div>' : ''}
      </div>
    </div>`;
  }).join('');
}

function _buildComboLotFilterTabs() {
  const container = document.getElementById('lot-filter-tabs');
  if (!container) return;
  let html = '<button class="filter-tab active" data-filter="all" onclick="filterLots(\'all\')">All</button>';
  for (const combo of _combos) {
    html += `<button class="filter-tab" data-filter="${combo.id}" onclick="filterLots('${combo.id}')">${escapeHtml(combo.name)}</button>`;
  }
  container.innerHTML = html;
}

/* ============================================================
   Combo Wizard — 5-Step Navigation
   ============================================================ */
let _comboWizardStep = 1;
const _WIZARD_STEP_COUNT = 5;
const _WIZARD_CHECK_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5L6.5 11.5L12.5 5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function _comboWizardUpdateUI(direction) {
  const stepper = document.getElementById('combo-wizard-stepper');
  const isEdit = !!document.getElementById('combo-edit-id').value;

  // Update step circles & connectors
  stepper.querySelectorAll('.wizard-step').forEach(el => {
    const s = parseInt(el.dataset.step);
    el.classList.remove('active', 'completed');
    const circle = el.querySelector('.wizard-step-circle');
    if (s === _comboWizardStep) {
      el.classList.add('active');
      circle.innerHTML = s;
    } else if (s < _comboWizardStep) {
      el.classList.add('completed');
      circle.innerHTML = _WIZARD_CHECK_SVG;
    } else {
      circle.innerHTML = s;
    }
  });
  stepper.querySelectorAll('.wizard-connector').forEach(el => {
    const after = parseInt(el.dataset.after);
    el.classList.toggle('completed', after < _comboWizardStep);
  });

  // Update panels
  document.querySelectorAll('.wizard-panel').forEach(el => {
    const p = parseInt(el.dataset.panel);
    el.classList.remove('active', 'slide-back');
    if (p === _comboWizardStep) {
      el.classList.add('active');
      if (direction === 'backward') el.classList.add('slide-back');
    }
  });

  // Update nav buttons
  const prevBtn = document.querySelector('.btn-wizard-prev');
  const nextBtn = document.querySelector('.btn-wizard-next');
  const saveBtn = document.querySelector('.btn-wizard-save');
  prevBtn.style.display = _comboWizardStep === 1 ? 'none' : '';
  nextBtn.style.display = _comboWizardStep === _WIZARD_STEP_COUNT ? 'none' : '';
  saveBtn.style.display = _comboWizardStep === _WIZARD_STEP_COUNT ? '' : 'none';

  // Edit-mode: hide logic selects in steps 2 & 4, show reapply in step 5
  if (isEdit) {
    document.getElementById('combo-buy-logic-group').style.display = 'none';
    document.getElementById('combo-sell-logic-group').style.display = 'none';
    document.getElementById('combo-reapply-group').style.display =
      _comboWizardStep === _WIZARD_STEP_COUNT ? '' : 'none';
  }
}

function _validateWizardStep(step) {
  if (step === 1) {
    const name = document.getElementById('combo-name').value.trim();
    if (!name) { showToast('이름을 입력해주세요', 'error'); return false; }
  }
  return true;
}

function comboWizardNext() {
  if (!_validateWizardStep(_comboWizardStep)) return;
  if (_comboWizardStep < _WIZARD_STEP_COUNT) {
    _comboWizardStep++;
    _comboWizardUpdateUI('forward');
  }
}

function comboWizardPrev() {
  if (_comboWizardStep > 1) {
    _comboWizardStep--;
    _comboWizardUpdateUI('backward');
  }
}

function comboWizardGoTo(target) {
  if (target === _comboWizardStep) return;
  if (target < _comboWizardStep) {
    // Going backward — always allowed
    _comboWizardStep = target;
    _comboWizardUpdateUI('backward');
  } else {
    // Going forward — validate each step in between
    for (let s = _comboWizardStep; s < target; s++) {
      if (!_validateWizardStep(s)) return;
    }
    _comboWizardStep = target;
    _comboWizardUpdateUI('forward');
  }
}

function showCreateComboModal() {
  document.getElementById('combo-modal-title').textContent = 'New Combo';
  document.getElementById('combo-edit-id').value = '';
  document.getElementById('combo-name').value = '';
  _populateLogicSelects();
  _populateReferenceSelect('');
  renderComboConditionParams('buy');
  renderComboConditionParams('sell');
  document.getElementById('combo-buy-logic-group').style.display = '';
  document.getElementById('combo-sell-logic-group').style.display = '';
  document.getElementById('combo-reapply-group').style.display = 'none';
  document.getElementById('combo-reapply-orders').checked = false;
  document.getElementById('combo-modal').style.display = 'flex';
  _comboWizardStep = 1;
  _comboWizardUpdateUI('forward');
}

function editCombo(comboId) {
  const combo = _combos.find(c => c.id === comboId);
  if (!combo) return;
  document.getElementById('combo-modal-title').textContent = 'Edit Combo';
  document.getElementById('combo-edit-id').value = comboId;
  document.getElementById('combo-name').value = combo.name;
  _populateLogicSelects(combo.buy_logic_name, combo.sell_logic_name);
  _populateReferenceSelect(combo.reference_combo_id || '', comboId);
  // Logic type cannot be changed on edit
  document.getElementById('combo-buy-logic-group').style.display = 'none';
  document.getElementById('combo-sell-logic-group').style.display = 'none';
  renderComboConditionParams('buy', combo.buy_params);
  renderComboConditionParams('sell', combo.sell_params);
  document.getElementById('combo-reapply-group').style.display = '';
  document.getElementById('combo-reapply-orders').checked = false;
  document.getElementById('combo-modal').style.display = 'flex';
  _comboWizardStep = 1;
  _comboWizardUpdateUI('forward');
}

function closeComboModal() {
  document.getElementById('combo-modal').style.display = 'none';
}

function _populateLogicSelects(buyVal, sellVal) {
  const buySelect = document.getElementById('combo-buy-logic');
  const sellSelect = document.getElementById('combo-sell-logic');
  buySelect.innerHTML = _buyLogics.map(b => `<option value="${b.name}" ${b.name === buyVal ? 'selected' : ''}>${escapeHtml(b.display_name)}</option>`).join('');
  sellSelect.innerHTML = _sellLogics.map(s => `<option value="${s.name}" ${s.name === sellVal ? 'selected' : ''}>${escapeHtml(s.display_name)}</option>`).join('');
}

function _populateReferenceSelect(currentRef, excludeId) {
  const select = document.getElementById('combo-reference');
  let html = '<option value="">None</option>';
  for (const c of _combos) {
    if (c.id === excludeId) continue;
    html += `<option value="${c.id}" ${c.id === currentRef ? 'selected' : ''}>${escapeHtml(c.name)}</option>`;
  }
  select.innerHTML = html;
}

function renderComboConditionParams(side, existingParams) {
  const logicName = document.getElementById(side === 'buy' ? 'combo-buy-logic' : 'combo-sell-logic').value;
  const logics = side === 'buy' ? _buyLogics : _sellLogics;
  const meta = logics.find(l => l.name === logicName);
  const condContainer = document.getElementById(`combo-${side}-condition-params`);
  const sizingContainer = document.getElementById(`combo-${side}-sizing-params`);
  if (!meta) {
    if (condContainer) condContainer.innerHTML = '';
    if (sizingContainer) sizingContainer.innerHTML = '';
    return;
  }

  const tunableParams = meta.tunable_params || {};
  const defaults = meta.default_params || {};
  const current = existingParams || {};

  // Split params by group
  const condParams = {};
  const sizingParams = {};
  for (const [key, spec] of Object.entries(tunableParams)) {
    if (spec.group === 'sizing') sizingParams[key] = spec;
    else condParams[key] = spec;
  }

  // Render condition params
  if (condContainer) {
    condContainer.innerHTML = _renderParamsHtml({
      tunableParams: condParams, defaults, current, side,
      inputId: (key) => `combo-${side}-${key}`,
      dataAttrs: (key) => `data-combo-side="${side}"`,
      onToggle: () => `toggleDependentParams(this,'${side}')`,
      showUnit: true,
    });
  }

  // Render sizing params
  if (sizingContainer) {
    if (Object.keys(sizingParams).length === 0) {
      sizingContainer.innerHTML = '<div class="wizard-empty-state">이 전략은 별도 금액 설정이 없습니다.</div>';
    } else {
      // scaled_plan 계산기 버튼 (buy side만)
      let extraHtml = '';
      if (side === 'buy' && sizingParams.sizing_mode) {
        const smOptions = (sizingParams.sizing_mode.options || []).map(o => typeof o === 'object' ? o.value : o);
        if (smOptions.includes('scaled_plan')) {
          const curSm = current.sizing_mode ?? defaults.sizing_mode ?? 'fixed';
          const btnVisible = curSm === 'scaled_plan' ? '' : 'display:none';
          extraHtml = `<div data-depends-on="sizing_mode" data-depends-values="scaled_plan" style="${btnVisible};margin-top:4px;">
            <button type="button" class="btn btn-outline btn-sm" onclick="openBuyPlanCalc()">매수 계획 계산기</button>
          </div>`;
        }
      }

      sizingContainer.innerHTML = _renderParamsHtml({
        tunableParams: sizingParams, defaults, current, side,
        inputId: (key) => `combo-${side}-${key}`,
        dataAttrs: (key) => `data-combo-side="${side}"`,
        onToggle: () => `toggleDependentParams(this,'${side}')`,
        showUnit: true,
        extraHtml,
      });
    }
  }
}

function toggleDependentParams(selectEl, side) {
  const condContainer = document.getElementById(`combo-${side}-condition-params`);
  const sizingContainer = document.getElementById(`combo-${side}-sizing-params`);
  if (condContainer) _toggleDeps(condContainer, selectEl.dataset.param, selectEl.value);
  if (sizingContainer) _toggleDeps(sizingContainer, selectEl.dataset.param, selectEl.value);
}

function _collectComboParams(side) {
  return _collectParamValues(`[data-combo-side="${side}"]`);
}

async function saveCombo() {
  const editId = document.getElementById('combo-edit-id').value;
  const name = document.getElementById('combo-name').value.trim();
  if (!name) { showToast('Name is required', 'error'); return; }

  const buyParams = _collectComboParams('buy');
  const sellParams = _collectComboParams('sell');
  const refComboId = document.getElementById('combo-reference').value || null;

  const isEdit = !!editId;
  const url = isEdit
    ? '/api/accounts/' + ACCOUNT_ID + '/combos/' + editId
    : '/api/accounts/' + ACCOUNT_ID + '/combos';
  const method = isEdit ? 'PUT' : 'POST';

  const body = { name, buy_params: buyParams, sell_params: sellParams };
  if (!isEdit) {
    body.buy_logic_name = document.getElementById('combo-buy-logic').value;
    body.sell_logic_name = document.getElementById('combo-sell-logic').value;
  }
  if (isEdit && document.getElementById('combo-reapply-orders').checked) {
    body.reapply_open_orders = true;
  }
  if (refComboId) body.reference_combo_id = refComboId;

  try {
    const resp = await apiFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      const msg = isEdit
        ? (body.reapply_open_orders ? 'Combo updated + open orders will be re-placed' : 'Combo updated')
        : 'Combo created';
      showToast(msg, 'success');
      closeComboModal();
      await loadCombosAndLots(ACCOUNT_ID);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Save failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function toggleCombo(accountId, comboId, isEnabled) {
  const action = isEnabled ? 'disable' : 'enable';
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/combos/' + comboId + '/' + action, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('Combo ' + action + 'd', 'success');
      await loadCombosAndLots(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Toggle failed'), 'error');
    }
  } catch (e) { showToast('Network error: ' + e.message, 'error'); }
}

async function deleteCombo(accountId, comboId) {
  if (!confirm('Delete this combo? This cannot be undone.')) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/combos/' + comboId, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('Combo deleted', 'success');
      await loadCombosAndLots(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Delete failed'), 'error');
    }
  } catch (e) { showToast('Network error: ' + e.message, 'error'); }
}

/* ============================================================
   Circuit Breaker
   ============================================================ */

async function loadCircuitBreaker(accountId) {
  const el = document.getElementById('circuit-breaker-panel');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const tripped = !!data.circuit_breaker_tripped;
    el.innerHTML = `
      <div class="status-card-row">
        <div class="status-card-icon" style="background:${tripped ? 'var(--danger-light)' : 'var(--success-light)'};color:${tripped ? 'var(--danger)' : 'var(--success)'}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
        </div>
        <div class="status-card-content">
          <div class="cb-status ${tripped ? 'cb-tripped' : 'cb-ok'}">
            <span class="cb-indicator"></span>
            <span class="cb-label">${tripped ? 'TRIPPED - Trading Halted' : 'Normal - Trading Active'}</span>
          </div>
          ${tripped ? `<button class="btn btn-danger btn-sm" style="margin-top:0.65rem;" onclick="resetCircuitBreaker('${accountId}')">Reset Circuit Breaker</button>` : ''}
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load circuit breaker status</p>';
  }
}

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
   Buy Pause Status
   ============================================================ */

async function loadBuyPauseStatus(accountId) {
  const el = document.getElementById('buy-pause-panel');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const bp = data.buy_pause || { state: 'ACTIVE', reason: null, since: null, consecutive_low_balance: 0 };

    if (bp.state === 'ACTIVE') {
      el.innerHTML = `
        <div class="status-card-row">
          <div class="status-card-icon" style="background:var(--success-light);color:var(--success)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="status-card-content">
            <div class="bp-status bp-ok">
              <span class="bp-indicator"></span>
              <span class="bp-label">Normal - Buying Active</span>
            </div>
          </div>
        </div>
      `;
      return;
    }

    const isPaused = bp.state === 'PAUSED';
    const stateLabel = isPaused ? 'PAUSED' : 'THROTTLED';
    const stateClass = isPaused ? 'bp-paused' : 'bp-throttled';
    const reasonText = bp.reason === 'LOW_BALANCE' ? 'Insufficient Balance' : (bp.reason || 'Unknown');
    const sinceText = bp.since ? new Date(bp.since).toLocaleString() : '-';
    const countText = bp.consecutive_low_balance || 0;

    el.innerHTML = `
      <div class="status-card-row">
        <div class="status-card-icon" style="background:${isPaused ? 'var(--warning-light)' : 'var(--orange-light)'};color:${isPaused ? 'var(--warning)' : 'var(--orange)'}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="10" y1="15" x2="10" y2="9"/><line x1="14" y1="15" x2="14" y2="9"/></svg>
        </div>
        <div class="status-card-content">
          <div class="bp-status ${stateClass}">
            <span class="bp-indicator"></span>
            <span class="bp-label">${stateLabel} - ${reasonText}</span>
          </div>
          <div class="bp-details">
            <div class="bp-detail-row"><span class="bp-detail-label">Since</span><span>${sinceText}</span></div>
            <div class="bp-detail-row"><span class="bp-detail-label">Consecutive Low Balance</span><span>${countText}x</span></div>
            ${isPaused ? '<div class="bp-detail-row"><span class="bp-detail-label">Sell Monitoring</span><span>Active (auto-resume on sell)</span></div>' : ''}
            ${bp.state === 'THROTTLED' ? '<div class="bp-detail-row"><span class="bp-detail-label">Buy Frequency</span><span>1 per 5 cycles</span></div>' : ''}
          </div>
          <button class="btn btn-primary btn-sm" style="margin-top:0.65rem;" onclick="resumeBuying('${accountId}')">Resume Buying</button>
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load buy pause status</p>';
  }
}

async function resumeBuying(accountId) {
  if (!confirm('Resume buying? This will clear the pause state.')) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/buy-pause/resume', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('Buying resumed', 'success');
      loadBuyPauseStatus(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Resume failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Buy Plan Calculator (매수 계획 계산기)
   ============================================================ */

function openBuyPlanCalc() {
  const modal = document.getElementById('buyplan-calc-modal');
  if (!modal) return;
  // USDT 잔고 자동 입력 시도
  const usdtEl = document.querySelector('.asset-value');
  if (usdtEl) {
    const parsed = parseFloat(usdtEl.textContent);
    if (!isNaN(parsed) && parsed > 0) {
      document.getElementById('bp-initialUsdt').value = parsed.toFixed(2);
    }
  }
  // plan_x_pct 값 동기화
  const xPctInput = document.querySelector('[data-param="plan_x_pct"]');
  if (xPctInput && xPctInput.value) {
    document.getElementById('bp-xPct').value = xPctInput.value;
  }
  modal.style.display = 'flex';
  runBuyPlanCalc();
}

function closeBuyPlanCalc() {
  const modal = document.getElementById('buyplan-calc-modal');
  if (modal) modal.style.display = 'none';
}

function calcBuyPlan(initialUsdt, xPct, minOrderUsdt, previewAfter5) {
  const x = xPct / 100;
  if (!(x > 0 && x < 0.2)) return null;
  if (!(initialUsdt > 0)) return null;

  let R = initialUsdt;
  const firstFive = [];
  let tradeCount = 0;

  for (let k = 1; k <= 5; k++) {
    const pct = k * x;
    const usdt = R * pct;
    if (usdt < minOrderUsdt) {
      return { firstFive, post5TargetUsdt: null, after5: [], maxTrades: tradeCount, lastUsdt: null, remainUsdt: R, reason: k + '회차 주문금액이 최소 주문금액 미만' };
    }
    R -= usdt;
    firstFive.push({ round: k, pct: pct * 100, usdt, remain: R });
    tradeCount++;
  }

  const A = firstFive[4].usdt;
  const after5 = [];
  let tmpR = R;
  for (let i = 0; i < previewAfter5; i++) {
    if (tmpR <= 0) break;
    const needPct = (A / tmpR) * 100;
    after5.push({ round: 6 + i, pct: Math.min(needPct, 100), usdt: Math.min(A, tmpR) });
    if (tmpR >= A) tmpR -= A; else break;
  }

  let reason = '';
  while (R > 0) {
    if (R >= A) { if (A < minOrderUsdt) { reason = '6회 이후 목표금액(A)이 최소 주문금액 미만'; break; } R -= A; tradeCount++; continue; }
    if (R < minOrderUsdt) { reason = '잔고가 최소 주문금액 미만'; break; }
    tradeCount++; R = 0; break;
  }

  return { firstFive, post5TargetUsdt: A, after5, maxTrades: tradeCount, remainUsdt: R, reason };
}

function runBuyPlanCalc() {
  const warn = document.getElementById('bp-warn');
  warn.style.display = 'none';
  const result = calcBuyPlan(
    Number(document.getElementById('bp-initialUsdt').value),
    Number(document.getElementById('bp-xPct').value),
    Number(document.getElementById('bp-minOrder').value),
    Number(document.getElementById('bp-preview').value),
  );
  if (!result) { warn.textContent = '입력값을 확인해 주세요.'; warn.style.display = 'block'; return; }

  const summary = document.getElementById('bp-summary');
  summary.innerHTML = `
    <div class="asset-card"><div class="asset-label">5회차 기준금액 A</div><div class="asset-value">${result.post5TargetUsdt != null ? fmt(result.post5TargetUsdt, 2) : '-'}</div></div>
    <div class="asset-card"><div class="asset-label">예상 최대 거래 횟수</div><div class="asset-value">${result.maxTrades}</div></div>
    <div class="asset-card"><div class="asset-label">종료 후 잔고</div><div class="asset-value">${fmt(result.remainUsdt, 2)}</div></div>
  `;
  if (result.reason) { warn.textContent = '중단 사유: ' + result.reason; warn.style.display = 'block'; }

  document.getElementById('bp-firstFive').innerHTML = result.firstFive.map(r =>
    `<tr><td>${r.round}</td><td>${fmt(r.pct, 2)}%</td><td>${fmt(r.usdt, 2)}</td><td>${fmt(r.remain, 2)}</td></tr>`
  ).join('') || '<tr><td colspan="4">-</td></tr>';

  document.getElementById('bp-afterFive').innerHTML = result.after5.map(r =>
    `<tr><td>${r.round}</td><td>${fmt(r.pct, 2)}%</td><td>${fmt(r.usdt, 2)}</td></tr>`
  ).join('') || '<tr><td colspan="3">-</td></tr>';
}

/* ============================================================
   Utility Functions
   ============================================================ */

function fmt(val, decimals) {
  if (val == null) return '-';
  return Number(val).toFixed(decimals);
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

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

/* ============================================================
   Auto-initialize on page load
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;

  if (path === '/accounts') {
    loadAccounts();
  }
  // All other pages (account_detail, admin sub-pages) use inline scripts
});
