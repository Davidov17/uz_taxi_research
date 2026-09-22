// Admin dashboard frontend. Plain JS, no build step: fetches JSON from
// /api/* (see web/api.py) and renders it. Every chart/list here reads
// straight from the API response shape produced by the application-layer
// services (StatisticsService / ExportService / QualitativeService) — no
// client-side aggregation happens here, so what you see is exactly what
// the backend computed, not a recomputation that could drift from it.

const state = {
  cities: [],
  platforms: [],
  languages: [],
  driverTypes: [],
  surveyPage: 0,
  surveyPageSize: 20,
  screenshotPage: 0,
  screenshotPageSize: 24,
};

const CHART_COLORS = ["#2563eb", "#f97316", "#16a34a", "#dc2626", "#7c3aed", "#0891b2", "#ca8a04"];
const charts = {};

function currentFilters() {
  const params = new URLSearchParams();
  const city = document.getElementById("filter-city").value;
  const platform = document.getElementById("filter-platform").value;
  const driverType = document.getElementById("filter-driver-type").value;
  const language = document.getElementById("filter-language").value;
  const dateFrom = document.getElementById("filter-date-from").value;
  const dateTo = document.getElementById("filter-date-to").value;
  if (city) params.set("city_id", city);
  if (platform) params.set("platform_id", platform);
  if (driverType) params.set("driver_type_id", driverType);
  if (language) params.set("language", language);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  return params;
}

async function getJSON(path, params) {
  const url = params ? `${path}?${params.toString()}` : path;
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

function fmtNum(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtDate(value) {
  if (!value) return "—";
  return value.slice(0, 10);
}

// ---- tabs -------------------------------------------------------------------

function initTabs() {
  document.querySelectorAll(".tab-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-link").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });
}

// ---- filter dropdowns --------------------------------------------------------

async function loadReferenceData() {
  state.cities = await getJSON("/api/cities");
  state.platforms = await getJSON("/api/platforms");
  state.driverTypes = await getJSON("/api/driver-types");
  state.languages = await getJSON("/api/languages");

  const citySelect = document.getElementById("filter-city");
  for (const c of state.cities) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    citySelect.appendChild(opt);
  }
  const platformSelect = document.getElementById("filter-platform");
  for (const p of state.platforms) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    platformSelect.appendChild(opt);
  }
  const driverTypeSelect = document.getElementById("filter-driver-type");
  for (const d of state.driverTypes) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name;
    driverTypeSelect.appendChild(opt);
  }
  const languageSelect = document.getElementById("filter-language");
  for (const l of state.languages) {
    const opt = document.createElement("option");
    opt.value = l.code;
    opt.textContent = l.name;
    languageSelect.appendChild(opt);
  }
}

// ---- charts helper ------------------------------------------------------------

function renderBarChart(canvasId, breakdown, { horizontal = false } = {}) {
  const ctx = document.getElementById(canvasId);
  const labels = Object.keys(breakdown.counts);
  const values = Object.values(breakdown.counts);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: `count (n=${breakdown.total})`, data: values, backgroundColor: CHART_COLORS }],
    },
    options: {
      indexAxis: horizontal ? "y" : "x",
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

function renderGroupedBarChart(canvasId, breakdownsBySeries) {
  // Same category axis, multiple breakdowns side by side as separate
  // datasets/bars — used where two sources answer the same question
  // (a structured choice vs. a best-effort text-heuristic reading) and
  // must stay visually distinct, never summed into one number.
  const ctx = document.getElementById(canvasId);
  const seriesNames = Object.keys(breakdownsBySeries);
  const allLabels = [...new Set(seriesNames.flatMap((name) => Object.keys(breakdownsBySeries[name].counts)))];
  const datasets = seriesNames.map((name, i) => ({
    label: `${name} (n=${breakdownsBySeries[name].total})`,
    data: allLabels.map((label) => breakdownsBySeries[name].counts[label] || 0),
    backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
  }));
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels: allLabels, datasets },
    options: { scales: { y: { beginAtZero: true } } },
  });
}

function renderValueBarChart(canvasId, valuesByLabel, unit) {
  const ctx = document.getElementById(canvasId);
  const labels = Object.keys(valuesByLabel);
  const values = labels.map((l) => valuesByLabel[l].average);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: `avg ${unit}`, data: values, backgroundColor: CHART_COLORS }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  });
}

// ---- EXECUTIVE (formerly "Overview") -----------------------------------------

async function renderOverview() {
  const filters = currentFilters();
  const [overview, executive, questionnaire] = await Promise.all([
    getJSON("/api/overview", filters),
    getJSON("/api/executive-summary", filters),
    getJSON("/api/questionnaire-stats", filters),
  ]);

  renderBarChart("chart-by-city", overview.surveys_by_city);
  renderBarChart("chart-by-platform", overview.surveys_by_platform);
  renderBarChart("chart-by-language", overview.surveys_by_language);

  document.getElementById("kpi-respondents").textContent = fmtNum(executive.total_respondents);
  document.getElementById("kpi-multi-platform").textContent = `${fmtNum(executive.multi_platform_pct, 1)}%`;
  document.getElementById("kpi-top-platform").textContent = executive.most_used_platform || "—";
  document.getElementById("kpi-switch").textContent = `${fmtNum(executive.switch_willingness_pct, 1)}%`;
  document.getElementById("kpi-screenshots").textContent = `${fmtNum(executive.screenshot_share_pct, 1)}%`;
  document.getElementById("kpi-top-request").textContent = executive.top_improvement_request || "—";

  renderBarChart("chart-platform-count", questionnaire.platform_count_distribution);
  renderBarChart("chart-employment-relationship", questionnaire.employment_relationship);
  renderBarChart("chart-loyalty-program", questionnaire.loyalty_program);
  renderBarChart("chart-driver-motivation", questionnaire.driver_motivation, { horizontal: true });
}

// ---- DRIVER ECONOMICS -------------------------------------------------------------

const ECONOMICS_LABELS = {
  weekly_earnings: "Avg weekly earnings",
  trips_per_day: "Avg trips / day",
  hours_per_day: "Avg hours / day",
  earnings_per_hour: "Earnings / working hour",
  earnings_per_trip: "Earnings / trip",
  commission_pct: "Avg commission %",
};

async function renderEconomics() {
  const data = await getJSON("/api/driver-economics", currentFilters());
  const container = document.getElementById("economics-cards");
  container.innerHTML = "";
  for (const [key, label] of Object.entries(ECONOMICS_LABELS)) {
    const m = data[key];
    const card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = `
      <span class="stat-label">${label}</span>
      <span class="stat-value">${m.average !== null ? fmtNum(m.average, 1) : "—"}</span>
      <span class="stat-sub">median ${fmtNum(m.median, 1)} · min ${fmtNum(m.minimum, 1)} · max ${fmtNum(m.maximum, 1)} · n=${m.count}</span>
    `;
    container.appendChild(card);
  }

  renderBarChart("chart-hours-bucket", data.hours_per_day_bucket, { horizontal: true });
  renderGroupedBarChart("chart-seasonal-pattern", {
    Structured: data.seasonal_pattern,
    "From free text (legacy)": data.seasonality_text_classification,
  });
  renderGroupedBarChart("chart-driver-type", {
    Structured: data.driver_type_distribution,
    "From free text": data.driver_type_employment_text,
  });
  renderBarChart("chart-market-leader", data.perceived_market_leader, { horizontal: true });
}

// ---- PLATFORM -----------------------------------------------------------------

async function renderPlatform() {
  const data = await getJSON("/api/platform-stats", currentFilters());
  renderBarChart("chart-platform-usage", data.platform_usage);
  renderBarChart("chart-multi-app", data.multi_app);
  renderBarChart("chart-exclusivity", data.exclusivity);
  renderBarChart("chart-bonuses", data.receives_bonuses);
  renderValueBarChart("chart-commission-platform", data.commission_by_platform, "%");
  renderValueBarChart(
    "chart-cash-share",
    { Cash: data.cash_pct, Digital: data.digital_pct },
    "%"
  );
}

// ---- SCREENSHOTS --------------------------------------------------------------

function screenshotCard(item) {
  const thumb = item.cached_locally
    ? `<img class="thumb" src="/api/screenshots/${item.attachment_id}/image" alt="Screenshot ${item.attachment_id}" loading="lazy" />`
    : `<div class="thumb-placeholder">Not cached locally<br />(metadata only)</div>`;
  const desc = item.description ? escapeHtml(item.description) : "—";
  return `
    <div class="screenshot-card">
      ${thumb}
      <div class="meta">
        <strong>#${item.attachment_id}</strong>
        <span>${item.platform || "Unspecified platform"} · ${item.city}</span>
        <span class="muted">${fmtDate(item.uploaded_at)}</span>
        <span>${desc}</span>
        <a href="#" data-survey-id="${item.survey_id}" class="ss-survey-link">View survey ${item.survey_id} →</a>
      </div>
    </div>
  `;
}

async function renderScreenshots() {
  const params = currentFilters();
  params.set("page", state.screenshotPage);
  params.set("page_size", state.screenshotPageSize);
  const data = await getJSON("/api/screenshots", params);

  document.getElementById("ss-submitted").textContent = fmtNum(data.summary.submitted);
  document.getElementById("ss-total").textContent = fmtNum(data.summary.total_respondents);
  document.getElementById("ss-pct").textContent = `${fmtNum(data.summary.pct, 1)}%`;

  renderBarChart("chart-screenshots-platform", data.by_platform);
  renderBarChart("chart-screenshots-city", data.by_city);
  renderBarChart("chart-screenshots-date", data.by_date);

  const gallery = document.getElementById("screenshot-gallery");
  if (data.items.length === 0) {
    gallery.innerHTML = `<p class="muted">No screenshots in the current filter.</p>`;
  } else {
    gallery.innerHTML = data.items.map(screenshotCard).join("");
    gallery.querySelectorAll(".ss-survey-link").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        document.querySelector('.tab-link[data-tab="raw"]').click();
        renderSurveyDetail(link.dataset.surveyId);
      });
    });
  }

  const totalPages = Math.max(1, Math.ceil(data.total / state.screenshotPageSize));
  document.getElementById("ss-page-info").textContent = `Page ${state.screenshotPage + 1} of ${totalPages} (${data.total} screenshots)`;
  document.getElementById("ss-prev-page").disabled = state.screenshotPage === 0;
  document.getElementById("ss-next-page").disabled = state.screenshotPage + 1 >= totalPages;
}

// ---- QUALITATIVE -----------------------------------------------------------------

function renderQualList(elementId, items, { showPlatform = false } = {}) {
  const ul = document.getElementById(elementId);
  ul.innerHTML = "";
  if (items.length === 0) {
    ul.innerHTML = `<li class="muted">No responses in the current filter.</li>`;
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    const platformPart = showPlatform && item.platform ? ` · ${item.platform}` : "";
    li.innerHTML = `${escapeHtml(item.text)}<span class="meta">${item.survey_id} · ${item.city} · ${fmtDate(item.survey_date)}${platformPart}</span>`;
    ul.appendChild(li);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function renderQualitative() {
  const data = await getJSON("/api/qualitative", currentFilters());
  renderQualList("qual-market-leader", data.market_leader_responses);
  renderQualList("qual-reasons", data.reasons_to_join_new_platform);
  renderQualList("qual-improvements", data.improvement_suggestions);
  renderQualList("qual-requirements", data.category_requirements, { showPlatform: true });
  renderQualList("qual-employment", data.employment_responses);
  renderQualList("qual-bonus-descriptions", data.bonus_descriptions, { showPlatform: true });
  renderQualList("qual-payout-notes", data.payout_notes, { showPlatform: true });
}

// ---- RAW DATA -----------------------------------------------------------------

async function renderSurveyList() {
  const params = currentFilters();
  params.set("limit", state.surveyPageSize);
  params.set("offset", state.surveyPage * state.surveyPageSize);
  const data = await getJSON("/api/surveys", params);

  const tbody = document.querySelector("#survey-table tbody");
  tbody.innerHTML = "";
  for (const item of data.items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${item.survey_id}</td><td>${item.city}</td><td>${fmtDate(item.survey_date)}</td>`;
    tr.addEventListener("click", () => renderSurveyDetail(item.survey_id));
    tbody.appendChild(tr);
  }

  const totalPages = Math.max(1, Math.ceil(data.total / state.surveyPageSize));
  document.getElementById("page-info").textContent = `Page ${state.surveyPage + 1} of ${totalPages} (${data.total} surveys)`;
  document.getElementById("prev-page").disabled = state.surveyPage === 0;
  document.getElementById("next-page").disabled = state.surveyPage + 1 >= totalPages;
}

function kvTable(obj, keys) {
  if (!obj) return "<p class='muted'>No data.</p>";
  const rows = (keys || Object.keys(obj))
    .map((k) => `<div>${k}</div><div>${obj[k] === null || obj[k] === undefined || obj[k] === "" ? "—" : escapeHtml(String(obj[k]))}</div>`)
    .join("");
  return `<div class="kv">${rows}</div>`;
}

function rowsTable(rows) {
  if (!rows || rows.length === 0) return "<p class='muted'>None.</p>";
  const headers = Object.keys(rows[0]);
  const head = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>`;
  const body = rows
    .map((r) => `<tr>${headers.map((h) => `<td>${r[h] === null || r[h] === undefined || r[h] === "" ? "—" : escapeHtml(String(r[h]))}</td>`).join("")}</tr>`)
    .join("");
  return `<table>${head}${body}</table>`;
}

function screenshotsTable(rows) {
  if (!rows || rows.length === 0) return "<p class='muted'>None.</p>";
  const headers = Object.keys(rows[0]).filter((h) => h !== "attachment_id" && h !== "cached_locally");
  const head = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}<th>image</th></tr>`;
  const body = rows
    .map((r) => {
      const cells = headers
        .map((h) => `<td>${r[h] === null || r[h] === undefined || r[h] === "" ? "—" : escapeHtml(String(r[h]))}</td>`)
        .join("");
      const link = r.cached_locally === "yes"
        ? `<a href="/api/screenshots/${r.attachment_id}/image" target="_blank" rel="noopener">View image</a>`
        : `<span class="muted">not cached</span>`;
      return `<tr>${cells}<td>${link}</td></tr>`;
    })
    .join("");
  return `<table>${head}${body}</table>`;
}

async function renderSurveyDetail(surveyId) {
  const panel = document.getElementById("survey-detail");
  panel.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const detail = await getJSON(`/api/surveys/${encodeURIComponent(surveyId)}`);
    panel.innerHTML = `
      <h3>${detail.summary.survey_id}</h3>
      ${kvTable(detail.summary)}
      <h4>Driver Platforms</h4>${rowsTable(detail.platforms)}
      <h4>Working Statistics</h4>${kvTable(detail.working_statistics)}
      <h4>Earnings</h4>${rowsTable(detail.earnings)}
      <h4>Bonuses</h4>${rowsTable(detail.bonuses)}
      <h4>Payments</h4>${rowsTable(detail.payments)}
      <h4>Categories</h4>${rowsTable(detail.categories)}
      <h4>Market Intelligence</h4>${rowsTable(detail.market_intelligence)}
      <h4>Driver Experience</h4>${kvTable(detail.driver_experience)}
      <h4>Screenshots</h4>${screenshotsTable(detail.screenshots)}
      <h4>Raw Answers (verbatim)</h4>${rowsTable(detail.raw_answers)}
    `;
  } catch (err) {
    panel.innerHTML = `<p class="muted">Could not load survey: ${escapeHtml(err.message)}</p>`;
  }
}

// ---- EXPORT -----------------------------------------------------------------------

function initExport() {
  document.getElementById("export-excel").addEventListener("click", () => {
    const params = currentFilters();
    if (document.getElementById("export-include-incomplete").checked) params.set("include_incomplete", "true");
    window.location.href = `/api/export/excel?${params.toString()}`;
  });
  document.getElementById("export-csv").addEventListener("click", () => {
    const sheet = document.getElementById("csv-sheet").value;
    const params = currentFilters();
    if (document.getElementById("export-include-incomplete").checked) params.set("include_incomplete", "true");
    window.location.href = `/api/export/csv/${sheet}?${params.toString()}`;
  });
}

// ---- wiring -----------------------------------------------------------------------

async function refreshAll() {
  await Promise.all([renderOverview(), renderEconomics(), renderPlatform(), renderQualitative(), renderScreenshots()]);
  state.surveyPage = 0;
  state.screenshotPage = 0;
  await renderSurveyList();
}

function initFilters() {
  document.getElementById("filters").addEventListener("submit", (e) => {
    e.preventDefault();
    refreshAll();
  });
  document.getElementById("clear-filters").addEventListener("click", () => {
    document.getElementById("filter-city").value = "";
    document.getElementById("filter-platform").value = "";
    document.getElementById("filter-driver-type").value = "";
    document.getElementById("filter-language").value = "";
    document.getElementById("filter-date-from").value = "";
    document.getElementById("filter-date-to").value = "";
    refreshAll();
  });
  document.getElementById("prev-page").addEventListener("click", () => {
    if (state.surveyPage > 0) {
      state.surveyPage -= 1;
      renderSurveyList();
    }
  });
  document.getElementById("next-page").addEventListener("click", () => {
    state.surveyPage += 1;
    renderSurveyList();
  });
  document.getElementById("ss-prev-page").addEventListener("click", () => {
    if (state.screenshotPage > 0) {
      state.screenshotPage -= 1;
      renderScreenshots();
    }
  });
  document.getElementById("ss-next-page").addEventListener("click", () => {
    state.screenshotPage += 1;
    renderScreenshots();
  });
}

async function init() {
  initTabs();
  initFilters();
  initExport();
  await loadReferenceData();
  await refreshAll();
}

init().catch((err) => {
  console.error(err);
  document.querySelector("main").insertAdjacentHTML(
    "afterbegin",
    `<p style="color:red">Failed to load dashboard: ${escapeHtml(err.message)}</p>`
  );
});
