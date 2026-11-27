import { escapeHtml } from "/static/js/api.js";

// Tile icons in the abnamro.nl style: solid green shapes with a yellow accent
const ICONS = {
  rules: `<rect x="8" y="5" width="32" height="38" rx="4" fill="#00857A"/><path d="M15 16h18M15 24h18M15 32h11" stroke="#fff" stroke-width="3" stroke-linecap="round"/><circle cx="36" cy="36" r="7" fill="#FFD200"/><path d="M33 36l2 2 4-4" stroke="#004C4C" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`,
  content: `<rect x="4" y="10" width="40" height="28" rx="3" fill="#00857A"/><path d="M7 14l17 12 17-12" stroke="#FFD200" stroke-width="3" fill="none" stroke-linejoin="round"/>`,
  stale: `<path d="M24 4L46 42H2Z" fill="#00857A"/><path d="M24 12L39 38H9Z" fill="#FFD200"/><path d="M24 20v9" stroke="#004C4C" stroke-width="3.5" stroke-linecap="round"/><circle cx="24" cy="34" r="2.2" fill="#004C4C"/>`,
  requests: `<path d="M11 4h18l9 9v31H11z" fill="#00857A"/><path d="M29 4v9h9" fill="#004C4C"/><path d="M17 23h15M17 30h15M17 37h9" stroke="#fff" stroke-width="3" stroke-linecap="round"/>`,
  simulation: `<circle cx="24" cy="24" r="20" fill="#00857A"/><path d="M19 14l15 10-15 10z" fill="#FFD200"/>`,
  latency: `<path d="M6 34a18 18 0 0136 0" stroke="#00857A" stroke-width="6" fill="none" stroke-linecap="round"/><path d="M24 34l9-11" stroke="#004C4C" stroke-width="3.5" stroke-linecap="round"/><circle cx="24" cy="34" r="4" fill="#FFD200"/>`,
};

function formatRun(value) {
  if (!value) return "Not run yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export async function renderOverview(mount, state, { api }) {
  const data = await api("/api/overview");
  const tiles = [
    ["rules", "Live rules", data.live_rules, "Eligibility rules in use", "rules"],
    ["content", "Active content", data.active_content, "Tips and offers on email, push, in-app and SMS", "matrix"],
    ["stale", "Stale content", `${data.pct_content_older_than_300_days ?? 0}%`, "Older than 300 days: review and refresh", "matrix"],
    ["requests", "Open change requests", data.open_change_requests, "Waiting for review or promotion", "changeRequests"],
    ["simulation", "Last simulation", "", `${formatRun(data.last_simulation_run)}${data.last_snapshotdate ? ` · snapshot ${data.last_snapshotdate}` : ""}`, "simulation"],
    ["latency", "UC vs Lakebase", "", "Compare database latency side by side", "latency"],
  ];
  mount.innerHTML = `
    <section class="tiles">
      ${tiles
        .map(
          ([icon, title, value, sub, page]) => `
            <button type="button" class="tile" data-goto="${page}">
              <svg class="tile-icon" viewBox="0 0 48 48" aria-hidden="true">${ICONS[icon]}</svg>
              <span class="tile-text">
                <span class="tile-title">${escapeHtml(title)}</span>
                <span class="tile-sub">${escapeHtml(sub)}</span>
              </span>
              <span class="tile-value">${escapeHtml(value)}</span>
              <span class="tile-chevron" aria-hidden="true">›</span>
            </button>`
        )
        .join("")}
    </section>
    <section class="panel">
      <h3>What this app enforces</h3>
      <ul>
        <li>Business users draft every change into <code>change_request</code>; live config is never edited directly.</li>
        <li>Approvers cannot approve their own requests.</li>
        <li>Promotion is all or nothing across both stores: one Lakebase transaction and one Unity Catalog <code>BEGIN ATOMIC</code>, committed together.</li>
        <li>Impact preview estimates eligibility and cap pressure before any promote.</li>
      </ul>
    </section>
  `;
}
