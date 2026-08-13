/* CARES UI: an observability layer over the backend's EngineOutput. */

(() => {
  "use strict";

  const state = {
    user: null,
    current: null,
    history: [],
    daily: [],
    adaptation: [],
    incidents: [],
    guardians: [],
    location: null,
    actions: [],
    page: "dashboard",
    eventSource: null,
    realtimeConnected: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function escapeHTML(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function number(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
  }

  function signedNumber(value, digits = 1) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return `${parsed >= 0 ? "+" : ""}${parsed.toFixed(digits)}`;
  }

  function percentage(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
  }

  function timestamp(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number" || (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value))) {
      return `T+${number(value, 1)} s`;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function initials(name) {
    return String(name || "—").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "—";
  }

  function actionLabel(value) {
    return String(value || "UNKNOWN_ACTION");
  }

  function riskDescriptor(level) {
    const descriptors = {
      LOW: { label: "LOW RISK", summary: "Within personal baseline", className: "risk-low" },
      MEDIUM: { label: "MEDIUM RISK", summary: "Abnormal physiological variation detected", className: "risk-medium" },
      HIGH: { label: "HIGH RISK", summary: "Persistent significant deviation detected", className: "risk-high" },
    };
    return descriptors[String(level)] || { label: "RISK UNAVAILABLE", summary: "Waiting for an engine decision.", className: "risk-low" };
  }

  async function request(path, options = {}) {
    const config = { credentials: "same-origin", ...options, headers: { ...(options.headers || {}) } };
    if (config.body && typeof config.body !== "string") {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path, config);
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.error || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setMessage(selector, message = "") {
    const element = $(selector);
    if (element) element.textContent = message;
  }

  function showAuth(tab = "login") {
    state.eventSource?.close();
    state.eventSource = null;
    $("#auth-view").classList.remove("is-hidden");
    $("#app-view").classList.add("is-hidden");
    $$("[data-auth-tab]").forEach((button) => {
      const active = button.dataset.authTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $("#login-form").classList.toggle("is-hidden", tab !== "login");
    $("#register-form").classList.toggle("is-hidden", tab !== "register");
    setMessage('[data-form-message="login"]');
    setMessage('[data-form-message="register"]');
  }

  function showApp(user) {
    state.user = user;
    $("#auth-view").classList.add("is-hidden");
    $("#app-view").classList.remove("is-hidden");
    $("#topbar-avatar").textContent = initials(user.name);
    $("#settings-name").textContent = user.name || "—";
    $("#settings-email").textContent = user.email || "—";
    $("#settings-created").textContent = timestamp(user.created_at);
    loadData();
    connectRealtime();
  }

  function notify(message) {
    const alert = $("#app-alert");
    alert.textContent = message;
    alert.classList.remove("is-hidden");
    window.clearTimeout(notify.timeout);
    notify.timeout = window.setTimeout(() => alert.classList.add("is-hidden"), 6500);
  }

  async function loadData() {
    try {
      const results = await Promise.all([
        request("/api/dashboard/current"),
        request("/api/dashboard/history?limit=100"),
        request("/api/baseline/current"),
        request("/api/baseline/daily?limit=100"),
        request("/api/baseline/adaptation?limit=100"),
        request("/api/incidents?limit=100"),
        request("/api/guardian"),
        request("/api/location/latest"),
        request("/api/actions?limit=100"),
      ]);
      state.current = results[0];
      state.history = results[1].events || [];
      state.baseline = results[2].baseline || null;
      state.daily = results[3].records || [];
      state.adaptation = results[4].events || [];
      state.incidents = results[5].incidents || [];
      state.guardians = results[6].guardians || [];
      state.location = results[7].location || null;
      state.actions = results[8].actions || [];
      renderAll();
    } catch (error) {
      if (error.status === 401) {
        showAuth("login");
      } else {
        notify(`CARES could not load this workspace: ${error.message}`);
      }
    }
  }

  function connectRealtime() {
    state.eventSource?.close();
    const source = new EventSource("/api/events/stream");
    state.eventSource = source;
    source.onopen = () => {
      state.realtimeConnected = true;
      updateConnection(true);
    };
    source.onerror = () => {
      state.realtimeConnected = false;
      updateConnection(false);
    };
    source.onmessage = (message) => {
      try { handleRealtime(JSON.parse(message.data)); } catch (_) { /* Ignore malformed stream frames. */ }
    };
  }

  function handleRealtime(event) {
    if (!event || !event.type) return;
    if (event.type === "engine_output" && event.data) {
      state.current = { ...(state.current || {}), engine_event: event.data };
      state.history = [event.data, ...state.history.filter((item) => item.id !== event.data.id)].slice(0, 100);
      refreshSecondaryData();
    } else if (event.type === "location" && event.data) {
      state.location = event.data;
      renderLocation();
      refreshSecondaryData();
    } else if (event.type === "guardian_action" && event.data) {
      state.actions = [event.data, ...state.actions.filter((item) => item.id !== event.data.id)].slice(0, 100);
      renderActions();
    } else if (event.type === "incident" && event.data) {
      state.incidents = [event.data, ...state.incidents.filter((item) => item.id !== event.data.id)].slice(0, 100);
      renderIncidents();
    }
    renderCurrent();
    renderCharts();
    renderTables();
  }

  async function refreshSecondaryData() {
    try {
      const [current, incidents, actions, location] = await Promise.all([
        request("/api/dashboard/current"), request("/api/incidents?limit=100"), request("/api/actions?limit=100"), request("/api/location/latest"),
      ]);
      state.current = current;
      state.incidents = incidents.incidents || [];
      state.actions = actions.actions || [];
      state.location = location.location || null;
      renderAll();
    } catch (_) { /* The current event remains visible while the API recovers. */ }
  }

  function updateConnection(connected) {
    const labels = $$("#connection-status, #stream-label, #settings-stream");
    labels.forEach((element) => { element.textContent = connected ? "Live updates connected" : "Realtime unavailable"; });
    $("#connection-status .status-dot")?.classList.toggle("status-dot-muted", !connected);
    $("#stream-dot")?.classList.toggle("status-dot-muted", !connected);
    $("#stream-dot")?.classList.toggle("status-dot-alert", !connected);
    $("#monitoring-mode").textContent = connected ? "Live backend stream" : "Awaiting backend stream";
  }

  function navigate(page) {
    if (!$(`[data-page-view="${page}"]`)) return;
    state.page = page;
    $$("[data-page-view]").forEach((view) => view.classList.toggle("is-active", view.dataset.pageView === page));
    $$("[data-page]").forEach((item) => item.classList.toggle("is-active", item.dataset.page === page));
    $("#page-title").textContent = page.replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    $("#main-content").focus({ preventScroll: true });
    $(".sidebar")?.classList.remove("is-open");
  }

  function renderAll() {
    renderCurrent();
    renderActions();
    renderLocation();
    renderBaseline();
    renderTables();
    renderCharts();
    renderIncidents();
    renderGuardians();
    renderSettings();
  }

  function currentEvent() {
    return state.current?.engine_event || null;
  }

  function setFields(event) {
    const values = event ? {
      current_value: number(event.current_value, 1),
      baseline: number(event.baseline, 1),
      deviation: signedNumber(event.deviation, 1),
      percentage_deviation: signedNumber(event.percentage_deviation, 1),
      risk_score: number(event.risk_score, 1),
      confidence: percentage(Number(event.confidence) * 100, 0),
      confidence_fraction: percentage(Number(event.confidence) * 100, 0) + "%",
      trend: signedNumber(event.trend, 2),
      persistence: number(event.persistence, 1),
      recovery_state: event.recovery_state || "—",
      timestamp: timestamp(event.timestamp),
    } : {};
    $$('[data-field]').forEach((element) => { element.textContent = values[element.dataset.field] ?? "—"; });
  }

  function renderCurrent() {
    const event = currentEvent();
    const empty = !event;
    $("#dashboard-empty").classList.toggle("is-hidden", !empty);
    $("#dashboard-content").classList.toggle("is-hidden", empty);
    if (empty) return;
    setFields(event);
    const descriptor = riskDescriptor(event.risk_level);
    const riskCard = $("#risk-card");
    riskCard.classList.remove("risk-low", "risk-medium", "risk-high");
    riskCard.classList.add(descriptor.className);
    $("#risk-label").textContent = descriptor.label;
    $("#risk-summary").textContent = descriptor.summary;
    $("#risk-score-value").textContent = number(event.risk_score, 1);
    $("#why-deviation").textContent = `${signedNumber(event.deviation, 1)} BPM · ${signedNumber(event.percentage_deviation, 1)}%`;
    $("#engine-explanation").textContent = event.explanation || "No explanation was returned by the engine.";
    const codes = event.reason_codes || [];
    $("#reason-codes").innerHTML = codes.length ? codes.map((code) => `<span class="tag tag-teal">${escapeHTML(code)}</span>`).join("") : '<span class="tag tag-muted">No reason codes returned</span>';
    $("#monitoring-mode").textContent = state.realtimeConnected ? "Live backend stream" : "Backend event loaded";
    document.body.dataset.risk = String(event.risk_level || "");
  }

  function renderActions() {
    const eventActions = currentEvent()?.recommended_actions || [];
    const actions = (state.actions && state.actions.length) ? state.actions : eventActions.map((action) => ({ action_type: action, status: "GENERATED", timestamp: currentEvent()?.timestamp, engine_event_id: currentEvent()?.id }));
    const html = actions.slice(0, 8).map((action) => `<div class="action-row"><span class="action-name"><span class="action-symbol">${action.status === "DELIVERED" ? "✓" : "·"}</span><span>${escapeHTML(actionLabel(action.action_type))}</span></span><span class="action-status status-${escapeHTML(String(action.status || "GENERATED").toLowerCase())}">${escapeHTML(action.status || "GENERATED")}</span></div>`).join("");
    const content = html || '<div class="empty-inline">No guardian actions recorded yet.</div>';
    $("#dashboard-actions").innerHTML = content;
    $("#guardian-actions-table").innerHTML = actions.length ? `<div class="table-scroll"><table><thead><tr><th>Action</th><th>Status</th><th>Generated</th><th>Engine event</th></tr></thead><tbody>${actions.map((action) => `<tr><td><strong>${escapeHTML(action.action_type)}</strong></td><td><span class="action-status status-${escapeHTML(String(action.status || "").toLowerCase())}">${escapeHTML(action.status || "—")}</span></td><td>${escapeHTML(timestamp(action.timestamp))}</td><td>#${escapeHTML(action.engine_event_id)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="table-empty">No guardian action events yet.</div>';
  }

  function renderLocation() {
    const location = state.location;
    $("#settings-gps").textContent = location ? "Connected" : "Not connected";
    if (!location) {
      $("#dashboard-location").innerHTML = '<div class="empty-inline">Hardware location unavailable.</div>';
      $("#location-details").innerHTML = '<div class="empty-inline">No hardware location event has been received.</div>';
      $("#map-container").innerHTML = '<div class="map-empty"><span>⌖</span><p>Map unavailable until hardware coordinates arrive.</p></div>';
      if ($("#map-container").nextElementSibling) $("#map-container").nextElementSibling.textContent = "Map links use the actual coordinates supplied by hardware. No location is generated by CARES.";
      $("#location-notice").innerHTML = '<span class="notice-icon">⌖</span><div><strong>Waiting for hardware GPS</strong><p>Only actual hardware coordinates will be displayed here.</p></div>';
      return;
    }
    const address = location.formatted_address || "Address unavailable — coordinates shown";
    const coordinateText = `${number(location.latitude, 6)}, ${number(location.longitude, 6)}`;
    const meta = `${location.accuracy === null ? "Accuracy unavailable" : `± ${number(location.accuracy, 1)} m`} · ${escapeHTML(location.source)} · ${escapeHTML(timestamp(location.timestamp))}`;
    $("#dashboard-location").innerHTML = `<p class="location-address">${escapeHTML(address)}</p><p class="location-coordinates">${escapeHTML(coordinateText)}</p><p class="location-meta">${meta}</p>`;
    $("#location-details").innerHTML = `<div class="location-detail-value"><span>Human-readable address</span><strong>${escapeHTML(address)}</strong></div><div class="location-detail-value"><span>Coordinates</span><strong>${escapeHTML(coordinateText)}</strong></div><div class="location-detail-value"><span>Accuracy</span><strong>${location.accuracy === null ? "Unavailable" : `± ${number(location.accuracy, 1)} metres`}</strong></div><div class="location-detail-value"><span>Source</span><strong>${escapeHTML(location.source)}</strong></div><div class="location-detail-value"><span>Last updated</span><strong>${escapeHTML(timestamp(location.timestamp))}</strong></div>`;
    $("#location-notice").innerHTML = `<span class="notice-icon">✓</span><div><strong>Hardware GPS location received</strong><p>Source: ${escapeHTML(location.source)}. Coordinates are authoritative; address resolution is optional.</p></div>`;
    const lat = Number(location.latitude);
    const lon = Number(location.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const delta = 0.01;
    const bbox = `${lon - delta},${lat - delta},${lon + delta},${lat + delta}`;
    const mapSrc = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${encodeURIComponent(`${lat},${lon}`)}`;
    const mapLink = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lon}`)}`;
    $("#map-container").innerHTML = `<iframe title="Map of current hardware location" loading="lazy" src="${mapSrc}"></iframe>`;
    const disclaimer = $("#map-container").nextElementSibling;
    if (disclaimer) disclaimer.innerHTML = `<a href="${mapLink}" target="_blank" rel="noreferrer">Open these coordinates in Google Maps ↗</a>`;
  }

  function renderBaseline() {
    const event = currentEvent();
    const baseline = state.baseline;
    $("#baseline-large").textContent = baseline ? number(baseline.baseline, 1) : (event ? number(event.baseline, 1) : "—");
    const calibrating = event?.reason_codes?.includes("BASELINE_CALIBRATING");
    $("#baseline-status").textContent = calibrating ? "Calibrating" : (baseline ? "Ready" : "Not available");
    $("#baseline-status").className = `tag ${calibrating ? "tag-medium" : baseline ? "tag-low" : "tag-muted"}`;
    $("#calibration-label").textContent = baseline?.status ? `${baseline.status}${baseline.calibration_progress === null ? "" : ` · ${number(baseline.calibration_progress, 0)}%`}` : (calibrating ? "Calibration in progress" : "Awaiting engine history");
    $("#calibration-bar").style.width = baseline?.calibration_progress === null || baseline?.calibration_progress === undefined ? "0%" : `${Math.max(0, Math.min(100, Number(baseline.calibration_progress)))}%`;
    $("#trusted-samples").textContent = baseline?.trusted_samples ?? (state.daily[0]?.trusted_samples ?? "—");
    $("#baseline-deviation").textContent = event ? `${signedNumber(event.deviation, 1)} BPM` : "—";
  }

  function tableEmpty(message) { return `<div class="table-empty">${escapeHTML(message)}</div>`; }

  function renderTables() {
    const events = state.history || [];
    const eventRows = events.slice(0, 30).map((event) => `<tr><td>${escapeHTML(timestamp(event.timestamp))}</td><td><span class="table-risk table-risk-${escapeHTML(String(event.risk_level || "").toLowerCase())}">${escapeHTML(event.risk_level || "—")}</span></td><td>${number(event.current_value, 1)} BPM</td><td>${number(event.baseline, 1)} BPM</td><td>${signedNumber(event.deviation, 1)} BPM</td><td>${number(event.risk_score, 1)}</td><td class="table-explanation" title="${escapeHTML(event.explanation || "")}">${escapeHTML(event.explanation || "—")}</td></tr>`).join("");
    const eventTable = eventRows ? `<div class="table-scroll"><table><thead><tr><th>Timestamp</th><th>Risk</th><th>HR</th><th>Baseline</th><th>Deviation</th><th>Score</th><th>Explanation</th></tr></thead><tbody>${eventRows}</tbody></table></div>` : tableEmpty("No engine events have been recorded yet.");
    $("#live-events-table").innerHTML = eventTable;
    $("#history-table").innerHTML = eventTable;

    const dailyRows = (state.daily || []).map((item) => `<tr><td>${escapeHTML(item.date)}</td><td>${number(item.mean_bpm, 1)}</td><td>${number(item.median_bpm, 1)}</td><td>${number(item.std_bpm, 1)}</td><td>${number(item.minimum_bpm, 1)}</td><td>${number(item.maximum_bpm, 1)}</td><td>${escapeHTML(item.trusted_samples)}</td><td>${escapeHTML(item.adaptation_updates)}</td><td>${escapeHTML(item.adaptation_holds)}</td></tr>`).join("");
    $("#daily-table").innerHTML = dailyRows ? `<div class="table-scroll"><table><thead><tr><th>Date</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Minimum</th><th>Maximum</th><th>Trusted samples</th><th>Updates</th><th>Holds</th></tr></thead><tbody>${dailyRows}</tbody></table></div>` : tableEmpty("Daily baseline records will appear after the backend finalizes a day.");

    const adaptationRows = (state.adaptation || []).map((item) => `<tr><td>${escapeHTML(timestamp(item.timestamp))}</td><td>${number(item.previous_baseline, 1)}</td><td>${number(item.observation_mean, 1)}</td><td>${number(item.observation_std, 1)}</td><td>${signedNumber(item.deviation, 1)}</td><td>${escapeHTML(item.risk_level || "—")}</td><td>${item.signal_quality === null ? "—" : number(item.signal_quality, 2)}</td><td><span class="tag ${item.decision === "UPDATED" ? "tag-low" : "tag-muted"}">${escapeHTML(item.decision || "—")}</span></td><td>${number(item.new_baseline, 1)}</td><td class="table-explanation" title="${escapeHTML(item.reason || "")}">${escapeHTML(item.reason || "—")}</td></tr>`).join("");
    $("#adaptation-table").innerHTML = adaptationRows ? `<div class="table-scroll"><table><thead><tr><th>Timestamp</th><th>Previous</th><th>Observation</th><th>Std dev</th><th>Deviation</th><th>Risk</th><th>Signal quality</th><th>Decision</th><th>New baseline</th><th>Reason</th></tr></thead><tbody>${adaptationRows}</tbody></table></div>` : tableEmpty("No baseline adaptation audit events yet.");
  }

  function chartValues(events, field) {
    return events.slice().reverse().map((event) => {
      const value = Number(event[field]);
      return Number.isFinite(value) ? value : null;
    });
  }

  function makeChart(selector, series, colors, ariaLabel) {
    const container = $(selector);
    if (!container) return;
    const length = Math.max(...series.map((item) => item.values.length), 0);
    const points = series.flatMap((item) => item.values).filter((value) => value !== null);
    if (!length || !points.length) {
      container.innerHTML = '<div class="chart-empty">Waiting for backend events.</div>';
      return;
    }
    let min = Math.min(...points);
    let max = Math.max(...points);
    if (min === max) { min -= 1; max += 1; }
    const width = 640;
    const height = 205;
    const pad = { top: 12, right: 8, bottom: 20, left: 8 };
    const x = (index) => pad.left + (length === 1 ? 0 : index / (length - 1)) * (width - pad.left - pad.right);
    const y = (value) => height - pad.bottom - ((value - min) / (max - min)) * (height - pad.top - pad.bottom);
    const paths = series.map((item, seriesIndex) => {
      let path = "";
      let hasPoint = false;
      item.values.forEach((value, index) => {
        if (value === null) return;
        const command = hasPoint ? "L" : "M";
        path += `${command} ${x(index).toFixed(2)} ${y(value).toFixed(2)} `;
        hasPoint = true;
      });
      return path ? `<path d="${path}" fill="none" stroke="${colors[seriesIndex]}" stroke-linecap="round" stroke-linejoin="round" stroke-width="3"/>` : "";
    }).join("");
    const grid = [0, .5, 1].map((ratio) => `<line x1="${pad.left}" y1="${(pad.top + ratio * (height - pad.top - pad.bottom)).toFixed(2)}" x2="${width - pad.right}" y2="${(pad.top + ratio * (height - pad.top - pad.bottom)).toFixed(2)}" stroke="#e3eceb" stroke-width="1"/>`).join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(ariaLabel)}"><title>${escapeHTML(ariaLabel)}</title>${grid}<text x="8" y="14" fill="#82979d" font-size="11">${escapeHTML(number(max, 1))}</text><text x="8" y="196" fill="#82979d" font-size="11">${escapeHTML(number(min, 1))}</text>${paths}</svg>`;
  }

  function renderCharts() {
    const events = state.history || [];
    makeChart("#chart-hr", [{ values: chartValues(events, "current_value") }, { values: chartValues(events, "baseline") }], ["#0b6671", "#e7b875"], "Heart rate and personal baseline");
    makeChart("#chart-risk", [{ values: chartValues(events, "risk_score") }], ["#b44246"], "Risk score");
    makeChart("#chart-deviation", [{ values: chartValues(events, "deviation") }], ["#0b6671"], "Deviation from personal baseline");
    makeChart("#chart-confidence", [{ values: chartValues(events, "confidence") }], ["#197a64"], "Evidence confidence");
    makeChart("#history-chart-hr", [{ values: chartValues(events, "current_value") }, { values: chartValues(events, "baseline") }], ["#0b6671", "#e7b875"], "Historical heart rate and personal baseline");
    makeChart("#history-chart-risk", [{ values: chartValues(events, "risk_score") }], ["#b44246"], "Historical risk score");
  }

  function renderIncidents() {
    const incidents = state.incidents || [];
    if (!incidents.length) {
      $("#incidents-list").innerHTML = '<div class="empty-state"><div class="empty-icon">✓</div><h2>No incidents recorded</h2><p>When the backend records an actual high-risk engine event, its explanation and available location will appear here.</p></div>';
      return;
    }
    $("#incidents-list").innerHTML = incidents.map((incident) => {
      const event = incident.engine_event || {};
      const location = incident.location || null;
      return `<article class="incident-card"><div class="incident-card-head"><div><p class="eyebrow eyebrow-light">Recorded CARES incident</p><h2>${escapeHTML(incident.risk_level || "HIGH")} · Engine event #${escapeHTML(incident.engine_event_id)}</h2></div><span class="incident-time">${escapeHTML(timestamp(incident.timestamp))}</span></div><p class="incident-explanation">${escapeHTML(incident.explanation || event.explanation || "No explanation returned.")}</p><div class="incident-meta"><div><span>HR</span><strong>${number(event.current_value, 1)} BPM</strong></div><div><span>Personal baseline</span><strong>${number(event.baseline, 1)} BPM</strong></div><div><span>Deviation</span><strong>${signedNumber(event.deviation, 1)} BPM</strong></div><div><span>Location</span><strong>${location ? escapeHTML(location.formatted_address || `${location.latitude}, ${location.longitude}`) : "Unavailable"}</strong></div><div><span>Status</span><strong>${escapeHTML(incident.status || "OPEN")}</strong></div></div></article>`;
    }).join("");
  }

  function renderGuardians() {
    const guardians = state.guardians || [];
    $("#guardians-list").innerHTML = guardians.length ? guardians.map((guardian) => `<div class="guardian-card"><span class="guardian-avatar">${escapeHTML(initials(guardian.name))}</span><div class="guardian-card-main"><strong>${escapeHTML(guardian.name)}</strong><span>${escapeHTML(guardian.relationship)} · ${escapeHTML(guardian.phone_number)}</span><span class="tag tag-low">Guardian contact saved</span><span class="tag tag-muted">Notification delivery not configured</span></div><div class="guardian-actions"><button class="mini-button" data-edit-guardian="${guardian.id}" type="button">Edit</button><button class="mini-button danger" data-delete-guardian="${guardian.id}" type="button">Remove</button></div></div>`).join("") : '<div class="empty-inline">No guardian contacts saved yet.</div>';
  }

  function renderSettings() {
    $("#settings-name").textContent = state.user?.name || "—";
    $("#settings-email").textContent = state.user?.email || "—";
    $("#settings-created").textContent = timestamp(state.user?.created_at);
    $("#settings-gps").textContent = state.location ? "Connected" : "Not connected";
  }

  function resetGuardianForm() {
    $("#guardian-id").value = "";
    $("#guardian-form").reset();
    $("#guardian-form-heading").textContent = "Add a guardian";
    $("#guardian-cancel").classList.add("is-hidden");
    setMessage("#guardian-form-message");
  }

  function editGuardian(id) {
    const guardian = state.guardians.find((item) => String(item.id) === String(id));
    if (!guardian) return;
    $("#guardian-id").value = guardian.id;
    $("#guardian-name").value = guardian.name;
    $("#guardian-relationship").value = guardian.relationship;
    $("#guardian-phone").value = guardian.phone_number;
    $("#guardian-form-heading").textContent = "Edit guardian";
    $("#guardian-cancel").classList.remove("is-hidden");
    navigate("guardian");
  }

  async function submitGuardian(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const id = $("#guardian-id").value;
    try {
      const payload = { name: $("#guardian-name").value.trim(), relationship: $("#guardian-relationship").value.trim(), phone_number: $("#guardian-phone").value.trim() };
      if (id) await request(`/api/guardian/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
      else await request("/api/guardian", { method: "POST", body: payload });
      resetGuardianForm();
      await refreshSecondaryData();
      state.guardians = (await request("/api/guardian")).guardians || [];
      renderGuardians();
      notify("Guardian contact saved. No notification was sent.");
    } catch (error) { setMessage("#guardian-form-message", error.message); }
  }

  async function deleteGuardian(id) {
    if (!window.confirm("Remove this guardian contact?")) return;
    try {
      await request(`/api/guardian/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.guardians = (await request("/api/guardian")).guardians || [];
      renderGuardians();
    } catch (error) { notify(error.message); }
  }

  async function login(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    setMessage('[data-form-message="login"]', "Signing in…");
    try {
      const payload = await request("/api/auth/login", { method: "POST", body: { email: $("#login-email").value.trim(), password: $("#login-password").value } });
      form.reset();
      showApp(payload.user);
    } catch (error) { setMessage('[data-form-message="login"]', error.message); }
  }

  async function register(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    if ($("#register-password").value !== $("#register-confirm").value) {
      setMessage('[data-form-message="register"]', "Passwords do not match.");
      return;
    }
    setMessage('[data-form-message="register"]', "Creating your account…");
    try {
      await request("/api/auth/register", { method: "POST", body: { name: $("#register-name").value.trim(), email: $("#register-email").value.trim(), password: $("#register-password").value } });
      showAuth("login");
      setMessage('[data-form-message="login"]', "Account created. You can now log in.");
    } catch (error) { setMessage('[data-form-message="register"]', error.message); }
  }

  async function logout() {
    try { await request("/api/auth/logout", { method: "POST" }); } catch (_) { /* A local logout still clears the view. */ }
    state.user = null;
    showAuth("login");
  }

  function bindEvents() {
    $$("[data-auth-tab]").forEach((button) => button.addEventListener("click", () => showAuth(button.dataset.authTab)));
    $("#login-form").addEventListener("submit", login);
    $("#register-form").addEventListener("submit", register);
    $("#guardian-form").addEventListener("submit", submitGuardian);
    $("#guardian-cancel").addEventListener("click", resetGuardianForm);
    $("#logout-button").addEventListener("click", logout);
    document.addEventListener("click", (event) => {
      const pageButton = event.target.closest("[data-page]");
      if (pageButton) { navigate(pageButton.dataset.page); $(".sidebar")?.classList.remove("is-open"); }
      const editButton = event.target.closest("[data-edit-guardian]");
      if (editButton) editGuardian(editButton.dataset.editGuardian);
      const deleteButton = event.target.closest("[data-delete-guardian]");
      if (deleteButton) deleteGuardian(deleteButton.dataset.deleteGuardian);
    });
    $("#mobile-menu").addEventListener("click", () => {
      const sidebar = $(".sidebar");
      const open = sidebar.classList.toggle("is-open");
      $("#mobile-menu").setAttribute("aria-expanded", String(open));
    });
    $$("[data-toggle]").forEach((button) => button.addEventListener("click", () => {
      const target = $(`#${button.dataset.toggle}`);
      const expanded = target.classList.toggle("is-hidden") === false;
      button.setAttribute("aria-expanded", String(expanded));
      button.textContent = expanded ? "Collapse" : "Expand";
    }));
  }

  async function boot() {
    bindEvents();
    try {
      const payload = await request("/api/auth/me");
      showApp(payload.user);
    } catch (_) {
      showAuth("login");
    }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
