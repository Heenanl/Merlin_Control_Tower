import { api, escapeHtml } from "/static/js/api.js";

export async function renderMatrix(mount, state, { notify }) {
  const data = await api("/api/matrix");
  const grouped = new Map();
  for (const row of data.rows) {
    const key = row.content_id;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(row);
  }

  mount.innerHTML = `
    <section class="panel stack">
      <div>
        <h3>Rule × Content matrix</h3>
        <p class="small">Click a mapping to draft an add/remove request. Promotions still require approval.</p>
      </div>
      <div class="matrix-grid">
        ${[...grouped.entries()]
          .map(
            ([contentId, rows]) => `
              <article class="card">
                <h4>${escapeHtml(contentId)} · ${escapeHtml(rows[0].proposition_id)} · ${escapeHtml(rows[0].channel_code)}</h4>
                <div class="actions">
                  ${rows
                    .map(
                      (row) => `
                        <button class="matrix-cell ${row.mapped ? "is-on" : ""}" data-content-id="${escapeHtml(row.content_id)}" data-rule-id="${escapeHtml(row.eligibility_rule_id)}">
                          <strong>${escapeHtml(row.eligibility_rule_id)}</strong><br/>
                          ${escapeHtml(row.eligibility_rule)}<br/>
                          <span class="small">${escapeHtml(row.eligibility_rule_type)}</span>
                        </button>`
                    )
                    .join("")}
                </div>
              </article>`
          )
          .join("")}
      </div>
    </section>
  `;

  mount.querySelectorAll(".matrix-cell").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const payload = {
          content_id: button.dataset.contentId,
          eligibility_rule_id: Number(button.dataset.ruleId),
          comment: "Drafted from rule/content matrix",
        };
        const result = await api("/api/matrix/toggle", { method: "POST", body: JSON.stringify(payload) });
        notify(`Drafted ${result.request_id}`);
      } catch (error) {
        notify(error.message, "error");
      }
    });
  });
}
