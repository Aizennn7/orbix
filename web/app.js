const ROUTES = {
  dashboard: "Dashboard",
  engine: "Signal engine",
  backtest: "Backtest",
  data: "Data workspace",
  connections: "Data connections",
  paper: "Paper log",
  "strategy-lab": "Strategy lab",
};
const state = { market: null, strategies: null, lastSignal: null, route: "dashboard", providerKeys: [""], providerConfig: null, lastBacktestKey: null, backtestTimer: null, backtestRunning: false };
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
    $("#marketSource").textContent = data.source;
    const synthetic = data.source === "SYNTHETIC DEMO";
    $("#sourceBadge").textContent = synthetic ? "SYNTHETIC DEMO" : "CSV HISTORY";
    $("#feedNoticeText").innerHTML = synthetic
      ? "<strong>Synthetic demo prices are active.</strong> No live FX feed or Quotex connection is installed. Signals and test results are not real-market results."
      : "<strong>Imported CSV history is active.</strong> Prices are historical file data, not a live feed. Signals and test results do not predict future performance.";
    $("#candleCount").textContent = data.total_candles.toLocaleString();
    $("#chartRange").textContent = `${data.candles.length} BARS`;
    if (state.route === "dashboard") drawChart(data.candles);
    if (state.route === "backtest") scheduleAutomaticBacktest();
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
  $("#signalSide").textContent = side;
  $("#signalReason").textContent = signal.reason || "No explanation returned.";
  const entryTime = signal.entry_time || signal.generated_at;
  const predictionTime = signal.prediction_time || signal.expires_at;
  $("#entryTime").textContent = compactTime(entryTime);
  $("#signalLeadTime").textContent = `${signal.horizon || 1} minute${(signal.horizon || 1) === 1 ? "" : "s"}`;
  $("#expiryTime").textContent = `${compactTime(predictionTime)} · ${signal.horizon || 1}m`;
  $("#modelScore").textContent = signal.probability_up == null ? "—" : `${(signal.probability_up * 100).toFixed(1)}% up`;
  $("#signalGlyph").textContent = glyph;
  $("#signalResult").dataset.side = side.toLowerCase().replaceAll(" ", "-");
  $("#signalSide").title = signal.signal_id || "";

  $("#dashboardSignalSide").textContent = side;
  $("#dashboardSignalGlyph").textContent = glyph;
  $("#dashboardSignalReason").textContent = signal.reason || "No explanation returned.";
  $("#dashboardSignalPair").textContent = `${signal.pair} · M1`;
  $("#dashboardSignalTimestamp").textContent = compactTime(signal.entry_time || signal.generated_at);
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
    state.market?.total_candles || 0,
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
    $("#importStatus").textContent = "Synthetic demo data restored. Archived CSV datasets remain on disk.";
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
  const hasEdits = Boolean($("#providerName").value.trim() || $("#providerApiUrl").value.trim() || activeKeys);
  let status = "NOT CONNECTED";
  let detail = "No provider configured · synthetic demo active";
  let pageMessage = "No provider is configured. Synthetic demo data remains active.";
  let formMessage = "Keys stay in browser memory only. They are not saved to disk or sent to Orbix’s server.";
  const indicator = $("#connectionIndicator");
  const pageIndicator = $("#providerStatusIndicator");
  [indicator, pageIndicator].forEach((dot) => dot?.classList.remove("is-configured", "is-pending"));

  if (state.providerConfig) {
    status = "CONFIGURED · NOT CONNECTED";
    detail = `${state.providerConfig.name} · ${activeKeys} key${activeKeys === 1 ? "" : "s"} in this session · not tested`;
    pageMessage = `${state.providerConfig.name} is saved in this tab’s memory. It is not connected or tested; a provider-specific adapter is needed first.`;
    formMessage = "Configuration kept in memory for this page session only. No credentials were sent, validated, or used.";
    [indicator, pageIndicator].forEach((dot) => dot?.classList.add("is-configured"));
  } else if (hasEdits) {
    status = "UNSAVED SETTINGS";
    detail = `${activeKeys} API key${activeKeys === 1 ? "" : "s"} entered · connection not tested`;
    pageMessage = "Settings have not been saved for this tab session. No provider connection is active.";
    formMessage = "Unsaved edits stay in this page’s memory. Keep them for this session when ready; they will not be sent anywhere.";
    [indicator, pageIndicator].forEach((dot) => dot?.classList.add("is-pending"));
  }

  $("#connectionStatus").textContent = status;
  $("#connectionDetail").textContent = detail;
  $("#providerPageState").textContent = status;
  $("#providerPageMessage").textContent = pageMessage;
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

function wireEvents() {
  $("#generateButton").addEventListener("click", generateSignal);
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
  initializeBlackholeAnimation();
  let route = window.location.hash.slice(1);
  if (!Object.hasOwn(ROUTES, route)) {
    route = "dashboard";
    window.history.replaceState(null, "", "#dashboard");
  }
  setActivePage(route, false);
  await Promise.all([loadStrategies(), loadMarket(), refreshHistory(), refreshStorageSummary()]);
  scheduleAutomaticBacktest();
  if (state.route === "dashboard") startBlackholeAnimation();
}

document.addEventListener("DOMContentLoaded", start);
