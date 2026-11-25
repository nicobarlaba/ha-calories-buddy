/** Food Vault View: search, add global foods, and edit personal overrides */
import { t } from "../i18n/index.js";
import { escapeHtml, round1 } from "../utils.js";

export function renderVaultView(container, vaultState, uiState, callbacks) {
  const lang = uiState.language;
  const foods = vaultState.foods || [];
  const query = vaultState.query || "";
  const showAddForm = Boolean(vaultState.showAddForm);
  const overrideFood = vaultState.overrideFood || null;

  container.innerHTML = `
    <div class="quick-input-row" style="margin-bottom: 10px;">
      <input type="text" id="vault-search-input" value="${escapeHtml(query)}" placeholder="${escapeHtml(t(lang, "vault_search_placeholder"))}" />
      <button type="button" id="vault-search-btn">${escapeHtml(t(lang, "search_btn"))}</button>
      <button type="button" class="secondary" id="vault-barcode-btn">📷 ${escapeHtml(t(lang, "barcode_btn"))}</button>
    </div>

    <div class="controls-row" style="margin-bottom: 12px;">
      <button type="button" class="secondary" id="toggle-add-food-btn">${escapeHtml(t(lang, "add_food_btn"))}</button>
    </div>

    ${
      showAddForm
        ? `
      <div class="meal-card" style="margin-bottom: 12px;">
        <div class="form-grid">
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "food_name_label"))}</label>
            <input type="text" id="new-food-name" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "brand_label"))}</label>
            <input type="text" id="new-food-brand" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "state_label"))}</label>
            <select id="new-food-state">
              <option value="ready_to_eat">${escapeHtml(t(lang, "state_ready_to_eat"))}</option>
              <option value="raw">${escapeHtml(t(lang, "state_raw"))}</option>
              <option value="cooked">${escapeHtml(t(lang, "state_cooked"))}</option>
            </select>
          </div>
          <div class="form-field">
            <label>kcal (${escapeHtml(t(lang, "per_100g"))})</label>
            <input type="number" step="0.1" id="new-food-kcal" value="0" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "protein"))} (g)</label>
            <input type="number" step="0.1" id="new-food-p" value="0" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "carbs"))} (g)</label>
            <input type="number" step="0.1" id="new-food-c" value="0" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "fat"))} (g)</label>
            <input type="number" step="0.1" id="new-food-f" value="0" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "yield_label"))}</label>
            <input type="number" step="0.05" min="0.1" id="new-food-yield" value="1.0" />
          </div>
        </div>
        <div class="meal-actions">
          <button type="button" class="success" id="save-new-food-btn">${escapeHtml(t(lang, "save_global_btn"))}</button>
          <button type="button" class="secondary" id="cancel-new-food-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `
        : ""
    }

    ${
      overrideFood
        ? `
      <div class="meal-card pending" style="margin-bottom: 12px;">
        <div class="meal-header">
          <span>${escapeHtml(t(lang, "override_badge"))}: ${escapeHtml(overrideFood.name)}</span>
          <button type="button" class="secondary" id="close-override-btn">✕</button>
        </div>
        <div class="form-grid">
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "preferred_alias_label"))}</label>
            <input type="text" id="ovr-alias" value="${escapeHtml(overrideFood.preferred_alias || "")}" />
          </div>
          <div class="form-field">
            <label>kcal (${escapeHtml(t(lang, "per_100g"))})</label>
            <input type="number" step="0.1" id="ovr-kcal" value="${round1(overrideFood.macros_per_100g.kcal)}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "protein"))} (g)</label>
            <input type="number" step="0.1" id="ovr-p" value="${round1(overrideFood.macros_per_100g.protein_g)}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "carbs"))} (g)</label>
            <input type="number" step="0.1" id="ovr-c" value="${round1(overrideFood.macros_per_100g.carbs_g)}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "fat"))} (g)</label>
            <input type="number" step="0.1" id="ovr-f" value="${round1(overrideFood.macros_per_100g.fat_g)}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "yield_label"))}</label>
            <input type="number" step="0.05" min="0.1" id="ovr-yield" value="${overrideFood.yield_factor || 1.0}" />
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "custom_portion_label"))}</label>
            <input type="text" id="ovr-portion" placeholder="tbsp=10" value="${escapeHtml(Object.entries(overrideFood.person_portions || {}).map(([k, v]) => `${k}=${v}`).join(", "))}" />
          </div>
        </div>
        <button type="button" class="success" id="save-override-btn">${escapeHtml(t(lang, "save_override_btn"))}</button>
      </div>
    `
        : ""
    }

    <div>
      ${foods
        .map(
          (f, idx) => `
          <div class="food-list-item">
            <div>
              <strong>${escapeHtml(f.preferred_alias || f.name)}</strong>
              ${f.brand ? `<span class="pill">${escapeHtml(f.brand)}</span>` : ""}
              ${f.verified ? `<span class="pill good">${escapeHtml(t(lang, "verified_badge"))}</span>` : ""}
              ${f.has_override ? `<span class="pill">${escapeHtml(t(lang, "override_badge"))}</span>` : ""}
              ${f.atwater_warning ? `<span class="pill warn">⚠️</span>` : ""}
              <div class="meal-item-meta">
                ${round1(f.macros_per_100g.kcal)} kcal | P ${round1(f.macros_per_100g.protein_g)}g C ${round1(f.macros_per_100g.carbs_g)}g F ${round1(f.macros_per_100g.fat_g)}g
                · ${escapeHtml(t(lang, `state_${f.preparation_state}`) || f.preparation_state)} (×${f.yield_factor})
              </div>
            </div>
            <button type="button" class="secondary edit-override-btn" data-idx="${idx}">${escapeHtml(t(lang, "edit_btn"))}</button>
          </div>
        `
        )
        .join("")}
    </div>
  `;

  const triggerSearch = () => {
    const q = (container.querySelector("#vault-search-input")?.value || "").trim();
    callbacks.onSearchVault(q);
  };
  container.querySelector("#vault-search-btn")?.addEventListener("click", triggerSearch);
  container.querySelector("#vault-search-input")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      triggerSearch();
    }
  });

  container.querySelector("#vault-barcode-btn")?.addEventListener("click", callbacks.onOpenBarcode);
  container.querySelector("#toggle-add-food-btn")?.addEventListener("click", callbacks.onToggleAddForm);
  container.querySelector("#cancel-new-food-btn")?.addEventListener("click", callbacks.onToggleAddForm);

  container.querySelector("#save-new-food-btn")?.addEventListener("click", () => {
    const name = (container.querySelector("#new-food-name")?.value || "").trim();
    if (!name) return;
    callbacks.onSaveFood({
      name,
      brand: (container.querySelector("#new-food-brand")?.value || "").trim() || null,
      preparation_state: container.querySelector("#new-food-state")?.value || "ready_to_eat",
      kcal_per_100g: Number(container.querySelector("#new-food-kcal")?.value || 0),
      protein_g_per_100g: Number(container.querySelector("#new-food-p")?.value || 0),
      carbs_g_per_100g: Number(container.querySelector("#new-food-c")?.value || 0),
      fat_g_per_100g: Number(container.querySelector("#new-food-f")?.value || 0),
      yield_factor: Number(container.querySelector("#new-food-yield")?.value || 1.0),
      verified: true,
      scope: "global",
    });
  });

  container.querySelectorAll(".edit-override-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      callbacks.onSelectOverrideFood(foods[idx] || null);
    });
  });

  container.querySelector("#close-override-btn")?.addEventListener("click", () => {
    callbacks.onSelectOverrideFood(null);
  });

  container.querySelector("#save-override-btn")?.addEventListener("click", () => {
    if (!overrideFood) return;
    const rawPortions = (container.querySelector("#ovr-portion")?.value || "").trim();
    const customPortions = {};
    if (rawPortions) {
      rawPortions.split(",").forEach((pair) => {
        const [k, v] = pair.split("=");
        const grams = Number(v);
        if (k && k.trim() && grams > 0) {
          customPortions[k.trim().toLowerCase()] = grams;
        }
      });
    }
    callbacks.onSaveFood({
      food_id: overrideFood.food_id,
      name: overrideFood.name,
      preferred_alias: (container.querySelector("#ovr-alias")?.value || "").trim() || null,
      kcal_per_100g: Number(container.querySelector("#ovr-kcal")?.value || 0),
      protein_g_per_100g: Number(container.querySelector("#ovr-p")?.value || 0),
      carbs_g_per_100g: Number(container.querySelector("#ovr-c")?.value || 0),
      fat_g_per_100g: Number(container.querySelector("#ovr-f")?.value || 0),
      yield_factor: Number(container.querySelector("#ovr-yield")?.value || 1.0),
      custom_portions: customPortions,
      scope: "person",
    });
  });
}
