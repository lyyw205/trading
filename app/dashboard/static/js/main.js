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

function _utcToKST(utcSec) {
  return new Date((utcSec + 9 * 3600) * 1000);
}

function _fmtKST(utcSec, mode) {
  const kstDate = _utcToKST(utcSec);
  const yearStr = String(kstDate.getUTCFullYear()).slice(2);
  const monthStr = String(kstDate.getUTCMonth() + 1).padStart(2, '0');
  const dayStr = String(kstDate.getUTCDate()).padStart(2, '0');
  const hourStr = String(kstDate.getUTCHours()).padStart(2, '0');
  const minuteStr = String(kstDate.getUTCMinutes()).padStart(2, '0');
  if (mode === 'time') return hourStr + ':' + minuteStr;
  if (mode === 'tooltip') return yearStr + '-' + monthStr + '-' + dayStr + ' ' + hourStr + ':' + minuteStr;
  return monthStr + '-' + dayStr;
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
    timeScale: {
      borderColor: isLight ? '#E5E7EB' : '#334155',
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time) => _fmtKST(time, 'time'),
    },
    localization: {
      timeFormatter: (time) => _fmtKST(time, 'tooltip'),
    },
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
  // Auto-inject CSRF token for mutating requests
  const method = (merged.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    merged.headers = merged.headers || {};
    if (!merged.headers['X-CSRFToken']) {
      merged.headers['X-CSRFToken'] = getCsrfToken();
    }
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
  for (const cookie of cookies) {
    const idx = cookie.indexOf('=');
    if (idx === -1) continue;
    const cookieName = cookie.substring(0, idx).trim();
    const cookieValue = cookie.substring(idx + 1).trim();
    if (cookieName === 'csrftoken') return cookieValue;
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
  // Accounts page now handles its own rendering via inline scripts.
  // This stub exists for backward compatibility with DOMContentLoaded call.
  if (typeof loadAccountsPage === 'function') {
    await loadAccountsPage();
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

  // Load combos first to build symbol dropdown, then chart + rest in parallel
  await loadCombosAndLots(accountId);

  // Build symbol dropdown from combos
  const symbolSelect = document.getElementById('chart-symbol-select');
  if (symbolSelect && _combos.length) {
    const allSymbols = [...new Set(_combos.flatMap(c => c.symbols || []))];
    symbolSelect.innerHTML = allSymbols.map(s =>
      `<option value="${escapeHtml(s)}">${escapeHtml(s).replace('USDT', '')}</option>`
    ).join('');
    _dashCurrentSymbol = allSymbols[0] || null;

    symbolSelect.addEventListener('change', () => {
      _dashCurrentSymbol = symbolSelect.value;
      _dashTradeEvents = null;
      loadPriceChart(_dashAccountId, 'price-chart', _dashCurrentInterval, _dashCurrentSymbol);
    });
  }

  // Load remaining sections in parallel
  await Promise.allSettled([
    loadPriceChart(accountId, 'price-chart', '1m', _dashCurrentSymbol),
    loadAssetStatus(accountId),
    loadProtectionStatus(accountId),
    loadClosedLots(accountId),
  ]);
}

// ── Price chart state (module-level for overlay re-render) ──
let _dashChart = null;
let _dashCandleSeries = null;
let _dashCandleMap = null;
let _dashTradeGroups = null;
let _dashOverlayRAF = null;
let _dashAccountId = null;
let _dashContainerId = null;
let _dashTradeEvents = null;
let _dashCurrentInterval = '1m';
let _dashCurrentSymbol = null;

// Interval → seconds mapping for candle bucketing
const INTERVAL_SEC = { '1m': 60, '5m': 300, '1h': 3600, '1d': 86400 };

async function loadPriceChart(accountId, containerId, interval, symbol) {
  const container = document.getElementById(containerId);
  if (!container || typeof LightweightCharts === 'undefined') return;

  _dashAccountId = accountId;
  _dashContainerId = containerId;
  _dashCurrentInterval = interval || '1m';
  if (symbol) _dashCurrentSymbol = symbol;

  // Dispose previous chart
  if (_dashChart) {
    _dashChart.remove();
    _dashChart = null;
    _dashCandleSeries = null;
  }

  // Keep overlay div, clear everything else
  const overlay = document.getElementById('trade-overlay');
  Array.from(container.children).forEach(ch => {
    if (ch.id !== 'trade-overlay') ch.remove();
  });
  if (overlay) overlay.innerHTML = '';

  const theme = getChartTheme();
  _dashChart = LightweightCharts.createChart(container, {
    ...theme,
    crosshair: { mode: 1 },
    width: container.clientWidth,
    height: container.clientHeight || 400,
  });

  _dashCandleSeries = _dashChart.addCandlestickSeries({
    upColor: '#0ecb81', downColor: '#f6465d',
    borderUpColor: '#0ecb81', borderDownColor: '#f6465d',
    wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
  });

  // Fetch candles
  try {
    let candleUrl = '/api/dashboard/' + accountId + '/price-candles?interval=' + _dashCurrentInterval;
    if (_dashCurrentSymbol) candleUrl += '&symbol=' + encodeURIComponent(_dashCurrentSymbol);
    const resp = await apiFetch(candleUrl);
    if (resp.ok) {
      const candles = await resp.json();
      // Convert ts_ms → time (unix seconds)
      const mapped = candles.map(candle => ({
        time: Math.floor(candle.ts_ms / 1000),
        open: candle.open, high: candle.high, low: candle.low, close: candle.close,
      }));
      _dashCandleSeries.setData(mapped);
      _dashChart.timeScale().fitContent();

      // Build candle map for overlay
      _dashCandleMap = {};
      mapped.forEach(c => { _dashCandleMap[c.time] = c; });
    }
  } catch (e) { console.error('Failed to load candles', e); }

  // Fetch trade events (only once, cache for TF switches)
  if (!_dashTradeEvents) {
    try {
      let eventsUrl = '/api/dashboard/' + accountId + '/trade-events';
      if (_dashCurrentSymbol) eventsUrl += '?symbol=' + encodeURIComponent(_dashCurrentSymbol);
      const resp = await apiFetch(eventsUrl);
      if (resp.ok) _dashTradeEvents = await resp.json();
    } catch (e) { console.error('Failed to load trade events', e); }
  }

  // Build trade groups snapped to candle times
  _dashTradeGroups = {};
  if (_dashTradeEvents && _dashCandleMap) {
    const sortedTimes = Object.keys(_dashCandleMap).map(Number).sort((a, b) => a - b);
    const intSec = INTERVAL_SEC[_dashCurrentInterval] || 300;

    function snapToCandle(t) {
      if (_dashCandleMap[t]) return t;
      let lo = 0, hi = sortedTimes.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (sortedTimes[mid] < t) lo = mid + 1;
        else hi = mid;
      }
      const candidates = [];
      if (lo < sortedTimes.length) candidates.push(sortedTimes[lo]);
      if (lo > 0) candidates.push(sortedTimes[lo - 1]);
      let best = candidates[0], bestDist = Math.abs(t - best);
      for (const candidateTime of candidates) {
        const distance = Math.abs(t - candidateTime);
        if (distance < bestDist) { best = candidateTime; bestDist = distance; }
      }
      return best;
    }

    _dashTradeEvents
      .filter(e => e.time && e.price)
      .forEach(e => {
        let time = Math.floor(e.time / intSec) * intSec;
        time = snapToCandle(time);
        if (!_dashTradeGroups[time]) _dashTradeGroups[time] = [];
        _dashTradeGroups[time].push({
          time,
          price: parseFloat(e.price),
          side: (e.side || '') === 'buy' ? 'buy' : 'sell',
          ts_ms: e.time * 1000,
        });
      });
  }

  // Schedule overlay render on visible range changes
  const scheduleOverlay = () => {
    if (_dashOverlayRAF) return;
    _dashOverlayRAF = requestAnimationFrame(() => {
      _dashOverlayRAF = null;
      _renderDashTradeOverlay();
    });
  };
  requestAnimationFrame(() => requestAnimationFrame(scheduleOverlay));
  _dashChart.timeScale().subscribeVisibleTimeRangeChange(scheduleOverlay);
  _dashChart.timeScale().subscribeVisibleLogicalRangeChange(scheduleOverlay);

  // Resize handler
  if (!window._dashResizeHandler) {
    window._dashResizeHandler = () => {
      if (_dashChart) {
        const c = document.getElementById(_dashContainerId);
        if (c) _dashChart.applyOptions({ width: c.clientWidth });
        scheduleOverlay();
      }
    };
    window.addEventListener('resize', window._dashResizeHandler);
  }

  // Timeframe selector handler
  const tfSelector = document.getElementById('tf-selector');
  if (tfSelector && !tfSelector._bound) {
    tfSelector._bound = true;
    tfSelector.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.tf-btn');
      if (!btn) return;
      tfSelector.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _dashTradeEvents = null; // force re-fetch for fresh data
      loadPriceChart(_dashAccountId, _dashContainerId, btn.dataset.tf, _dashCurrentSymbol);
    });
  }
}

// ── Trade overlay tooltip (shared singleton) ──
let _dashTooltip = null;
let _dashTtHideTimer = null;

function _getDashTooltip() {
  if (_dashTooltip) return _dashTooltip;
  _dashTooltip = document.createElement('div');
  _dashTooltip.className = 'chart-tooltip';
  document.body.appendChild(_dashTooltip);
  _dashTooltip.addEventListener('mouseenter', () => {
    if (_dashTtHideTimer) { clearTimeout(_dashTtHideTimer); _dashTtHideTimer = null; }
  });
  _dashTooltip.addEventListener('mouseleave', () => {
    _dashTooltip.style.display = 'none';
  });
  return _dashTooltip;
}

function _attachDashTooltip(label, trades) {
  label.addEventListener('mouseenter', () => {
    if (_dashTtHideTimer) { clearTimeout(_dashTtHideTimer); _dashTtHideTimer = null; }
    const tooltip = _getDashTooltip();
    let html = '';
    trades.forEach((trade, i) => {
      const tradeDate = new Date(trade.ts_ms);
      const dateStr = tradeDate.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' });
      const timeStr = tradeDate.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
      const sideLabel = trade.side === 'buy' ? 'BUY' : 'SELL';
      if (i > 0) html += '<div style="border-top:1px solid var(--border); margin:4px 0;"></div>';
      html += `<div class="tt-head"><span class="tt-title ${trade.side}">${sideLabel}</span><span class="tt-time ${trade.side}">${dateStr} ${timeStr}</span></div>`;
      html += '<div class="tt-body">';
      html += `<div class="tt-row"><span class="tt-key">Price</span><span class="tt-val">${trade.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>`;
      html += '</div>';
    });
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    const lRect = label.getBoundingClientRect();
    const tooltipWidth = tooltip.offsetWidth || 140;
    const tooltipHeight = tooltip.offsetHeight || 80;
    let left = lRect.right + 6;
    if (left + tooltipWidth > window.innerWidth - 10) left = lRect.left - tooltipWidth - 6;
    let top = lRect.top - 8;
    if (top + tooltipHeight > window.innerHeight - 10) top = window.innerHeight - tooltipHeight - 10;
    if (top < 4) top = 4;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  });
  label.addEventListener('mouseleave', () => {
    _dashTtHideTimer = setTimeout(() => {
      const tooltip = _getDashTooltip();
      tooltip.style.display = 'none';
      _dashTtHideTimer = null;
    }, 150);
  });
}

function _renderDashTradeOverlay() {
  const overlay = document.getElementById('trade-overlay');
  if (!overlay || !_dashChart || !_dashCandleSeries || !_dashTradeGroups) return;
  overlay.innerHTML = '';

  const container = document.getElementById(_dashContainerId);
  if (!container) return;
  const rect = container.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;

  const range = _dashChart.timeScale().getVisibleRange();
  if (!range) return;

  const frag = document.createDocumentFragment();

  for (const timeKey of Object.keys(_dashTradeGroups)) {
    const time = Number(timeKey);
    if (time < range.from || time > range.to) continue;

    const x = _dashChart.timeScale().timeToCoordinate(time);
    if (x === null || x < 0 || x > rect.width) continue;

    const trades = _dashTradeGroups[timeKey];
    const candle = _dashCandleMap[time];
    const buys = trades.filter(t => t.side === 'buy');
    const sells = trades.filter(t => t.side === 'sell');

    // Buy group (below candle)
    if (buys.length) {
      const avgPrice = buys.reduce((sum, trade) => sum + trade.price, 0) / buys.length;
      const dotY = _dashCandleSeries.priceToCoordinate(avgPrice);
      if (dotY !== null && dotY >= 0 && dotY <= rect.height) {
        const lowY = _dashCandleSeries.priceToCoordinate(candle ? candle.low : avgPrice);
        const anchorY = lowY !== null ? lowY : dotY;

        const dot = document.createElement('div');
        dot.className = 'trade-dot buy';
        dot.style.cssText = `left:${x - 3}px;top:${dotY - 3}px`;
        frag.appendChild(dot);

        const labelOffset = 14;
        const labelY = anchorY + labelOffset;
        const line = document.createElement('div');
        line.className = 'trade-line';
        line.style.cssText = `left:${x}px;top:${anchorY}px;height:${Math.max(0, labelY - anchorY)}px`;
        frag.appendChild(line);

        const label = document.createElement('div');
        label.className = 'trade-label buy';
        label.textContent = buys.length > 1 ? `B${buys.length}` : 'B';
        label.style.cssText = `left:${x - 8}px;top:${labelY}px`;
        frag.appendChild(label);

        _attachDashTooltip(label, buys);
      }
    }

    // Sell group (above candle)
    if (sells.length) {
      const avgPrice = sells.reduce((sum, trade) => sum + trade.price, 0) / sells.length;
      const dotY = _dashCandleSeries.priceToCoordinate(avgPrice);
      if (dotY !== null && dotY >= 0 && dotY <= rect.height) {
        const highY = _dashCandleSeries.priceToCoordinate(candle ? candle.high : avgPrice);
        const anchorY = highY !== null ? highY : dotY;

        const dot = document.createElement('div');
        dot.className = 'trade-dot sell';
        dot.style.cssText = `left:${x - 3}px;top:${dotY - 3}px`;
        frag.appendChild(dot);

        const labelOffset = 14;
        const labelY = anchorY - labelOffset - 12;
        const line = document.createElement('div');
        line.className = 'trade-line';
        line.style.cssText = `left:${x}px;top:${labelY + 12}px;height:${Math.max(0, anchorY - (labelY + 12))}px`;
        frag.appendChild(line);

        const label = document.createElement('div');
        label.className = 'trade-label sell';
        label.textContent = sells.length > 1 ? `S${sells.length}` : 'S';
        label.style.cssText = `left:${x - 8}px;top:${labelY}px`;
        frag.appendChild(label);

        _attachDashTooltip(label, sells);
      }
    }
  }

  overlay.appendChild(frag);
}

async function loadAssetStatus(accountId) {
  const el = document.getElementById('asset-status');
  if (!el) return;
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/asset-status');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const assetData = await resp.json();

    const symbols = assetData.held_symbols || [];
    const totalHeldValue = symbols.reduce((sum, holding) => sum + holding.value_usdt, 0);
    const totalPnl = symbols.reduce((sum, holding) => sum + holding.pnl_usdt, 0);
    const portfolioValue = totalHeldValue + (assetData.reserve_pool_usdt || 0) + (assetData.usdt_balance || 0);
    const pnlPct = assetData.total_invested_usdt > 0 ? (totalPnl / assetData.total_invested_usdt * 100) : 0;
    const pnlColor = totalPnl >= 0 ? 'var(--success)' : 'var(--danger, #ef4444)';
    const pnlSign = totalPnl >= 0 ? '+' : '';

    // --- P1-1: 자산 구성 바 데이터 ---
    const compositionItems = [];
    const barColors = ['#f7931a', '#627eea', '#26a17b', '#e84142', '#2775ca', '#8247e5', '#00d1b2', '#ff6b6b'];
    symbols.forEach((holding, i) => {
      if (holding.value_usdt > 0) {
        compositionItems.push({
          label: holding.symbol.replace('USDT', ''),
          value: holding.value_usdt,
          color: barColors[i % barColors.length]
        });
      }
    });
    if ((assetData.reserve_pool_usdt || 0) > 0) {
      compositionItems.push({ label: 'Reserve', value: assetData.reserve_pool_usdt, color: '#6366f1' });
    }
    if ((assetData.usdt_balance || 0) > 0) {
      compositionItems.push({ label: 'USDT', value: assetData.usdt_balance, color: '#94a3b8' });
    }
    const compTotal = compositionItems.reduce((s, c) => s + c.value, 0);

    // --- P1: 포트폴리오 요약 헤더 ---
    const summaryHtml = `
    <div class="asset-card asset-card--summary" style="background: linear-gradient(135deg, var(--bg-floating) 0%, var(--bg-secondary) 100%);">
      <div style="display:flex;flex-wrap:wrap;gap:1.5rem;align-items:flex-start;justify-content:space-between;">
        <div style="flex:1;min-width:160px;">
          <div class="asset-label">포트폴리오 평가금액</div>
          <div style="font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums;margin:0.25rem 0;">
            ${fmt(portfolioValue, 2)} <span style="font-size:0.9rem;color:var(--text-muted);">USDT</span>
          </div>
          <div class="asset-sub">투자원금 ${fmt(assetData.total_invested_usdt, 2)} USDT</div>
          <div class="asset-sub">가용 잔고 ${fmt(assetData.free_balance_usdt || 0, 2)} USDT</div>
        </div>
        <div style="flex:0 0 auto;min-width:140px;text-align:right;">
          <div class="asset-label">미실현 손익</div>
          <div style="font-size:1.3rem;font-weight:700;font-variant-numeric:tabular-nums;color:${pnlColor};margin:0.25rem 0;">
            ${pnlSign}${fmt(totalPnl, 2)} USDT
          </div>
          <div style="font-size:0.8rem;color:${pnlColor};font-weight:600;">${pnlSign}${pnlPct.toFixed(2)}%</div>
          ${assetData.total_invested_usdt > 0 ? `
          <div style="margin-top:0.4rem;height:6px;border-radius:3px;background:var(--card-border);overflow:hidden;width:120px;margin-left:auto;">
            <div style="height:100%;border-radius:3px;background:${pnlColor};width:${Math.min(Math.abs(pnlPct), 100)}%;transition:width 0.3s;"></div>
          </div>` : ''}
        </div>
      </div>
      ${compTotal > 0 ? `
      <div style="margin-top:1rem;">
        <div class="asset-label" style="margin-bottom:0.4rem;">자산 구성</div>
        <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--card-border);">
          ${compositionItems.map(c => `<div style="width:${(c.value / compTotal * 100).toFixed(1)}%;background:${c.color};transition:width 0.3s;" title="${c.label}: ${fmt(c.value, 2)} USDT (${(c.value / compTotal * 100).toFixed(1)}%)"></div>`).join('')}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:0.5rem 1rem;margin-top:0.4rem;">
          ${compositionItems.map(c => `<span style="font-size:0.7rem;color:var(--text-muted);display:flex;align-items:center;gap:0.3rem;"><span style="width:8px;height:8px;border-radius:2px;background:${c.color};display:inline-block;"></span>${c.label} ${(c.value / compTotal * 100).toFixed(1)}%</span>`).join('')}
        </div>
      </div>` : ''}
    </div>`;

    // --- Held Symbols 테이블 ---
    let symbolsHtml = '';
    if (symbols.length > 0) {
      symbolsHtml = `
      <div class="asset-card asset-card--symbols">
        <div class="asset-card-row" style="justify-content:space-between;align-items:center;">
          <div style="display:flex;align-items:center;gap:0.75rem;">
            <div class="asset-icon asset-icon-warning">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.5 8H14a2 2 0 0 1 0 4h-4.5m0-4v8m0-4H14a2 2 0 0 1 0 4H9.5m2-10v2m0 8v2"/></svg>
            </div>
            <div>
              <div class="asset-label">보유 심볼</div>
              <div class="asset-value">${fmt(totalHeldValue, 2)} USDT</div>
            </div>
          </div>
          <div class="asset-sub">${symbols.length}개 심볼</div>
        </div>
        <div class="held-symbols-table" style="margin-top:0.75rem;">
          <table style="width:100%;border-collapse:collapse;font-size:0.8rem;">
            <thead>
              <tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--card-border);">
                <th style="padding:0.4rem 0.5rem;font-weight:600;">심볼</th>
                <th style="padding:0.4rem 0.5rem;font-weight:600;text-align:right;">수량</th>
                <th style="padding:0.4rem 0.5rem;font-weight:600;text-align:right;">평균단가</th>
                <th style="padding:0.4rem 0.5rem;font-weight:600;text-align:right;">현재가</th>
                <th style="padding:0.4rem 0.5rem;font-weight:600;text-align:right;">평가금액</th>
                <th style="padding:0.4rem 0.5rem;font-weight:600;text-align:right;">손익</th>
              </tr>
            </thead>
            <tbody>
              ${symbols.map(holding => {
                const holdingPnlColor = holding.pnl_usdt >= 0 ? 'var(--success)' : 'var(--danger, #ef4444)';
                const holdingPnlSign = holding.pnl_usdt >= 0 ? '+' : '';
                return `<tr style="border-bottom:1px solid var(--card-border);">
                  <td style="padding:0.4rem 0.5rem;font-weight:600;">${escapeHtml(holding.symbol).replace('USDT','')}</td>
                  <td style="padding:0.4rem 0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(holding.qty, 6)}</td>
                  <td style="padding:0.4rem 0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(holding.avg_entry, 2)}</td>
                  <td style="padding:0.4rem 0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(holding.current_price, 2)}</td>
                  <td style="padding:0.4rem 0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(holding.value_usdt, 2)}</td>
                  <td style="padding:0.4rem 0.5rem;text-align:right;font-variant-numeric:tabular-nums;color:${holdingPnlColor};">${holdingPnlSign}${fmt(holding.pnl_usdt, 2)} (${holdingPnlSign}${holding.pnl_pct}%)</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
    } else {
      symbolsHtml = `
      <div class="asset-card asset-card--symbols">
        <div class="asset-card-row">
          <div class="asset-icon asset-icon-warning">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.5 8H14a2 2 0 0 1 0 4h-4.5m0-4v8m0-4H14a2 2 0 0 1 0 4H9.5m2-10v2m0 8v2"/></svg>
          </div>
          <div>
            <div class="asset-label">보유 심볼</div>
            <div class="asset-value">없음</div>
          </div>
        </div>
      </div>`;
    }

    el.innerHTML = `
      ${summaryHtml}
      ${symbolsHtml}
      <div class="asset-card--sidestack">
        <div class="asset-card">
          <div class="asset-card-row">
            <div class="asset-icon asset-icon-info">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </div>
            <div>
              <div class="asset-label">Reserve Pool</div>
              <div class="asset-value">${fmt(assetData.reserve_pool_usdt, 2)} USDT</div>
              <div class="asset-sub">${assetData.reserve_pool_qty != null ? fmt(assetData.reserve_pool_qty, 6) + ' qty' : ''} · ${assetData.reserve_pool_pct != null ? assetData.reserve_pool_pct + '%' : ''}</div>
            </div>
          </div>
        </div>
        <div class="asset-card earnings-card ${(assetData.pending_earnings_usdt || 0) > 0 ? 'has-earnings' : ''}">
          <div class="asset-card-row">
            <div class="asset-icon asset-icon-orange">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>
            </div>
            <div>
              <div class="asset-label">적립금 (Pending)</div>
              <div class="asset-value">${fmt(assetData.pending_earnings_usdt || 0, 2)} USDT</div>
            </div>
          </div>
          <button class="btn btn-sm btn-approve" style="margin-top:0.5rem;"
                  onclick="openEarningsModal('${accountId}')"
                  ${(assetData.pending_earnings_usdt || 0) <= 0 ? 'disabled' : ''}>
            Reserve 추가
          </button>
        </div>
        <div class="asset-card">
          <div class="asset-card-row">
            <div class="asset-icon asset-icon-primary">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>
            </div>
            <div>
              <div class="asset-label">투자원금</div>
              <div class="asset-value">${fmt(assetData.total_invested_usdt, 2)} USDT</div>
            </div>
          </div>
        </div>
      </div>
      ${_renderRealizedPnl(assetData)}
      ${_renderOpenLotsBySymbol(assetData)}
    `;
  } catch (e) {
    el.innerHTML = '<p class="error-text">Failed to load asset status</p>';
  }
}

function _renderRealizedPnl(data) {
  const todayColor = (data.realized_pnl_today || 0) >= 0 ? 'var(--success)' : 'var(--danger, #ef4444)';
  const weekColor = (data.realized_pnl_week || 0) >= 0 ? 'var(--success)' : 'var(--danger, #ef4444)';
  const todaySign = (data.realized_pnl_today || 0) >= 0 ? '+' : '';
  const weekSign = (data.realized_pnl_week || 0) >= 0 ? '+' : '';
  return `
  <div class="asset-card asset-card--realized">
    <div class="asset-label" style="margin-bottom:0.5rem;">실현 손익</div>
    <div style="display:flex;gap:2rem;flex-wrap:wrap;">
      <div style="flex:1;min-width:140px;">
        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.2rem;">오늘</div>
        <div style="font-size:1.1rem;font-weight:700;font-variant-numeric:tabular-nums;color:${todayColor};">
          ${todaySign}${fmt(data.realized_pnl_today || 0, 2)} USDT
        </div>
        <div class="asset-sub">${data.closed_lots_today || 0}건 청산</div>
      </div>
      <div style="flex:1;min-width:140px;">
        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.2rem;">이번 주</div>
        <div style="font-size:1.1rem;font-weight:700;font-variant-numeric:tabular-nums;color:${weekColor};">
          ${weekSign}${fmt(data.realized_pnl_week || 0, 2)} USDT
        </div>
        <div class="asset-sub">${data.closed_lots_week || 0}건 청산</div>
      </div>
    </div>
  </div>`;
}

function _formatHoldingDuration(hours) {
  if (hours < 1) return Math.round(hours * 60) + '분';
  if (hours < 24) return hours.toFixed(1) + '시간';
  const days = Math.floor(hours / 24);
  const rem = Math.round(hours % 24);
  return days + '일 ' + rem + '시간';
}

function _renderOpenLotsBySymbol(data) {
  const lots = data.open_lots_by_symbol || [];
  if (lots.length === 0) return '';
  const totalCount = lots.reduce((s, l) => s + l.count, 0);
  return `
  <div class="asset-card asset-card--open-lots">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
      <div class="asset-label">심볼별 오픈 Lot</div>
      <div class="asset-sub">총 ${totalCount}건</div>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:0.5rem;">
      ${lots.map(l => `
        <div style="background:var(--bg-secondary);border:1px solid var(--card-border);border-radius:var(--radius);padding:0.5rem 0.75rem;min-width:120px;flex:1;">
          <div style="font-weight:600;font-size:0.85rem;">${escapeHtml(l.symbol).replace('USDT','')}</div>
          <div style="font-size:1rem;font-weight:700;font-variant-numeric:tabular-nums;">${l.count}건</div>
          <div class="asset-sub">보유 ${_formatHoldingDuration(l.holding_hours)}</div>
        </div>
      `).join('')}
    </div>
  </div>`;
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
  apiFetch('/api/dashboard/' + accountId + '/pending-earnings')
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
    const resp = await apiFetch('/api/dashboard/' + _earningsAccountId + '/approve-earnings', {
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
let _lotsPage = 0;
const LOTS_PER_PAGE = 30;

function switchLotStatus(status) {
  document.querySelectorAll('#lot-status-tabs .filter-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.status === status);
  });
  document.getElementById('lots-open-view').style.display = (status === 'OPEN' || status === 'ALL') ? '' : 'none';
  document.getElementById('lots-closed-view').style.display = (status === 'CLOSED' || status === 'ALL') ? '' : 'none';
  // 탭 전환 시 테이블 재렌더링
  if (status === 'OPEN' || status === 'ALL') _renderLots(_currentLotFilter);
  if (status === 'CLOSED' || status === 'ALL') _renderClosedLots();
}

/**
 * Shared pagination renderer with ellipsis support.
 * @param {object} opts
 * @param {string}   opts.containerId  - ID of the container element
 * @param {number}   opts.total        - total item count
 * @param {number}   opts.totalPages   - total page count
 * @param {number}   opts.currentPage  - zero-based current page index
 * @param {number}   opts.perPage      - items per page
 * @param {string}   opts.goPageFn     - name of the global go-page function
 */
function _renderPagination({ containerId, total, totalPages, currentPage, perPage, goPageFn }) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (totalPages <= 1) { container.innerHTML = ''; return; }

  const start = currentPage * perPage + 1;
  const end = Math.min((currentPage + 1) * perPage, total);
  const isFirst = currentPage === 0;
  const isLast = currentPage >= totalPages - 1;

  // Build page number list with ellipsis
  const pages = [];
  if (totalPages <= 7) {
    for (let i = 0; i < totalPages; i++) pages.push(i);
  } else {
    pages.push(0);
    if (currentPage > 3) pages.push('...');
    for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages - 2, currentPage + 2); i++) {
      pages.push(i);
    }
    if (currentPage < totalPages - 4) pages.push('...');
    pages.push(totalPages - 1);
  }

  let html = '<div class="pagination">';
  html += `<span class="pagination-info">${total}건 중 ${start}–${end}건</span>`;

  // Prev arrow
  html += `<button class="pagination-nav" onclick="${goPageFn}(${currentPage - 1})" ${isFirst ? 'disabled' : ''} aria-label="이전">&#8249;</button>`;

  // Page buttons
  for (const p of pages) {
    if (p === '...') {
      html += `<span class="pagination-btn pagination-ellipsis">…</span>`;
    } else {
      html += `<button class="pagination-btn${p === currentPage ? ' active' : ''}" onclick="${goPageFn}(${p})" aria-label="${p + 1}페이지"${p === currentPage ? ' aria-current="page"' : ''}>${p + 1}</button>`;
    }
  }

  // Next arrow
  html += `<button class="pagination-nav" onclick="${goPageFn}(${currentPage + 1})" ${isLast ? 'disabled' : ''} aria-label="다음">&#8250;</button>`;
  html += '</div>';

  container.innerHTML = html;
}

async function loadLots(accountId) {
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/lots');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    _allLots = await resp.json();
    _lotsPage = 0;
    _renderLots(_currentLotFilter);
  } catch (e) {
    const tbody = document.getElementById('lots-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="table-empty error-text">Failed to load lots: ' + escapeHtml(e.message) + '</td></tr>';
  }
}

function filterLots(filter) {
  _currentLotFilter = filter;
  _lotsPage = 0;
  _renderLots(filter);
}

function lotsGoPage(page) {
  _lotsPage = page;
  _renderLots(_currentLotFilter);
}

function _renderLots(filter) {
  document.querySelectorAll('#lot-filter-tabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === filter);
  });
  const filtered = filter === 'all' ? _allLots : _allLots.filter(l => l.combo_id === filter);
  const tbody = document.getElementById('lots-tbody');
  if (!tbody) return;
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No lots found</td></tr>';
    _renderLotsPagination(0, 0);
    return;
  }

  const totalPages = Math.ceil(filtered.length / LOTS_PER_PAGE);
  if (_lotsPage >= totalPages) _lotsPage = totalPages - 1;
  const start = _lotsPage * LOTS_PER_PAGE;
  const page = filtered.slice(start, start + LOTS_PER_PAGE);

  tbody.innerHTML = page.map((lot, i) => {
    const pnl = lot.pnl_pct;
    const pnlClass = pnl == null ? '' : (pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    return `<tr>
      <td>${start + i + 1}</td>
      <td><span class="strategy-badge">${escapeHtml(lot.strategy || '-')}</span></td>
      <td>${fmt(lot.buy_price, 2)}</td>
      <td>${fmt(lot.qty, 6)}</td>
      <td>${fmt(lot.cost_usdt, 2)}</td>
      <td>${lot.sell_order_price ? fmt(lot.sell_order_price, 2) : '-'}</td>
      <td class="${pnlClass}">${pnl != null ? pnl.toFixed(2) + '%' : '-'}</td>
      <td><span class="order-status">${escapeHtml(lot.sell_order_status || '-')}</span></td>
    </tr>`;
  }).join('');

  _renderLotsPagination(filtered.length, totalPages);
}

function _renderLotsPagination(total, totalPages) {
  _renderPagination({
    containerId: 'lots-pagination',
    total,
    totalPages,
    currentPage: _lotsPage,
    perPage: LOTS_PER_PAGE,
    goPageFn: 'lotsGoPage',
  });
}

/* ============================================================
   Closed Lots
   ============================================================ */

let _closedLots = [];
let _closedLotsPage = 0;
const CLOSED_LOTS_PER_PAGE = 30;

async function loadClosedLots(accountId) {
  try {
    const resp = await apiFetch('/api/dashboard/' + accountId + '/lots?status=CLOSED&limit=200');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    _closedLots = await resp.json();
    _closedLotsPage = 0;
    _renderClosedLots();
  } catch (e) {
    const tbody = document.getElementById('closed-lots-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="table-empty error-text">Failed to load closed lots: ' + escapeHtml(e.message) + '</td></tr>';
  }
}

function closedLotsGoPage(page) {
  _closedLotsPage = page;
  _renderClosedLots();
}

function _renderClosedLots() {
  const tbody = document.getElementById('closed-lots-tbody');
  if (!tbody) return;
  if (!_closedLots.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="table-empty">거래 완료된 항목이 없습니다</td></tr>';
    _renderClosedLotsPagination(0, 0);
    return;
  }

  const totalPages = Math.ceil(_closedLots.length / CLOSED_LOTS_PER_PAGE);
  if (_closedLotsPage >= totalPages) _closedLotsPage = totalPages - 1;
  const start = _closedLotsPage * CLOSED_LOTS_PER_PAGE;
  const page = _closedLots.slice(start, start + CLOSED_LOTS_PER_PAGE);

  tbody.innerHTML = page.map((lot, i) => {
    const profit = lot.net_profit_usdt;
    const profitClass = profit == null ? '' : (profit >= 0 ? 'pnl-positive' : 'pnl-negative');
    const profitStr = profit != null ? (profit >= 0 ? '+' : '') + fmt(profit, 2) : '-';
    const sellPrice = lot.sell_price != null ? fmt(lot.sell_price, 2) : '-';
    const cost = fmt(lot.buy_price * lot.buy_qty, 2);
    let sellTime = '';
    if (lot.sell_time) {
      try {
        const d = new Date(lot.sell_time);
        const kst = new Date(d.getTime() + 9 * 3600000);
        sellTime = kst.getUTCFullYear() + '-'
          + String(kst.getUTCMonth() + 1).padStart(2, '0') + '-'
          + String(kst.getUTCDate()).padStart(2, '0') + ' '
          + String(kst.getUTCHours()).padStart(2, '0') + ':'
          + String(kst.getUTCMinutes()).padStart(2, '0');
      } catch (_) {}
    }
    return `<tr>
      <td>${start + i + 1}</td>
      <td><span class="strategy-badge">${escapeHtml(lot.strategy || '-')}</span></td>
      <td>${fmt(lot.buy_price, 2)}</td>
      <td>${fmt(lot.qty, 6)}</td>
      <td>${cost}</td>
      <td>${sellPrice}</td>
      <td class="${profitClass}">${profitStr}</td>
      <td>${sellTime || '-'}</td>
    </tr>`;
  }).join('');

  _renderClosedLotsPagination(_closedLots.length, totalPages);
}

function _renderClosedLotsPagination(total, totalPages) {
  _renderPagination({
    containerId: 'closed-lots-pagination',
    total,
    totalPages,
    currentPage: _closedLotsPage,
    perPage: CLOSED_LOTS_PER_PAGE,
    goPageFn: 'closedLotsGoPage',
  });
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
    container.innerHTML = '<p style="color:#94a3b8;">콤보가 없습니다. "+ New Combo"를 클릭하여 생성하세요.</p>';
    return;
  }

  container.innerHTML = _combos.map(combo => {
    const buyMeta = _buyLogics.find(b => b.name === combo.buy_logic_name) || {};
    const sellMeta = _sellLogics.find(s => s.name === combo.sell_logic_name) || {};
    const enabledClass = combo.is_enabled ? 'badge-success' : 'badge-neutral';
    const enabledText = combo.is_enabled ? 'ON' : 'OFF';

    const bpKeys = Object.keys(combo.buy_params || {}).filter(k => !k.startsWith('_'));
    const spKeys = Object.keys(combo.sell_params || {});
    const totalParams = bpKeys.length + spKeys.length;

    let symbolsHtml = '';
    if (combo.symbols && combo.symbols.length) {
      const MAX_SHOW = 5;
      const syms = combo.symbols;
      const visible = syms.slice(0, MAX_SHOW).map(s => `<span class="symbol-tag">${escapeHtml(s)}</span>`).join('');
      const moreId = 'combo-syms-' + combo.id;
      const hidden = syms.length > MAX_SHOW
        ? `<span class="combo-symbols-more" onclick="document.getElementById('${moreId}').classList.add('show');this.style.display='none'">+${syms.length - MAX_SHOW}개 더</span>` +
          `<span class="combo-symbols-hidden" id="${moreId}">${syms.slice(MAX_SHOW).map(s => `<span class="symbol-tag">${escapeHtml(s)}</span>`).join('')}</span>`
        : '';
      symbolsHtml = `<div class="combo-symbols-row">${visible}${hidden}</div>`;
    }

    let paramsSection = '';
    if (totalParams) {
      const buyIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M2 12l10-10 10 10"/></svg>';
      const sellIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22V2M2 12l10 10 10-10"/></svg>';

      let buyCard = '';
      if (bpKeys.length) {
        const buyRows = bpKeys.map(k =>
          `<div class="combo-param-row"><span class="combo-param-key">${escapeHtml(k)}</span><span class="combo-param-val">${escapeHtml(String(combo.buy_params[k]))}</span></div>`
        ).join('');
        buyCard = `<div class="combo-param-card" data-accent="buy"><div class="combo-param-card-title">${buyIcon} 매수 파라미터</div>${buyRows}</div>`;
      }

      let sellCard = '';
      if (spKeys.length) {
        const sellRows = spKeys.map(k =>
          `<div class="combo-param-row"><span class="combo-param-key">${escapeHtml(k)}</span><span class="combo-param-val">${escapeHtml(String(combo.sell_params[k]))}</span></div>`
        ).join('');
        sellCard = `<div class="combo-param-card" data-accent="sell"><div class="combo-param-card-title">${sellIcon} 매도 파라미터</div>${sellRows}</div>`;
      }

      const toggleId = 'combo-params-' + combo.id;
      paramsSection = `
        <button class="combo-params-toggle" onclick="this.classList.toggle('open');document.getElementById('${toggleId}').classList.toggle('open')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          파라미터 ${totalParams}개
        </button>
        <div class="combo-params-wrap" id="${toggleId}">
          <div class="combo-params-section">${buyCard}${sellCard}</div>
        </div>`;
    }

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
          <span class="combo-logic-pill combo-logic-buy"><span class="combo-logic-dot"></span> 매수: ${escapeHtml(buyMeta.display_name || combo.buy_logic_name)}</span>
          <span class="combo-logic-pill combo-logic-sell"><span class="combo-logic-dot"></span> 매도: ${escapeHtml(sellMeta.display_name || combo.sell_logic_name)}</span>
        </div>
        ${symbolsHtml}
        ${paramsSection}
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
const WIZARD_STEP_COUNT = 5;
const WIZARD_CHECK_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5L6.5 11.5L12.5 5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

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
      circle.innerHTML = WIZARD_CHECK_SVG;
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
  nextBtn.style.display = _comboWizardStep === WIZARD_STEP_COUNT ? 'none' : '';
  saveBtn.style.display = _comboWizardStep === WIZARD_STEP_COUNT ? '' : 'none';

  // Edit-mode: hide logic selects in steps 2 & 4, show reapply in step 5
  if (isEdit) {
    document.getElementById('combo-buy-logic-group').style.display = 'none';
    document.getElementById('combo-sell-logic-group').style.display = 'none';
    document.getElementById('combo-reapply-group').style.display =
      _comboWizardStep === WIZARD_STEP_COUNT ? '' : 'none';
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
  if (_comboWizardStep < WIZARD_STEP_COUNT) {
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

/* ============================================================
   Multi-Symbol Tag Management
   ============================================================ */
let _comboSymbols = [];

function addComboSymbol() {
  const input = document.getElementById('combo-symbol-input');
  const symbol = input.value.trim().toUpperCase();
  if (!symbol) return;
  if (!symbol.endsWith('USDT') && !symbol.endsWith('BTC') && !symbol.endsWith('ETH')) {
    showToast('Invalid symbol format (e.g. ETHUSDT)', 'error');
    return;
  }
  if (_comboSymbols.includes(symbol)) {
    input.value = '';
    return;
  }
  _comboSymbols.push(symbol);
  input.value = '';
  renderComboSymbolTags();
}

function addComboSymbolDirect(symbol) {
  if (_comboSymbols.includes(symbol)) return;
  _comboSymbols.push(symbol);
  renderComboSymbolTags();
}

function removeComboSymbol(symbol) {
  _comboSymbols = _comboSymbols.filter(s => s !== symbol);
  renderComboSymbolTags();
}

function renderComboSymbolTags() {
  const container = document.getElementById('combo-symbol-tags');
  if (!container) return;
  container.innerHTML = _comboSymbols.map(s =>
    `<span class="symbol-tag">${escapeHtml(s)}<button class="symbol-tag-remove" onclick="removeComboSymbol('${escapeHtml(s)}')">&times;</button></span>`
  ).join('');
}

function openCreateComboModal() {
  document.getElementById('combo-modal-title').textContent = 'New Combo';
  document.getElementById('combo-edit-id').value = '';
  document.getElementById('combo-name').value = '';
  _comboSymbols = [];
  renderComboSymbolTags();
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
  _comboSymbols = Array.isArray(combo.symbols) ? combo.symbols.slice() : [];
  renderComboSymbolTags();
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
  const method = isEdit ? 'PATCH' : 'POST';

  if (_comboSymbols.length === 0) { showToast('At least one symbol is required', 'error'); return; }

  const body = { name, buy_params: buyParams, sell_params: sellParams, symbols: _comboSymbols.slice() };
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
   Protection Status (Circuit Breaker + Buy Pause combined)
   ============================================================ */

async function loadProtectionStatus(accountId) {
  const el = document.getElementById('protection-status-panel');
  if (!el) return;

  try {
    const [accResp, dashResp] = await Promise.all([
      apiFetch('/api/accounts/' + accountId),
      apiFetch('/api/dashboard/' + accountId),
    ]);
    if (!accResp.ok || !dashResp.ok) throw new Error('API error');
    const accData = await accResp.json();
    const dashData = await dashResp.json();

    const tripped = !!accData.circuit_breaker_tripped;
    const cbFailures = accData.circuit_breaker_failures || 0;
    const cbDisabledAt = accData.circuit_breaker_disabled_at;
    const lastSuccess = accData.last_success_at;
    const bp = dashData.buy_pause || { state: 'ACTIVE', reason: null, since: null, consecutive_low_balance: 0 };
    const bpActive = bp.state === 'ACTIVE';
    const isPaused = bp.state === 'PAUSED';

    // — Buy Pause card (top) —
    let bpDot = 'ok', bpLabel = '정상', bpColor = 'success';
    let bpInfoRows = '';
    let bpAction = '';

    if (!bpActive) {
      bpDot = isPaused ? 'danger' : 'warn';
      bpLabel = isPaused ? '매수 중단' : '매수 감속';
      bpColor = isPaused ? 'danger' : 'warning';
      const reason = bp.reason === 'LOW_BALANCE' ? '잔고 부족' : (bp.reason || '알 수 없음');
      const since = bp.since ? new Date(bp.since).toLocaleString() : '-';
      const count = bp.consecutive_low_balance || 0;
      bpInfoRows = `
        <div class="prot-info-grid">
          <div class="prot-info-item"><span class="prot-info-label">사유</span><span class="prot-info-val">${reason}</span></div>
          <div class="prot-info-item"><span class="prot-info-label">발생 시점</span><span class="prot-info-val">${since}</span></div>
          <div class="prot-info-item"><span class="prot-info-label">연속 횟수</span><span class="prot-info-val">${count}회</span></div>
          <div class="prot-info-item"><span class="prot-info-label">상태</span><span class="prot-info-val">${isPaused ? '매도 감지 시 자동 재개' : '5사이클당 1회 매수'}</span></div>
        </div>`;
      bpAction = `<button class="btn btn-primary btn-sm prot-action-btn" onclick="resumeBuying('${accountId}')">매수 재개</button>`;
    }

    const bpHtml = `
      <div class="protection-card ${!bpActive ? 'prot-alert' : ''}">
        <div class="prot-header">
          <div class="prot-icon-wrap prot-icon-${bpColor}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="10" y1="15" x2="10" y2="9"/><line x1="14" y1="15" x2="14" y2="9"/></svg>
          </div>
          <div class="prot-header-text">
            <div class="prot-label">매수 일시정지</div>
            <div class="prot-status"><span class="protection-dot ${bpDot}"></span>${bpLabel}</div>
          </div>
          ${bpAction}
        </div>
        ${bpInfoRows}
      </div>`;

    // — Circuit Breaker card (bottom) —
    let cbDot = 'ok', cbLabel = '정상', cbColor = 'success';
    let cbInfoRows = '';
    let cbAction = '';

    if (tripped) {
      cbDot = 'danger';
      cbLabel = '발동됨 — 거래 중단';
      cbColor = 'danger';
      const disabledSince = cbDisabledAt ? new Date(cbDisabledAt).toLocaleString() : '-';
      const lastOk = lastSuccess ? new Date(lastSuccess).toLocaleString() : '-';
      cbInfoRows = `
        <div class="prot-info-grid">
          <div class="prot-info-item"><span class="prot-info-label">사유</span><span class="prot-info-val">연속 ${cbFailures}회 실패</span></div>
          <div class="prot-info-item"><span class="prot-info-label">발동 시점</span><span class="prot-info-val">${disabledSince}</span></div>
          <div class="prot-info-item"><span class="prot-info-label">마지막 정상 거래</span><span class="prot-info-val">${lastOk}</span></div>
        </div>`;
      cbAction = `<button class="btn btn-danger btn-sm prot-action-btn" onclick="resetCircuitBreaker('${accountId}')">CB 초기화</button>`;
    } else if (cbFailures > 0) {
      cbDot = 'warn';
      cbLabel = '주의 (' + cbFailures + '/5 실패)';
      cbColor = 'warning';
      cbInfoRows = `
        <div class="prot-info-grid">
          <div class="prot-info-item"><span class="prot-info-label">상태</span><span class="prot-info-val">실패 누적 중 (${cbFailures}/5)</span></div>
        </div>`;
    }

    const cbHtml = `
      <div class="protection-card ${tripped ? 'prot-alert' : ''}">
        <div class="prot-header">
          <div class="prot-icon-wrap prot-icon-${cbColor}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          </div>
          <div class="prot-header-text">
            <div class="prot-label">서킷 브레이커</div>
            <div class="prot-status"><span class="protection-dot ${cbDot}"></span>${cbLabel}</div>
          </div>
          ${cbAction}
        </div>
        ${cbInfoRows}
      </div>`;

    el.innerHTML = bpHtml + cbHtml;
  } catch (e) {
    el.innerHTML = '<p class="error-text">보호 상태를 불러오지 못했습니다</p>';
  }
}

async function resetCircuitBreaker(accountId) {
  if (!confirm('서킷 브레이커를 초기화하시겠습니까? 거래가 재개됩니다.')) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/reset-circuit-breaker', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('서킷 브레이커가 초기화되었습니다', 'success');
      loadProtectionStatus(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('오류: ' + (err.detail || '초기화 실패'), 'error');
    }
  } catch (e) {
    showToast('네트워크 오류: ' + e.message, 'error');
  }
}

async function resumeBuying(accountId) {
  if (!confirm('매수를 재개하시겠습니까?')) return;
  try {
    const resp = await apiFetch('/api/accounts/' + accountId + '/buy-pause/resume', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
    if (resp.ok) {
      showToast('매수가 재개되었습니다', 'success');
      loadProtectionStatus(accountId);
    } else {
      const err = await resp.json().catch(() => ({}));
      showToast('오류: ' + (err.detail || '재개 실패'), 'error');
    }
  } catch (e) {
    showToast('네트워크 오류: ' + e.message, 'error');
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
      document.getElementById('bp-initial-usdt').value = parsed.toFixed(2);
    }
  }
  // plan_x_pct 값 동기화
  const xPctInput = document.querySelector('[data-param="plan_x_pct"]');
  if (xPctInput && xPctInput.value) {
    document.getElementById('bp-x-pct').value = xPctInput.value;
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
    Number(document.getElementById('bp-initial-usdt').value),
    Number(document.getElementById('bp-x-pct').value),
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

  document.getElementById('bp-first-five').innerHTML = result.firstFive.map(r =>
    `<tr><td>${r.round}</td><td>${fmt(r.pct, 2)}%</td><td>${fmt(r.usdt, 2)}</td><td>${fmt(r.remain, 2)}</td></tr>`
  ).join('') || '<tr><td colspan="4">-</td></tr>';

  document.getElementById('bp-after-five').innerHTML = result.after5.map(r =>
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

function formatComboSymbols(symbols, fallbackSymbol) {
  const list = (symbols && symbols.length) ? symbols : (fallbackSymbol ? [fallbackSymbol] : []);
  if (!list.length) return '<span class="text-muted">-</span>';
  const MAX_SHOW = 5;
  const shown = list.slice(0, MAX_SHOW).map(s => escapeHtml(s)).join(', ');
  if (list.length > MAX_SHOW) {
    return shown + ` <span class="text-muted">외 ${list.length - MAX_SHOW}개</span>`;
  }
  return shown;
}

function showToast(msg, type) {
  const existing = document.querySelector('.toast:not(#toast)');
  if (existing) existing.remove();
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

/**
 * Shared admin helper: populate an account <select> dropdown.
 * @param {string} [selectId='filter-account'] - id of the <select> element
 * @param {function} [onLoaded] - optional callback receiving the accounts array
 */
async function loadAccountOptions(selectId, onLoaded) {
  try {
    const resp = await apiFetch('/api/admin/accounts');
    if (!resp.ok) return;
    const accounts = await resp.json();
    const sel = document.getElementById(selectId || 'filter-account');
    if (!sel) return;
    accounts.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.name || a.id;
      sel.appendChild(opt);
    });
    if (onLoaded) onLoaded(accounts);
  } catch (e) {}
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
