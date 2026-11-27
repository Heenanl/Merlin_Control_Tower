import { api, escapeHtml } from "/static/js/api.js";

// Rule descriptions contain the raw placeholder name; show it in plain language.
const pretty = (text) =>
  String(text ?? "").replace(/in the last followup_after_days days/g, "within the follow-up window");

const typeChip = (type) => `<span class="chip ${type === "Include" ? "include" : "exclude"}">${escapeHtml(type)}</span>`;

export async function renderMatrix(mount, state, { notify }) {
  const data = await api("/api/matrix");

  // One entry per content item, with all rules and whether each is applied to it.
  const contents = new Map();
  for (const row of data.rows) {
    if (!contents.has(row.content_id)) {
      contents.set(row.content_id, {
        content_id: row.content_id,
        proposition_id: row.proposition_id,
        proposition_name: row.proposition_name,
        channel_code: row.channel_code,
        language_code: row.language_code,
        message_type: row.message_type,
        rules: [],
      });
    }
    contents.get(row.content_id).rules.push(row);
  }
  const items = [...contents.values()];
  const ui = {
    selected: contents.has(state.matrixSelected) ? state.matrixSelected : items[0]?.content_id,
    contentQuery: state.matrixQuery || "", // set by the search box in the hero card
    ruleQuery: "",
    ruleFilter: "applied",
    drafted: new Set(),
  };

  mount.innerHTML = `
    <section class="panel stack">
      <div>
        <h3>Rule × Content</h3>
        <p class="small">Pick a content item to see which eligibility rules apply to it. Adding or removing a rule drafts a change request; nothing goes live until it is approved and promoted.</p>
      </div>
      <div class="mx-layout">
        <aside class="mx-sidebar">
          <input id="mx-content-search" type="search" placeholder="Search content or proposition" />
          <ul id="mx-content-list" class="mx-content-list"></ul>
        </aside>
        <div id="mx-detail" class="mx-detail"></div>
      </div>
    </section>
  `;
  mount.querySelector("#mx-content-search").value = ui.contentQuery;
  state.matrixQuery = "";
  const listEl = mount.querySelector("#mx-content-list");
  const detailEl = mount.querySelector("#mx-detail");

  function renderContentList() {
    const q = ui.contentQuery.toLowerCase();
    const visible = items.filter((c) =>
      [c.content_id, c.proposition_id, c.proposition_name, c.channel_code].some((v) => String(v ?? "").toLowerCase().includes(q))
    );
    listEl.innerHTML = visible.length
      ? visible
          .map((c) => {
            const applied = c.rules.filter((r) => r.mapped).length;
            return `
              <li>
                <button class="mx-content ${c.content_id === ui.selected ? "is-active" : ""}" data-content-id="${escapeHtml(c.content_id)}">
                  <div class="mx-content-top"><strong>${escapeHtml(c.content_id)}</strong><span class="badge">${applied} rules</span></div>
                  <div class="small">${escapeHtml(c.proposition_id)} · ${escapeHtml(c.proposition_name || "")}</div>
                  <div class="mx-tags"><span class="tag">${escapeHtml(c.channel_code)}</span><span class="tag">${escapeHtml(c.language_code)}</span><span class="tag">${escapeHtml(c.message_type)}</span></div>
                </button>
              </li>`;
          })
          .join("")
      : `<li class="mx-empty">No content matches.</li>`;
  }

  function renderDetail() {
    const c = contents.get(ui.selected);
    if (!c) {
      detailEl.innerHTML = `<div class="mx-empty">Select a content item.</div>`;
      return;
    }
    const applied = c.rules.filter((r) => r.mapped);
    const includes = applied.filter((r) => r.eligibility_rule_type === "Include").length;
    const counts = { applied: applied.length, available: c.rules.length - applied.length, all: c.rules.length };
    detailEl.innerHTML = `
      <div class="mx-detail-header">
        <h4>${escapeHtml(c.content_id)} <span class="mx-muted">· ${escapeHtml(c.proposition_id)} ${escapeHtml(c.proposition_name || "")}</span></h4>
        <div class="mx-tags">
          <span class="tag">${escapeHtml(c.channel_code)}</span><span class="tag">${escapeHtml(c.language_code)}</span><span class="tag">${escapeHtml(c.message_type)}</span>
          <span class="small">${applied.length} rules applied: ${includes} include, ${applied.length - includes} exclude</span>
        </div>
      </div>
      <div class="mx-toolbar">
        <div class="segmented">
          ${[["applied", "Applied"], ["available", "Not applied"], ["all", "All"]]
            .map(([key, label]) => `<button data-filter="${key}" class="${ui.ruleFilter === key ? "is-active" : ""}">${label} (${counts[key]})</button>`)
            .join("")}
        </div>
        <input id="mx-rule-search" type="search" placeholder="Search rules" value="${escapeHtml(ui.ruleQuery)}" />
      </div>
      <ul id="mx-rules" class="mx-rules"></ul>
    `;
    renderRules();
  }

  function renderRules() {
    const c = contents.get(ui.selected);
    const q = ui.ruleQuery.toLowerCase();
    const rules = c.rules
      .filter((r) => (ui.ruleFilter === "applied" ? r.mapped : ui.ruleFilter === "available" ? !r.mapped : true))
      .filter((r) => !q || `${r.eligibility_rule_id} ${pretty(r.eligibility_rule)}`.toLowerCase().includes(q))
      .sort((a, b) => Number(b.mapped) - Number(a.mapped) || a.eligibility_rule_id - b.eligibility_rule_id);
    detailEl.querySelector("#mx-rules").innerHTML = rules.length
      ? rules
          .map((r) => {
            const key = `${c.content_id}|${r.eligibility_rule_id}`;
            const drafted = ui.drafted.has(key);
            const label = drafted ? "Drafted" : r.mapped ? "Remove" : "Add";
            return `
              <li class="mx-rule ${r.mapped ? "is-applied" : ""}">
                <div class="mx-rule-id">${escapeHtml(r.eligibility_rule_id)}</div>
                <div>
                  <div class="mx-rule-name">${escapeHtml(pretty(r.eligibility_rule))}</div>
                  <div class="mx-rule-meta">
                    ${typeChip(r.eligibility_rule_type)}
                    <span class="tag">${escapeHtml(r.eligibility_rule_level)}</span>
                    ${r.status && r.status !== "live" ? `<span class="chip ${escapeHtml(r.status)}">${escapeHtml(r.status)}</span>` : ""}
                  </div>
                </div>
                <button class="button compact ${r.mapped ? "" : "primary"}" data-rule-id="${escapeHtml(r.eligibility_rule_id)}" ${drafted ? "disabled" : ""}>${label}</button>
              </li>`;
          })
          .join("")
      : `<li class="mx-empty">No rules in this view.</li>`;
  }

  mount.querySelector("#mx-content-search").addEventListener("input", (e) => {
    ui.contentQuery = e.target.value;
    renderContentList();
  });

  listEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-content-id]");
    if (!btn) return;
    ui.selected = btn.dataset.contentId;
    state.matrixSelected = ui.selected;
    renderContentList();
    renderDetail();
  });

  detailEl.addEventListener("input", (e) => {
    if (e.target.id !== "mx-rule-search") return;
    ui.ruleQuery = e.target.value;
    renderRules();
  });

  detailEl.addEventListener("click", async (e) => {
    const filterBtn = e.target.closest("[data-filter]");
    if (filterBtn) {
      ui.ruleFilter = filterBtn.dataset.filter;
      renderDetail();
      return;
    }
    const ruleBtn = e.target.closest("[data-rule-id]");
    if (!ruleBtn || ruleBtn.disabled) return;
    ruleBtn.disabled = true;
    ruleBtn.textContent = "Drafting\u2026";
    try {
      const payload = {
        content_id: ui.selected,
        eligibility_rule_id: Number(ruleBtn.dataset.ruleId),
        comment: "Drafted from rule/content view",
      };
      const result = await api("/api/matrix/toggle", { method: "POST", body: JSON.stringify(payload) });
      ui.drafted.add(`${ui.selected}|${ruleBtn.dataset.ruleId}`);
      notify(`Drafted ${result.request_id}. Review it under Change requests.`);
      renderRules();
    } catch (error) {
      notify(error.message, "error");
      renderRules();
    }
  });

  renderContentList();
  renderDetail();
}
