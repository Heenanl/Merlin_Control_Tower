import { escapeHtml } from "/static/js/api.js";

export async function renderOverview(mount, state, { api }) {
  const data = await api("/api/overview");
  const cards = [
    ["Live rules", data.live_rules],
    ["Active content", data.active_content],
    ["Content older than 300 days", `${data.pct_content_older_than_300_days ?? 0}%`],
    ["Open change requests", data.open_change_requests],
    ["Last simulation run", data.last_simulation_run ?? "Not run yet"],
    ["Snapshot date", data.last_snapshotdate ?? "Not available"],
  ];
  mount.innerHTML = `
    <section class="grid kpi">
      ${cards
        .map(
          ([label, value]) => `
            <article class="card kpi">
              <h3>${escapeHtml(label)}</h3>
              <div class="value">${escapeHtml(value)}</div>
            </article>`
        )
        .join("")}
    </section>
    <section class="panel">
      <h3>What this app enforces</h3>
      <ul>
        <li>Business users draft every change into <code>change_request</code>; live config is never edited directly.</li>
        <li>Approvers cannot approve their own requests.</li>
        <li>Promotion runs in one <code>BEGIN ATOMIC</code> transaction across the Merlin config tables and audit log.</li>
        <li>Impact preview estimates eligibility and cap pressure before any promote.</li>
      </ul>
    </section>
  `;
}
