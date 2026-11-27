import { api, escapeHtml, getBackend, setBackend, BACKEND_LABELS } from "/static/js/api.js";
import { renderLatency } from "/static/js/pages/latency.js";
import { renderOverview } from "/static/js/pages/overview.js";
import { renderMatrix } from "/static/js/pages/matrix.js";
import { renderRules } from "/static/js/pages/rules.js";
import { renderChangeRequests } from "/static/js/pages/change_requests.js";
import { renderSimulation } from "/static/js/pages/simulation.js";

const pages = {
  overview: renderOverview,
  matrix: renderMatrix,
  rules: renderRules,
  changeRequests: renderChangeRequests,
  simulation: renderSimulation,
  latency: renderLatency,
};

const state = {
  me: null,
  alerts: [],
  activePage: "overview",
};

// Hero band title per page (the overview shows the full abnamro.nl-style hero with greeting card)
const PAGE_TITLES = {
  overview: "Governed changes for Merlin",
  matrix: "Rule × Content",
  rules: "Rules & Propositions",
  changeRequests: "Change requests",
  simulation: "Run simulation",
  latency: "Unity Catalog vs Lakebase",
};

const USER_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

function firstName(username) {
  const name = String(username || "").split("@")[0].split(/[._-]/)[0];
  return name ? name[0].toUpperCase() + name.slice(1) : "";
}

function paintGreeting() {
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const name = firstName(state.me?.preferred_username);
  document.getElementById("greeting").textContent = name ? `${part}, ${name}` : `${part},`;
}

export function goTo(page) {
  document.querySelector(`.tab[data-page="${page}"]`)?.click();
}

function wireNavigation() {
  // any element with data-goto="<page>" (brand, hero button, overview tiles) switches tab
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-goto]");
    if (target) goTo(target.dataset.goto);
  });
  document.getElementById("hero-search").addEventListener("submit", (event) => {
    event.preventDefault();
    state.matrixQuery = document.getElementById("hero-search-input").value.trim();
    goTo("matrix");
  });
  document.getElementById("menu-search").addEventListener("click", () => {
    if (state.activePage === "overview") document.getElementById("hero-search-input").focus();
    else goTo("matrix");
  });
}

function paintAlerts() {
  const alerts = document.getElementById("alerts");
  const banner = state.me?.self_approval_enabled
    ? `<div class="alert warn-banner">Demo mode: self-approval is enabled. Turn it off before real use.</div>`
    : "";
  alerts.innerHTML = banner + state.alerts.map((a) => `<div class="alert ${a.level}">${a.message}</div>`).join("");
}

export function notify(message, level = "success") {
  state.alerts = [{ message, level }];
  paintAlerts();
}

async function loadUser() {
  state.me = await api("/api/me");
  const userPill = document.getElementById("user-pill");
  userPill.innerHTML = `${USER_ICON}<span>${escapeHtml(state.me.preferred_username)}</span>`;
  userPill.title = `Roles: ${state.me.roles.join(", ")}`;
  paintGreeting();
  if (!getBackend() || (getBackend() === "lakebase" && !state.me.lakebase_available)) {
    setBackend(state.me.lakebase_available ? state.me.default_backend || "lakebase" : "uc");
  }
  paintBackendToggle();
  paintAlerts();
}

// --- UC vs Lakebase toggle + latency pill ---------------------------------------------------------
function paintBackendToggle() {
  const current = getBackend();
  document.querySelectorAll(".backend-toggle button").forEach((button) => {
    const active = button.dataset.backend === current;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-checked", String(active));
    if (button.dataset.backend === "lakebase" && !state.me?.lakebase_available) {
      button.disabled = true;
      button.title = "Lakebase is not configured for this app";
    }
  });
}

function wireBackendToggle() {
  document.querySelectorAll(".backend-toggle button").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.dataset.backend === getBackend()) return;
      setBackend(button.dataset.backend);
      paintBackendToggle();
      notify(`Reads now come from ${BACKEND_LABELS[button.dataset.backend]}. Writes always go to both stores.`, "info");
      await renderPage();
    });
  });
}

function formatMs(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function wireLatencyPill() {
  const pill = document.getElementById("latency-pill");
  window.addEventListener("merlin:latency", (event) => {
    const entry = event.detail;
    if (!entry.statements.length) return; // requests that did not touch a database (e.g. cached roles)
    const parts = [];
    if (entry.lakebaseMs) parts.push(`Lakebase ${formatMs(entry.lakebaseMs)}`);
    if (entry.ucMs) parts.push(`UC ${formatMs(entry.ucMs)}`);
    pill.textContent = `DB time · ${parts.join(" + ")}`;
    pill.className = `latency-pill ${entry.ucMs > entry.lakebaseMs ? "is-uc" : "is-lakebase"}`;
    pill.title = `${entry.method} ${entry.path}: ${entry.statements.length} statement(s), server ${formatMs(entry.serverMs)}, round trip ${formatMs(entry.roundTripMs)}. Click for the comparison.`;
  });
  pill.addEventListener("click", () => document.querySelector('.tab[data-page="latency"]').click());
}

async function renderPage() {
  const mount = document.getElementById("page-content");
  document.body.dataset.page = state.activePage;
  document.getElementById("hero-title").textContent = PAGE_TITLES[state.activePage] || "";
  mount.innerHTML = document.getElementById("loading-template").innerHTML;
  try {
    await pages[state.activePage](mount, state, { api, notify });
  } catch (error) {
    notify(error.message, "error");
    mount.innerHTML = "";
  }
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("is-active"));
      button.classList.add("is-active");
      state.activePage = button.dataset.page;
      await renderPage();
    });
  });
}

wireLatencyPill();
wireNavigation();
await loadUser();
wireTabs();
wireBackendToggle();
await renderPage();
