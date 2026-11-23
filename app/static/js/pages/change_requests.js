import { api, escapeHtml, renderTable, statusChip } from "/static/js/api.js";

// Only offer the lifecycle actions that are valid for the request's current status.
const ACTIONS_BY_STATUS = {
  draft: [["submit", "Submit", ""], ["approve", "Approve", "success"], ["reject", "Reject", "danger"]],
  submitted: [["approve", "Approve", "success"], ["reject", "Reject", "danger"]],
  approved: [["promote", "Promote", "warn"], ["reject", "Reject", "danger"]],
  promoted: [["revert", "Revert", "danger"]],
  rejected: [],
  reverted: [],
};

function actionButtons(row) {
  const id = escapeHtml(row.request_id);
  const buttons = [`<button class="button" data-action="preview" data-request-id="${id}">Preview</button>`];
  for (const [action, label, style] of ACTIONS_BY_STATUS[row.status] || []) {
    buttons.push(`<button class="button ${style}" data-action="${action}" data-request-id="${id}">${label}</button>`);
  }
  return buttons.join("");
}

async function act(requestId, action, notify) {
  const result = await api(`/api/change-requests/${requestId}/${action}`, { method: "POST", body: JSON.stringify({}) });
  notify(`${action} complete for ${requestId}`);
  return result;
}

export async function renderChangeRequests(mount, state, { notify }) {
  const data = await api("/api/change-requests");
  mount.innerHTML = `
    <section class="stack">
      <article class="panel">
        <h3>Change requests</h3>
        <p class="small">Preview before promote. Drafts can be submitted by drafters; approval and promotion require the approver role.</p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>ID</th><th>Title</th><th>Status</th><th>Drafter</th><th>Updated</th><th>Actions</th></tr>
            </thead>
            <tbody>
              ${data.rows
                .map(
                  (row) => `
                  <tr>
                    <td><code>${escapeHtml(row.request_id)}</code></td>
                    <td>${escapeHtml(row.title)}</td>
                    <td>${statusChip(row.status)}</td>
                    <td>${escapeHtml(row.drafted_by)}</td>
                    <td>${escapeHtml(row.updated_at || row.created_at)}</td>
                    <td class="actions">${actionButtons(row)}</td>
                  </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </article>
      <article class="panel" id="request-detail"><h3>Request detail</h3><div class="small">Select Preview on a request.</div></article>
    </section>
  `;

  mount.querySelectorAll("button[data-request-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const requestId = button.dataset.requestId;
      const action = button.dataset.action;
      try {
        if (action === "preview") {
          const [request, preview, history] = await Promise.all([
            api(`/api/change-requests/${requestId}`),
            api(`/api/change-requests/${requestId}/preview`),
            api(`/api/change-requests/${requestId}/history`),
          ]);
          document.getElementById("request-detail").innerHTML = `
            <h3>${escapeHtml(request.title)}</h3>
            <p>${statusChip(request.status)} · ${escapeHtml(request.comment || "No comment")}</p>
            <h4>Operations</h4>
            ${renderTable(request.operations)}
            <h4>Impact preview</h4>
            ${renderTable(preview.rows)}
            <p class="small">Scaled weekly cap: ${escapeHtml(preview.scaled_weekly_cap_before)} → ${escapeHtml(preview.scaled_weekly_cap_after)}; holdout: ${escapeHtml(preview.holdout_share_before)} → ${escapeHtml(preview.holdout_share_after)}</p>
            <h4>Audit history</h4>
            ${renderTable(history.rows)}
          `;
        } else {
          button.disabled = true;
          button.textContent = "Working…";
          await act(requestId, action, notify);
          await renderChangeRequests(mount, state, { notify });
        }
      } catch (error) {
        notify(error.message, "error");
      }
    });
  });
}
