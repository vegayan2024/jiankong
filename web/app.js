// ========================================================
// QUANT RADAR - Realtime Financial Monitor Client Logic
// Jiankong Edition: Stocks, Benchmark Indices & Hot ETFs
// ========================================================

const state = {
  quotes: [],
  alerts: [],
  announcements: [],
  news: [],
  groups: [],
  mainFilter: 'ALL',   // ALL | INDEX | ETF | THEMES | TERMINAL | A | HK
  groupFilter: 'ALL',  // ALL | INDEX | ETF | THEMES | TERMINAL | <group-name>
  searchQuery: '',
  viewMode: 'grouped', // 'grouped' | 'card' | 'table'
  sortMode: 'default', // 'default' | 'pct-desc' | 'pct-asc' | 'volatility'
  activeTab: 'tab-alerts',
  newsFilter: 'all',   // 'all' | 'hits'
  pollInterval: null,
  ws: null,
  activeDetailCode: null,
  foldedGroups: new Set(),
  themesSummary: [],
  lastRenderFingerprint: '',
  previousPrices: new Map()
};

// DOM Elements Cache
const el = {
  updateTimer: document.getElementById('update-timer'),
  marketFullDatetime: document.getElementById('market-full-datetime'),
  matrixTimeVal: document.getElementById('matrix-time-val'),
  metricTotal: document.getElementById('metric-total'),
  metricUp: document.getElementById('metric-up'),
  metricDown: document.getElementById('metric-down'),
  metricFlat: document.getElementById('metric-flat'),
  metricAlerts: document.getElementById('metric-alerts'),
  filteredCount: document.getElementById('filtered-count'),
  cardsContainer: document.getElementById('cards-container'),
  tableContainer: document.getElementById('table-container'),
  tableBody: document.getElementById('table-body'),
  marketFilter: document.getElementById('market-filter'),
  groupSelect: document.getElementById('group-select'),
  viewMode: document.getElementById('view-mode'),
  searchInput: document.getElementById('search-input'),
  searchClear: document.getElementById('search-clear'),
  themeInsightSection: document.getElementById('theme-insight-section'),
  themeInsightPromptBar: document.getElementById('theme-insight-prompt-bar'),
  themeInsightGrid: document.getElementById('theme-insight-grid'),
  btnToggleInsightExpand: document.getElementById('btn-toggle-insight-expand'),
  alertsList: document.getElementById('alerts-list'),
  announcementsList: document.getElementById('announcements-list'),
  newsList: document.getElementById('news-list'),
  badgeAlerts: document.getElementById('badge-alerts-count'),
  badgeAnn: document.getElementById('badge-ann-count'),
  badgeNews: document.getElementById('badge-news-count'),
  btnNewsAll: document.getElementById('btn-news-all'),
  btnNewsHits: document.getElementById('btn-news-hits'),
  newsCountAll: document.getElementById('news-count-all'),
  newsCountHits: document.getElementById('news-count-hits'),
  btnRefreshNews: document.getElementById('btn-refresh-news'),
  btnClearAlerts: document.getElementById('btn-clear-alerts'),
  modalAdd: document.getElementById('modal-add'),
  btnAddStock: document.getElementById('btn-add-stock'),
  btnCloseModal: document.getElementById('btn-close-modal'),
  btnCancelModal: document.getElementById('btn-cancel-modal'),
  formAddStock: document.getElementById('form-add-stock'),
  inputAssetType: document.getElementById('input-asset-type'),
  inputCode: document.getElementById('input-code'),
  inputName: document.getElementById('input-name'),
  inputGroup: document.getElementById('input-group'),
  inputThreshold: document.getElementById('input-threshold'),
  inputThresholdRange: document.getElementById('input-threshold-range'),
  btnCheckCode: document.getElementById('btn-check-code'),
  codePreview: document.getElementById('code-preview'),
  btnRefresh: document.getElementById('btn-refresh'),
  refreshIcon: document.getElementById('refresh-icon'),
  // Drawer
  drawerDetail: document.getElementById('drawer-detail'),
  drawerOverlay: document.getElementById('drawer-overlay'),
  drawerTitle: document.getElementById('drawer-title'),
  drawerCode: document.getElementById('drawer-code'),
  drawerBadge: document.getElementById('drawer-badge'),
  drawerSpecs: document.getElementById('drawer-specs'),
  drawerAnns: document.getElementById('drawer-anns'),
  drawerNews: document.getElementById('drawer-news'),
  drawerAlerts: document.getElementById('drawer-alerts'),
  btnCloseDrawer: document.getElementById('btn-close-drawer'),
  // Quick Threshold Modal
  modalThreshold: document.getElementById('modal-threshold'),
  thCodeTitle: document.getElementById('th-code-title'),
  thInputVal: document.getElementById('th-input-val'),
  thInputRange: document.getElementById('th-input-range'),
  btnSubmitTh: document.getElementById('btn-submit-th'),
  btnCancelTh: document.getElementById('btn-cancel-th'),
  btnCloseTh: document.getElementById('btn-close-th'),
  // Toast
  toastContainer: document.getElementById('toast-container')
};

let currentEditingCode = null;

function debounce(fn, delay = 120) {
  let timer = null;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

// ==========================================
// Initialization
// ==========================================
async function initApp() {
  bindEvents();
  await loadGroups();
  await refreshData();
  await fetchThemesSummary();
  updateThemeInsightVisibility();
  setupWebSocket();

  // 3-second high-frequency poll for ultra reliability
  state.pollInterval = setInterval(refreshData, 3000);
}

// ==========================================
// Event Listeners
// ==========================================
function bindEvents() {
  // Main Category / Market Filter Buttons
  if (el.marketFilter) {
    el.marketFilter.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn) return;
      el.marketFilter.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.mainFilter = btn.dataset.filter;
      updateThemeInsightVisibility();
      renderMatrix();
    });
  }

  // Sector / Subcategory Select Dropdown
  if (el.groupSelect) {
    el.groupSelect.addEventListener('change', (e) => {
      state.groupFilter = e.target.value;
      updateThemeInsightVisibility();
      renderMatrix();
    });
  }

  // Toggle Theme Insight Expand Button
  if (el.btnToggleInsightExpand) {
    el.btnToggleInsightExpand.addEventListener('click', () => {
      toggleThemeInsight();
    });
  }

  // View Mode Switcher (Grouped vs Flat Grid vs Table)
  if (el.viewMode) {
    el.viewMode.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn) return;
      el.viewMode.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.viewMode = btn.dataset.view;
      if (state.viewMode === 'table') {
        el.cardsContainer.style.display = 'none';
        el.tableContainer.style.display = 'block';
      } else if (state.viewMode === 'card') {
        el.cardsContainer.style.display = 'grid';
        el.cardsContainer.className = 'cards-grid';
        el.tableContainer.style.display = 'none';
      } else {
        el.cardsContainer.style.display = 'flex';
        el.cardsContainer.className = 'cards-grouped-container';
        el.tableContainer.style.display = 'none';
      }
      renderMatrix();
    });
  }

  // Search Input (with debounce for silky smooth typing)
  if (el.searchInput) {
    const debouncedSearch = debounce(() => {
      renderMatrix();
    }, 120);

    el.searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim().toLowerCase();
      el.searchClear.style.display = state.searchQuery ? 'flex' : 'none';
      debouncedSearch();
    });
  }

  if (el.searchClear) {
    el.searchClear.addEventListener('click', () => {
      el.searchInput.value = '';
      state.searchQuery = '';
      el.searchClear.style.display = 'none';
      renderMatrix();
      el.searchInput.focus();
    });
  }

  // Keyboard shortcut '/' to focus search, ESC to close modals/drawers
  window.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== el.searchInput) {
      e.preventDefault();
      el.searchInput.focus();
    } else if (e.key === 'Escape') {
      closeAllModals();
    }
  });

  // Sort Chips
  document.querySelectorAll('.sort-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.sort-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.sortMode = chip.dataset.sort;
      renderMatrix();
    });
  });

  // Right Side Intel Tabs
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const targetPane = document.getElementById(btn.dataset.tab);
      if (targetPane) targetPane.classList.add('active');
      state.activeTab = btn.dataset.tab;
    });
  });

  // Clear Alerts
  if (el.btnClearAlerts) {
    el.btnClearAlerts.addEventListener('click', async () => {
      try {
        await fetch('/api/alerts/clear', { method: 'POST' });
        state.alerts = [];
        renderAlerts();
        el.metricAlerts.textContent = '0';
        el.badgeAlerts.textContent = '0';
        showToast('已清空所有异动预警', 'info');
      } catch (err) {
        showToast('清空失败: ' + err.message, 'error');
      }
    });
  }

  // Refresh Button
  if (el.btnRefresh) {
    el.btnRefresh.addEventListener('click', async () => {
      if (el.refreshIcon) el.refreshIcon.classList.add('rotating');
      await refreshData(true);
      setTimeout(() => {
        if (el.refreshIcon) el.refreshIcon.classList.remove('rotating');
      }, 600);
      showToast('行情与情报数据已刷新', 'success');
    });
  }

  // 7x24 News Segmented Filters & Instant Refresh
  if (el.btnNewsAll) {
    el.btnNewsAll.addEventListener('click', () => switchNewsFilter('all'));
  }
  if (el.btnNewsHits) {
    el.btnNewsHits.addEventListener('click', () => switchNewsFilter('hits'));
  }
  if (el.btnRefreshNews) {
    el.btnRefreshNews.addEventListener('click', async () => {
      try {
        el.btnRefreshNews.textContent = '⏳...';
        const resp = await fetch('/api/news/refresh', { method: 'POST' });
        const res = await resp.json();
        if (res.status === 'success') {
          state.news = res.data || [];
          renderNews();
          showToast(`7x24快讯已刷新 (共 ${res.total} 条，命中自选 ${res.hit_count} 条)`, 'success');
        }
      } catch (e) {
        showToast('快讯刷新异常: ' + e.message, 'error');
      } finally {
        el.btnRefreshNews.textContent = '🔄 刷新';
      }
    });
  }

  // Add Stock Modal
  if (el.btnAddStock) el.btnAddStock.addEventListener('click', openAddModal);
  if (el.btnCloseModal) el.btnCloseModal.addEventListener('click', closeAllModals);
  if (el.btnCancelModal) el.btnCancelModal.addEventListener('click', closeAllModals);

  // Asset Type change in Add Modal -> set smart defaults
  if (el.inputAssetType) {
    el.inputAssetType.addEventListener('change', (e) => {
      const type = e.target.value;
      if (type === 'index') {
        el.inputThreshold.value = '1.5';
        el.inputThresholdRange.value = '1.5';
        el.inputGroup.value = '主要指数';
        el.inputCode.placeholder = '如 000300.SH, 399006.SZ, HSI.HK';
      } else if (type === 'etf') {
        el.inputThreshold.value = '2.5';
        el.inputThresholdRange.value = '2.5';
        el.inputGroup.value = '热点ETF';
        el.inputCode.placeholder = '如 518880.SH, 159995.SZ';
      } else {
        el.inputThreshold.value = '3.5';
        el.inputThresholdRange.value = '3.5';
        el.inputGroup.value = '自选';
        el.inputCode.placeholder = '如 600519.SH, 00700.HK, 002408.SZ';
      }
    });
  }

  // Add Modal Slider sync
  if (el.inputThresholdRange && el.inputThreshold) {
    el.inputThresholdRange.addEventListener('input', (e) => {
      el.inputThreshold.value = e.target.value;
    });
    el.inputThreshold.addEventListener('input', (e) => {
      el.inputThresholdRange.value = e.target.value;
    });
  }

  // Code Check in Add Modal
  if (el.btnCheckCode) el.btnCheckCode.addEventListener('click', checkAssetCode);
  if (el.inputCode) el.inputCode.addEventListener('blur', checkAssetCode);

  // Submit Add Stock
  if (el.formAddStock) {
    el.formAddStock.addEventListener('submit', async (e) => {
      e.preventDefault();
      const assetType = el.inputAssetType.value;
      const code = el.inputCode.value.trim();
      const name = el.inputName.value.trim();
      const group = el.inputGroup.value.trim() || (assetType === 'index' ? '主要指数' : (assetType === 'etf' ? '热点ETF' : '自选'));
      const threshold = parseFloat(el.inputThreshold.value) || 3.0;

      try {
        const resp = await fetch('/api/watchlist/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, name: name || null, group, threshold, asset_type: assetType })
        });
        const res = await resp.json();
        if (resp.ok) {
          closeAllModals();
          await loadGroups();
          await refreshData(true);
          showToast(`已成功添加 [${code}] 进入监控矩阵`, 'success');
        } else {
          showToast('添加失败: ' + (res.detail || '未知错误'), 'error');
        }
      } catch (err) {
        showToast('请求错误: ' + err.message, 'error');
      }
    });
  }

  // Quick Threshold Modal Listeners
  if (el.thInputRange && el.thInputVal) {
    el.thInputRange.addEventListener('input', (e) => el.thInputVal.value = e.target.value);
    el.thInputVal.addEventListener('input', (e) => el.thInputRange.value = e.target.value);
  }
  if (el.btnCloseTh) el.btnCloseTh.addEventListener('click', closeAllModals);
  if (el.btnCancelTh) el.btnCancelTh.addEventListener('click', closeAllModals);
  if (el.btnSubmitTh) {
    el.btnSubmitTh.addEventListener('click', async () => {
      if (!currentEditingCode) return;
      const newTh = parseFloat(el.thInputVal.value);
      if (isNaN(newTh) || newTh <= 0) {
        showToast('请输入有效阈值', 'error');
        return;
      }
      try {
        await fetch(`/api/watchlist/${encodeURIComponent(currentEditingCode)}?threshold=${newTh}`, { method: 'PATCH' });
        const target = state.quotes.find(q => q.code === currentEditingCode);
        if (target) target.threshold = newTh;
        closeAllModals();
        renderMatrix();
        showToast(`已将 [${currentEditingCode}] 预警阈值设为 ±${newTh}%`, 'success');
      } catch (err) {
        showToast('修改失败: ' + err.message, 'error');
      }
    });
  }

  // Detail Drawer Listeners
  if (el.btnCloseDrawer) el.btnCloseDrawer.addEventListener('click', closeDrawer);
  if (el.drawerOverlay) el.drawerOverlay.addEventListener('click', closeDrawer);
}

// ==========================================
// Code Online Verification
// ==========================================
async function checkAssetCode() {
  const code = el.inputCode.value.trim();
  if (!code) return;
  try {
    el.codePreview.style.display = 'block';
    el.codePreview.textContent = '🔍 正在联网查询标的信息...';
    const resp = await fetch(`/api/check-code/${encodeURIComponent(code)}`);
    const data = await resp.json();
    if (data.valid) {
      const unit = data.asset_type === 'index' ? '点' : '元';
      el.codePreview.innerHTML = `✅ 识别成功: <strong>${data.name}</strong> [${data.asset_type.toUpperCase()}] (${data.code}) | 最新值: ${data.price} ${unit}`;
      if (!el.inputName.value) {
        el.inputName.value = data.name;
      }
      if (data.asset_type) {
        el.inputAssetType.value = data.asset_type;
      }
    } else {
      el.codePreview.textContent = '⚠️ ' + (data.message || '未能识别的代码');
    }
  } catch (err) {
    el.codePreview.textContent = '❌ 检测异常: ' + err.message;
  }
}

// ==========================================
// Modal & Drawer Helpers
// ==========================================
function openAddModal() {
  closeAllModals();
  el.modalAdd.style.display = 'flex';
  el.inputCode.value = '';
  el.inputName.value = '';
  el.codePreview.style.display = 'none';
  el.inputCode.focus();
}

function openThresholdModal(code, currentTh) {
  closeAllModals();
  currentEditingCode = code;
  el.thCodeTitle.textContent = code;
  el.thInputVal.value = currentTh;
  el.thInputRange.value = currentTh;
  el.modalThreshold.style.display = 'flex';
}

function openDrawer(code) {
  state.activeDetailCode = code;
  el.drawerOverlay.style.display = 'block';
  el.drawerDetail.style.display = 'flex';
  loadStockDetail(code);
}

function closeDrawer() {
  el.drawerOverlay.style.display = 'none';
  el.drawerDetail.style.display = 'none';
  state.activeDetailCode = null;
}

function closeAllModals() {
  if (el.modalAdd) el.modalAdd.style.display = 'none';
  if (el.modalThreshold) el.modalThreshold.style.display = 'none';
  closeDrawer();
}

// ==========================================
// Toast Notifications
// ==========================================
function showToast(message, type = 'info') {
  if (!el.toastContainer) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  const icon = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
  toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
  el.toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}

// ==========================================
// Stock Detail Drawer Loader
// ==========================================
async function loadStockDetail(code) {
  const stock = state.quotes.find(q => q.code === code);
  el.drawerTitle.textContent = stock ? stock.name : code;
  el.drawerCode.textContent = code;
  
  if (stock) {
    const badge = getBadgeInfo(stock);
    el.drawerBadge.className = badge.className;
    el.drawerBadge.textContent = badge.text;

    const unit = stock.asset_type === 'index' ? '点' : (stock.market === 'HK' ? 'HK$' : '¥');
    const pct = stock.pct_change || 0;
    const color = pct > 0 ? 'var(--c-up)' : (pct < 0 ? 'var(--c-down)' : 'var(--text-main)');

    el.drawerSpecs.innerHTML = `
      <div class="spec-item">
        <span class="spec-label">最新价/点数</span>
        <span class="spec-val" style="color:${color}; font-size:16px;">${unit} ${formatPrice(stock)}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">当日涨跌幅</span>
        <span class="spec-val" style="color:${color};">${pct > 0 ? '+' : ''}${pct.toFixed(2)}%</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">今日最高</span>
        <span class="spec-val">${unit} ${(stock.high || stock.price || 0).toFixed(2)}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">今日最低</span>
        <span class="spec-val">${unit} ${(stock.low || stock.price || 0).toFixed(2)}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">预警阈值比例</span>
        <span class="spec-val">±${stock.threshold || 3.0}%</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">所属分类</span>
        <span class="spec-val">${stock.group || '自选'}</span>
      </div>
    `;
  }

  // Fetch detailed announcements and news for this asset
  try {
    const resp = await fetch(`/api/stock/${encodeURIComponent(code)}`);
    if (resp.ok) {
      const data = await resp.json();
      const res = data.data;

      // Render matching announcements
      if (res.announcements && res.announcements.length) {
        el.drawerAnns.innerHTML = res.announcements.map(ann => `
          <div class="feed-card">
            <div class="feed-top">
              <span class="feed-tag tag-info">巨潮官方信披</span>
              <span class="feed-time">📅 ${ann.time || ''}</span>
            </div>
            <div class="feed-title">${ann.title}</div>
            ${ann.url ? `<a href="${ann.url}" target="_blank" class="feed-link">📄 下载查看公告原文 (PDF) &rarr;</a>` : ''}
          </div>
        `).join('');
      } else {
        el.drawerAnns.innerHTML = `<div class="empty-state" style="padding:20px;"><p>今日暂无针对此标的的法定披露公告</p></div>`;
      }

      // Render matching news
      if (res.news && res.news.length) {
        el.drawerNews.innerHTML = res.news.map(n => `
          <div class="feed-card">
            <div class="feed-top">
              <span class="feed-tag tag-info">7x24电报</span>
              <span class="feed-time">⚡ ${n.datetime || n.time || ''}</span>
            </div>
            <div class="feed-body">${n.content}</div>
          </div>
        `).join('');
      } else {
        el.drawerNews.innerHTML = `<div class="empty-state" style="padding:20px;"><p>暂未匹配到此标的的实时电报</p></div>`;
      }

      // Render matching alerts
      if (res.alerts && res.alerts.length) {
        el.drawerAlerts.innerHTML = res.alerts.map(a => `
          <div class="feed-card">
            <div class="feed-top">
              <span class="feed-tag ${a.level === 'URGENT' ? 'tag-urgent' : 'tag-alert'}">${a.level}</span>
              <span class="feed-time">🕒 ${a.time}</span>
            </div>
            <div class="feed-body">${a.message}</div>
          </div>
        `).join('');
      } else {
        el.drawerAlerts.innerHTML = `<div class="empty-state" style="padding:20px;"><p>此标的暂未触发盘中剧烈异动</p></div>`;
      }
    }
  } catch (err) {
    console.warn('获取标的详情异常:', err);
  }
}

// ==========================================
// Groups Loader
// ==========================================
async function loadGroups() {
  try {
    const resp = await fetch('/api/watchlist/groups');
    const data = await resp.json();
    if (data.status === 'success') {
      state.groups = data.data;

      const select = el.groupSelect;
      if (!select) return;

      select.innerHTML = `
        <option value="ALL">全部板块与分类</option>
        <option value="INDEX">📊 主要基准指数</option>
        <option value="ETF">🎯 2年高波动热点ETF</option>
        <option value="THEMES">🔥 风格题材领衔龙头 (高波动)</option>
        <option value="TERMINAL">🛡️ Terminal 周期产业基本盘</option>
      `;

      // Create grouped option tags
      const etfGroup = document.createElement('optgroup');
      etfGroup.label = '── 2年热点高弹性ETF ──';
      const themeGroup = document.createElement('optgroup');
      themeGroup.label = '── 风格题材高波动龙头 ──';
      const cyclicGroup = document.createElement('optgroup');
      cyclicGroup.label = '── 周期产业基本盘 ──';

      state.groups.forEach(g => {
        if (g === '主要指数') return;
        const opt = document.createElement('option');
        opt.value = g;
        opt.textContent = g;

        if (g.startsWith('热点ETF:')) {
          etfGroup.appendChild(opt);
        } else if (g.startsWith('风格:')) {
          themeGroup.appendChild(opt);
        } else {
          cyclicGroup.appendChild(opt);
        }
      });

      if (etfGroup.children.length) select.appendChild(etfGroup);
      if (themeGroup.children.length) select.appendChild(themeGroup);
      if (cyclicGroup.children.length) select.appendChild(cyclicGroup);
    }
  } catch (err) {
    console.error('加载分组失败:', err);
  }
}

// ==========================================
// Data Refresh (Poll + Initial, with Overview API aggregation)
// ==========================================
async function refreshData(manual = false) {
  try {
    let qData = null, aData = null, annData = null, nData = null;

    try {
      // 1. 优先调用高性能聚合接口 (节省 75% 网络往返开销)
      const ovResp = await fetch('/api/overview');
      if (ovResp.ok) {
        const ov = await ovResp.json();
        if (ov.status === 'success') {
          qData = {
            status: 'success',
            data: ov.quotes || [],
            total: ov.stats?.total || 0,
            up_count: ov.stats?.up_count || 0,
            down_count: ov.stats?.down_count || 0,
            flat_count: ov.stats?.flat_count || 0,
            updated_at: ov.updated_at
          };
          aData = { status: 'success', data: ov.alerts || [] };
          annData = { status: 'success', data: ov.announcements || [] };
          nData = { status: 'success', data: ov.news || [] };
        }
      }
    } catch (e) {
      console.warn('Overview 聚合接口异常，自动降级至独立接口:', e);
    }

    // 2. 降级容错：若 overview 未成功，回退至独立并发请求
    if (!qData) {
      const [qResp, aResp, annResp, nResp] = await Promise.all([
        fetch('/api/quotes'),
        fetch('/api/alerts'),
        fetch('/api/announcements'),
        fetch('/api/news')
      ]);
      qData = await qResp.json();
      aData = await aResp.json();
      annData = await annResp.json();
      nData = await nResp.json();
    }

    if (qData && qData.status === 'success') {
      state.quotes = qData.data;
      if (el.metricTotal) el.metricTotal.textContent = qData.total;
      if (el.metricUp) el.metricUp.textContent = qData.up_count;
      if (el.metricDown) el.metricDown.textContent = qData.down_count;
      if (el.metricFlat) el.metricFlat.textContent = qData.flat_count || 0;
      const fullDt = qData.updated_at || (qData.date ? `${qData.date} ${qData.time}` : '');
      if (el.updateTimer) el.updateTimer.textContent = fullDt;
      if (el.marketFullDatetime) el.marketFullDatetime.textContent = fullDt;
      if (el.matrixTimeVal) el.matrixTimeVal.textContent = fullDt;
    }

    if (aData && aData.status === 'success') {
      state.alerts = aData.data;
      if (el.metricAlerts) el.metricAlerts.textContent = state.alerts.length;
      if (el.badgeAlerts) el.badgeAlerts.textContent = state.alerts.length;
      renderAlerts();
    }

    if (annData && annData.status === 'success') {
      state.announcements = annData.data;
      if (el.badgeAnn) el.badgeAnn.textContent = state.announcements.length;
      renderAnnouncements();
    }

    if (nData && nData.status === 'success') {
      state.news = nData.data;
      if (el.badgeNews) el.badgeNews.textContent = state.news.length;
      renderNews();
    }

    // 智能局部更新（若无结构变化则执行微补丁更新）
    smartUpdateMatrix();

    // If detail drawer is open, refresh its specs
    if (state.activeDetailCode) {
      const activeStock = state.quotes.find(q => q.code === state.activeDetailCode);
      if (activeStock) {
        loadStockDetail(state.activeDetailCode);
      }
    }
  } catch (err) {
    console.warn('数据拉取异常:', err);
  }
}

// ==========================================
// WebSocket Connection
// ==========================================
function setupWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  try {
    state.ws = new WebSocket(wsUrl);
    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'TICK') {
          const wsDt = msg.datetime || (msg.date ? `${msg.date} ${msg.timestamp}` : msg.timestamp);
          if (el.updateTimer) el.updateTimer.textContent = wsDt;
          if (el.marketFullDatetime) el.marketFullDatetime.textContent = wsDt;
          if (el.matrixTimeVal) el.matrixTimeVal.textContent = wsDt;
        }
      } catch (e) {}
    };
    state.ws.onclose = () => {
      setTimeout(setupWebSocket, 5000);
    };
  } catch (e) {
    console.warn('WebSocket 连接未建立，降级至 HTTP 轮询');
  }
}

// ==========================================
// Filter & Sort Engine
// ==========================================
function getFilteredAndSortedQuotes() {
  let list = [...state.quotes];

  // 1. Primary Filter Buttons
  if (state.mainFilter === 'INDEX') {
    list = list.filter(q => q.asset_type === 'index');
  } else if (state.mainFilter === 'ETF') {
    list = list.filter(q => q.asset_type === 'etf');
  } else if (state.mainFilter === 'THEMES') {
    list = list.filter(q => (q.group || '').startsWith('风格:'));
  } else if (state.mainFilter === 'TERMINAL') {
    list = list.filter(q => q.asset_type === 'stock' && !(q.group || '').startsWith('风格:'));
  } else if (state.mainFilter === 'A') {
    list = list.filter(q => q.asset_type === 'stock' && (q.market === 'SH' || q.market === 'SZ' || q.market === 'BJ'));
  } else if (state.mainFilter === 'HK') {
    list = list.filter(q => q.market === 'HK');
  }

  // 2. Dropdown Group Filter
  if (state.groupFilter === 'INDEX') {
    list = list.filter(q => q.asset_type === 'index');
  } else if (state.groupFilter === 'ETF') {
    list = list.filter(q => q.asset_type === 'etf');
  } else if (state.groupFilter === 'THEMES') {
    list = list.filter(q => (q.group || '').startsWith('风格:'));
  } else if (state.groupFilter === 'TERMINAL') {
    list = list.filter(q => q.asset_type === 'stock' && !(q.group || '').startsWith('风格:'));
  } else if (state.groupFilter !== 'ALL') {
    list = list.filter(q => q.group === state.groupFilter);
  }

  // 3. Search Query Filter
  if (state.searchQuery) {
    const kw = state.searchQuery;
    list = list.filter(q =>
      q.code.toLowerCase().includes(kw) ||
      (q.name && q.name.toLowerCase().includes(kw)) ||
      (q.group && q.group.toLowerCase().includes(kw)) ||
      (q.asset_type && q.asset_type.toLowerCase().includes(kw))
    );
  }

  // 4. Sorting
  if (state.sortMode === 'pct-desc') {
    list.sort((a, b) => (b.pct_change || 0) - (a.pct_change || 0));
  } else if (state.sortMode === 'pct-asc') {
    list.sort((a, b) => (a.pct_change || 0) - (b.pct_change || 0));
  } else if (state.sortMode === 'volatility') {
    list.sort((a, b) => Math.abs(b.pct_change || 0) - Math.abs(a.pct_change || 0));
  } else {
    // Default recommended priority:
    // 1: Indices
    // 2: Hot ETFs
    // 3: Style Theme Leaders
    // 4: Terminal Industrial Base Stocks
    const typePriority = { 'index': 1, 'etf': 2, 'stock': 3 };
    list.sort((a, b) => {
      const pA = typePriority[a.asset_type] || 3;
      const pB = typePriority[b.asset_type] || 3;
      if (pA !== pB) return pA - pB;

      if (a.asset_type === 'stock' && b.asset_type === 'stock') {
        const aIsTheme = (a.group || '').startsWith('风格:');
        const bIsTheme = (b.group || '').startsWith('风格:');
        if (aIsTheme && !bIsTheme) return -1;
        if (!aIsTheme && bIsTheme) return 1;
      }
      return a.code.localeCompare(b.code);
    });
  }

  return list;
}

// ==========================================
// Formatting Helpers
// ==========================================
function formatPrice(item) {
  const p = item.price || 0;
  if (item.asset_type === 'index') {
    return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (item.asset_type === 'etf') {
    return p >= 10 ? p.toFixed(2) : p.toFixed(3);
  }
  return p.toFixed(2);
}

function getCurrencyPrefix(item) {
  if (item.asset_type === 'index') return '';
  if (item.market === 'HK') return 'HK$ ';
  return '¥ ';
}

function getBadgeInfo(item) {
  if (item.asset_type === 'index') {
    return { className: 'market-badge badge-index', text: '指数' };
  }
  if (item.asset_type === 'etf') {
    return { className: 'market-badge badge-etf', text: 'ETF' };
  }
  if (item.market === 'HK') {
    return { className: 'market-badge badge-hk', text: '港股' };
  }
  return { className: 'market-badge badge-a', text: item.market || 'A股' };
}

function getGroupTagClass(item) {
  if (item.asset_type === 'index') return 'group-tag group-index';
  if (item.asset_type === 'etf') return 'group-tag group-etf';
  if ((item.group || '').startsWith('风格:')) return 'group-tag group-theme';
  return 'group-tag';
}

// ==========================================
// Category & Group Helpers
// ==========================================
function getGroupIcon(groupName) {
  if (groupName === '主要指数') return '📊';
  if (groupName.startsWith('热点ETF:大宗') || groupName.startsWith('热点ETF:黄金')) return '🏆';
  if (groupName.startsWith('热点ETF:硬科技') || groupName.startsWith('热点ETF:芯片')) return '💾';
  if (groupName.startsWith('热点ETF:算力') || groupName.startsWith('热点ETF:通信')) return '⚡';
  if (groupName.startsWith('热点ETF:人形机器人')) return '🦾';
  if (groupName.startsWith('热点ETF:创新药')) return '💊';
  if (groupName.startsWith('热点ETF:AI') || groupName.startsWith('热点ETF:游戏')) return '🎮';
  if (groupName.startsWith('热点ETF')) return '🎯';
  if (groupName.startsWith('风格:AI语料') || groupName.startsWith('风格:大模型')) return '🤖';
  if (groupName.startsWith('风格:算力光模块') || groupName.startsWith('风格:光模块')) return '⚡';
  if (groupName.startsWith('风格:先进封装') || groupName.startsWith('风格:半导体') || groupName.startsWith('风格:自主算力')) return '💾';
  if (groupName.startsWith('风格:固态电池') || groupName.startsWith('风格:新材料')) return '🔋';
  if (groupName.startsWith('风格:商业航天') || groupName.startsWith('风格:卫星')) return '🚀';
  if (groupName.startsWith('风格:低空经济')) return '🚁';
  if (groupName.startsWith('风格:人形机器人') || groupName.startsWith('风格:机器人')) return '🦾';
  if (groupName.startsWith('风格:跨境电商') || groupName.startsWith('风格:IP')) return '🌐';
  if (groupName.startsWith('风格:战略贵金属') || groupName.startsWith('风格:黄金')) return '🏆';
  if (groupName.startsWith('风格:创新药')) return '💊';
  if (groupName.startsWith('风格:')) return '🔥';
  if (groupName === '化工') return '🧪';
  if (groupName === '矿业') return '⛏️';
  if (groupName === '民爆') return '💥';
  if (groupName === '造纸') return '📜';
  if (groupName === '农产品加工') return '🌾';
  if (groupName === '通用资源') return '🏗️';
  return '📁';
}

function getGroupPriority(g) {
  if (g === '主要指数') return 1;
  if (g.startsWith('热点ETF:')) return 2;
  if (g.startsWith('风格:')) {
    const hotThemes = [
      '风格:AI语料与应用',
      '风格:算力光模块',
      '风格:先进封装与半导体',
      '风格:固态电池与新材料',
      '风格:商业航天与卫星',
      '风格:人形机器人',
      '风格:低空经济',
      '风格:自主算力芯片',
      '风格:跨境电商与IP经济',
      '风格:战略贵金属',
      '风格:创新药出海'
    ];
    const idx = hotThemes.indexOf(g);
    return idx !== -1 ? (30 + idx) : 50;
  }
  if (g === '化工') return 60;
  if (g === '矿业') return 61;
  if (g === '民爆') return 62;
  if (g === '造纸') return 63;
  if (g === '农产品加工') return 64;
  if (g === '通用资源') return 70;
  return 80;
}

window.toggleGroupFold = function(safeGName) {
  const gName = decodeURIComponent(safeGName);
  if (!state.foldedGroups) state.foldedGroups = new Set();
  if (state.foldedGroups.has(gName)) {
    state.foldedGroups.delete(gName);
  } else {
    state.foldedGroups.add(gName);
  }
  renderMatrix();
};

// ==========================================
// Theme Insight Logic (iwencai.com 问财标准全景洞察)
// ==========================================
async function fetchThemesSummary() {
  try {
    const resp = await fetch('/api/themes/summary');
    const res = await resp.json();
    if (res.status === 'success') {
      state.themesSummary = res.data || [];
      renderThemeInsight();
    }
  } catch (err) {
    console.warn('获取题材全景数据失败:', err);
  }
}

function renderThemeInsight() {
  const grid = el.themeInsightGrid;
  if (!grid || !state.themesSummary || !state.themesSummary.length) return;

  grid.innerHTML = state.themesSummary.map(t => {
    const avgPct = t.realtime_avg_pct || 0;
    const isUp = avgPct > 0;
    const isDown = avgPct < 0;
    const avgClass = isUp ? 'is-up' : (isDown ? 'is-down' : 'is-flat');
    const avgSign = isUp ? '+' : '';

    const leadersHtml = (t.top_stocks || []).map((s, idx) => {
      const qPct = s.pct_change || 0;
      const sUp = qPct > 0;
      const sDown = qPct < 0;
      const qClass = sUp ? 'is-up' : (sDown ? 'is-down' : 'is-flat');
      const qSign = sUp ? '+' : '';
      const priceText = s.price > 0 ? `${s.price.toFixed(2)}元 (${qSign}${qPct.toFixed(2)}%)` : '--';
      const roleBadgeClass = idx === 0 ? 'role-l1' : 'role-l2';
      const roleShort = idx === 0 ? '龙一' : '龙二';

      return `
        <div class="leader-item" onclick="locateStockCode('${s.code}')" title="点击定位 ${s.name} 行情详情">
          <div class="leader-head">
            <span class="leader-role ${roleBadgeClass}">${roleShort}</span>
            <span class="leader-code-name"><strong>${s.name}</strong> <span class="leader-code">${s.code}</span></span>
            <span class="leader-quote ${qClass}">${priceText}</span>
          </div>
          <p class="leader-desc" title="${s.desc}">${s.desc}</p>
        </div>
      `;
    }).join('');

    return `
      <div class="theme-card" data-group="${t.group}">
        <div class="theme-card-header">
          <div class="theme-title-wrap">
            <div class="theme-name-row">
              <span class="theme-name">${t.name}</span>
              <span class="theme-cat-tag">${t.category}</span>
            </div>
            <div class="theme-meta-row">
              <span class="theme-hot-tag">问财热度 ${t.hot_level}</span>
              <span class="theme-flow-tag">${t.fund_flow}</span>
              <span class="theme-rt-avg ${avgClass}">实时均幅: ${avgSign}${avgPct.toFixed(2)}%</span>
            </div>
          </div>
          <button class="theme-jump-btn" onclick="locateThemeGroup('${t.group}')" title="在下方自选资产矩阵中定位该板块监控排">
            定位监控排 ➔
          </button>
        </div>
        
        <div class="theme-card-body">
          <div class="theme-driver-box">
            <span class="driver-label">💡 问财核心驱动:</span>
            <p class="driver-text">${t.driver_info}</p>
          </div>
          
          <div class="theme-leaders-box">
            <div class="leaders-title-row">
              <span class="leaders-title">🏆 题材领衔头部两家公司 (龙一 / 龙二):</span>
            </div>
            <div class="leaders-list">
              ${leadersHtml}
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function updateThemeInsightVisibility() {
  const sec = el.themeInsightSection;
  const prompt = el.themeInsightPromptBar;
  if (!sec) return;

  const isThemeFilter = state.mainFilter === 'THEMES' || 
                        state.groupFilter === 'THEMES' || 
                        (typeof state.groupFilter === 'string' && state.groupFilter.startsWith('风格:'));

  if (isThemeFilter) {
    sec.style.display = 'flex';
    if (prompt) prompt.style.display = 'none';
    if (el.btnToggleInsightExpand) el.btnToggleInsightExpand.textContent = '收起洞察 ▴';
    if (!state.themesSummary || !state.themesSummary.length) {
      fetchThemesSummary();
    }
  } else if (state.mainFilter === 'ALL') {
    if (sec.style.display !== 'none') {
      if (prompt) prompt.style.display = 'none';
    } else {
      if (prompt) prompt.style.display = 'block';
    }
  } else {
    sec.style.display = 'none';
    if (prompt) prompt.style.display = 'none';
  }
}

window.toggleThemeInsight = function(forceOpen) {
  const sec = el.themeInsightSection;
  const prompt = el.themeInsightPromptBar;
  const btn = el.btnToggleInsightExpand;
  if (!sec) return;

  const isCurrentlyVisible = sec.style.display !== 'none';
  const shouldOpen = forceOpen !== undefined ? forceOpen : !isCurrentlyVisible;

  if (shouldOpen) {
    sec.style.display = 'flex';
    if (prompt) prompt.style.display = 'none';
    if (btn) btn.textContent = '收起洞察 ▴';
    if (!state.themesSummary || !state.themesSummary.length) {
      fetchThemesSummary();
    }
  } else {
    sec.style.display = 'none';
    if (state.mainFilter === 'ALL' && prompt) prompt.style.display = 'block';
    if (btn) btn.textContent = '展开洞察 ▾';
  }
};

window.locateThemeGroup = function(groupName) {
  if (state.viewMode !== 'grouped') {
    state.viewMode = 'grouped';
    if (el.viewMode) {
      el.viewMode.querySelectorAll('.seg-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.view === 'grouped');
      });
    }
    el.cardsContainer.style.display = 'flex';
    el.cardsContainer.className = 'cards-grouped-container';
    el.tableContainer.style.display = 'none';
    renderMatrix();
  }

  if (state.foldedGroups && state.foldedGroups.has(groupName)) {
    state.foldedGroups.delete(groupName);
    renderMatrix();
  }

  setTimeout(() => {
    const lanes = document.querySelectorAll('.group-swimlane');
    for (const lane of lanes) {
      if (lane.dataset.group === groupName) {
        lane.scrollIntoView({ behavior: 'smooth', block: 'center' });
        lane.classList.remove('highlight-pulse');
        void lane.offsetWidth;
        lane.classList.add('highlight-pulse');
        break;
      }
    }
  }, 60);
};

window.locateStockCode = function(code) {
  const card = document.querySelector(`.stock-card[data-code="${code}"]`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.remove('highlight-pulse');
    void card.offsetWidth;
    card.classList.add('highlight-pulse');
  } else {
    openDrawer(code);
  }
};

// ==========================================
// Render Single Stock Card
// ==========================================
function renderSingleCard(stock) {
  const pct = stock.pct_change || 0;
  const isUp = pct > 0;
  const isDown = pct < 0;
  const pillClass = isUp ? 'pill-up' : (isDown ? 'pill-down' : 'pill-flat');
  const sign = isUp ? '+' : '';
  const isAlerted = Math.abs(pct) >= (stock.threshold || 3.0);
  const badge = getBadgeInfo(stock);
  const groupClass = getGroupTagClass(stock);
  const currency = getCurrencyPrefix(stock);
  const formattedPrice = formatPrice(stock);
  const typeCardClass = stock.asset_type === 'index' ? 'is-index' : (stock.asset_type === 'etf' ? 'is-etf' : '');

  // Calculate intraday range bar fill percentage
  const curP = stock.price || 0;
  const highP = stock.high || curP;
  const lowP = stock.low || curP;
  let rangePct = 50;
  if (highP > lowP) {
    rangePct = Math.min(100, Math.max(0, ((curP - lowP) / (highP - lowP)) * 100));
  }

  return `
    <div class="stock-card ${typeCardClass} ${!stock.enabled ? 'is-disabled' : ''} ${isAlerted ? 'is-alerted' : ''}" 
         data-code="${stock.code}" onclick="handleCardClick(event, '${stock.code}')">
      <div class="card-top">
        <div class="stock-ident">
          <div class="stock-title-row">
            <span class="stock-name">${stock.name}</span>
            <span class="${badge.className}">${badge.text}</span>
          </div>
          <span class="stock-code">${stock.code}</span>
        </div>
        <span class="${groupClass}" title="${stock.group}">${stock.group || '自选'}</span>
      </div>

      <div class="card-mid">
        <div class="price-container">
          ${currency ? `<span class="currency-sym">${currency}</span>` : ''}
          <span class="main-price" style="color: ${isUp ? 'var(--c-up)' : (isDown ? 'var(--c-down)' : 'var(--text-main)')};">
            ${formattedPrice}
          </span>
          ${stock.asset_type === 'index' ? `<span class="currency-sym">点</span>` : ''}
        </div>
        <div class="change-pill ${pillClass}">
          ${sign}${pct.toFixed(2)}%
        </div>
      </div>

      <div class="day-range-wrapper">
        <div class="range-labels">
          <span>低: ${formatPrice({ ...stock, price: lowP })}</span>
          <span>高: ${formatPrice({ ...stock, price: highP })}</span>
        </div>
        <div class="range-bar-track">
          <div class="range-bar-fill" style="width: ${rangePct}%; background-color: ${isUp ? 'var(--c-up)' : (isDown ? 'var(--c-down)' : 'var(--accent-blue)')};"></div>
        </div>
      </div>

      <div class="card-bottom">
        <div class="threshold-tag">
          <span class="action-chip" onclick="event.stopPropagation(); openThresholdModal('${stock.code}', ${stock.threshold})" title="修改预警阈值比例">
            ⚙️ ±${stock.threshold}%
          </span>
        </div>
        <div class="card-actions">
          <button class="action-chip" onclick="event.stopPropagation(); toggleStockEnabled('${stock.code}', ${!stock.enabled})" title="${stock.enabled ? '暂停监控' : '开启监控'}">
            ${stock.enabled ? '🟢 监控' : '⏸️ 暂停'}
          </button>
          <button class="action-chip" onclick="event.stopPropagation(); openDrawer('${stock.code}')" title="查看深度详情与信披">
            📊 详情
          </button>
          <button class="action-chip danger" onclick="event.stopPropagation(); deleteStock('${stock.code}', '${stock.name}')" title="从自选池移除">
            🗑️
          </button>
        </div>
      </div>
    </div>
  `;
}

// ==========================================
// Smart In-Place DOM Patching Engine
// ==========================================
function smartUpdateMatrix() {
  const items = getFilteredAndSortedQuotes();
  if (el.filteredCount) el.filteredCount.textContent = `显示 ${items.length} 个标的`;

  const foldedArr = Array.from(state.foldedGroups || []).sort().join(',');
  const currentFingerprint = `${state.mainFilter}_${state.groupFilter}_${state.searchQuery}_${state.viewMode}_${state.sortMode}_${items.length}_${foldedArr}`;

  const hasDom = (state.viewMode === 'table')
    ? (el.tableBody && el.tableBody.children.length > 0 && !el.tableBody.querySelector('.text-dim'))
    : (el.cardsContainer && el.cardsContainer.children.length > 0 && !el.cardsContainer.querySelector('.empty-state'));

  if (hasDom && state.lastRenderFingerprint === currentFingerprint) {
    // 局部 In-place DOM 增量补丁更新 (零重排、无卡顿闪烁、极佳手感)
    patchDOMQuotesInPlace(items);
  } else {
    // 结构或过滤条件改变，全量重新布局
    state.lastRenderFingerprint = currentFingerprint;
    if (state.viewMode === 'table') {
      renderTable(items);
    } else if (state.viewMode === 'card') {
      renderCards(items);
    } else {
      renderGrouped(items);
    }
    // 同步初始化旧价格基线
    items.forEach(stock => {
      state.previousPrices.set(stock.code, stock.price || 0);
    });
  }
}

function patchDOMQuotesInPlace(items) {
  if (state.viewMode === 'table') {
    patchTableQuotes(items);
  } else {
    patchCardQuotes(items);
  }
}

function patchCardQuotes(items) {
  const groupStats = new Map();

  for (const stock of items) {
    const code = stock.code;
    const curP = stock.price || 0;
    const prevP = state.previousPrices.get(code);
    const pct = stock.pct_change || 0;
    const isUp = pct > 0;
    const isDown = pct < 0;
    const pillClass = isUp ? 'pill-up' : (isDown ? 'pill-down' : 'pill-flat');
    const sign = isUp ? '+' : '';
    const isAlerted = Math.abs(pct) >= (stock.threshold || 3.0);

    const gName = stock.group || '自选';
    if (!groupStats.has(gName)) {
      groupStats.set(gName, { totalPct: 0, count: 0, alerts: 0 });
    }
    const gs = groupStats.get(gName);
    gs.totalPct += pct;
    gs.count += 1;
    if (isAlerted) gs.alerts += 1;

    const card = document.querySelector(`.stock-card[data-code="${code}"]`);
    if (!card) continue;

    // 1. 异动预警样式标记
    card.classList.toggle('is-alerted', isAlerted);

    // 2. 价格更新与高频跳动闪烁动画
    const priceEl = card.querySelector('.main-price');
    if (priceEl) {
      const formattedPrice = formatPrice(stock);
      if (priceEl.textContent.trim() !== formattedPrice) {
        priceEl.textContent = formattedPrice;
        priceEl.style.color = isUp ? 'var(--c-up)' : (isDown ? 'var(--c-down)' : 'var(--text-main)');
        if (prevP !== undefined && prevP > 0 && curP !== prevP) {
          const flashClass = curP > prevP ? 'price-flash-up' : 'price-flash-down';
          priceEl.classList.remove('price-flash-up', 'price-flash-down');
          void priceEl.offsetWidth;
          priceEl.classList.add(flashClass);
          setTimeout(() => priceEl.classList.remove(flashClass), 850);
        }
      }
    }

    // 3. 涨跌幅胶囊更新
    const pillEl = card.querySelector('.change-pill');
    if (pillEl) {
      pillEl.className = `change-pill ${pillClass}`;
      pillEl.textContent = `${sign}${pct.toFixed(2)}%`;
    }

    // 4. 日内区间滑动条更新
    const highP = stock.high || curP;
    const lowP = stock.low || curP;
    let rangePct = 50;
    if (highP > lowP) {
      rangePct = Math.min(100, Math.max(0, ((curP - lowP) / (highP - lowP)) * 100));
    }
    const rangeFill = card.querySelector('.range-bar-fill');
    if (rangeFill) {
      rangeFill.style.width = `${rangePct}%`;
      rangeFill.style.backgroundColor = isUp ? 'var(--c-up)' : (isDown ? 'var(--c-down)' : 'var(--accent-blue)');
    }
    const rangeLabels = card.querySelectorAll('.range-labels span');
    if (rangeLabels.length >= 2) {
      rangeLabels[0].textContent = `低: ${formatPrice({ ...stock, price: lowP })}`;
      rangeLabels[1].textContent = `高: ${formatPrice({ ...stock, price: highP })}`;
    }

    state.previousPrices.set(code, curP);
  }

  // 局部更新分组泳道头部统计
  if (state.viewMode === 'grouped') {
    for (const [gName, gs] of groupStats.entries()) {
      const lane = document.querySelector(`.group-swimlane[data-group="${gName}"]`);
      if (!lane) continue;
      const avgPct = gs.count ? (gs.totalPct / gs.count) : 0;
      const isUp = avgPct > 0;
      const isDown = avgPct < 0;
      const avgClass = isUp ? 'is-up' : (isDown ? 'is-down' : 'is-flat');
      const avgSign = isUp ? '+' : '';
      const avgEl = lane.querySelector('.group-avg-tag');
      if (avgEl) {
        avgEl.className = `group-avg-tag ${avgClass}`;
        avgEl.textContent = `板块均幅: ${avgSign}${avgPct.toFixed(2)}%`;
      }
    }
  }
}

function patchTableQuotes(items) {
  for (const stock of items) {
    const code = stock.code;
    const curP = stock.price || 0;
    const prevP = state.previousPrices.get(code);
    const pct = stock.pct_change || 0;
    const isUp = pct > 0;
    const isDown = pct < 0;
    const colorStyle = isUp ? 'var(--c-up)' : (isDown ? 'var(--c-down)' : 'var(--text-main)');
    const sign = isUp ? '+' : '';

    const tr = document.querySelector(`#table-body tr[data-code="${code}"]`);
    if (!tr) continue;

    const cells = tr.querySelectorAll('td');
    if (cells.length >= 8) {
      const priceSpan = cells[4].querySelector('span');
      if (priceSpan) {
        const currency = getCurrencyPrefix(stock);
        const formattedPrice = formatPrice(stock);
        priceSpan.textContent = `${currency}${formattedPrice}`;
        priceSpan.style.color = colorStyle;
        if (prevP !== undefined && prevP > 0 && curP !== prevP) {
          const flashClass = curP > prevP ? 'price-flash-up' : 'price-flash-down';
          priceSpan.classList.remove('price-flash-up', 'price-flash-down');
          void priceSpan.offsetWidth;
          priceSpan.classList.add(flashClass);
          setTimeout(() => priceSpan.classList.remove(flashClass), 850);
        }
      }
      const pctSpan = cells[5].querySelector('span');
      if (pctSpan) {
        pctSpan.textContent = `${sign}${pct.toFixed(2)}%`;
        pctSpan.style.color = colorStyle;
      }
      if (cells[6]) cells[6].textContent = formatPrice({ ...stock, price: stock.high || curP });
      if (cells[7]) cells[7].textContent = formatPrice({ ...stock, price: stock.low || curP });
    }
    state.previousPrices.set(code, curP);
  }
}

// ==========================================
// Render Watchlist Matrix (Router)
// ==========================================
function renderMatrix() {
  state.lastRenderFingerprint = ''; // 强制重新计算与重排
  smartUpdateMatrix();
}

// 1. Grouped Swimlane View (同类型/板块归并整排陈列)
function renderGrouped(items) {
  const container = el.cardsContainer;
  if (!container) return;

  if (!items.length) {
    container.innerHTML = `
      <div class="empty-state" style="padding: 60px 20px;">
        <span class="empty-icon">🔍</span>
        <p>未找到符合筛选条件的监控标的</p>
      </div>
    `;
    return;
  }

  // Group items by item.group
  const groupMap = new Map();
  items.forEach(item => {
    const g = item.group || '自选';
    if (!groupMap.has(g)) {
      groupMap.set(g, []);
    }
    groupMap.get(g).push(item);
  });

  // Sort groups: 主要指数 -> 热点ETF -> 风格题材 -> 基础盘行业
  const sortedGroupNames = Array.from(groupMap.keys()).sort((a, b) => {
    const pA = getGroupPriority(a);
    const pB = getGroupPriority(b);
    if (pA !== pB) return pA - pB;
    return a.localeCompare(b, 'zh-Hans-CN');
  });

  container.innerHTML = sortedGroupNames.map(gName => {
    const gItems = groupMap.get(gName);
    const icon = getGroupIcon(gName);
    const isTheme = gName.startsWith('风格:');
    const isIndex = gName === '主要指数';
    const isEtf = gName.startsWith('热点ETF:');
    
    let groupTypeClass = '';
    if (isTheme) groupTypeClass = 'is-theme-group';
    else if (isIndex) groupTypeClass = 'is-index-group';
    else if (isEtf) groupTypeClass = 'is-etf-group';

    // Calculate group average change %
    const totalPct = gItems.reduce((acc, cur) => acc + (cur.pct_change || 0), 0);
    const avgPct = gItems.length ? (totalPct / gItems.length) : 0;
    const isUp = avgPct > 0;
    const isDown = avgPct < 0;
    const avgClass = isUp ? 'is-up' : (isDown ? 'is-down' : 'is-flat');
    const avgSign = isUp ? '+' : '';

    // Alert count in this group
    const alertCount = gItems.filter(s => Math.abs(s.pct_change || 0) >= (s.threshold || 3.0)).length;
    const isFolded = state.foldedGroups && state.foldedGroups.has(gName);
    const safeGName = encodeURIComponent(gName);

    return `
      <div class="group-swimlane ${groupTypeClass}" data-group="${gName}">
        <div class="group-swimlane-header" onclick="toggleGroupFold('${safeGName}')" style="cursor: pointer;" title="点击折叠/展开本组">
          <div class="group-header-left">
            <span class="group-icon">${icon}</span>
            <span class="group-name-title">${gName}</span>
            <span class="group-count-badge">${gItems.length} 只标的</span>
            <span class="group-avg-tag ${avgClass}">
              板块均幅: ${avgSign}${avgPct.toFixed(2)}%
            </span>
          </div>
          <div class="group-header-right">
            ${alertCount > 0 ? `<span class="group-alert-pill">🚨 ${alertCount} 只异动</span>` : ''}
            <button class="group-fold-btn" onclick="event.stopPropagation(); toggleGroupFold('${safeGName}')">
              ${isFolded ? '展开 ▾' : '收起 ▴'}
            </button>
          </div>
        </div>
        <div class="group-cards-row" style="${isFolded ? 'display: none;' : ''}">
          ${gItems.map(renderSingleCard).join('')}
        </div>
      </div>
    `;
  }).join('');
}

// 2. Continuous Flat Grid View
function renderCards(items) {
  const container = el.cardsContainer;
  if (!container) return;

  if (!items.length) {
    container.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1; padding: 60px 20px;">
        <span class="empty-icon">🔍</span>
        <p>未找到符合筛选条件的监控标的</p>
      </div>
    `;
    return;
  }

  container.innerHTML = items.map(renderSingleCard).join('');
}

// Table View
function renderTable(items) {
  const tbody = el.tableBody;
  if (!tbody) return;

  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="text-center" style="padding: 40px; color: var(--text-dim);">无匹配数据</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(stock => {
    const pct = stock.pct_change || 0;
    const isUp = pct > 0;
    const isDown = pct < 0;
    const colorStyle = isUp ? 'color: var(--c-up);' : (isDown ? 'color: var(--c-down);' : 'color: var(--text-main);');
    const sign = isUp ? '+' : '';
    const badge = getBadgeInfo(stock);
    const currency = getCurrencyPrefix(stock);
    const formattedPrice = formatPrice(stock);
    const formattedHigh = stock.high ? formatPrice({ ...stock, price: stock.high }) : formattedPrice;
    const formattedLow = stock.low ? formatPrice({ ...stock, price: stock.low }) : formattedPrice;

    return `
      <tr class="${!stock.enabled ? 'is-disabled' : ''}" data-code="${stock.code}" onclick="handleCardClick(event, '${stock.code}')" style="cursor: pointer;">
        <td><strong class="stock-code" style="color:#fff;">${stock.code}</strong></td>
        <td><strong>${stock.name}</strong></td>
        <td><span class="${badge.className}">${badge.text}</span></td>
        <td><span class="${getGroupTagClass(stock)}">${stock.group || '自选'}</span></td>
        <td class="text-right"><span style="font-family: var(--font-mono); font-weight:700; ${colorStyle}">${currency}${formattedPrice}</span></td>
        <td class="text-right"><span style="font-family: var(--font-mono); font-weight:700; ${colorStyle}">${sign}${pct.toFixed(2)}%</span></td>
        <td class="text-right" style="font-family: var(--font-mono);">${formattedHigh}</td>
        <td class="text-right" style="font-family: var(--font-mono);">${formattedLow}</td>
        <td class="text-center">
          <span class="action-chip" onclick="event.stopPropagation(); openThresholdModal('${stock.code}', ${stock.threshold})">±${stock.threshold}%</span>
        </td>
        <td class="text-center">
          <button class="action-chip" onclick="event.stopPropagation(); toggleStockEnabled('${stock.code}', ${!stock.enabled})">
            ${stock.enabled ? '🟢 监控中' : '⏸️ 已暂停'}
          </button>
        </td>
        <td class="text-center">
          <button class="action-chip danger" onclick="event.stopPropagation(); deleteStock('${stock.code}', '${stock.name}')" title="删除">🗑️</button>
        </td>
      </tr>
    `;
  }).join('');
}

// Card Click Handler
function handleCardClick(e, code) {
  // If clicked inside an interactive button, do not open drawer
  if (e.target.closest('button') || e.target.closest('.action-chip')) return;
  openDrawer(code);
}

// ==========================================
// Right Intel Panel Renders
// ==========================================
function renderAlerts() {
  const container = el.alertsList;
  if (!container) return;

  if (!state.alerts.length) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">🛡️</span>
        <p>暂无新触发的盘中异动警报</p>
      </div>
    `;
    return;
  }

  container.innerHTML = state.alerts.map(a => {
    const isUrgent = a.level === 'URGENT';
    const tagClass = isUrgent ? 'feed-tag tag-urgent' : 'feed-tag tag-alert';
    const isUp = a.type === 'SURGE';
    const icon = isUp ? '🚀' : '📉';

    return `
      <div class="feed-card" onclick="openDrawer('${a.code}')" style="cursor: pointer;">
        <div class="feed-top">
          <span class="${tagClass}">${a.level}</span>
          <span class="feed-time">🕒 ${a.time}</span>
        </div>
        <div class="feed-title">${icon} ${a.name} (${a.code})</div>
        <div class="feed-body">${a.message}</div>
      </div>
    `;
  }).join('');
}

function renderAnnouncements() {
  const container = el.announcementsList;
  if (!container) return;

  if (!state.announcements.length) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">📑</span>
        <p>今日监控池标的暂无法定信披公告</p>
      </div>
    `;
    return;
  }

  container.innerHTML = state.announcements.map(ann => {
    return `
      <div class="feed-card" onclick="openDrawer('${ann.code}')" style="cursor: pointer;">
        <div class="feed-top">
          <span class="feed-tag tag-info">巨潮官方信披</span>
          <span class="feed-time">📅 ${ann.time}</span>
        </div>
        <div class="feed-title">${ann.name} (${ann.code})</div>
        <div class="feed-body">${ann.title}</div>
        ${ann.url ? `<a href="${ann.url}" target="_blank" onclick="event.stopPropagation();" class="feed-link">📄 下载查看公告原文 (PDF) &rarr;</a>` : ''}
      </div>
    `;
  }).join('');
}

window.switchNewsFilter = function(filter) {
  state.newsFilter = filter;
  if (el.btnNewsAll && el.btnNewsHits) {
    if (filter === 'all') {
      el.btnNewsAll.classList.add('active');
      el.btnNewsHits.classList.remove('active');
    } else {
      el.btnNewsAll.classList.remove('active');
      el.btnNewsHits.classList.add('active');
    }
  }
  renderNews();
};

function renderNews() {
  const container = el.newsList;
  if (!container) return;

  const allItems = state.news || [];
  const hitItems = allItems.filter(n => n.is_hit);
  const totalCount = allItems.length;
  const hitCount = hitItems.length;

  if (el.newsCountAll) el.newsCountAll.textContent = totalCount;
  if (el.newsCountHits) el.newsCountHits.textContent = hitCount;
  if (el.badgeNews) {
    el.badgeNews.textContent = hitCount > 0 ? `${totalCount} (🎯${hitCount})` : totalCount;
  }

  const itemsToRender = state.newsFilter === 'hits' ? hitItems : allItems;

  if (!itemsToRender.length) {
    if (state.newsFilter === 'hits') {
      container.innerHTML = `
        <div class="empty-state" style="padding: 40px 16px;">
          <span class="empty-icon">🎯</span>
          <p style="font-weight: 600; color: #fff;">当前 7x24 电报流中暂无直接提及自选标的</p>
          <p style="font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.6;">
            已为您保持毫秒级轮询监听（已扫描 ${totalCount} 条电报）<br>
            可点击下方按钮切换查看全市场宏观政策与行业动态。
          </p>
          <button class="action-chip" onclick="switchNewsFilter('all')" style="margin-top: 12px; font-size: 11px; padding: 4px 12px;">
            查看全部电报 (${totalCount})
          </button>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div class="empty-state">
          <span class="empty-icon">⚡</span>
          <p>正在获取全网 7x24 实时电报流...</p>
        </div>
      `;
    }
    return;
  }

  container.innerHTML = itemsToRender.map(n => {
    const isHit = n.is_hit && n.matched_stocks && n.matched_stocks.length;
    const firstStock = isHit ? n.matched_stocks[0] : null;
    const cardClass = isHit ? 'feed-card is-hit' : 'feed-card';
    const clickAttr = firstStock ? `onclick="openDrawer('${firstStock.code}')" style="cursor: pointer;"` : '';

    const hitTags = isHit ? n.matched_stocks.map(s => `
      <span class="feed-hit-tag" onclick="event.stopPropagation(); openDrawer('${s.code}')" title="点击查看 ${s.name} 详情">
        🎯 命中自选: ${s.name} (${s.code})
      </span>
    `).join(' ') : `<span class="feed-tag tag-info">7x24快讯</span>`;

    return `
      <div class="${cardClass}" ${clickAttr}>
        <div class="feed-top">
          <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">
            ${hitTags}
          </div>
          <span class="feed-time">⚡ ${n.datetime || n.time}</span>
        </div>
        ${n.title ? `<div class="feed-title">${n.title}</div>` : ''}
        <div class="feed-body">${n.body || n.content}</div>
      </div>
    `;
  }).join('');
}

// ==========================================
// Stock Operations (Toggle, Edit, Delete)
// ==========================================
window.toggleStockEnabled = async function(code, enabled) {
  try {
    await fetch(`/api/watchlist/${encodeURIComponent(code)}?enabled=${enabled}`, { method: 'PATCH' });
    const target = state.quotes.find(q => q.code === code);
    if (target) target.enabled = enabled;
    renderMatrix();
    showToast(`标的 [${code}] 已${enabled ? '恢复' : '暂停'}监控`, 'info');
  } catch (err) {
    showToast('操作失败: ' + err.message, 'error');
  }
};

window.openThresholdModal = function(code, currentTh) {
  openThresholdModal(code, currentTh);
};

window.openDrawer = function(code) {
  openDrawer(code);
};

window.deleteStock = async function(code, name) {
  if (!confirm(`确定要从监控池中移除 [${code}] ${name} 吗？`)) return;
  try {
    const resp = await fetch(`/api/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE' });
    if (resp.ok) {
      state.quotes = state.quotes.filter(q => q.code !== code);
      renderMatrix();
      await refreshData();
      showToast(`已成功移除 [${code}] ${name}`, 'success');
    } else {
      showToast('移除失败', 'error');
    }
  } catch (err) {
    showToast('删除异常: ' + err.message, 'error');
  }
};

// Start Application
document.addEventListener('DOMContentLoaded', initApp);
