import { api, escapeHtml, renderTable } from "/static/js/api.js";

export async function renderRules(mount, state, { notify }) {
  const [tables, propositionRows, ruleRows] = await Promise.all([
    api("/api/config/tables"),
    api("/api/config/proposition?limit=50"),
    api("/api/config/eligibility_rule?limit=50"),
  ]);

  mount.innerHTML = `
    <section class="stack">
      <article class="panel">
        <h3>New proposition wizard</h3>
        <form id="wizard-form" class="stack">
          <div class="form-grid">
            <label>Proposition ID<input name="proposition_id" placeholder="P99" required /></label>
            <label>Content ID<input name="content_id" placeholder="1299AA" required /></label>
            <label>Name<input name="name" placeholder="Mortgage green upgrade" required /></label>
            <label>Product family<input name="product_family" placeholder="mortgage" required /></label>
            <label>Weight<input name="proposition_weight" type="number" min="0" step="0.01" value="1.0" /></label>
            <label>Channel<select name="channel_code"><option value="email">Email</option><option value="push">Push notification</option><option value="in_app">In-app message</option><option value="sms">SMS</option></select></label>
            <label>Language<select name="language_code"><option>NL</option><option>EN</option></select></label>
            <label>Message type<select name="message_type"><option>offer</option><option>tip</option></select></label>
          </div>
          <label>Comment<textarea name="comment" rows="2" placeholder="Why this proposition is being added"></textarea></label>
          <div class="actions"><button class="button primary" type="submit">Draft proposition package</button></div>
        </form>
      </article>
      <article class="panel">
        <h3>Current propositions</h3>
        ${renderTable(propositionRows.rows)}
      </article>
      <article class="panel">
        <h3>Current eligibility rules</h3>
        ${renderTable(ruleRows.rows)}
      </article>
    </section>
  `;

  mount.querySelector("#wizard-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = Object.fromEntries(form.entries());
    payload.proposition_weight = Number(payload.proposition_weight);
    try {
      const result = await api("/api/rules/proposition-wizard", { method: "POST", body: JSON.stringify(payload) });
      notify(`Drafted ${result.request_id}`);
    } catch (error) {
      notify(error.message, "error");
    }
  });
}
