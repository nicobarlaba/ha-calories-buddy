/** Food Vault View: Foods catalog & Homemade Recipes archive */
import { t } from "../i18n/index.js";
import { escapeHtml, localizedFoodName, round1 } from "../utils.js";
import { renderRecipesSubview } from "./recipes-subview.js";

const VAULT_CATEGORIES = [
  "all",
  "protein",
  "carbs",
  "legumes",
  "dairy",
  "produce",
  "fats",
  "other",
];

export function renderVaultView(
  container,
  vaultState,
  uiState,
  callbacks,
  recipeState = null,
  dashboard = null
) {
  const lang = uiState.language;
  const subTab = vaultState.subTab === "recipes" ? "recipes" : "foods";
  const subTabBarHtml = `
    <div class="subtab-bar">
      <button type="button" class="subtab-btn vault-subtab-btn ${subTab === "foods" ? "active" : ""}" data-subtab="foods">🥫 ${escapeHtml(t(lang, "vault_subtab_foods"))}</button>
      <button type="button" class="subtab-btn vault-subtab-btn ${subTab === "recipes" ? "active" : ""}" data-subtab="recipes">📖 ${escapeHtml(t(lang, "vault_subtab_recipes"))}</button>
    </div>
  `;

  if (subTab === "recipes") {
    container.innerHTML = `${subTabBarHtml}<div id="recipes-subview-host"></div>`;
    container.querySelectorAll(".vault-subtab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        callbacks.onSwitchVaultSubTab?.(btn.getAttribute("data-subtab") || "foods");
      });
    });
    renderRecipesSubview(
      container.querySelector("#recipes-subview-host"),
      recipeState || {},
      dashboard,
      uiState,
      callbacks
    );
    return;
  }

  const foods = vaultState.foods || [];
  const query = vaultState.query || "";
  const category = vaultState.category || "all";
  const offset = Number(vaultState.offset || 0);
  const limit = Number(vaultState.limit || 20);
  const totalCount = Number(vaultState.totalCount || foods.length);
  const showAddForm = Boolean(vaultState.showAddForm);
  const overrideFood = vaultState.overrideFood || null;

  const totalPages = Math.max(1, Math.ceil(totalCount / Math.max(1, limit)));
  const currentPage = Math.floor(offset / Math.max(1, limit)) + 1;
  const startIdx = totalCount > 0 ? offset + 1 : 0;
  const endIdx = Math.min(totalCount, offset + foods.length);

  container.innerHTML = `
    ${subTabBarHtml}
    <div class="quick-input-row" style="margin-bottom: 10px;">
      <input type="text" id="vault-search-input" value="${escapeHtml(query)}" placeholder="${escapeHtml(t(lang, "vault_search_placeholder"))}" />
      <button type="button" id="vault-search-btn">${escapeHtml(t(lang, "search_btn"))}</button>
      <button type="button" class="secondary" id="vault-barcode-btn">📷 ${escapeHtml(t(lang, "barcode_btn"))}</button>
    </div>

    <div class="controls-row" style="margin-bottom: 10px;">
      ${VAULT_CATEGORIES.map(
        (cat) =>
          `<button type="button" class="chip-btn vault-cat-chip ${category === cat ? "active" : ""}" data-cat="${escapeHtml(cat)}">${escapeHtml(t(lang, `cat_${cat}`))}</button>`
      ).join("")}
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
            <label>${escapeHtml(t(lang, "fiber"))} (g)</label>
            <input type="number" step="0.1" id="new-food-fib" value="0" />
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
          <span>${escapeHtml(t(lang, "override_badge"))}: ${escapeHtml(localizedFoodName(overrideFood, lang))}</span>
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
            <label>${escapeHtml(t(lang, "fiber"))} (g)</label>
            <input type="number" step="0.1" id="ovr-fib" value="${round1(overrideFood.macros_per_100g.fiber_g || 0)}" />
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
              <strong>${escapeHtml(localizedFoodName(f, lang))}</strong>
              ${f.brand ? `<span class="pill">${escapeHtml(f.brand)}</span>` : ""}
              ${f.source === "recipe" ? `<span class="pill">📖 ${escapeHtml(t(lang, "recipe_badge"))}</span>` : ""}
              ${f.verified ? `<span class="pill good">${escapeHtml(t(lang, "verified_badge"))}</span>` : ""}
              ${f.has_override ? `<span class="pill">${escapeHtml(t(lang, "override_badge"))}</span>` : ""}
              ${f.atwater_warning ? `<span class="pill warn">⚠️</span>` : ""}
              <div class="meal-item-meta">
                ${round1(f.macros_per_100g.kcal)} kcal | P ${round1(f.macros_per_100g.protein_g)}g C ${round1(f.macros_per_100g.carbs_g)}g F ${round1(f.macros_per_100g.fat_g)}g Fib ${round1(f.macros_per_100g.fiber_g || 0)}g
                · ${escapeHtml(t(lang, `state_${f.preparation_state}`) || f.preparation_state)} (×${f.yield_factor})
              </div>
            </div>
            <button type="button" class="secondary edit-override-btn" data-idx="${idx}">${escapeHtml(t(lang, "edit_btn"))}</button>
          </div>
        `
        )
        .join("")}
    </div>

    <div class="pagination-bar">
      <span class="hint-text">${escapeHtml(t(lang, "showing_range").replace("{start}", startIdx).replace("{end}", endIdx).replace("{total}", totalCount))}</span>
      <div class="controls-row">
        <button type="button" class="secondary" id="vault-prev-btn" ${offset <= 0 ? "disabled" : ""}>← ${escapeHtml(t(lang, "prev_page"))}</button>
        <span>${escapeHtml(t(lang, "page_of").replace("{page}", currentPage).replace("{pages}", totalPages))}</span>
        <button type="button" class="secondary" id="vault-next-btn" ${offset + foods.length >= totalCount ? "disabled" : ""}>${escapeHtml(t(lang, "next_page"))} →</button>
      </div>
    </div>
  `;

  container.querySelectorAll(".vault-subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onSwitchVaultSubTab?.(btn.getAttribute("data-subtab") || "foods");
    });
  });

  const triggerSearch = () => {
    const q = (container.querySelector("#vault-search-input")?.value || "").trim();
    callbacks.onSearchVault(q, category, 0);
  };
  container.querySelector("#vault-search-btn")?.addEventListener("click", triggerSearch);
  container.querySelector("#vault-search-input")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      triggerSearch();
    }
  });

  container.querySelectorAll(".vault-cat-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const cat = chip.getAttribute("data-cat") || "all";
      const q = (container.querySelector("#vault-search-input")?.value || "").trim();
      callbacks.onSearchVault(q, cat, 0);
    });
  });

  container.querySelector("#vault-prev-btn")?.addEventListener("click", () => {
    callbacks.onSearchVault(query, category, Math.max(0, offset - limit));
  });
  container.querySelector("#vault-next-btn")?.addEventListener("click", () => {
    callbacks.onSearchVault(query, category, offset + limit);
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
      fiber_g_per_100g: Number(container.querySelector("#new-food-fib")?.value || 0),
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
      fiber_g_per_100g: Number(container.querySelector("#ovr-fib")?.value || 0),
      yield_factor: Number(container.querySelector("#ovr-yield")?.value || 1.0),
      custom_portions: customPortions,
      scope: "person",
    });
  });
}
