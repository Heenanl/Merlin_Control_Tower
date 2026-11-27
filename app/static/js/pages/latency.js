import { escapeHtml, getBackend, latencyLog, BACKEND_LABELS } from "/static/js/api.js";

function formatMs(ms) {
  if (!ms) return "–";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
}

function bar(ms, max, cls) {
  // log scale so a 10 ms bar is still visible next to a 2 s bar
  const width = ms > 0 ? Math.max(2, (100 * Math.log10(1 + ms)) / Math.log10(1 + max)) : 0;
  return `<div class="lat-bar ${cls}" style="width:${width.toFixed(1)}%"></div>`;
}

function benchmarkTable(data) {
  const max = Math.max(...data.results.flatMap((r) => [r.uc_median_ms, r.lakebase_median_ms]));
  const rows = data.results
    .map(
      (r) => `
      <tr>
        <td><strong>${escapeHtml(r.query)}</strong><div class="muted small">${r.rows_lakebase} rows ${
          r.same_row_count ? "in both stores" : `<span class="bad">vs ${r.rows_uc} in UC</span>`
        }</div></td>
        <td class="num">${formatMs(r.uc_median_ms)}</td>
        <td class="num">${formatMs(r.lakebase_median_ms)}</td>
        <td class="num"><span class="speedup">${r.speedup}×</span></td>
        <td class="bars">${bar(r.uc_median_ms, max, "uc")}${bar(r.lakebase_median_ms, max, "lakebase")}</td>
      </tr>`
    )
    .join("");
  const ucTotal = data.results.reduce((t, r) => t + r.uc_median_ms, 0);
  const lbTotal = data.results.reduce((t, r) => t + r.lakebase_median_ms, 0);
  return `
    <div class="lat-summary">
      <div><span class="dot uc"></span>Unity Catalog <strong>${formatMs(ucTotal)}</strong></div>
      <div><span class="dot lakebase"></span>Lakebase <strong>${formatMs(lbTotal)}</strong></div>
      <div>All ${data.results.length} queries are <strong>${(ucTotal / Math.max(lbTotal, 0.1)).toFixed(0)}×</strong> faster on Lakebase (median of ${data.runs} runs each)</div>
    </div>
    <div class="table-wrap"><table class="lat-table">
      <thead><tr><th>Query (same SQL on both)</th><th class="num">Unity Catalog</th><th class="num">Lakebase</th><th class="num">Faster</th><th>Median time (log scale)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function sessionTable() {
  const entries = latencyLog.filter((e) => e.statements.length && !e.path.startsWith("/api/latency")).slice(0, 15);
  if (!entries.length) return `<p class="muted">Open other pages, then come back to see their database time here.</p>`;
  return `<div class="table-wrap"><table class="lat-table">
    <thead><tr><th>Time</th><th>Request</th><th>Read source</th><th class="num">Lakebase</th><th class="num">Unity Catalog</th><th class="num">Server total</th></tr></thead>
    <tbody>${entries
      .map(
        (e) => `<tr>
          <td>${e.at.toLocaleTimeString()}</td>
          <td><code>${escapeHtml(e.method)} ${escapeHtml(e.path)}</code></td>
          <td>${e.backend === "lakebase" ? '<span class="tag lakebase">Lakebase</span>' : '<span class="tag uc">Unity Catalog</span>'}</td>
          <td class="num">${formatMs(e.lakebaseMs)}</td>
          <td class="num">${formatMs(e.ucMs)}</td>
          <td class="num">${formatMs(e.serverMs)}</td>
        </tr>`
      )
      .join("")}</tbody></table></div>`;
}

export async function renderLatency(mount, state, { api, notify }) {
  const available = state.me?.lakebase_available;
  mount.innerHTML = `
    <section class="grid two">
      <article class="card info-card">
        <h3>Two stores, one configuration</h3>
        <p><strong>Lakebase</strong> (Postgres) is the main store for config, change requests and the audit log.
        <strong>Unity Catalog</strong> keeps an identical copy, because the simulation joins the config with 50k customers and 600k scores on the SQL warehouse.</p>
        <ul>
          <li><strong>Reads</strong> come from the store picked with the <em>Data source</em> toggle (now: ${escapeHtml(BACKEND_LABELS[getBackend()] || "–")}).</li>
          <li><strong>Writes</strong> always go to both: one Lakebase transaction plus one UC <code>BEGIN ATOMIC</code>, committed together.</li>
          <li>The pill at the top shows the database time of the last request.</li>
        </ul>
      </article>
      <article class="card info-card">
        <h3>Benchmark</h3>
        <p>Runs the app's own read queries 3 times on each store, alternating, and compares the median server-side time (app to database and back).</p>
        <div class="actions"><button class="button primary" id="run-benchmark" ${available ? "" : "disabled"}>Run benchmark</button></div>
        ${available ? "" : '<p class="muted">Lakebase is not configured for this app.</p>'}
      </article>
    </section>
    <section class="panel" id="benchmark-result"><h3>Result</h3><p class="muted">Not run yet.</p></section>
    <section class="panel"><h3>This session</h3><div id="session-latency">${sessionTable()}</div></section>
  `;
  const button = mount.querySelector("#run-benchmark");
  button?.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Running…";
    const target = mount.querySelector("#benchmark-result");
    target.innerHTML = `<h3>Result</h3><p class="muted">Running 36 queries; the SQL warehouse can take a few seconds to wake up…</p>`;
    try {
      const data = await api("/api/latency/benchmark?runs=3");
      target.innerHTML = `<h3>Result</h3>${benchmarkTable(data)}`;
    } catch (error) {
      notify(error.message, "error");
      target.innerHTML = `<h3>Result</h3><p class="muted">Benchmark failed.</p>`;
    } finally {
      button.disabled = false;
      button.textContent = "Run again";
      mount.querySelector("#session-latency").innerHTML = sessionTable();
    }
  });
}
