/**
 * Orbix Mobile App - Main Logic
 * Mobile PWA client. Local IndexedDB store with optional Supabase sync.
 */
import { db } from './db.js';
import { liveDB } from './live_db.js';
import { evaluateAll, getBestSignal, generateSignalId, strategies } from './engine.js';

class OrbixMobileApp {
  constructor() {
    this.currentPage = 'dashboard';
    this.candles = [];
    this.datasetId = null;
    this.pair = 'EUR/USD';
  }

  async init() {
    console.log('[Orbix Mobile] Initialising local store...');
    await db.init();
    
    // Check live config at root level
    await liveDB.checkRootConfig();
    liveDB.startAutoSync();
    
    // Listen for live signals from Supabase at root
    window.addEventListener('orbix-live-signal', (e) => {
      console.log('[Orbix Mobile] Live signal from root Supabase:', e.detail);
      this.showNotification(`LIVE: ${e.detail.direction} ${e.detail.pair} @ ${e.detail.confidence}%`);
      this.loadData().then(() => this.renderAll());
    });
    
    // Load data
    await this.loadData();
    
    // Try live fetch from root API
    const live = await liveDB.fetchLiveData();
    if (live && live.candles && live.candles.length) {
      this.candles = live.candles;
      console.log(`[Orbix Mobile] Using ${this.candles.length} live candles from root`);
    }
    
    // Bind UI
    this.bindEvents();
    this.renderStrategies();
    
    // Generate demo data if empty
    if (this.candles.length === 0) {
      await this.generateDemoData();
      await this.loadData();
    }
    
    this.renderAll();
    
    // Auto-refresh every 60s (like live feed) + live sync
    setInterval(() => this.refresh(), 60000);
    
    console.log('[Orbix Mobile] Ready');
  }

  async loadData() {
    const active = await db.getActiveDataset();
    if (active) {
      this.datasetId = active.dataset_id;
      this.pair = active.pair;
      this.candles = await db.getCandles(this.datasetId, 500);
    } else {
      this.candles = [];
    }
    const datasets = await db.getDatasets();
    console.log(`[Orbix Mobile] Loaded ${this.candles.length} candles from ${datasets.length} datasets`);
  }

  async generateDemoData() {
    console.log('[Orbix Mobile] Generating demo M1 candles...');
    const datasetId = `demo-${Date.now()}`;
    const now = new Date();
    const candles = [];
    let price = 1.0850;
    
    for (let i = 199; i >= 0; i--) {
      const ts = new Date(now - i * 60000);
      const volatility = 0.0003;
      const trend = Math.sin(i / 30) * 0.0001;
      const change = (Math.random() - 0.5) * volatility + trend;
      price += change;
      const open = price;
      const close = price + (Math.random() - 0.5) * volatility;
      const high = Math.max(open, close) + Math.random() * volatility * 0.5;
      const low = Math.min(open, close) - Math.random() * volatility * 0.5;
      
      candles.push({
        timestamp: ts.toISOString(),
        open: parseFloat(open.toFixed(5)),
        high: parseFloat(high.toFixed(5)),
        low: parseFloat(low.toFixed(5)),
        close: parseFloat(close.toFixed(5)),
        volume: Math.floor(Math.random() * 1000)
      });
      price = close;
    }
    
    await db.importCandles(datasetId, 'EUR/USD', candles);
    await db.setSetting('demo_generated', {at: new Date().toISOString(), count: candles.length});
  }

  bindEvents() {
    // Navigation
    document.querySelectorAll('.nav-item').forEach(el => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        this.navigate(el.dataset.page);
      });
    });

    // Actions
    document.querySelectorAll('.action-btn').forEach(btn => {
      btn.addEventListener('click', () => this.handleAction(btn.dataset.action));
    });

    document.getElementById('scanAllBtn')?.addEventListener('click', () => this.scanAll());
    document.getElementById('refreshMarket')?.addEventListener('click', () => this.refresh());
    document.getElementById('importBtn')?.addEventListener('click', () => this.importCSV());
    document.getElementById('clearDbBtn')?.addEventListener('click', () => this.clearDB());
    document.getElementById('csvFile')?.addEventListener('change', (e) => this.handleFile(e));
    document.getElementById('settingsBtn')?.addEventListener('click', () => this.showSettings());

    // Handle hash navigation
    window.addEventListener('hashchange', () => {
      const hash = location.hash.slice(1) || 'dashboard';
      this.navigate(hash);
    });
  }

  navigate(page) {
    this.currentPage = page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('is-active'));
    document.getElementById(`page-${page}`)?.classList.add('is-active');
    document.querySelectorAll('.nav-item').forEach(n => {
      n.classList.toggle('active', n.dataset.page === page);
    });
    location.hash = page;
    this.renderPage(page);
  }

  renderAll() {
    this.renderDashboard();
    this.renderPaperLog();
    this.renderDataPage();
  }

  renderPage(page) {
    if (page === 'dashboard') this.renderDashboard();
    if (page === 'paper') this.renderPaperLog();
    if (page === 'data') this.renderDataPage();
    if (page === 'trader') this.renderTrader();
  }

  renderDashboard() {
    // Stats
    db.getStorageInfo().then(info => {
      document.getElementById('statCandles').textContent = info.candles;
      document.getElementById('statSignals').textContent = info.signals;
      document.getElementById('statWin').textContent = info.dbType;
    });

    // Latest signal
    const signal = this.candles.length ? getBestSignal(this.candles) : {side: 'NO_SIGNAL', reason: 'No data'};
    const sideEl = document.getElementById('signalSide');
    const badge = document.getElementById('signalBadge');
    
    if (sideEl) {
      sideEl.textContent = signal.side;
      sideEl.className = 'signal-side ' + (signal.side === 'BUY' ? 'buy' : signal.side === 'SELL' ? 'sell' : '');
    }
    if (badge) {
      badge.textContent = signal.side;
      badge.className = 'badge ' + (signal.side === 'BUY' ? 'buy' : signal.side === 'SELL' ? 'sell' : '');
    }
    
    document.getElementById('signalPair').textContent = this.pair;
    document.getElementById('signalProb').textContent = signal.probability ? `${(signal.probability*100).toFixed(1)}%` : '—';
    document.getElementById('signalTime').textContent = new Date().toLocaleTimeString();
    document.getElementById('signalReason').textContent = signal.reason || '—';

    // Candles table
    const rows = document.getElementById('candleRows');
    if (rows) {
      const last20 = this.candles.slice(-20).reverse();
      rows.innerHTML = last20.map(c => `
        <tr>
          <td>${new Date(c.timestamp).toLocaleTimeString()}</td>
          <td>${c.open.toFixed(5)}</td>
          <td>${c.high.toFixed(5)}</td>
          <td>${c.low.toFixed(5)}</td>
          <td style="color:${c.close>=c.open?'#4ade80':'#f87171'}">${c.close.toFixed(5)}</td>
        </tr>
      `).join('') || '<tr><td colspan="5">No candles</td></tr>';
    }

    // Chart (simple)
    this.renderChart();
  }

  renderChart() {
    const canvas = document.getElementById('candleChart');
    if (!canvas || this.candles.length === 0) return;
    const ctx = canvas.getContext('2d');
    const data = this.candles.slice(-50);
    const closes = data.map(c => c.close);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const range = max - min || 0.001;
    
    canvas.width = canvas.offsetWidth * 2;
    canvas.height = 120 * 2;
    ctx.scale(2,2);
    const w = canvas.offsetWidth;
    const h = 120;
    
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle = 'rgba(255,255,255,.15)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    closes.forEach((c, i) => {
      const x = (i / (closes.length-1)) * w;
      const y = h - ((c - min) / range) * h;
      if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    ctx.stroke();
    
    // Last price dot
    const lastY = h - ((closes[closes.length-1] - min) / range) * h;
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(w-4, lastY, 3, 0, Math.PI*2);
    ctx.fill();
  }

  renderStrategies() {
    const container = document.getElementById('strategyList');
    if (!container) return;
    container.innerHTML = Object.entries(strategies).map(([key, s]) => `
      <div class="strategy-item">
        <div>
          <div class="name">${s.name}</div>
          <div class="desc">${s.description}</div>
        </div>
        <div class="status">${key}</div>
      </div>
    `).join('');
  }

  scanAll() {
    if (this.candles.length === 0) {
      document.getElementById('scanResults').innerHTML = '<span style="color:#f87171">No candles loaded</span>';
      return;
    }
    const all = evaluateAll(this.candles);
    const html = Object.entries(all).map(([k, v]) => `
      <div style="padding:10px;border:1px solid var(--line);border-radius:8px;margin:6px 0;background:rgba(255,255,255,.02);">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <strong style="font-size:10px;">${k}</strong>
          <span class="badge ${v.side==='BUY'?'buy':v.side==='SELL'?'sell':''}">${v.side}</span>
        </div>
        <div style="font-size:8px;color:var(--muted);margin-top:4px;">${v.reason}</div>
        ${v.probability ? `<div style="font-size:8px;margin-top:4px;">Prob: ${(v.probability*100).toFixed(1)}%</div>` : ''}
      </div>
    `).join('');
    document.getElementById('scanResults').innerHTML = html;
  }

  async renderPaperLog() {
    const signals = await db.getSignals(50);
    const tbody = document.getElementById('paperRows');
    if (!tbody) return;
    if (signals.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4">No signals — generate one from Dashboard</td></tr>';
      return;
    }
    tbody.innerHTML = signals.map(s => `
      <tr>
        <td>${new Date(s.generated_at).toLocaleString()}</td>
        <td><span class="badge ${s.side==='BUY'?'buy':s.side==='SELL'?'sell':''}">${s.side}</span></td>
        <td>${s.entry_price?.toFixed(5) || '—'}</td>
        <td>${s.outcome || s.status}</td>
      </tr>
    `).join('');
  }

  async renderDataPage() {
    const datasets = await db.getDatasets();
    const info = await db.getStorageInfo();
    
    // Fetch root-level database config from API if available
    let rootConfig = null;
    try {
      const res = await fetch('/api/database/config');
      if (res.ok) {
        const data = await res.json();
        rootConfig = data.config;
      }
    } catch (e) {
      console.log('[Orbix Mobile] Root DB config not available offline, using local');
    }
    
    document.getElementById('datasetCount').textContent = `${datasets.length} datasets`;
    document.getElementById('datasetList').innerHTML = datasets.map(d => `
      <div style="padding:10px;border:1px solid var(--line);border-radius:8px;margin:6px 0;">
        <div style="font-size:10px;font-weight:600;">${d.dataset_id}</div>
        <div style="font-size:8px;color:var(--muted);">${d.pair} • ${d.candle_count} candles • ${new Date(d.updated_at).toLocaleString()}</div>
      </div>
    `).join('') || 'No datasets';
    
    const rootInfo = rootConfig ? `
      <div style="margin-top:8px;padding:8px;background:rgba(100,150,255,.1);border:1px solid rgba(100,150,255,.2);border-radius:6px;">
        <div style="font-size:8px;font-weight:600;color:#8ab4ff;">ROOT LEVEL DATABASE CONFIG</div>
        <div style="font-size:7px;margin-top:4px;color:var(--muted);">
          Backend: ${rootConfig.backend} (configured: ${rootConfig.configured_backend})<br>
          Data dir: ${rootConfig.data_dir}<br>
          Location: database/ at root — single source of truth<br>
          ${rootConfig.supabase_configured ? '✓ Supabase cloud sync ready' : '○ Local only (SQLite/DuckDB)'}
        </div>
      </div>
    ` : '';

    document.getElementById('storageInfo').innerHTML = `
      <div style="display:grid;gap:6px;font-size:9px;">
        <div>DB Type: <strong>${info.dbType}</strong></div>
        <div>Datasets: ${info.datasets} • Candles: ${info.candles} • Signals: ${info.signals}</div>
        <div>Est. Size: ${info.estimatedSize}</div>
        <div style="margin-top:8px;padding:8px;background:rgba(74,222,128,.1);border:1px solid rgba(74,222,128,.2);border-radius:6px;color:#4ade80;">
          ✓ Data is cached locally — works offline<br>
          ✓ Server configuration comes from the .env at the project root
        </div>
        ${rootInfo}
      </div>
    `;
  }

  renderTrader() {
    const el = document.getElementById('traderCard');
    if (!el) return;
    el.innerHTML = `
      <div style="padding:12px;">
        <div style="font-size:10px;font-weight:600;margin-bottom:8px;">JOURNAL — OFFLINE</div>
        <div style="font-size:8px;color:var(--muted);line-height:1.6;">
          • All trades stored in local IndexedDB<br>
          • Works 24/7 even without internet<br>
          • Sync to Supabase when online (future)<br>
          • Discipline rules enforced locally
        </div>
        <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <div style="padding:10px;background:var(--panel);border:1px solid var(--line);border-radius:8px;">
            <div style="font-size:7px;color:var(--dim);">TODAY</div>
            <div style="font-size:14px;font-weight:600;margin-top:4px;">—</div>
          </div>
          <div style="padding:10px;background:var(--panel);border:1px solid var(--line);border-radius:8px;">
            <div style="font-size:7px;color:var(--dim);">WIN RATE</div>
            <div style="font-size:14px;font-weight:600;margin-top:4px;">—</div>
          </div>
        </div>
      </div>
    `;
  }

  async handleAction(action) {
    switch(action) {
      case 'generate':
        await this.generateSignal();
        break;
      case 'import':
        document.getElementById('csvFile')?.click();
        break;
      case 'backtest':
        this.navigate('engine');
        setTimeout(() => this.scanAll(), 100);
        break;
      case 'sync':
        if (liveDB.isLive) {
          await liveDB.syncToCloud();
          alert('✓ Synced to Supabase at root level — now live online 24/7');
        } else {
          this.showSettings();
        }
        break;
    }
  }

  async generateSignal() {
    if (this.candles.length === 0) {
      alert('No candles — import CSV or wait for demo data');
      return;
    }
    const result = getBestSignal(this.candles);
    const signal = {
      signal_id: generateSignalId(),
      pair: this.pair,
      strategy: result.strategy || 'orbix_confluence',
      side: result.side,
      entry_price: this.candles[this.candles.length-1].close,
      generated_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 60000).toISOString(),
      probability_up: result.probability || 0.5,
      reason: result.reason,
      status: 'PENDING'
    };
    
    if (result.side !== 'NO_SIGNAL') {
      await db.saveSignal(signal);
      // Haptic feedback on mobile
      if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
      // Browser notification, only when the user has granted permission.
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        try {
          new Notification(`Orbix ${result.side}`, {
            body: `${this.pair} ${result.side} @ ${signal.entry_price.toFixed(5)} - ${result.reason.slice(0, 60)}`
          });
        } catch {}
      }
      alert(`Signal: ${result.side} ${this.pair} @ ${signal.entry_price.toFixed(5)}\n${result.reason}`);
      this.renderAll();
    } else {
      alert(`No Signal: ${result.reason}`);
    }
  }

  async importCSV() {
    const fileInput = document.getElementById('csvFile');
    const file = fileInput?.files[0];
    const pair = document.getElementById('pairSelect')?.value || 'EUR/USD';
    if (!file) {
      alert('Select CSV file first');
      return;
    }
    const text = await file.text();
    const lines = text.trim().split('\n');
    const headers = lines[0].toLowerCase().split(',').map(h => h.trim());
    const candles = [];
    
    for (let i = 1; i < lines.length; i++) {
      const vals = lines[i].split(',');
      const row = {};
      headers.forEach((h, idx) => row[h] = vals[idx]);
      const ts = row.timestamp || row.datetime || row.time || row.date;
      if (!ts) continue;
      try {
        candles.push({
          timestamp: new Date(ts).toISOString(),
          open: parseFloat(row.open || row.o),
          high: parseFloat(row.high || row.h),
          low: parseFloat(row.low || row.l),
          close: parseFloat(row.close || row.c),
          volume: parseFloat(row.volume || row.v || 0)
        });
      } catch {}
    }
    
    if (candles.length === 0) {
      alert('No valid candles found in CSV');
      return;
    }
    
    const datasetId = `import-${Date.now()}`;
    await db.importCandles(datasetId, pair, candles);
    alert(`Imported ${candles.length} candles for ${pair}`);
    await this.loadData();
    this.renderAll();
  }

  handleFile(e) {
    // Triggered from hidden input
    this.importCSV();
  }

  async clearDB() {
    if (!confirm('Clear all local data? This deletes all cached candles and signals in this browser.')) return;
    await db.clearAll();
    this.candles = [];
    this.datasetId = null;
    await this.generateDemoData();
    await this.loadData();
    this.renderAll();
    alert('DB cleared, demo data regenerated');
  }

  async refresh() {
    await this.loadData();
    // Try live fetch
    const live = await liveDB.fetchLiveData();
    if (live && live.candles && live.candles.length) {
      this.candles = live.candles;
    }
    this.renderDashboard();
    // Sync if live
    if (liveDB.isLive) liveDB.syncToCloud();
  }

  showSettings() {
    const modal = document.getElementById('settingsModal');
    const statusEl = document.getElementById('liveStatus');
    const urlInput = document.getElementById('supabaseUrlInput');
    const keyInput = document.getElementById('supabaseKeyInput');
    
    if (!modal) {
      alert(`Orbix Mobile v0.5.0\n\n• 5 strategies: OCG, ML consensus, pullback, reversal, breakout\n• Local IndexedDB cache, works offline\n• Optional Supabase sync when configured on the server\n\nDB: ${this.candles.length} candles\nPair: ${this.pair}\nMode: PWA Browser\nLive: ${liveDB.isLive ? 'YES - Supabase' : 'NO - offline'}`);
      return;
    }
    
    modal.style.display = 'flex';
    
    // Fill current config
    const status = liveDB.getStatus();
    statusEl.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;">
        <span style="width:8px;height:8px;border-radius:50%;background:${status.isLive ? '#4ade80' : '#fbbf24'};display:inline-block;"></span>
        <strong>${status.isLive ? 'LIVE ONLINE' : 'OFFLINE DB'}</strong>
      </div>
      <div style="margin-top:6px;font-size:11px;color:rgba(255,255,255,.6);">
        Backend: ${status.backend}<br>
        Location: ${status.location}<br>
        Data dir: ${status.dataDir} at root<br>
        Online: ${status.isOnline ? 'yes' : 'no'}<br>
        ${status.supabaseConfigured ? '✓ Supabase URL configured' : '○ Supabase not configured — set at root .env for live'}
      </div>
    `;
    
    urlInput.value = localStorage.getItem('SUPABASE_URL') || '';
    keyInput.value = localStorage.getItem('SUPABASE_ANON_KEY') || '';
    
    // Bind close
    document.getElementById('closeSettings').onclick = () => modal.style.display = 'none';
    modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };
    
    document.getElementById('saveLiveConfig').onclick = async () => {
      const url = urlInput.value.trim();
      const key = keyInput.value.trim();
      if (!url || !key) {
        alert('Enter both URL and anon key from root .env');
        return;
      }
      liveDB.configure(url, key);
      alert('✓ Saved. Syncing to Supabase for this session.\nFor a persistent setting, put SUPABASE_URL and SUPABASE_ANON_KEY in the server .env.');
      modal.style.display = 'none';
      liveDB.updateStatus();
    };
  }

  showNotification(msg) {
    // Simple toast
    const toast = document.createElement('div');
    toast.textContent = msg;
    toast.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:rgba(100,150,255,.9);color:white;padding:12px 20px;border-radius:12px;font-size:13px;z-index:9999;backdrop-filter:blur(10px);';
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  }
}

// Boot
const app = new OrbixMobileApp();
window.addEventListener('DOMContentLoaded', () => app.init());
window.OrbixMobileApp = app;
