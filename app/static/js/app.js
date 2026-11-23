import { api } from "/static/js/api.js";
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
};

const state = {
  me: null,
  alerts: [],
  activePage: "overview",
};

function paintAlerts() {
  const alerts = document.getElementById("alerts");
  alerts.innerHTML = state.alerts.map((a) => `<div class="alert ${a.level}">${a.message}</div>`).join("");
}

export function notify(message, level = "success") {
  state.alerts = [{ message, level }];
  paintAlerts();
}

async function loadUser() {
  state.me = await api("/api/me");
  document.getElementById("user-pill").textContent = `${state.me.preferred_username} · ${state.me.roles.join(", ")}`;
  if (state.me.self_approval_enabled) {
    notify("Demo mode: self-approval is enabled. Turn it off before real use.", "error");
  }
}

async function renderPage() {
  const mount = document.getElementById("page-content");
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

await loadUser();
wireTabs();
await renderPage();
