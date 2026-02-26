/**
 * main.js - Core JavaScript for Crypto Multi-Trader Dashboard
 * 전략 tune 패널과 lot 필터 탭은 tunable_params 메타데이터 기반 동적 렌더링
 */

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
  ]);
}

async function loadPriceChart(accountId, containerId) {
  const container = document.getElementById(containerId);
  if (!container || typeof LightweightCharts === 'undefined') return;
  container.innerHTML = '';

  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#1e293b' }, textColor: '#e2e8f0' },
    grid: { vertLines: { color: '#334155' }, horzLines: { color: '#334155' } },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: '#334155' },
    timeScale: { borderColor: '#334155', timeVisible: true, secondsVisible: false },
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
      <div class="asset-card earnings-card ${(data.pending_earnings_usdt || 0) > 0 ? 'has-earnings' : ''}">
        <div class="asset-label">적립금 (Pending)</div>
        <div class="asset-value">${fmt(data.pending_earnings_usdt || 0, 2)} USDT</div>
        <button class="btn btn-sm btn-approve"
                onclick="openEarningsModal('${accountId}')"
                ${(data.pending_earnings_usdt || 0) <= 0 ? 'disabled' : ''}>
          Reserve 추가
        </button>
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
    if (bpKeys.length) paramsHtml += '<span class="combo-params">' + bpKeys.map(k => escapeHtml(k) + '=' + escapeHtml(String(combo.buy_params[k]))).join(', ') + '</span>';
    if (spKeys.length) paramsHtml += ' | <span class="combo-params">' + spKeys.map(k => escapeHtml(k) + '=' + escapeHtml(String(combo.sell_params[k]))).join(', ') + '</span>';

    return `<div class="tune-panel" style="margin-bottom:0.75rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h3 class="tune-panel-title" style="margin:0;">${escapeHtml(combo.name)}</h3>
        <div style="display:flex;gap:0.5rem;align-items:center;">
          <span class="status-badge ${enabledClass}">${enabledText}</span>
          <button class="btn btn-outline btn-sm" onclick="editCombo('${combo.id}')">Edit</button>
          <button class="btn btn-outline btn-sm" onclick="toggleCombo('${accountId}','${combo.id}',${combo.is_enabled})">${combo.is_enabled ? 'Disable' : 'Enable'}</button>
          <button class="btn btn-danger btn-sm" onclick="deleteCombo('${accountId}','${combo.id}')">Delete</button>
        </div>
      </div>
      <div style="color:#94a3b8;font-size:0.85rem;margin-top:0.25rem;">
        Buy: ${escapeHtml(buyMeta.display_name || combo.buy_logic_name)} | Sell: ${escapeHtml(sellMeta.display_name || combo.sell_logic_name)}
      </div>
      ${paramsHtml ? '<div style="color:#64748b;font-size:0.8rem;margin-top:0.25rem;">' + paramsHtml + '</div>' : ''}
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

function showCreateComboModal() {
  document.getElementById('combo-modal-title').textContent = 'New Combo';
  document.getElementById('combo-edit-id').value = '';
  document.getElementById('combo-name').value = '';
  _populateLogicSelects();
  _populateReferenceSelect('');
  renderComboParams('buy');
  renderComboParams('sell');
  document.getElementById('combo-buy-logic-group').style.display = '';
  document.getElementById('combo-sell-logic-group').style.display = '';
  document.getElementById('combo-modal').style.display = 'flex';
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
  renderComboParams('buy', combo.buy_params);
  renderComboParams('sell', combo.sell_params);
  document.getElementById('combo-modal').style.display = 'flex';
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

function renderComboParams(side, existingParams) {
  const logicName = document.getElementById(side === 'buy' ? 'combo-buy-logic' : 'combo-sell-logic').value;
  const logics = side === 'buy' ? _buyLogics : _sellLogics;
  const meta = logics.find(l => l.name === logicName);
  const container = document.getElementById(side === 'buy' ? 'combo-buy-params' : 'combo-sell-params');
  if (!container || !meta) { if (container) container.innerHTML = ''; return; }

  const tunableParams = meta.tunable_params || {};
  const defaults = meta.default_params || {};
  const current = existingParams || {};

  let html = '<div class="tune-grid">';
  for (const [key, pm] of Object.entries(tunableParams)) {
    const val = current[key] ?? defaults[key] ?? '';
    const title = pm.title || key;
    const unit = pm.unit ? ` <span class="form-hint">(${escapeHtml(pm.unit)})</span>` : '';
    const inputId = `combo-${side}-${key}`;

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
      html += `<select id="${inputId}" class="form-input" data-combo-side="${side}" data-param="${key}" data-type="bool">
        <option value="true" ${isTrue ? 'selected' : ''}>Yes</option>
        <option value="false" ${!isTrue ? 'selected' : ''}>No</option>
      </select>`;
    } else if (pm.type === 'select') {
      const onchangeAttr = pm.visible_when ? '' : ` onchange="toggleDependentParams(this,'${side}')"`;
      html += `<select id="${inputId}" class="form-input" data-combo-side="${side}" data-param="${key}" data-type="select"${onchangeAttr}>`;
      for (const opt of (pm.options || [])) {
        const ov = typeof opt === 'object' ? opt.value : opt;
        const ol = typeof opt === 'object' ? opt.label : opt;
        html += `<option value="${escapeHtml(String(ov))}" ${val === ov ? 'selected' : ''}>${escapeHtml(String(ol))}</option>`;
      }
      html += '</select>';
    } else {
      const step = pm.step || (pm.type === 'int' ? 1 : 0.001);
      html += `<input type="number" step="${step}" id="${inputId}" class="form-input" value="${val}" data-combo-side="${side}" data-param="${key}" data-type="${pm.type || 'float'}">`;
    }
    html += '</div>';
  }
  html += '</div>';

  // scaled_plan 계산기 버튼 (buy side만)
  if (side === 'buy' && tunableParams.sizing_mode) {
    const smOptions = (tunableParams.sizing_mode.options || []).map(o => typeof o === 'object' ? o.value : o);
    if (smOptions.includes('scaled_plan')) {
      const curSm = current.sizing_mode ?? defaults.sizing_mode ?? 'fixed';
      const btnVisible = curSm === 'scaled_plan' ? '' : 'display:none';
      html += `<div data-depends-on="sizing_mode" data-depends-values="scaled_plan" style="${btnVisible};margin-top:4px;">
        <button type="button" class="btn btn-outline btn-sm" onclick="openBuyPlanCalc()">매수 계획 계산기</button>
      </div>`;
    }
  }

  container.innerHTML = html;
}

function toggleDependentParams(selectEl, side) {
  const val = selectEl.value;
  const key = selectEl.dataset.param;
  const container = document.getElementById(side === 'buy' ? 'combo-buy-params' : 'combo-sell-params');
  if (!container) return;
  container.querySelectorAll(`[data-depends-on="${key}"]`).forEach(group => {
    const allowed = group.dataset.dependsValues.split(',');
    group.style.display = allowed.includes(val) ? '' : 'none';
  });
}

function _collectComboParams(side) {
  const params = {};
  document.querySelectorAll(`[data-combo-side="${side}"]`).forEach(input => {
    const key = input.dataset.param;
    const type = input.dataset.type;
    if (type === 'bool') params[key] = input.value === 'true';
    else if (type === 'int') { if (input.value !== '') params[key] = parseInt(input.value, 10); }
    else if (type === 'select') params[key] = input.value;
    else { if (input.value !== '') params[key] = parseFloat(input.value); }
  });
  return params;
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
  if (refComboId) body.reference_combo_id = refComboId;

  try {
    const resp = await apiFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      showToast(isEdit ? 'Combo updated' : 'Combo created', 'success');
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

async function loadAdminUsers() {
  const tbody = document.getElementById('admin-users-tbody');
  if (!tbody) return;
  try {
    const resp = await apiFetch('/api/admin/users');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const users = await resp.json();
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No users</td></tr>';
      return;
    }
    tbody.innerHTML = users.map(u => {
      const isActive = u.is_active !== false;
      return `<tr>
        <td>${escapeHtml(u.email || '-')}</td>
        <td>
          <select class="form-input" style="width:auto;" onchange="changeUserRole('${u.id}', this.value)">
            <option value="user" ${u.role === 'user' ? 'selected' : ''}>User</option>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option>
          </select>
        </td>
        <td><span class="status-badge ${isActive ? 'badge-success' : 'badge-danger'}">${isActive ? 'Active' : 'Inactive'}</span></td>
        <td>
          <button class="btn btn-outline btn-sm" onclick="resetUserPassword('${u.id}')">Reset PW</button>
          <button class="btn ${isActive ? 'btn-danger' : 'btn-primary'} btn-sm" onclick="toggleUserActive('${u.id}', ${isActive})">${isActive ? 'Deactivate' : 'Activate'}</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" class="table-empty error-text">Failed to load users</td></tr>';
  }
}

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

async function createUser() {
  const email = document.getElementById('new-user-email').value.trim();
  const password = document.getElementById('new-user-password').value;
  const role = document.getElementById('new-user-role').value;

  if (!email || !password) { showToast('Email and password required', 'error'); return; }
  if (password.length < 8) { showToast('Password must be at least 8 characters', 'error'); return; }

  try {
    const resp = await apiFetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ email, password, role }),
    });
    if (resp.ok) {
      showToast('User created', 'success');
      document.getElementById('new-user-email').value = '';
      document.getElementById('new-user-password').value = '';
      loadAdminUsers();
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Create failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function resetUserPassword(userId) {
  const newPassword = prompt('Enter new password (min 8 chars):');
  if (!newPassword) return;
  if (newPassword.length < 8) { showToast('Password must be at least 8 characters', 'error'); return; }

  try {
    const resp = await apiFetch('/api/admin/users/' + userId + '/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ new_password: newPassword }),
    });
    if (resp.ok) {
      showToast('Password reset', 'success');
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Reset failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function toggleUserActive(userId, currentActive) {
  const action = currentActive ? 'deactivate' : 'activate';
  if (!confirm(`Are you sure you want to ${action} this user?`)) return;

  try {
    const resp = await apiFetch('/api/admin/users/' + userId + '/active', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ is_active: !currentActive }),
    });
    if (resp.ok) {
      showToast('User ' + action + 'd', 'success');
      loadAdminUsers();
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('Error: ' + (err.detail || 'Update failed'), 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

/* ============================================================
   Backtest (combo 기반 설정 UI)
   ============================================================ */

let _backtestPollTimer = null;
let _btBuyLogics = [];
let _btSellLogics = [];
let _btComboCount = 0;

/**
 * 백테스트 buy/sell 로직 메타데이터 로드 + 기본 combo 1개 생성
 */
async function loadBtLogics() {
  try {
    const [buyResp, sellResp] = await Promise.all([
      apiFetch('/api/buy-logics'),
      apiFetch('/api/sell-logics'),
    ]);
    if (buyResp.ok) _btBuyLogics = await buyResp.json();
    if (sellResp.ok) _btSellLogics = await sellResp.json();

    const container = document.getElementById('bt-combos-container');
    if (container) {
      container.innerHTML = '';
      _btComboCount = 0;
      addBtCombo();
    }
  } catch (e) {
    console.error('Failed to load logics for backtest', e);
  }
}

function addBtCombo() {
  const container = document.getElementById('bt-combos-container');
  if (!container) return;
  const idx = _btComboCount++;
  const div = document.createElement('div');
  div.className = 'tune-panel';
  div.id = 'bt-combo-' + idx;
  div.style.marginBottom = '0.75rem';

  let buyOpts = _btBuyLogics.map(b => `<option value="${b.name}">${escapeHtml(b.display_name)}</option>`).join('');
  let sellOpts = _btSellLogics.map(s => `<option value="${s.name}">${escapeHtml(s.display_name)}</option>`).join('');

  div.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h3 class="tune-panel-title" style="margin:0;">Combo ${idx + 1}</h3>
      <button type="button" class="btn btn-danger btn-sm" onclick="removeBtCombo(${idx})">Remove</button>
    </div>
    <div class="tune-grid" style="margin-top:0.5rem;">
      <div class="form-group">
        <label class="form-label">Name</label>
        <input type="text" class="form-input" id="bt-combo-name-${idx}" value="combo_${idx + 1}">
      </div>
      <div class="form-group">
        <label class="form-label">Buy Logic</label>
        <select class="form-input" id="bt-combo-buy-${idx}" onchange="renderBtComboParams(${idx},'buy')">${buyOpts}</select>
      </div>
      <div class="form-group">
        <label class="form-label">Sell Logic</label>
        <select class="form-input" id="bt-combo-sell-${idx}" onchange="renderBtComboParams(${idx},'sell')">${sellOpts}</select>
      </div>
      <div class="form-group">
        <label class="form-label">Reference Combo</label>
        <input type="text" class="form-input" id="bt-combo-ref-${idx}" placeholder="(none)">
      </div>
    </div>
    <div id="bt-combo-buy-params-${idx}" style="margin-top:0.5rem;"></div>
    <div id="bt-combo-sell-params-${idx}" style="margin-top:0.5rem;"></div>
  `;
  container.appendChild(div);
  renderBtComboParams(idx, 'buy');
  renderBtComboParams(idx, 'sell');
}

function removeBtCombo(idx) {
  const el = document.getElementById('bt-combo-' + idx);
  if (el) el.remove();
}

function renderBtComboParams(idx, side) {
  const selectId = side === 'buy' ? 'bt-combo-buy-' + idx : 'bt-combo-sell-' + idx;
  const containerId = side === 'buy' ? 'bt-combo-buy-params-' + idx : 'bt-combo-sell-params-' + idx;
  const logicName = document.getElementById(selectId).value;
  const logics = side === 'buy' ? _btBuyLogics : _btSellLogics;
  const meta = logics.find(l => l.name === logicName);
  const container = document.getElementById(containerId);
  if (!container || !meta) { if (container) container.innerHTML = ''; return; }

  const tunableParams = meta.tunable_params || {};
  const defaults = meta.default_params || {};
  let html = '<div class="tune-grid">';
  for (const [key, pm] of Object.entries(tunableParams)) {
    const val = defaults[key] ?? '';
    const title = pm.title || key;
    const inputId = `bt-c${idx}-${side}-${key}`;

    // visible_when: 조건부 표시
    let groupAttrs = '';
    let groupStyle = '';
    if (pm.visible_when) {
      const depKey = Object.keys(pm.visible_when)[0];
      const depVals = Array.isArray(pm.visible_when[depKey]) ? pm.visible_when[depKey] : [pm.visible_when[depKey]];
      const curDepVal = String(defaults[depKey] ?? '');
      const visible = depVals.includes(curDepVal);
      groupAttrs = ` data-depends-on="${escapeHtml(depKey)}" data-depends-values="${depVals.map(v => escapeHtml(String(v))).join(',')}"`;
      groupStyle = visible ? '' : ' style="display:none"';
    }

    html += `<div class="form-group"${groupAttrs}${groupStyle}>`;
    html += `<label class="form-label">${escapeHtml(title)}</label>`;
    if (pm.type === 'bool') {
      const isTrue = val === true || val === 'true';
      html += `<select id="${inputId}" class="form-input" data-bt-combo="${idx}" data-bt-side="${side}" data-param="${key}" data-type="bool">
        <option value="true" ${isTrue ? 'selected' : ''}>Yes</option>
        <option value="false" ${!isTrue ? 'selected' : ''}>No</option>
      </select>`;
    } else if (pm.type === 'select') {
      const onchangeAttr = pm.visible_when ? '' : ` onchange="toggleBtDependentParams(this,${idx},'${side}')"`;
      html += `<select id="${inputId}" class="form-input" data-bt-combo="${idx}" data-bt-side="${side}" data-param="${key}" data-type="select"${onchangeAttr}>`;
      for (const opt of (pm.options || [])) {
        const ov = typeof opt === 'object' ? opt.value : opt;
        const ol = typeof opt === 'object' ? opt.label : opt;
        html += `<option value="${escapeHtml(String(ov))}" ${val === ov ? 'selected' : ''}>${escapeHtml(String(ol))}</option>`;
      }
      html += '</select>';
    } else {
      const step = pm.step || (pm.type === 'int' ? 1 : 0.001);
      html += `<input type="number" step="${step}" id="${inputId}" class="form-input" value="${val}" data-bt-combo="${idx}" data-bt-side="${side}" data-param="${key}" data-type="${pm.type || 'float'}">`;
    }
    html += '</div>';
  }
  html += '</div>';
  container.innerHTML = html;
}

function toggleBtDependentParams(selectEl, idx, side) {
  const val = selectEl.value;
  const key = selectEl.dataset.param;
  const containerId = side === 'buy' ? 'bt-combo-buy-params-' + idx : 'bt-combo-sell-params-' + idx;
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll(`[data-depends-on="${key}"]`).forEach(group => {
    const allowed = group.dataset.dependsValues.split(',');
    group.style.display = allowed.includes(val) ? '' : 'none';
  });
}

function _collectBtCombos() {
  const combos = [];
  document.querySelectorAll('[id^="bt-combo-"][id$="-0"], [id^="bt-combo-"]').forEach(el => {
    const match = el.id.match(/^bt-combo-(\d+)$/);
    if (!match) return;
    const idx = parseInt(match[1], 10);
    const nameEl = document.getElementById('bt-combo-name-' + idx);
    if (!nameEl) return;
    const name = nameEl.value.trim() || 'combo_' + (idx + 1);
    const buyLogic = document.getElementById('bt-combo-buy-' + idx).value;
    const sellLogic = document.getElementById('bt-combo-sell-' + idx).value;
    const refName = document.getElementById('bt-combo-ref-' + idx).value.trim() || null;

    const buyParams = {};
    const sellParams = {};
    document.querySelectorAll(`[data-bt-combo="${idx}"]`).forEach(input => {
      const side = input.dataset.btSide;
      const key = input.dataset.param;
      const type = input.dataset.type;
      let val;
      if (type === 'bool') val = input.value === 'true';
      else if (type === 'int') val = input.value !== '' ? parseInt(input.value, 10) : undefined;
      else if (type === 'select') val = input.value;
      else val = input.value !== '' ? parseFloat(input.value) : undefined;
      if (val !== undefined) {
        if (side === 'buy') buyParams[key] = val;
        else sellParams[key] = val;
      }
    });

    combos.push({
      name,
      buy_logic_name: buyLogic,
      buy_params: buyParams,
      sell_logic_name: sellLogic,
      sell_params: sellParams,
      reference_combo_name: refName,
    });
  });
  return combos;
}

async function startBacktest() {
  const symbol = document.getElementById('bt-symbol').value;
  const initialUsdt = parseFloat(document.getElementById('bt-initial-usdt').value) || 10000;
  const startDate = document.getElementById('bt-start-date').value;
  const endDate = document.getElementById('bt-end-date').value;

  if (!startDate || !endDate) { showToast('Please select start and end dates', 'error'); return; }

  const startTsMs = new Date(startDate).getTime();
  const endTsMs = new Date(endDate + 'T23:59:59').getTime();
  if (startTsMs >= endTsMs) { showToast('Start date must be before end date', 'error'); return; }

  const combos = _collectBtCombos();
  if (!combos.length) { showToast('Add at least one combo', 'error'); return; }

  const btn = document.getElementById('bt-run-btn');
  btn.disabled = true;
  btn.textContent = 'Starting...';

  try {
    const resp = await apiFetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ symbol, start_ts_ms: startTsMs, end_ts_ms: endTsMs, initial_usdt: initialUsdt, combos }),
    });
    if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.detail || 'Failed to start backtest'); }
    const data = await resp.json();
    showToast('Backtest started', 'success');
    _startBacktestPolling(data.id);
    loadBacktestHistory();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Backtest';
  }
}

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
        showToast(data.status === 'COMPLETED' ? 'Backtest completed!' : 'Backtest failed: ' + (data.error_message || 'Unknown error'),
          data.status === 'COMPLETED' ? 'success' : 'error');
      }
    } catch (e) {}
  }, 2000);
}

async function loadBacktestHistory() {
  const tbody = document.getElementById('backtest-history-tbody');
  if (!tbody) return;
  try {
    const resp = await apiFetch('/api/backtest/list');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const runs = await resp.json();
    if (!runs.length) { tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No backtests yet</td></tr>'; return; }

    tbody.innerHTML = runs.map(r => {
      const created = new Date(r.created_at).toLocaleString();
      const startD = new Date(r.start_ts_ms).toLocaleDateString();
      const endD = new Date(r.end_ts_ms).toLocaleDateString();
      const strats = r.combos ? r.combos.map(c => c.name).join(', ') : (r.strategies ? r.strategies.join(', ') : '-');
      const pnlClass = r.pnl_pct != null ? (r.pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative') : '';
      const pnlText = r.pnl_pct != null ? r.pnl_pct.toFixed(2) + '%' : '-';
      let statusBadge = '';
      if (r.status === 'COMPLETED') statusBadge = '<span class="status-badge badge-success">Completed</span>';
      else if (r.status === 'RUNNING') statusBadge = '<span class="status-badge badge-warning">Running</span>';
      else if (r.status === 'PENDING') statusBadge = '<span class="status-badge badge-neutral">Pending</span>';
      else statusBadge = '<span class="status-badge badge-danger">Failed</span>';
      let actions = '';
      if (r.status === 'COMPLETED') actions = `<a href="/admin/backtest/${r.id}" class="btn btn-outline btn-sm">View Report</a>`;
      if (r.status !== 'RUNNING' && r.status !== 'PENDING') actions += ` <button class="btn btn-danger btn-sm" onclick="deleteBacktest('${r.id}')">Delete</button>`;
      return `<tr>
        <td>${escapeHtml(created)}</td><td>${escapeHtml(r.symbol)}</td><td>${escapeHtml(strats)}</td>
        <td>${escapeHtml(startD)} ~ ${escapeHtml(endD)}</td><td class="${pnlClass}">${pnlText}</td>
        <td>${statusBadge}</td><td>${actions}</td>
      </tr>`;
    }).join('');

    const hasActive = runs.some(r => r.status === 'RUNNING' || r.status === 'PENDING');
    if (hasActive && !_backtestPollTimer) {
      const activeRun = runs.find(r => r.status === 'RUNNING' || r.status === 'PENDING');
      if (activeRun) _startBacktestPolling(activeRun.id);
    }
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty error-text">Failed to load history</td></tr>';
  }
}

async function deleteBacktest(runId) {
  if (!confirm('Delete this backtest?')) return;
  try {
    const resp = await apiFetch('/api/backtest/' + runId, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) { showToast('Backtest deleted', 'success'); loadBacktestHistory(); }
    else { const err = await resp.json().catch(() => ({})); showToast('Error: ' + (err.detail || 'Delete failed'), 'error'); }
  } catch (e) { showToast('Network error: ' + e.message, 'error'); }
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
  } else if (path.startsWith('/accounts/')) {
    // account_detail.html의 인라인 스크립트에서 loadAccountDashboard() 호출
  } else if (path === '/admin') {
    loadAdminOverview();
    loadAdminAccounts();
    loadAdminUsers();
    loadBtLogics();
    loadBacktestHistory();
  }
});
