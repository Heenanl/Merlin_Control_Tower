import { api, escapeHtml, renderTable } from "/static/js/api.js";

export async function renderSimulation(mount, state, { notify }) {
  const summary = await api("/api/simulation/summary");
  mount.innerHTML = `
    <section class="stack">
      <article class="panel">
        <h3>Run simulation</h3>
        <p class="small">This replaces the current <code>next_best_experience</code> and <code>contact_decision_output</code> tables in one atomic transaction using the latest promoted config.</p>
        <div class="actions">
          <button id="run-simulation" class="button primary">Run simulation now</button>
        </div>
      </article>
      <article class="panel">
        <h3>Latest run</h3>
        <p class="small">Run timestamp: ${escapeHtml(summary.latest?.run_timestamp || "Not available")} · Snapshot: ${escapeHtml(summary.latest?.snapshotdate || "Not available")}</p>
        <h4>By proposition</h4>
        ${renderTable(summary.by_proposition)}
        <h4>By channel</h4>
        ${renderTable(summary.by_channel)}
      </article>
    </section>
  `;

  mount.querySelector("#run-simulation").addEventListener("click", async () => {
    try {
      const result = await api("/api/simulation/run", { method: "POST", body: JSON.stringify({}) });
      notify(`Simulation completed for snapshot ${result.latest?.snapshotdate || "today"}`);
    } catch (error) {
      notify(error.message, "error");
    }
  });
}
