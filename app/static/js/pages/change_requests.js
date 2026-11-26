import { api, escapeHtml, statusChip } from "/static/js/api.js";

// Only offer the lifecycle actions that are valid for the request's current status.
const ACTIONS_BY_STATUS = {
  draft: [["submit", "Submit", ""], ["approve", "Approve", "success"], ["reject", "Reject", "danger"]],
  submitted: [["approve", "Approve", "success"], ["reject", "Reject", "danger"]],
  approved: [["promote", "Promote to live", "warn"], ["reject", "Reject", "danger"]],
  promoted: [["revert", "Revert", "danger"]],
  rejected: [],
  reverted: [],
};
const CONFIRM = {
  promote: "Promote this request? It changes the live Merlin config.",
  revert: "Revert this request? The live config goes back to its state before the promote.",
  reject: "Reject this request?",
};
const FILTERS = [
  ["open", "Open", (s) => ["draft", "submitted", "approved"].includes(s)],
  ["promoted", "Live", (s) => s === "promoted"],
  ["closed", "Closed", (s) => ["rejected", "reverted"].includes(s)],
  ["all", "All", () => true],
];
const OP_LABEL = { insert: "Add", update: "Update", soft_delete: "Deactivate", hard_delete: "Delete" };
const TABLE_LABEL = {
  proposition: "Proposition",
  content: "Content",
  eligibility_rule: "Eligibility rule",
  eligibility_rule_set: "Rule set membership",
  content_eligibility_rule: "Content ↔ rule mapping",
  model_metadata: "Model setting",
};
const SYSTEM_COLUMNS = new Set(["created_by", "created_at", "updated_by", "updated_at", "version", "audit_timestamp", "source_system"]);

const fmtTime = (v) => (v ? new Date(v).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
const fmtVal = (v) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const keyText = (key) => Object.values(key || {}).map(fmtVal).join(" · ");
const shortId = (id) => String(id || "").replace(/^cr_/, "").slice(0, 8);

function stepper(req) {
  const steps = [
    ["Drafted", req.created_at, true],
    ["Submitted", req.submitted_at, Boolean(req.submitted_at)],
    [req.status === "rejected" ? "Rejected" : "Approved", req.decided_at, Boolean(req.decided_at)],
  ];
  if (req.status !== "rejected") steps.push(["Live", req.promoted_at, Boolean(req.promoted_at)]);
  if (req.status === "reverted") steps.push(["Reverted", req.reverted_at, true]);
  return `<ol class="stepper">${steps
    .map(([label, at, done]) => {
      const bad = label === "Rejected" || label === "Reverted";
      return `<li class="${done ? "done" : ""} ${bad ? "bad" : ""}"><span class="dot"></span><div><strong>${label}</strong><div class="small">${escapeHtml(fmtTime(at))}</div></div></li>`;
    })
    .join("")}</ol>`;
}

function operationCard(op) {
  const before = op.before || {};
  const after = op.after || {};
  const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((f) => !SYSTEM_COLUMNS.has(f));
  const changed = op.op === "update" || op.op === "soft_delete"
    ? fields.filter((f) => JSON.stringify(before[f]) !== JSON.stringify(after[f]))
    : fields;
  const rows = changed
    .map((f) => {
      const b = op.op === "insert" ? "" : fmtVal(before[f]);
      const a = op.op === "hard_delete" ? "" : fmtVal(after[f]);
      return `<tr><td class="field">${escapeHtml(f)}</td><td class="old">${escapeHtml(b)}</td><td class="arrow">→</td><td class="new">${escapeHtml(a)}</td></tr>`;
    })
    .join("");
  return `
    <article class="op-card">
      <div class="op-head">
        <span class="op-badge ${escapeHtml(op.op)}">${escapeHtml(OP_LABEL[op.op] || op.op)}</span>
        <strong>${escapeHtml(TABLE_LABEL[op.table] || op.table)}</strong>
        <span class="tag">${escapeHtml(keyText(op.key))}</span>
      </div>
      ${rows ? `<table class="diff"><thead><tr><th>Field</th><th>Before</th><th></th><th>After</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="small">No field changes.</div>`}
    </article>`;
}

function impactSection(preview) {
  const rows = (preview.rows || []).filter((r) => r.delta || r.eligible_before || r.eligible_after);
  const changed = rows.filter((r) => r.delta);
  const capChanged = preview.scaled_weekly_cap_before !== preview.scaled_weekly_cap_after;
  const holdChanged = preview.holdout_share_before !== preview.holdout_share_after;
  const kpi = (label, b, a, isChanged) => `
    <div class="impact-kpi ${isChanged ? "changed" : ""}"><div class="small">${label}</div><div><strong>${escapeHtml(fmtVal(b))}</strong>${isChanged ? ` → <strong>${escapeHtml(fmtVal(a))}</strong>` : ""}</div></div>`;
  const shown = changed.length ? changed : rows;
  return `
    <div class="impact-kpis">
      ${kpi("Propositions affected", changed.length, null, false)}
      ${kpi("Weekly cap (scaled)", preview.scaled_weekly_cap_before, preview.scaled_weekly_cap_after, capChanged)}
      ${kpi("Holdout share", preview.holdout_share_before, preview.holdout_share_after, holdChanged)}
    </div>
    ${!changed.length ? `<p class="small">This change does not change eligibility for any proposition. Current eligibility:</p>` : ""}
    <table class="impact"><thead><tr><th>Proposition</th><th class="num">Eligible before</th><th class="num">Eligible after</th><th class="num">Change</th><th class="num">Est. contacts</th></tr></thead>
      <tbody>${shown
        .map((r) => {
          const d = Number(r.delta || 0);
          return `<tr><td>${escapeHtml(r.proposition_id)}</td><td class="num">${Number(r.eligible_before).toLocaleString()}</td><td class="num">${Number(r.eligible_after).toLocaleString()}</td>
            <td class="num delta ${d > 0 ? "pos" : d < 0 ? "neg" : ""}">${d > 0 ? "+" : ""}${d.toLocaleString()}</td>
            <td class="num">${Number(r.estimated_contacts_before).toLocaleString()} → ${Number(r.estimated_contacts_after).toLocaleString()}</td></tr>`;
        })
        .join("")}</tbody></table>`;
}

function historySection(rows) {
  if (!rows?.length) return `<div class="small">No audit entries.</div>`;
  return `<ul class="timeline">${rows
    .map((h) => `<li><span class="dot"></span><div><strong>${escapeHtml(h.action)}</strong> by ${escapeHtml(h.actor)}${h.action === "promoted" || h.action === "reverted" ? ` <span class="tag">${escapeHtml(TABLE_LABEL[h.object_type] || h.object_type)}</span>` : ""}<div class="small">${escapeHtml(fmtTime(h.action_at))}</div></div></li>`)
    .join("")}</ul>`;
}

export async function renderChangeRequests(mount, state, { notify }) {
  const data = await api("/api/change-requests");
  const requests = data.rows;
  const ui = state.crUi || (state.crUi = { filter: "open", query: "", selected: null });
  const counts = Object.fromEntries(FILTERS.map(([k, , fn]) => [k, requests.filter((r) => fn(r.status)).length]));
  if (ui.filter === "open" && !counts.open && requests.length) ui.filter = "all";

  mount.innerHTML = `
    <section class="panel stack">
      <div>
        <h3>Change requests</h3>
        <p class="small">Every config change goes draft → submitted → approved → live, and can be reverted. Check the impact before promoting.</p>
      </div>
      <div class="mx-layout">
        <aside class="mx-sidebar">
          <div class="segmented" id="cr-filters">${FILTERS.map(([k, label]) => `<button data-filter="${k}" class="${ui.filter === k ? "is-active" : ""}">${label} (${counts[k]})</button>`).join("")}</div>
          <input id="cr-search" type="search" placeholder="Search title or person" value="${escapeHtml(ui.query)}" />
          <ul id="cr-list" class="mx-content-list"></ul>
        </aside>
        <div id="cr-detail" class="mx-detail"></div>
      </div>
    </section>
  `;
  const listEl = mount.querySelector("#cr-list");
  const detailEl = mount.querySelector("#cr-detail");
  let loadToken = 0;

  function visible() {
    const fn = FILTERS.find(([k]) => k === ui.filter)[2];
    const q = ui.query.toLowerCase();
    return requests.filter((r) => fn(r.status) && [r.title, r.drafted_by, r.approved_by, r.request_id].some((v) => String(v ?? "").toLowerCase().includes(q)));
  }

  function renderList() {
    const rows = visible();
    if (!rows.some((r) => r.request_id === ui.selected)) ui.selected = rows[0]?.request_id || null;
    listEl.innerHTML = rows.length
      ? rows
          .map((r) => `
            <li><button class="mx-content ${r.request_id === ui.selected ? "is-active" : ""}" data-request-id="${escapeHtml(r.request_id)}">
              <div class="mx-content-top"><strong class="cr-title">${escapeHtml(r.title)}</strong>${statusChip(r.status)}</div>
              <div class="small">${escapeHtml(r.drafted_by)} · ${escapeHtml(fmtTime(r.updated_at || r.created_at))}</div>
            </button></li>`)
          .join("")
      : `<li class="mx-empty">No requests in this view.</li>`;
  }

  async function renderDetail() {
    const token = ++loadToken;
    if (!ui.selected) {
      detailEl.innerHTML = `<div class="mx-empty">No request selected.</div>`;
      return;
    }
    detailEl.innerHTML = `<div class="mx-empty">Loading request…</div>`;
    const requestId = ui.selected;
    const [req, history] = await Promise.all([
      api(`/api/change-requests/${requestId}`),
      api(`/api/change-requests/${requestId}/history`),
    ]);
    if (token !== loadToken) return;
    const actions = (ACTIONS_BY_STATUS[req.status] || [])
      .map(([a, label, style]) => `<button class="button ${style}" data-action="${a}">${label}</button>`)
      .join("");
    detailEl.innerHTML = `
      <div class="cr-header">
        <div>
          <h4>${escapeHtml(req.title)}</h4>
          <div class="mx-tags">${statusChip(req.status)}<span class="tag" title="${escapeHtml(req.request_id)}">#${escapeHtml(shortId(req.request_id))}</span>
            <span class="small">Drafted by ${escapeHtml(req.drafted_by)}${req.approved_by ? ` · ${req.status === "rejected" ? "rejected" : "approved"} by ${escapeHtml(req.approved_by)}` : ""}</span></div>
        </div>
        <div class="actions">${actions}</div>
      </div>
      ${stepper(req)}
      ${req.comment ? `<blockquote class="cr-comment">${escapeHtml(req.comment)}</blockquote>` : ""}
      <section class="cr-section"><h5>What changes (${req.operations.length})</h5><div class="stack">${req.operations.map(operationCard).join("")}</div></section>
      <section class="cr-section"><h5>Impact on eligibility</h5><div id="cr-impact" class="mx-empty">Calculating impact… (a few seconds)</div></section>
      <section class="cr-section"><h5>Audit trail</h5>${historySection(history.rows)}</section>
    `;
    try {
      const preview = await api(`/api/change-requests/${requestId}/preview`);
      if (token !== loadToken) return;
      const el = detailEl.querySelector("#cr-impact");
      el.className = "";
      el.innerHTML = impactSection(preview);
    } catch (error) {
      if (token !== loadToken) return;
      detailEl.querySelector("#cr-impact").textContent = `Impact preview unavailable: ${error.message}`;
    }
  }

  mount.querySelector("#cr-filters").addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    ui.filter = b.dataset.filter;
    mount.querySelectorAll("#cr-filters button").forEach((x) => x.classList.toggle("is-active", x === b));
    renderList();
    renderDetail().catch((err) => notify(err.message, "error"));
  });
  mount.querySelector("#cr-search").addEventListener("input", (e) => {
    ui.query = e.target.value;
    const before = ui.selected;
    renderList();
    if (ui.selected !== before) renderDetail().catch((err) => notify(err.message, "error"));
  });
  listEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-request-id]");
    if (!b || b.dataset.requestId === ui.selected) return;
    ui.selected = b.dataset.requestId;
    renderList();
    renderDetail().catch((err) => notify(err.message, "error"));
  });
  detailEl.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const action = b.dataset.action;
    if (CONFIRM[action] && !window.confirm(CONFIRM[action])) return;
    detailEl.querySelectorAll("[data-action]").forEach((x) => (x.disabled = true));
    b.textContent = "Working…";
    try {
      await api(`/api/change-requests/${ui.selected}/${action}`, { method: "POST", body: JSON.stringify({}) });
      notify(`${action[0].toUpperCase()}${action.slice(1)} complete`);
      await renderChangeRequests(mount, state, { notify });
    } catch (error) {
      notify(error.message, "error");
      renderDetail().catch((err) => notify(err.message, "error"));
    }
  });

  renderList();
  await renderDetail();
}
