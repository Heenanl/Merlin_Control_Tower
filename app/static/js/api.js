export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
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
