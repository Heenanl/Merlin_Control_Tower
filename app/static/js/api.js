// --- UC vs Lakebase toggle -------------------------------------------------------------------
// The chosen read backend is kept per browser and sent with every API call (X-Merlin-Backend).
const BACKEND_KEY = "merlinBackend";
export const BACKEND_LABELS = { uc: "Unity Catalog (SQL warehouse)", lakebase: "Lakebase (Postgres)" };

export function getBackend() {
  return localStorage.getItem(BACKEND_KEY) || "";
}

export function setBackend(backend) {
  localStorage.setItem(BACKEND_KEY, backend);
}

// Recent API calls with their DB timings (newest first), for the latency pill and the Latency page.
export const latencyLog = [];

function parseDbHeader(value) {
  // "lakebase:read:8.9,uc:write:4620.1" -> [{backend, kind, ms}]
  return (value || "")
    .split(",")
    .filter(Boolean)
    .map((part) => {
      const [backend, kind, ms] = part.split(":");
      return { backend, kind, ms: Number(ms) };
    });
}

function recordLatency(path, method, response, roundTripMs, backend) {
  const statements = parseDbHeader(response.headers.get("X-Merlin-Db"));
  const sum = (b) => statements.filter((s) => s.backend === b).reduce((t, s) => t + s.ms, 0);
  const entry = {
    at: new Date(),
    method,
    path,
    backend,
    status: response.status,
    statements,
    ucMs: sum("uc"),
    lakebaseMs: sum("lakebase"),
    dbMs: statements.reduce((t, s) => t + s.ms, 0),
    serverMs: Number(response.headers.get("X-Merlin-Server-Ms") || 0),
    roundTripMs,
  };
  latencyLog.unshift(entry);
  latencyLog.length = Math.min(latencyLog.length, 50);
  window.dispatchEvent(new CustomEvent("merlin:latency", { detail: entry }));
}

export async function api(path, options = {}) {
  const backend = getBackend();
  const started = performance.now();
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(backend ? { "X-Merlin-Backend": backend } : {}),
      ...(options.headers || {}),
    },
  });
  recordLatency(path, options.method || "GET", response, performance.now() - started, backend);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    // Gateway errors (e.g. 504 timeout) return HTML, not JSON
    throw new Error(`Request failed: HTTP ${response.status} ${response.statusText}`.trim());
  }
  if (!response.ok) {
    throw new Error(data.detail || response.statusText || "Request failed");
  }
  return data;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function renderTable(rows) {
  if (!rows?.length) return "<div class='card'>No rows.</div>";
  const columns = Object.keys(rows[0]);
  const head = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((c) => `<td>${escapeHtml(typeof row[c] === "object" ? JSON.stringify(row[c]) : row[c])}</td>`)
          .join("")}</tr>`
    )
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function statusChip(status) {
  return `<span class="chip ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}
