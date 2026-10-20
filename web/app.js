const ROUTES = {
  dashboard: "Dashboard",
  engine: "Signal engine",
  backtest: "Backtest",
  data: "Data workspace",
  connections: "Data connections",
  paper: "Paper log",
  "strategy-lab": "Strategy lab",
  future: "Future",
  telegram: "Telegram channel",
};
const state = { market: null, strategies: null, lastSignal: null, route: "dashboard", providerKeys: [""], providerConfig: null, lastBacktestKey: null, backtestTimer: null, backtestRunning: false, automation: null, lastAutoSignalId: null, lastAutoCandleTimestamp: null, notificationsEnabled: false, toastTimer: null, telegram: null, telegramBusy: false, future: null, futureBusy: false };
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function formatPrice(value, pair = "EUR/USD") {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const digits = pair.includes("JPY") ? 3 : pair.includes("INR") ? 4 : 5;
  return Number(value).toFixed(digits);
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unit = units[0];
  for (let index = 1; size >= 1024 && index < units.length; index++) {
    size /= 1024;
    unit = units[index];
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${unit}`;
}

function formatRateInterval(interval) {
  if (!interval || interval.lower == null || interval.upper == null) return "not available";
  return `${(Number(interval.lower) * 100).toFixed(1)}%–${(Number(interval.upper) * 100).toFixed(1)}%`;
}

function compactTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(date);
}

function showError(message) {
  const notice = $("#importStatus");
  if (notice) notice.textContent = message;
}

function signalLeadLabel(generatedAt, predictionAt) {
  const difference = Math.round((Date.parse(predictionAt) - Date.parse(generatedAt)) / 1000);
  if (!Number.isFinite(difference)) return "— lead";
  const seconds = Math.abs(difference);
  const duration = seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
  return difference < 0 ? `expired ${duration} ago` : `${duration} lead`;
}

function showNotificationToast(message) {
  const toast = $("#notificationToast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 9000);
}

function updateNotificationButton() {
  const button = $("#enableNotifications");
  if (!button) return;
  if (!("Notification" in window)) {
    button.textContent = "ALERTS UNSUPPORTED";
    button.disabled = true;
  } else if (Notification.permission === "denied") {
    button.textContent = "ALERTS BLOCKED IN BROWSER";
    button.disabled = true;
  } else {
    button.disabled = false;
    button.textContent = state.notificationsEnabled ? "ALERTS ON · CLICK TO PAUSE" : "ENABLE ALERTS";
  }
}

function renderUpcomingEvents(guard) {
  const container = $("#upcomingEvents");
  if (!container) return;
  container.replaceChildren();
  const events = guard?.events || [];
  if (!events.length) {
    const empty = document.createElement("small");
    empty.textContent = guard?.active_event ? "An active event is inside the blackout window." : "No relevant high-impact events in the next 24 hours.";
    container.append(empty);
    return;
  }
  events.slice(0, 4).forEach((event) => {
    const row = document.createElement("div");
    row.className = "upcoming-event-row";
    const name = document.createElement("strong");
    name.textContent = `${event.country || "—"} · ${event.event || "Economic release"}`;
    const time = document.createElement("span");
    time.textContent = compactTime(event.time);
    row.append(name, time);
    container.append(row);
  });
}

function notifyForAutomaticSignal(signal) {
  if (!signal || !signal.signal_id) return;
  renderSignal(signal);
  refreshHistory();
  if (signal.side === "NO SIGNAL") return;
  const entry = compactTime(signal.entry_time || signal.generated_at);
  const expiry = compactTime(signal.prediction_time || signal.expires_at);
  const headline = signal.heading || `Orbix · ${signal.side}`;
  showNotificationToast(`${headline} · ${signal.pair} · entry ${entry} · prediction ${expiry}`);
  if (state.notificationsEnabled && "Notification" in window && Notification.permission === "granted") {
    const notification = new Notification(headline, {
      body: `${signal.pair} · entry ${entry} · prediction ${expiry}. ${signal.reason || "Research signal only."}`,
      tag: `orbix-${signal.signal_id}`,
    });
    notification.onclick = () => { window.focus(); window.location.hash = "#engine"; notification.close(); };
  }
}

async function refreshAutomationStatus() {
  if (state.automationPolling) return;
  state.automationPolling = true;
  try {
    const data = await api("/api/automation/status");
    state.automation = data;
    const market = data.market || {};
    const calendar = data.calendar || {};
    const guard = calendar.guard || { status: "UNAVAILABLE", message: "Calendar unavailable.", events: [] };
    const active = Boolean(data.automatic_signals_active);
    const connectionText = active ? "AUTOMATION READY" : market.connected && !market.data_fresh ? "MARKET DATA STALE · SIGNALS PAUSED" : market.connected ? "MARKET CONNECTED · SIGNALS PAUSED" : market.configured ? "MARKET FEED WAITING" : "SERVER FEED NOT CONFIGURED";
    $("#connectionStatus").textContent = connectionText;
    $("#connectionDetail").textContent = `${market.provider || "Market provider"} · ${market.refresh_seconds || 60}s refresh · news: ${guard.status.toLowerCase()}`;
    $("#connectionIndicator").classList.toggle("is-configured", Boolean(market.configured && market.connected));
    $("#connectionIndicator").classList.toggle("is-pending", Boolean(market.configured && !market.connected));

    $("#autoSignalState").textContent = active ? "ACTIVE · NEW CLOSED BARS ONLY" : "PAUSED · PROVIDER REQUIREMENTS";
    $("#autoSignalDetail").textContent = `${data.auto_status} News guard: ${guard.status.toLowerCase()}.`;
    $("#newsGuardState").textContent = guard.status === "BLACKOUT" ? "NEWS BLACKOUT" : guard.status;
    $("#newsGuardDetail").textContent = guard.message || "Economic-calendar state unavailable.";
    renderUpcomingEvents(guard);

    const feedStatus = !market.configured ? "NOT CONFIGURED" : market.last_error ? "FEED ERROR" : market.connected && !market.data_fresh ? "STALE DATA" : market.connected ? "CONNECTED" : market.paused_by_user ? "PAUSED BY USER" : market.active_source === "IMPORTED CSV" && !market.active_dataset_is_provider ? "MANUAL CSV ACTIVE" : "WAITING FOR SYNC";
    $("#marketFeedState").textContent = feedStatus;
    $("#marketFeedMessage").textContent = market.last_error || (market.connected
      ? `Last sync ${market.last_success ? compactTime(market.last_success) : "—"}; latest bar age ${Math.max(0, Math.round(market.latest_bar_age_seconds || 0))}s (limit ${market.max_bar_age_seconds}s); ${market.refresh_seconds}s refresh.`
      : market.configured ? "Use Sync / Activate Live Feed to select the provider dataset." : "Set ORBIX_TWELVE_DATA_API_KEY on the server.");
    $("#calendarFeedState").textContent = !calendar.configured ? "NOT CONFIGURED" : calendar.connected ? "CONNECTED" : calendar.last_error ? "CALENDAR ERROR" : "WAITING FOR REFRESH";
    $("#calendarFeedMessage").textContent = calendar.last_error || (calendar.connected
      ? `${Number(calendar.event_count || 0)} events cached · refresh ${calendar.last_updated ? compactTime(calendar.last_updated) : "—"} · ${calendar.timezone || "UTC"}.`
      : calendar.configured ? "Refresh the calendar to enable news-aware automatic signals." : "Set ORBIX_FINNHUB_API_KEY; provider access may require a paid plan.");
    $("#providerPageState").textContent = active ? "AUTOMATION READY" : "AUTOMATION PAUSED";
    $("#providerPageMessage").textContent = data.auto_status;
    $("#providerStatusIndicator").classList.toggle("is-configured", active);
    $("#providerStatusIndicator").classList.toggle("is-pending", !active && Boolean(market.configured || calendar.configured));
    $("#syncLiveFeedButton").disabled = !market.configured;
    $("#refreshCalendarButton").disabled = !calendar.configured;

    const latestCandleTimestamp = market.latest_candle?.timestamp;
    if (latestCandleTimestamp && state.lastAutoCandleTimestamp && latestCandleTimestamp !== state.lastAutoCandleTimestamp) {
      await Promise.all([loadMarket(), refreshHistory()]);
    }
    if (latestCandleTimestamp) state.lastAutoCandleTimestamp = latestCandleTimestamp;

    const signal = data.latest_auto_signal;
    if (signal?.signal_id && signal.signal_id !== state.lastAutoSignalId) {
      const firstPoll = state.lastAutoSignalId === null;
      state.lastAutoSignalId = signal.signal_id;
      const signalAge = Date.now() - Date.parse(signal.automation_generated_at || signal.generated_at || "");
      if (!firstPoll || (Number.isFinite(signalAge) && signalAge >= 0 && signalAge <= 120000)) {
        notifyForAutomaticSignal(signal);
      }
    }
  } catch (error) {
    $("#autoSignalState").textContent = "AUTOMATION STATUS UNAVAILABLE";
    $("#autoSignalDetail").textContent = error.message;
  } finally {
    state.automationPolling = false;
  }
}

function drawChart(candles) {
  const canvas = $("#priceChart");
  const empty = $("#chartEmpty");
  if (!canvas || !candles || !candles.length) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 7, right: 54, top: 15, bottom: 23 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const visible = candles.slice(-95);
  const high = Math.max(...visible.map((item) => Number(item.high)));
  const low = Math.min(...visible.map((item) => Number(item.low)));
  const spread = Math.max(high - low, Math.abs(high) * 0.00001);
  const y = (price) => pad.top + ((high - price) / spread) * plotHeight;
  const step = plotWidth / visible.length;
  const bodyWidth = Math.max(2, Math.min(7, step * 0.55));

  ctx.strokeStyle = "rgba(255,255,255,.075)";
  ctx.lineWidth = 1;
  for (let line = 0; line <= 4; line++) {
    const yy = pad.top + (plotHeight * line) / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(pad.left + plotWidth, yy); ctx.stroke();
    const price = high - (spread * line) / 4;
    ctx.fillStyle = "rgba(255,255,255,.4)";
    ctx.font = "9px ui-monospace, monospace";
    ctx.textAlign = "right";
    ctx.fillText(formatPrice(price, $("#pairSelect").value), width - 5, yy + 3);
  }

  visible.forEach((candle, index) => {
    const x = pad.left + index * step + step / 2;
    const openY = y(Number(candle.open));
    const closeY = y(Number(candle.close));
    const up = Number(candle.close) >= Number(candle.open);
    ctx.strokeStyle = up ? "rgba(255,255,255,.88)" : "rgba(255,255,255,.38)";
    ctx.fillStyle = up ? "rgba(255,255,255,.92)" : "rgba(255,255,255,.08)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, y(Number(candle.high))); ctx.lineTo(x, y(Number(candle.low))); ctx.stroke();
    const top = Math.min(openY, closeY);
    const bodyHeight = Math.max(1.4, Math.abs(openY - closeY));
    ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, bodyHeight);
    if (!up) ctx.strokeRect(x - bodyWidth / 2, top, bodyWidth, bodyHeight);
  });

  ctx.strokeStyle = "rgba(255,255,255,.72)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  const lastY = y(Number(visible[visible.length - 1].close));
  ctx.moveTo(pad.left, lastY); ctx.lineTo(pad.left + plotWidth, lastY); ctx.stroke();
  ctx.setLineDash([]);
  empty.style.display = "none";
}

async function loadMarket() {
  const pair = $("#pairSelect").value || state.market?.pair || "EUR/USD";
  try {
    const data = await api(`/api/market?pair=${encodeURIComponent(pair)}&limit=180`);
    state.market = data;
    $("#pairSelect").value = data.pair;
    $("#pairTitle").textContent = data.pair;
    $("#enginePairLabel").textContent = `${data.pair} · M1`;
    $("#backtestPairLabel").textContent = `${data.pair} · M1`;
    $("#importPairLabel").textContent = `Selected pair: ${data.pair}`;
    $("#dashboardSignalPair").textContent = `${data.pair} · M1`;
    $("#latestPrice").textContent = formatPrice(data.latest?.close, data.pair);
    $("#latestTime").textContent = data.latest ? compactTime(data.latest.timestamp) : "No candle";
    $("#marketSource").textContent = data.source_label || data.source;
    const synthetic = data.source === "SYNTHETIC DEMO";
    $("#sourceBadge").textContent = data.live_feed_connected ? "LIVE FEED" : synthetic ? "SYNTHETIC DEMO" : "CSV HISTORY";
    $("#feedNoticeText").innerHTML = data.live_feed_connected
      ? "<strong>Live M1 feed connected.</strong> Prices come from the configured Twelve Data account and may differ from a broker’s executable quotes. News blackouts and stale-candle checks remain active."
      : synthetic
        ? "<strong>Synthetic demo prices are active.</strong> No live FX feed is configured or connected, and no Quotex connection is present. Signals and test results are not real-market results."
        : "<strong>Imported CSV history is active.</strong> Prices are historical file data, not a live feed. Signals and test results do not predict future performance.";
    $("#candleCount").textContent = data.total_candles.toLocaleString();
    $("#chartRange").textContent = `${data.candles.length} BARS`;
    if (state.route === "dashboard") drawChart(data.candles);
    if (data.source === "IMPORTED CSV" || state.route === "backtest") scheduleAutomaticBacktest();
  } catch (error) {
    showError(error.message);
    if (state.market?.source === "IMPORTED CSV") $("#pairSelect").value = state.market.pair;
  }
}

async function refreshStorageSummary() {
  try {
    const [storage, learning] = await Promise.all([api("/api/storage"), api("/api/learning")]);
    $("#storageDatasetCount").textContent = Number(storage.datasets_archived).toLocaleString();
    $("#storageCandleCount").textContent = Number(storage.candles_archived).toLocaleString();
    $("#storageFileSize").textContent = formatBytes(storage.database_bytes);
    $("#storageHotWindow").textContent = `${Number(storage.active_window_in_memory).toLocaleString()} / ${Number(storage.signal_window_limit).toLocaleString()}`;
    const activeInfo = storage.active_source === "IMPORTED CSV"
      ? `Active: ${storage.active_pair} · ${Number(storage.active_candles).toLocaleString()} candles`
      : "Synthetic demo is active; archived CSV datasets are preserved.";
    $("#storageArchiveNote").textContent = `${activeInfo} Archive location: ${storage.path}.`;
    const history = $("#datasetHistory");
    history.replaceChildren();
    if (!storage.datasets?.length) {
      const empty = document.createElement("p");
      empty.className = "dataset-empty";
      empty.textContent = "No imported datasets archived yet.";
      history.append(empty);
    } else {
      storage.datasets.forEach((dataset) => {
        const row = document.createElement("div");
        row.className = "dataset-row";
        const description = document.createElement("div");
        description.className = "dataset-description";
        const title = document.createElement("strong");
        title.textContent = `${dataset.pair} · ${dataset.timeframe}${dataset.active ? " · ACTIVE" : ""}`;
        const details = document.createElement("span");
        const firstDate = String(dataset.first_timestamp || "").slice(0, 10);
        const lastDate = String(dataset.latest_timestamp || "").slice(0, 10);
        details.textContent = `${Number(dataset.candle_count).toLocaleString()} candles · ${firstDate} → ${lastDate}`;
        description.append(title, details);
        const activate = document.createElement("button");
        activate.type = "button";
        activate.className = "dataset-activate-button";
        activate.textContent = dataset.active ? "ACTIVE" : "LOAD";
        activate.disabled = dataset.active;
        activate.dataset.activateDataset = dataset.dataset_id;
        row.append(description, activate);
        history.append(row);
      });
    }
    $("#learningUpdateCount").textContent = Number(learning.online_updates).toLocaleString();
    $("#learningAccuracy").textContent = learning.prequential_accuracy == null
      ? "—"
      : `${(Number(learning.prequential_accuracy) * 100).toFixed(1)}%`;
    const scored = Number(learning.prequential_evaluated_predictions || 0);
    const correct = Number(learning.prequential_correct_predictions || 0);
    const interval = learning.prequential_accuracy_ci95;
    const accuracyEvidence = scored
      ? `Prediction record: ${correct}/${scored} correct before updates; 95% Wilson interval ${formatRateInterval(interval)}.`
      : "No non-tied imported-data predictions have matured for evaluation yet.";
    $("#learningNote").textContent = learning.enabled_for_imported_csv
      ? `${learning.update_policy} ${Number(learning.online_updates).toLocaleString()} updates from ${Number(learning.resolved_outcomes_seen).toLocaleString()} outcomes; ${Number(learning.training_samples).toLocaleString()} model samples. ${accuracyEvidence} Ties do not change weights.`
      : `Demo mode is active, so paper outcomes will not train the model. ${learning.update_policy}`;
  } catch (error) {
    $("#storageArchiveNote").textContent = `Storage or learning status unavailable: ${error.message}`;
  }
}

async function activateArchivedDataset(datasetId, button) {
  button.disabled = true;
  try {
    const result = await api(`/api/datasets/${encodeURIComponent(datasetId)}/activate`, { method: "POST" });
    $("#importStatus").textContent = `Activated archived ${result.pair} dataset with ${Number(result.candle_count).toLocaleString()} candles.`;
    await Promise.all([loadMarket(), refreshHistory(), refreshStorageSummary()]);
    scheduleAutomaticBacktest();
  } catch (error) {
    $("#importStatus").textContent = error.message;
    button.disabled = false;
  }
}

function renderSignal(signal) {
  state.lastSignal = signal;
  const side = signal.side || "NO SIGNAL";
  const glyph = side === "BUY" ? "↑" : side === "SELL" ? "↓" : "◌";
  const entryTime = signal.entry_time || signal.generated_at;
  const generatedAt = signal.generated_at || entryTime;
  const predictionTime = signal.prediction_time || signal.expires_at;
  const strategyName = state.strategies?.executable?.find((item) => item.id === signal.strategy)?.name || signal.strategy || "Orbix";
  const ruleMatch = String(signal.reason || "").match(/(\d)\/6/);
  $("#signalHeadline").textContent = signal.heading || `${strategyName} · ${side}`;
  $("#signalSide").textContent = side;
  $("#signalReason").textContent = signal.reason || "No explanation returned.";
  $("#entryTime").textContent = compactTime(entryTime);
  $("#generatedTime").textContent = compactTime(generatedAt);
  $("#expiryTime").textContent = `${compactTime(predictionTime)} · ${signalLeadLabel(generatedAt, predictionTime)}`;
  $("#modelScore").textContent = signal.strategy === "orbix_confluence"
    ? (ruleMatch ? `${ruleMatch[1]}/6 checks` : "rule-based")
    : signal.probability_up == null ? "—" : `${(signal.probability_up * 100).toFixed(1)}% up`;
  $("#signalGlyph").textContent = glyph;
  $("#signalResult").dataset.side = side.toLowerCase().replaceAll(" ", "-");
  $("#signalSide").title = signal.signal_id || "";
  if (signal.news_guard) {
    $("#newsGuardState").textContent = signal.news_guard.status === "BLACKOUT" ? "NEWS BLACKOUT" : signal.news_guard.status;
    $("#newsGuardDetail").textContent = signal.news_guard.message || "No event context returned.";
    renderUpcomingEvents(signal.news_guard);
  }

  $("#dashboardSignalSide").textContent = side;
  $("#dashboardSignalGlyph").textContent = glyph;
  $("#dashboardSignalReason").textContent = signal.reason || "No explanation returned.";
  $("#dashboardSignalPair").textContent = `${signal.pair} · M1`;
  $("#dashboardSignalTimestamp").textContent = compactTime(entryTime);
  refreshHistory();
}

async function generateSignal() {
  const button = $("#generateButton");
  button.disabled = true;
  button.querySelector("span:first-child").textContent = "ANALYZING…";
  try {
    const signal = await api("/api/signal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pair: $("#pairSelect").value || state.market?.pair || "EUR/USD",
        strategy: $("#strategySelect").value,
        horizon: 1,
      }),
    });
    renderSignal(signal);
  } catch (error) {
    $("#signalSide").textContent = "ENGINE ERROR";
    $("#signalReason").textContent = error.message;
    $("#signalGlyph").textContent = "!";
  } finally {
    button.disabled = false;
    button.querySelector("span:first-child").textContent = "GENERATE SIGNAL";
  }
}

function currentBacktestKey() {
  const payout = Math.max(1, Math.min(100, Number($("#payoutInput").value) || 80)) / 100;
  return [
    $("#pairSelect").value || state.market?.pair || "EUR/USD",
    state.market?.source || "",
    state.market?.dataset_id || "",
    Math.floor(Number(state.market?.total_candles || 0) / 50) * 50,
    $("#strategySelect").value,
    payout,
  ].join("|");
}

function scheduleAutomaticBacktest() {
  window.clearTimeout(state.backtestTimer);
  state.backtestTimer = window.setTimeout(() => {
    state.backtestTimer = null;
    if (state.market?.source !== "IMPORTED CSV") {
      $("#backtestNote").textContent = "Automatic walk-forward testing starts after importing real M1 history. Synthetic demo candles are excluded from automatic accuracy reports.";
      return;
    }
    void runBacktest(false);
  }, 250);
}

async function runBacktest(force = true) {
  if (state.backtestRunning) return;
  const key = currentBacktestKey();
  if (!force && state.lastBacktestKey === key) return;
  state.backtestRunning = true;
  const button = $("#backtestButton");
  button.disabled = true;
  button.querySelector("span:first-child").textContent = "TESTING…";
  try {
    const payout = Math.max(1, Math.min(100, Number($("#payoutInput").value) || 80)) / 100;
    const report = await api("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pair: $("#pairSelect").value || state.market?.pair || "EUR/USD", strategy: $("#strategySelect").value, horizon: 1, payout }),
    });
    $("#btSignals").textContent = report.signals;
    $("#btWinRate").textContent = report.win_rate == null ? "—" : `${(report.win_rate * 100).toFixed(1)}%`;
    $("#btNet").textContent = `${report.net_units > 0 ? "+" : ""}${report.net_units.toFixed(2)}u`;
    $("#btBreakEven").textContent = `${(report.break_even_win_rate * 100).toFixed(1)}%`;
    const ci = formatRateInterval(report.win_rate_ci95);
    const recent = report.last_20_decisive;
    const targetStatus = recent.trades < 20
      ? `19/20 check unavailable: only ${recent.trades} decisive trades in this test.`
      : `Last 20 decisive trades: ${recent.wins}/20; ${recent.target_met ? "observed target, not proof of future accuracy" : "target not met"}.`;
    const rateText = report.win_rate == null
      ? "No decisive signals in the test window."
      : `${report.wins}/${report.decisive_trades} decisive wins; 95% Wilson interval ${ci}.`;
    $("#backtestNote").textContent = `${report.data_source || "Current data"} · ${report.test_candles} later candles tested · ${rateText} ${targetStatus} Coverage ${(report.signal_coverage * 100).toFixed(1)}%; net ${report.net_units.toFixed(2)} units; max drawdown ${report.max_drawdown_units.toFixed(2)} units. ${report.note}`;
    state.lastBacktestKey = key;
  } catch (error) {
    $("#backtestNote").textContent = error.message;
  } finally {
    state.backtestRunning = false;
    button.disabled = false;
    button.querySelector("span:first-child").textContent = "RUN WALK-FORWARD TEST";
    if (state.market?.source === "IMPORTED CSV" && currentBacktestKey() !== key) scheduleAutomaticBacktest();
  }
}

async function syncLiveFeed() {
  const button = $("#syncLiveFeedButton");
  button.disabled = true;
  button.querySelector("span:first-child").textContent = "SYNCING…";
  try {
    const result = await api("/api/automation/sync", { method: "POST" });
    showNotificationToast(result.message || `Live feed sync ${result.status}.`);
    await Promise.all([loadMarket(), refreshStorageSummary(), refreshHistory(), refreshAutomationStatus()]);
  } catch (error) {
    $("#marketFeedMessage").textContent = error.message;
    $("#marketFeedState").textContent = "SYNC FAILED";
  } finally {
    button.disabled = !state.automation?.market?.configured;
    button.querySelector("span:first-child").textContent = "SYNC / ACTIVATE LIVE FEED";
  }
}

async function refreshCalendarNow() {
  const button = $("#refreshCalendarButton");
  button.disabled = true;
  button.querySelector("span:first-child").textContent = "REFRESHING…";
  try {
    await api("/api/automation/calendar/refresh", { method: "POST" });
    await refreshAutomationStatus();
  } catch (error) {
    $("#calendarFeedMessage").textContent = error.message;
    $("#calendarFeedState").textContent = "REFRESH FAILED";
  } finally {
    button.disabled = !state.automation?.calendar?.configured;
    button.querySelector("span:first-child").textContent = "REFRESH EVENT CALENDAR";
  }
}

async function toggleBrowserNotifications() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    const permission = await Notification.requestPermission();
    state.notificationsEnabled = permission === "granted";
  } else if (Notification.permission === "granted") {
    state.notificationsEnabled = !state.notificationsEnabled;
  }
  updateNotificationButton();
  showNotificationToast(state.notificationsEnabled ? "Browser alerts enabled for new automatic BUY/SELL signals." : "Browser alerts paused; in-page signals remain active.");
}

async function importCsv() {
  const file = $("#csvFile").files?.[0];
  if (!file) return;
  $("#importStatus").textContent = `Uploading ${file.name} (${formatBytes(file.size)}) to the local candle archive…`;
  try {
    const pair = $("#pairSelect").value || state.market?.pair || "EUR/USD";
    const result = await api(`/api/import?pair=${encodeURIComponent(pair)}`, {
      method: "POST",
      headers: { "Content-Type": "text/csv" },
      body: file,
    });
    $("#importStatus").textContent = `${result.candles_loaded.toLocaleString()} candles stored for ${result.pair}. ${result.note}`;
    $("#paperLogStatus").textContent = "";
    await Promise.all([loadMarket(), refreshHistory(), refreshStorageSummary()]);
    scheduleAutomaticBacktest();
  } catch (error) {
    $("#importStatus").textContent = error.message;
  }
}

async function resetDemo() {
  try {
    const pair = $("#pairSelect").value || state.market?.pair || "EUR/USD";
    await api(`/api/reset-demo?pair=${encodeURIComponent(pair)}`, { method: "POST" });
    $("#importStatus").textContent = "Synthetic demo restored; archived CSV datasets remain. Live feed automation is paused until you explicitly sync it again.";
    await Promise.all([loadMarket(), refreshHistory(), refreshStorageSummary()]);
    scheduleAutomaticBacktest();
  } catch (error) {
    $("#importStatus").textContent = error.message;
  }
}

async function resolvePaperSignal(signalId, button) {
  const input = document.querySelector(`[data-exit-for="${CSS.escape(signalId)}"]`);
  const exitPrice = Number(input?.value);
  if (!(exitPrice > 0)) {
    $("#paperLogStatus").textContent = "Enter a valid manually observed expiry price first.";
    input?.focus();
    return;
  }
  button.disabled = true;
  $("#paperLogStatus").textContent = "Saving paper outcome…";
  try {
    const result = await api("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signal_id: signalId, exit_price: exitPrice }),
    });
    $("#paperLogStatus").textContent = result.learned_from_confirmed_paper_result
      ? `Paper result saved: ${result.outcome}. Model updated from this confirmed result.`
      : `Paper result saved: ${result.outcome}. ${result.note}`;
    await Promise.all([refreshHistory(), refreshStorageSummary()]);
  } catch (error) {
    $("#paperLogStatus").textContent = error.message;
    button.disabled = false;
  }
}

async function refreshHistory() {
  try {
    const data = await api("/api/signals");
    const tbody = $("#historyRows");
    if (!data.signals.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No generated BUY/SELL signals yet.</td></tr>';
      return;
    }
    tbody.innerHTML = data.signals.map((row) => {
      const canResolve = row.can_resolve === true;
      const paperAction = canResolve
        ? `<div class="paper-exit-cell"><input class="paper-exit-input" type="number" min="0" step="any" placeholder="Exit price" aria-label="Manual expiry price for ${escapeHtml(row.pair)}" data-exit-for="${escapeHtml(row.signal_id)}"><button class="paper-resolve-button" data-resolve-id="${escapeHtml(row.signal_id)}">SAVE</button></div>`
        : row.source === "SYNTHETIC DEMO" ? "DEMO ONLY" : row.status === "PENDING" && row.source === "IMPORTED CSV" ? "SELECT DATASET" : "—";
      const result = row.outcome ? ` · ${escapeHtml(row.outcome)}` : "";
      return `
        <tr>
          <td>${escapeHtml(row.pair)}</td>
          <td>${escapeHtml(row.strategy.replaceAll("_", " "))}</td>
          <td>${escapeHtml(row.side)}</td>
          <td>${compactTime(row.entry_time || row.generated_at)}</td>
          <td>${compactTime(row.prediction_time || row.expires_at)}</td>
          <td>${escapeHtml(row.status)}${result}</td>
          <td>${paperAction}</td>
        </tr>`;
    }).join("");
  } catch (_) { /* the main screen remains usable if the paper log is unavailable */ }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function loadStrategies() {
  try {
    const data = await api("/api/strategies");
    state.strategies = data;
    const select = $("#strategySelect");
    select.innerHTML = data.executable.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
    $("#prototypeList").innerHTML = data.executable.map((item) => `
      <div class="prototype-item"><span>${escapeHtml(item.name)}</span><span>PROTOTYPE</span></div>`).join("");
    $("#catalogList").innerHTML = data.candidates.map((item) => `
      <div class="catalog-row"><span class="catalog-number">${String(item.number).padStart(2, "0")}</span><span>${escapeHtml(item.name)}</span><span class="catalog-status">${escapeHtml(item.status)}</span></div>`).join("");
  } catch (_) { /* default option remains */ }
}

function updateProviderKeyCount() {
  const activeKeys = state.providerKeys.filter((key) => key.trim()).length;
  $("#providerKeyCount").textContent = `${activeKeys} / 20 active · ${state.providerKeys.length} slot${state.providerKeys.length === 1 ? "" : "s"}`;
  $("#addKeyButton").disabled = state.providerKeys.length >= 20;
  $("#addKeyButton").innerHTML = state.providerKeys.length >= 20 ? "20 KEY LIMIT" : "ADD KEY <span>＋</span>";
}

function renderCreditDashboard() {
  const activeKeys = state.providerKeys
    .map((value, index) => ({ value: value.trim(), index }))
    .filter((key) => key.value);
  const count = `${activeKeys.length} / 20`;
  $("#creditTriggerCount").textContent = count;
  $("#creditKeyCount").textContent = count;

  const providerName = state.providerConfig?.name || $("#providerName").value.trim();
  $("#creditPopupNote").textContent = providerName
    ? `${providerName} is not connected to a quota endpoint. Per-key usage and limits cannot be fetched yet; no request is being made.`
    : "Provider quota data is unavailable until a provider-specific usage adapter is connected. No quota request is being made.";

  const rows = $("#creditKeyRows");
  rows.replaceChildren();
  if (!activeKeys.length) {
    const empty = document.createElement("p");
    empty.className = "credit-key-empty";
    empty.textContent = "No API keys entered in this tab session.";
    rows.append(empty);
    return;
  }

  activeKeys.forEach(({ value, index }) => {
    const row = document.createElement("div");
    row.className = "credit-key-row";
    const identity = document.createElement("div");
    identity.className = "credit-key-identity";
    const name = document.createElement("span");
    name.className = "credit-key-name";
    name.textContent = `API KEY ${String(index + 1).padStart(2, "0")}`;
    const mask = document.createElement("span");
    mask.className = "credit-key-mask";
    mask.textContent = value.length > 4 ? `•••• ${value.slice(-4)}` : "••••••••";
    identity.append(name, mask);
    const quota = document.createElement("span");
    quota.className = "credit-key-quota";
    quota.textContent = "— USED · — LIMIT";
    row.setAttribute("aria-label", `API key ${index + 1}; credit usage and limit unavailable`);
    row.append(identity, quota);
    rows.append(row);
  });
}

function setCreditPopupOpen(open) {
  const trigger = $("#creditDashboardTrigger");
  $("#creditDashboardPopup").hidden = !open;
  trigger.setAttribute("aria-expanded", String(open));
}

function renderProviderKeySlots() {
  const container = $("#keySlots");
  if (!container) return;
  container.replaceChildren();
  state.providerKeys.forEach((value, index) => {
    const row = document.createElement("div");
    row.className = "key-slot-row";

    const label = document.createElement("label");
    label.className = "key-slot-label";
    label.htmlFor = `provider-key-${index}`;
    label.textContent = `KEY ${String(index + 1).padStart(2, "0")}`;

    const controls = document.createElement("div");
    controls.className = "key-slot-controls";
    const input = document.createElement("input");
    input.className = "provider-input key-input";
    input.id = `provider-key-${index}`;
    input.type = "password";
    input.value = value;
    input.placeholder = `Paste API key ${index + 1}`;
    input.autocomplete = "new-password";
    input.autocapitalize = "off";
    input.spellcheck = false;
    input.dataset.keyIndex = String(index);
    input.setAttribute("aria-label", `API key ${index + 1} of 20`);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "key-visibility-button";
    toggle.dataset.toggleKey = String(index);
    toggle.textContent = "SHOW";
    toggle.setAttribute("aria-label", `Show API key ${index + 1}`);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "key-remove-button";
    remove.dataset.removeKey = String(index);
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove API key ${index + 1}`);

    controls.append(input, toggle, remove);
    row.append(label, controls);
    container.append(row);
  });
  updateProviderKeyCount();
}

function updateProviderConnectionStatus() {
  const activeKeys = state.providerKeys.filter((key) => key.trim()).length;
  const hasNotes = Boolean($("#providerName").value.trim() || $("#providerApiUrl").value.trim() || activeKeys);
  let formMessage = "These browser-only notes are not sent to the server and do not configure a live feed.";
  if (state.providerConfig) {
    formMessage = `${state.providerConfig.name} saved as local reference notes only. They are not tested, transmitted, or used by Orbix’s built-in integrations.`;
  } else if (hasNotes) {
    formMessage = `${activeKeys} key note${activeKeys === 1 ? "" : "s"} entered in this tab. These values cannot connect a provider; use server environment variables instead.`;
  }
  $("#providerFormStatus").textContent = formMessage;
  renderCreditDashboard();
}

function saveProviderConfiguration() {
  const name = $("#providerName").value.trim();
  const rawUrl = $("#providerApiUrl").value.trim();
  const keys = state.providerKeys.map((key) => key.trim()).filter(Boolean);
  if (!name || !rawUrl || keys.length === 0) {
    state.providerConfig = null;
    updateProviderConnectionStatus();
    $("#providerFormStatus").textContent = "Enter a provider name, an HTTPS/HTTP base URL, and at least one API key before keeping this session configuration.";
    return;
  }
  let baseUrl;
  try {
    const parsed = new URL(rawUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("unsupported protocol");
    baseUrl = parsed.toString();
  } catch (_) {
    state.providerConfig = null;
    updateProviderConnectionStatus();
    $("#providerFormStatus").textContent = "Enter a valid HTTP or HTTPS API base URL. No request has been made.";
    $("#providerApiUrl").focus();
    return;
  }
  state.providerConfig = { name, baseUrl, keyCount: keys.length };
  updateProviderConnectionStatus();
}

function clearProviderConfiguration() {
  state.providerConfig = null;
  state.providerKeys = [""];
  $("#providerName").value = "";
  $("#providerApiUrl").value = "";
  renderProviderKeySlots();
  updateProviderConnectionStatus();
  $("#providerName").focus();
}

function setActivePage(route, shouldScroll = true) {
  if (!Object.hasOwn(ROUTES, route)) route = "dashboard";
  state.route = route;
  document.querySelectorAll(".page-view").forEach((page) => {
    const active = page.dataset.page === route;
    page.hidden = !active;
    page.classList.toggle("is-active", active);
    if (active) {
      page.classList.remove("is-active");
      void page.offsetWidth;
      page.classList.add("is-active");
    }
  });
  document.querySelectorAll(".top-nav [data-route]").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.title = `${ROUTES[route]} — Orbix Signal Lab`;
  if (shouldScroll) window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  if (route === "dashboard") {
    if (state.market) requestAnimationFrame(() => drawChart(state.market.candles));
    startBlackholeAnimation();
  } else {
    stopBlackholeAnimation();
  }
  if (route === "backtest") {
    if (state.market) void loadMarket();
    scheduleAutomaticBacktest();
  }
  if (route === "future") void loadFuturePlan();
  if (route === "telegram") void refreshTelegramPage();
}

function handleRouteChange() {
  setCreditPopupOpen(false);
  const route = window.location.hash.slice(1);
  if (!Object.hasOwn(ROUTES, route)) {
    window.history.replaceState(null, "", "#dashboard");
    setActivePage("dashboard", false);
    return;
  }
  setActivePage(route);
}

let blackholeCanvas = null;
let blackholeContext = null;
let blackholeWidth = 0;
let blackholeHeight = 0;
let blackholeRatio = 1;
let blackholeStars = [];
let blackholeFrame = 0;
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const TAU = Math.PI * 2;

function createBlackholeStars() {
  blackholeStars = Array.from({ length: 92 }, (_, index) => {
    const seed = index + 1;
    const x = (Math.sin(seed * 127.1) * 43758.5453) % 1;
    const y = (Math.sin(seed * 311.7) * 19642.349) % 1;
    return {
      x: Math.abs(x),
      y: Math.abs(y),
      radius: .35 + (index % 4) * .22,
      phase: index * 1.73,
      alpha: .12 + (index % 5) * .045,
    };
  });
}

function sizeBlackholeCanvas() {
  if (!blackholeCanvas || !blackholeContext) return;
  const rect = blackholeCanvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  blackholeWidth = rect.width;
  blackholeHeight = rect.height;
  blackholeRatio = Math.min(window.devicePixelRatio || 1, 2);
  blackholeCanvas.width = Math.round(blackholeWidth * blackholeRatio);
  blackholeCanvas.height = Math.round(blackholeHeight * blackholeRatio);
  blackholeContext.setTransform(blackholeRatio, 0, 0, blackholeRatio, 0, 0);
  createBlackholeStars();
  drawBlackhole(0);
}

function drawBlackhole(time) {
  if (!blackholeContext || blackholeWidth < 2 || blackholeHeight < 2) return;
  const ctx = blackholeContext;
  const width = blackholeWidth;
  const height = blackholeHeight;
  const centerX = width * .53;
  const centerY = height * .5;
  const radius = Math.min(width, height) * .135;
  const spin = time * .00012;
  ctx.clearRect(0, 0, width, height);

  for (const star of blackholeStars) {
    const flicker = .78 + .22 * Math.sin(time * .001 + star.phase);
    ctx.fillStyle = `rgba(255,255,255,${star.alpha * flicker})`;
    ctx.beginPath();
    ctx.arc(star.x * width, star.y * height, star.radius, 0, TAU);
    ctx.fill();
  }

  const glow = ctx.createRadialGradient(centerX, centerY, radius * .5, centerX, centerY, radius * 4.5);
  glow.addColorStop(0, "rgba(255,255,255,.11)");
  glow.addColorStop(.28, "rgba(255,255,255,.055)");
  glow.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(centerX - radius * 5, centerY - radius * 5, radius * 10, radius * 10);

  // Elliptical accretion disk, orbiting particles, and a dark event horizon.
  ctx.save();
  ctx.translate(centerX, centerY);
  ctx.rotate(-.13);
  ctx.scale(1, .39);
  ctx.globalCompositeOperation = "lighter";
  const diskGlow = ctx.createRadialGradient(0, 0, radius * .85, 0, 0, radius * 3.25);
  diskGlow.addColorStop(0, "rgba(255,255,255,0)");
  diskGlow.addColorStop(.22, "rgba(255,255,255,.11)");
  diskGlow.addColorStop(.43, "rgba(255,255,255,.035)");
  diskGlow.addColorStop(.67, "rgba(255,255,255,.14)");
  diskGlow.addColorStop(.76, "rgba(255,255,255,.045)");
  diskGlow.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = diskGlow;
  ctx.beginPath();
  ctx.arc(0, 0, radius * 3.25, 0, TAU);
  ctx.fill();

  for (let ring = 0; ring < 5; ring++) {
    const ringRadius = radius * (1.45 + ring * .31);
    ctx.beginPath();
    ctx.ellipse(0, 0, ringRadius, ringRadius, 0, 0, TAU);
    ctx.strokeStyle = `rgba(255,255,255,${.1 + (ring % 2) * .07})`;
    ctx.lineWidth = ring === 1 ? 1.25 : .65;
    ctx.stroke();
  }

  for (let particle = 0; particle < 150; particle++) {
    const fraction = particle / 150;
    const orbit = radius * (1.35 + ((particle * 17) % 100) / 100 * 1.75);
    const angularSpeed = .34 + (particle % 9) * .025;
    const angle = fraction * TAU * 8 + spin * angularSpeed * 10;
    const px = Math.cos(angle) * orbit;
    const py = Math.sin(angle) * orbit;
    const hotSide = .2 + .8 * (Math.cos(angle + .6) + 1) / 2;
    const opacity = Math.min(.8, (.19 + (particle % 5) * .08) * hotSide);
    ctx.fillStyle = `rgba(255,255,255,${opacity})`;
    ctx.beginPath();
    ctx.arc(px, py, .45 + (particle % 4) * .21, 0, TAU);
    ctx.fill();
  }
  ctx.restore();

  ctx.save();
  ctx.translate(centerX, centerY);
  ctx.rotate(-.13);
  ctx.beginPath();
  ctx.ellipse(0, 0, radius * 1.34, radius * .53, 0, Math.PI * .08, Math.PI * .92);
  const photonArc = ctx.createLinearGradient(-radius * 1.3, 0, radius * 1.3, 0);
  photonArc.addColorStop(0, "rgba(255,255,255,0)");
  photonArc.addColorStop(.45, "rgba(255,255,255,.06)");
  photonArc.addColorStop(.71, "rgba(255,255,255,.8)");
  photonArc.addColorStop(1, "rgba(255,255,255,.05)");
  ctx.strokeStyle = photonArc;
  ctx.lineWidth = 1.1;
  ctx.shadowBlur = 11;
  ctx.shadowColor = "rgba(255,255,255,.75)";
  ctx.stroke();
  ctx.restore();

  const horizon = ctx.createRadialGradient(centerX - radius * .3, centerY - radius * .36, radius * .05, centerX, centerY, radius * 1.08);
  horizon.addColorStop(0, "#252525");
  horizon.addColorStop(.28, "#090909");
  horizon.addColorStop(.72, "#010101");
  horizon.addColorStop(1, "#000000");
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, TAU);
  ctx.fillStyle = horizon;
  ctx.shadowBlur = 19;
  ctx.shadowColor = "rgba(255,255,255,.23)";
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = "rgba(255,255,255,.44)";
  ctx.lineWidth = .75;
  ctx.stroke();

  // A slowly sweeping lens flare catches the outer photon ring.
  const flareAngle = spin * .55 - .35;
  const flareX = centerX + Math.cos(flareAngle) * radius * 1.33;
  const flareY = centerY + Math.sin(flareAngle) * radius * .52;
  const flare = ctx.createRadialGradient(flareX, flareY, 0, flareX, flareY, radius * .45);
  flare.addColorStop(0, "rgba(255,255,255,.5)");
  flare.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = flare;
  ctx.beginPath();
  ctx.arc(flareX, flareY, radius * .45, 0, TAU);
  ctx.fill();
}

function initializeBlackholeAnimation() {
  blackholeCanvas = $("#blackholeCanvas");
  blackholeContext = blackholeCanvas?.getContext("2d") || null;
  if (blackholeContext) sizeBlackholeCanvas();
}

function animateBlackhole(time) {
  drawBlackhole(time);
  blackholeFrame = window.requestAnimationFrame(animateBlackhole);
}

function stopBlackholeAnimation() {
  if (blackholeFrame) window.cancelAnimationFrame(blackholeFrame);
  blackholeFrame = 0;
}

function startBlackholeAnimation() {
  stopBlackholeAnimation();
  sizeBlackholeCanvas();
  if (state.route === "dashboard" && !document.hidden && !prefersReducedMotion.matches) {
    blackholeFrame = window.requestAnimationFrame(animateBlackhole);
  }
}

function telegramDeliveryLabel(signalId, data) {
  const sent = new Set(data?.sent_signal_ids || []);
  return sent.has(signalId) ? "SENT" : "QUEUED";
}

function renderTelegramStatus(data) {
  state.telegram = data;
  const configured = Boolean(data.configured);
  const modeTag = $("#telegramModeTag");
  if (modeTag) modeTag.textContent = configured ? "CONNECTED" : "NOT CONFIGURED";
  const indicator = $("#telegramIndicator");
  if (indicator) {
    indicator.classList.toggle("is-configured", configured);
    indicator.classList.toggle("is-pending", !configured);
  }
  const target = $("#telegramTarget");
  if (target) target.textContent = data.target_chat || "NOT SET";
  const message = $("#telegramStateMessage");
  if (message) {
    message.textContent = configured
      ? `Admin bot ready · automatic posting ${data.auto_post_enabled ? "ON" : "OFF"} · ${data.timezone}`
      : "Set ORBIX_TELEGRAM_BOT_TOKEN and ORBIX_TELEGRAM_CHAT_ID on the server, then restart.";
  }
  const tokenState = $("#telegramTokenState");
  if (tokenState) tokenState.textContent = configured ? "CONFIGURED (HIDDEN)" : "NOT CONFIGURED";
  const chatState = $("#telegramChatState");
  if (chatState) chatState.textContent = configured ? "CONFIGURED" : "NOT CONFIGURED";
  const timezoneState = $("#telegramTimezoneState");
  if (timezoneState) timezoneState.textContent = data.timezone || "UTC";
  const autoState = $("#telegramAutoState");
  if (autoState) autoState.textContent = data.auto_post_enabled ? "ON" : "OFF";
  ["#telegramSendLatest", "#telegramSendBatch", "#telegramTest"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = !configured || state.telegramBusy;
  });
  const autoToggle = $("#telegramAutoPost");
  if (autoToggle) {
    autoToggle.checked = Boolean(data.auto_post_enabled);
    autoToggle.disabled = !configured;
  }
  const rows = $("#telegramLogRows");
  if (rows) {
    rows.replaceChildren();
    const signals = data.recent_signals || [];
    if (!signals.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "empty-row";
      cell.textContent = "No automatic signals recorded yet.";
      row.append(cell);
      rows.append(row);
    } else {
      signals.slice().reverse().forEach((signal) => {
        const row = document.createElement("tr");
        const entry = document.createElement("td");
        entry.textContent = compactTime(signal.entry_time);
        const pair = document.createElement("td");
        pair.textContent = signal.pair || "—";
        const direction = document.createElement("td");
        direction.textContent = signal.side === "BUY" ? "CALL" : signal.side === "SELL" ? "PUT" : "ABSTAIN";
        const probability = document.createElement("td");
        probability.textContent = signal.probability_up == null ? "—" : `${(Number(signal.probability_up) * 100).toFixed(1)}%`;
        const delivery = document.createElement("td");
        const chip = document.createElement("span");
        const delivered = telegramDeliveryLabel(signal.signal_id, data) === "SENT";
        chip.className = `telegram-delivery-chip${delivered ? " is-sent" : ""}`;
        chip.textContent = delivered ? "SENT" : "QUEUED";
        delivery.append(chip);
        row.append(entry, pair, direction, probability, delivery);
        rows.append(row);
      });
    }
  }
  if (data.last_error) {
    const status = $("#telegramSendStatus");
    if (status) status.textContent = `Last delivery error: ${data.last_error}`;
  }
}

async function refreshTelegramPage(options = {}) {
  try {
    const data = await api("/api/telegram/status");
    renderTelegramStatus(data);
    if (options.announce) {
      const status = $("#telegramSendStatus");
      if (status) status.textContent = data.configured ? "Channel status refreshed." : "Bot credentials are not configured on the server yet.";
    }
  } catch (error) {
    const status = $("#telegramSendStatus");
    if (status) status.textContent = error.message;
  }
}

function setTelegramBusy(busy, message) {
  state.telegramBusy = busy;
  ["#telegramSendLatest", "#telegramSendBatch", "#telegramTest"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = busy || !state.telegram?.configured;
  });
  const status = $("#telegramSendStatus");
  if (status && message) status.textContent = message;
}

async function sendTelegramSignals(kind) {
  if (state.telegramBusy) return;
  const includeDemo = Boolean($("#telegramIncludeDemo")?.checked);
  if (kind === "test") {
    setTelegramBusy(true, "Sending a connection test…");
    try {
      const result = await api("/api/telegram/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setTelegramBusy(false, `Connection test delivered to ${result.chat_id} (message ${result.message_id}).`);
      await refreshTelegramPage();
    } catch (error) {
      setTelegramBusy(false, `Test failed: ${error.message}`);
    }
    return;
  }
  const limit = kind === "batch" ? 5 : 1;
  setTelegramBusy(true, kind === "batch" ? "Sending the latest unsent signals…" : "Sending the latest signal…");
  try {
    const result = await api("/api/telegram/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit, include_demo: includeDemo }),
    });
    if (result.status === "nothing_to_send") {
      setTelegramBusy(false, result.message || "No unsent signals are available.");
    } else {
      setTelegramBusy(false, `Delivered ${result.sent.length} signal(s) · message ${result.message_id}.`);
    }
    await refreshTelegramPage();
  } catch (error) {
    setTelegramBusy(false, `Send failed: ${error.message}`);
  }
}

async function previewTelegramMessage() {
  const includeDemo = Boolean($("#telegramIncludeDemo")?.checked);
  const output = $("#telegramPreviewText");
  try {
    const result = await api("/api/telegram/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 1, include_demo: includeDemo, dry_run: true }),
    });
    if (!output) return;
    if (result.status === "nothing_to_send") {
      output.hidden = false;
      output.textContent = result.message || "No signal is available to preview.";
    } else {
      output.hidden = false;
      output.textContent = result.text;
    }
  } catch (error) {
    if (output) {
      output.hidden = false;
      output.textContent = error.message;
    }
  }
}

async function toggleTelegramAutoPost() {
  const toggle = $("#telegramAutoPost");
  if (!toggle) return;
  const desired = toggle.checked;
  toggle.disabled = true;
  try {
    const data = await api("/api/telegram/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: desired }),
    });
    renderTelegramStatus({ ...state.telegram, ...data });
    const status = $("#telegramSendStatus");
    if (status) status.textContent = data.auto_post_enabled ? "Automatic posting enabled for new live signals." : "Automatic posting disabled.";
  } catch (error) {
    toggle.checked = !desired;
    toggle.disabled = false;
    const status = $("#telegramSendStatus");
    if (status) status.textContent = error.message;
  }
}

function renderFuturePlan(plan) {
  state.future = plan;
  const focus = plan.focus || {};
  const data = plan.data || {};
  const readiness = plan.readiness || {};
  const evidence = plan.evidence;
  $("#futurePair").textContent = `${focus.pair || data.pair || "—"} · M1`;
  $("#futureWhy").textContent = focus.why || "No liquidity note is available for this pair.";
  $("#futureAccuracyNote").textContent = focus.accuracy_note || "No pair is more likely to be accurate.";
  $("#futureLiquidityRank").textContent = focus.liquidity_rank ? `#${focus.liquidity_rank}` : "—";
  $("#futureDataSource").textContent = data.source || "—";
  $("#futureCandleCount").textContent = Number(data.candles || 0).toLocaleString();
  $("#futureReadyState").textContent = readiness.ready ? "ARMED" : "BLOCKED";

  const blocked = $("#futureBlocked");
  const reasons = $("#futureBlockedReasons");
  if (blocked && reasons) {
    reasons.replaceChildren();
    (readiness.reasons || []).forEach((reason) => {
      const item = document.createElement("li");
      item.textContent = reason;
      reasons.append(item);
    });
    blocked.hidden = Boolean(readiness.ready) || !(readiness.reasons || []).length;
  }

  const windows = plan.windows || [];
  const windowGrid = $("#futureWindowGrid");
  if (windowGrid) {
    windowGrid.replaceChildren();
    windows.forEach((window, index) => {
      const card = document.createElement("div");
      card.className = `future-window ${window.status === "ARMED" ? "is-armed" : "is-blocked"}`;
      const number = document.createElement("span");
      number.textContent = `WINDOW ${String(index + 1).padStart(2, "0")}`;
      const time = document.createElement("strong");
      time.textContent = new Date(window.entry_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      const stateChip = document.createElement("b");
      stateChip.className = "future-window-state";
      stateChip.textContent = window.status;
      const note = document.createElement("small");
      note.textContent = window.status === "ARMED" ? "Direction reveals on candle close" : "Waiting for readiness";
      card.append(number, time, stateChip, note);
      windowGrid.append(card);
    });
  }
  const windowCount = $("#futureWindowCount");
  if (windowCount) windowCount.textContent = `${windows.length} WINDOWS`;

  const replayRows = $("#futureReplayRows");
  if (replayRows) {
    replayRows.replaceChildren();
    const replay = plan.replay || [];
    if (!replay.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "empty-row";
      cell.textContent = "Not enough completed candles to replay the gate.";
      row.append(cell);
      replayRows.append(row);
    }
    replay.slice().reverse().forEach((item) => {
      const row = document.createElement("tr");
      const entry = document.createElement("td");
      entry.textContent = compactTime(item.entry_time);
      const prediction = document.createElement("td");
      prediction.textContent = compactTime(item.prediction_time);
      const direction = document.createElement("td");
      direction.textContent = item.direction || "—";
      const probability = document.createElement("td");
      probability.textContent = item.probability_up == null ? "—" : `${(Number(item.probability_up) * 100).toFixed(1)}%`;
      const why = document.createElement("td");
      why.className = "future-replay-why";
      why.textContent = String(item.reason || "").slice(0, 150);
      row.append(entry, prediction, direction, probability, why);
      replayRows.append(row);
    });
  }
  const replayCount = $("#futureReplayCount");
  if (replayCount) replayCount.textContent = `${(plan.replay || []).length} ROWS`;

  const hitRate = $("#futureHitRate");
  if (hitRate) hitRate.textContent = evidence?.win_rate == null ? "—" : `${(evidence.win_rate * 100).toFixed(1)}%`;
  const interval = $("#futureHitInterval");
  if (interval) {
    interval.textContent = evidence?.win_rate_ci95
      ? `${(evidence.win_rate_ci95.lower * 100).toFixed(1)}–${(evidence.win_rate_ci95.upper * 100).toFixed(1)}%`
      : "—";
  }
  const signals = $("#futureSignals");
  if (signals) signals.textContent = evidence ? `${evidence.signals} / ${evidence.test_candles}` : "—";
  const breakEven = $("#futureBreakEven");
  if (breakEven) breakEven.textContent = evidence?.break_even_win_rate == null ? "—" : `${(evidence.break_even_win_rate * 100).toFixed(1)}%`;
  const evidenceNote = $("#futureEvidenceNote");
  if (evidenceNote) {
    const intervalText = evidence?.win_rate_ci95
      ? `${(evidence.win_rate_ci95.lower * 100).toFixed(1)}–${(evidence.win_rate_ci95.upper * 100).toFixed(1)}%`
      : "wide";
    evidenceNote.textContent = evidence
      ? `Walk-forward re-run on the active ${data.source} dataset (${evidence.window_candles.toLocaleString()} candles, ${(evidence.assumed_payout * 100).toFixed(0)}% payout assumption). The observed hit rate has a ${intervalText} interval; that range is not proof of future accuracy.`
      : "At least 120 candles are needed before any walk-forward evidence exists.";
    if (data.source === "SYNTHETIC DEMO") evidenceNote.textContent += " Synthetic demo data is practice data, not market evidence.";
  }

  const alternatives = $("#futureAlternatives");
  if (alternatives) {
    alternatives.replaceChildren();
    (plan.alternatives || []).forEach((item) => {
      const row = document.createElement("div");
      row.className = `future-alternative${item.active ? " is-active" : ""}`;
      const name = document.createElement("strong");
      name.textContent = item.pair;
      const detail = document.createElement("span");
      detail.textContent = `${item.liquidity} ${item.notes}`;
      const status = document.createElement("em");
      status.textContent = item.active ? "ACTIVE" : "NO LOCAL DATA";
      row.append(name, detail, status);
      alternatives.append(row);
    });
  }
  const planStatus = $("#futurePlanStatus");
  if (planStatus) planStatus.textContent = `Plan generated ${compactTime(plan.generated_at)} · ${plan.strategy_name}.`;
}

async function loadFuturePlan() {
  if (state.futureBusy) return;
  state.futureBusy = true;
  const status = $("#futurePlanStatus");
  if (status) status.textContent = "Generating the single-pair plan…";
  try {
    const plan = await api("/api/future/plan?count=6");
    renderFuturePlan(plan);
  } catch (error) {
    if (status) status.textContent = error.message;
  } finally {
    state.futureBusy = false;
  }
}

function wireEvents() {
  $("#generateButton").addEventListener("click", generateSignal);
  $("#futureRefresh").addEventListener("click", loadFuturePlan);
  $("#telegramSendLatest").addEventListener("click", () => sendTelegramSignals("latest"));
  $("#telegramSendBatch").addEventListener("click", () => sendTelegramSignals("batch"));
  $("#telegramTest").addEventListener("click", () => sendTelegramSignals("test"));
  $("#telegramPreview").addEventListener("click", previewTelegramMessage);
  $("#telegramAutoPost").addEventListener("change", toggleTelegramAutoPost);
  $("#telegramRefresh").addEventListener("click", () => refreshTelegramPage({ announce: true }));
  $("#enableNotifications").addEventListener("click", toggleBrowserNotifications);
  $("#syncLiveFeedButton").addEventListener("click", syncLiveFeed);
  $("#refreshCalendarButton").addEventListener("click", refreshCalendarNow);
  $("#backtestButton").addEventListener("click", () => runBacktest(true));
  $("#pairSelect").addEventListener("change", async () => {
    await loadMarket();
    scheduleAutomaticBacktest();
  });
  $("#strategySelect").addEventListener("change", () => scheduleAutomaticBacktest());
  $("#payoutInput").addEventListener("change", () => scheduleAutomaticBacktest());
  $("#csvFile").addEventListener("change", importCsv);
  $("#resetDemoButton").addEventListener("click", resetDemo);
  $("#refreshHistory").addEventListener("click", refreshHistory);
  $("#refreshStorage").addEventListener("click", refreshStorageSummary);
  $("#datasetHistory").addEventListener("click", (event) => {
    const button = event.target.closest("[data-activate-dataset]");
    if (button && !button.disabled) activateArchivedDataset(button.dataset.activateDataset, button);
  });
  $("#addKeyButton").addEventListener("click", () => {
    if (state.providerKeys.length >= 20) return;
    state.providerKeys.push("");
    state.providerConfig = null;
    renderProviderKeySlots();
    updateProviderConnectionStatus();
    document.getElementById(`provider-key-${state.providerKeys.length - 1}`)?.focus();
  });
  $("#providerName").addEventListener("input", () => { state.providerConfig = null; updateProviderConnectionStatus(); });
  $("#providerApiUrl").addEventListener("input", () => { state.providerConfig = null; updateProviderConnectionStatus(); });
  $("#saveProviderButton").addEventListener("click", saveProviderConfiguration);
  $("#clearProviderButton").addEventListener("click", clearProviderConfiguration);
  $("#creditDashboardTrigger").addEventListener("click", () => {
    setCreditPopupOpen($("#creditDashboardPopup").hidden);
  });
  $("#creditPopupClose").addEventListener("click", () => {
    setCreditPopupOpen(false);
    $("#creditDashboardTrigger").focus();
  });
  document.addEventListener("click", (event) => {
    if (!$("#creditDashboard").contains(event.target)) setCreditPopupOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#creditDashboardPopup").hidden) {
      setCreditPopupOpen(false);
      $("#creditDashboardTrigger").focus();
    }
  });
  $("#keySlots").addEventListener("input", (event) => {
    const input = event.target.closest("[data-key-index]");
    if (!input) return;
    state.providerKeys[Number(input.dataset.keyIndex)] = input.value;
    state.providerConfig = null;
    updateProviderKeyCount();
    updateProviderConnectionStatus();
  });
  $("#keySlots").addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-key]");
    if (toggle) {
      const input = $(`#provider-key-${toggle.dataset.toggleKey}`);
      input.type = input.type === "password" ? "text" : "password";
      toggle.textContent = input.type === "password" ? "SHOW" : "HIDE";
      toggle.setAttribute("aria-label", `${input.type === "password" ? "Show" : "Hide"} API key ${Number(toggle.dataset.toggleKey) + 1}`);
      return;
    }
    const remove = event.target.closest("[data-remove-key]");
    if (!remove) return;
    state.providerKeys.splice(Number(remove.dataset.removeKey), 1);
    state.providerConfig = null;
    renderProviderKeySlots();
    updateProviderConnectionStatus();
  });
  $("#historyRows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-resolve-id]");
    if (button) resolvePaperSignal(button.dataset.resolveId, button);
  });
  $("#dismissNotice").addEventListener("click", () => { $("#feedNotice").hidden = true; });
  $("#toggleCatalog").addEventListener("click", () => {
    const list = $("#catalogList");
    const isOpen = !list.hidden;
    list.hidden = isOpen;
    $("#toggleCatalog").setAttribute("aria-expanded", String(!isOpen));
    $("#toggleCatalog").innerHTML = isOpen ? "SHOW ALL 30 <span>＋</span>" : "HIDE LIST <span>−</span>";
  });
  document.querySelectorAll(".top-nav [data-route]").forEach((link) => link.addEventListener("click", () => { /* native hash navigation supports back/forward */ }));
  window.addEventListener("hashchange", handleRouteChange);
  window.addEventListener("resize", () => {
    if (state.market && state.route === "dashboard") drawChart(state.market.candles);
    startBlackholeAnimation();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopBlackholeAnimation();
    else startBlackholeAnimation();
  });
  prefersReducedMotion.addEventListener("change", startBlackholeAnimation);
}

async function start() {
  wireEvents();
  renderProviderKeySlots();
  updateProviderConnectionStatus();
  updateNotificationButton();
  initializeBlackholeAnimation();
  let route = window.location.hash.slice(1);
  if (!Object.hasOwn(ROUTES, route)) {
    route = "dashboard";
    window.history.replaceState(null, "", "#dashboard");
  }
  setActivePage(route, false);
  await Promise.all([loadStrategies(), loadMarket(), refreshHistory(), refreshStorageSummary(), refreshAutomationStatus()]);
  scheduleAutomaticBacktest();
  window.setInterval(() => { void refreshAutomationStatus(); }, 10000);
  if (state.route === "dashboard") startBlackholeAnimation();
}

document.addEventListener("DOMContentLoaded", start);
