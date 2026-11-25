/** Preferences View: Two-tier Global Household & Personal constraints, tastes, learned foods */
import { t } from "../i18n/index.js";
import { escapeHtml } from "../utils.js";

const STANCE_OPTIONS = [
  "allergy",
  "intolerance",
  "blacklist",
  "dislike",
  "like",
  "love",
  "preferred_brand",
];

function stancePillClass(stance) {
  if (stance === "allergy" || stance === "intolerance" || stance === "blacklist") return "bad";
  if (stance === "dislike") return "warn";
  if (stance === "like" || stance === "love" || stance === "preferred_brand") return "good";
  return "";
}

export function renderPreferencesView(container, prefState, uiState, callbacks) {
  const lang = uiState.language;
  const entries = prefState.entries || [];
  const ctx = prefState.context || {};
  const feedback = prefState.feedback || [];

  const globalEntries = entries.filter((e) => e.scope === "global");
  const personEntries = entries.filter((e) => e.scope === "person");

  const renderEntryRow = (e) => `
    <div class="food-list-item">
      <div>
        <strong>${escapeHtml(e.target_value)}</strong>
        <span class="pill ${stancePillClass(e.stance)}">${escapeHtml(t(lang, `stance_${e.stance}`) || e.stance)}</span>
        <span class="pill">${escapeHtml(e.target_type)}</span>
        ${e.notes ? `<div class="meal-item-meta">${escapeHtml(e.notes)}</div>` : ""}
      </div>
      <button type="button" class="secondary del-pref-btn" data-id="${escapeHtml(e.pref_id)}">✕</button>
    </div>
  `;

  container.innerHTML = `
    <div class="meal-card" style="margin-bottom: 12px;">
      <div style="font-weight: 600; margin-bottom: 8px;">${escapeHtml(t(lang, "pref_add_title"))}</div>
      <div class="form-grid">
        <div class="form-field">
          <label>${escapeHtml(t(lang, "pref_scope_label"))}</label>
          <select id="pref-scope">
            <option value="person">${escapeHtml(t(lang, "pref_scope_person"))}</option>
            <option value="global">${escapeHtml(t(lang, "pref_scope_global"))}</option>
          </select>
        </div>
        <div class="form-field">
          <label>${escapeHtml(t(lang, "pref_type_label"))}</label>
          <select id="pref-type">
            <option value="ingredient">Ingredient / Food</option>
            <option value="brand">Brand</option>
            <option value="category">Category</option>
            <option value="dish">Dish</option>
          </select>
        </div>
        <div class="form-field">
          <label>${escapeHtml(t(lang, "pref_value_label"))}</label>
          <input type="text" id="pref-value" placeholder="e.g. peanuts, Scotti, broccoli" />
        </div>
        <div class="form-field">
          <label>${escapeHtml(t(lang, "pref_stance_label"))}</label>
          <select id="pref-stance">
            ${STANCE_OPTIONS.map(
              (st) => `<option value="${escapeHtml(st)}">${escapeHtml(t(lang, `stance_${st}`))}</option>`
            ).join("")}
          </select>
        </div>
      </div>
      <button type="button" class="success" id="add-pref-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "pref_add_btn"))}</button>
    </div>

    <div class="section-title">${escapeHtml(t(lang, "pref_person_title"))}</div>
    ${personEntries.length ? personEntries.map(renderEntryRow).join("") : `<div class="hint-text">-</div>`}

    <div class="section-title">${escapeHtml(t(lang, "pref_global_title"))}</div>
    ${globalEntries.length ? globalEntries.map(renderEntryRow).join("") : `<div class="hint-text">-</div>`}

    <div class="section-title">${escapeHtml(t(lang, "pref_frequent_title"))}</div>
    <div class="pills">
      ${(ctx.top_frequent_foods || []).length
        ? ctx.top_frequent_foods.map((f) => `<span class="pill">${escapeHtml(f)}</span>`).join("")
        : `<span class="hint-text">-</span>`}
    </div>

    ${
      feedback.length
        ? `<div class="section-title">${escapeHtml(t(lang, "pref_feedback_title"))}</div>
           ${feedback
             .map(
               (fb) => `
             <div class="food-list-item">
               <span><strong>${escapeHtml(fb.title)}</strong></span>
               <span class="pill">${escapeHtml(fb.rating)}</span>
             </div>
           `
             )
             .join("")}`
        : ""
    }
  `;

  container.querySelector("#add-pref-btn")?.addEventListener("click", () => {
    const val = (container.querySelector("#pref-value")?.value || "").trim();
    if (!val) return;
    callbacks.onAddPreference({
      scope: container.querySelector("#pref-scope")?.value || "person",
      target_type: container.querySelector("#pref-type")?.value || "ingredient",
      target_value: val,
      stance: container.querySelector("#pref-stance")?.value || "like",
    });
  });

  container.querySelectorAll(".del-pref-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onDeletePreference(btn.getAttribute("data-id"));
    });
  });
}
