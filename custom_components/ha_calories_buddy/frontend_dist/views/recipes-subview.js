/** Homemade Composite Recipes Sub-View: CRUD editor, live nutrition preview, AI text import, 1-tap log */
import { t } from "../i18n/index.js";
import {
  escapeHtml,
  localizedFoodName,
  pickDefaultSlotForNow,
  round1,
} from "../utils.js";

const UNIT_FACTORS_G = {
  g: 1,
  kg: 1000,
  oz: 28.3495,
  lb: 453.592,
  ml: 1,
  l: 1000,
};

function estimateIngGrams(ing) {
  const qty = Number(ing.quantity || 0);
  if (qty <= 0) return 0;
  const u = String(ing.unit || "g").trim().toLowerCase();
  if (u in UNIT_FACTORS_G) {
    const dense = ing.category === "oil" && (u === "ml" || u === "l") ? 0.92 : 1.0;
    return qty * UNIT_FACTORS_G[u] * dense;
  }
  if (ing.food_portions && typeof ing.food_portions[u] === "number") {
    return qty * ing.food_portions[u];
  }
  if (ing.grams_estimate && Number(ing.base_quantity || 0) > 0) {
    return (qty / Number(ing.base_quantity)) * Number(ing.grams_estimate);
  }
  if (ing.grams_estimate && qty === Number(ing.quantity)) {
    return Number(ing.grams_estimate);
  }
  if (u === "tbsp") return qty * 12;
  if (u === "tsp") return qty * 5;
  if (u === "cup") return qty * 200;
  if (u === "piece") return qty * 55;
  if (u === "slice") return qty * 30;
  return qty * 100;
}

function computeDraftPreview(draft) {
  let rawTotalG = 0;
  let kcal = 0;
  let protein = 0;
  let carbs = 0;
  let fat = 0;
  let fiber = 0;

  for (const ing of draft.ingredients || []) {
    const grams = estimateIngGrams(ing);
    rawTotalG += grams;
    const m100 = ing.macros_per_100g;
    if (!m100 || grams <= 0) continue;

    const st = String(ing.state || "unspecified");
    const refSt = String(ing.ref_state || "ready_to_eat");
    const yf = Math.max(0.1, Number(ing.yield_factor || 1.0));
    let refG = grams;
    if (st === "cooked" && refSt === "raw") {
      refG = grams / yf;
    } else if (st === "raw" && refSt === "cooked") {
      refG = grams * yf;
    }
    const factor = refG / 100;
    kcal += Number(m100.kcal || 0) * factor;
    protein += Number(m100.protein_g || 0) * factor;
    carbs += Number(m100.carbs_g || 0) * factor;
    fat += Number(m100.fat_g || 0) * factor;
    fiber += Number(m100.fiber_g || 0) * factor;
  }

  const finalW = Number(draft.final_weight_g || 0);
  const effG = finalW > 0 ? finalW : rawTotalG;
  const servings = Math.max(0.1, Number(draft.servings || 1));
  const safeEff = Math.max(1, effG);
  const s100 = 100 / safeEff;
  const sSrv = 1 / servings;

  return {
    rawTotalG: round1(rawTotalG),
    effG: round1(effG),
    gramsPerServing: round1(effG * sSrv),
    perServing: {
      kcal: Math.round(kcal * sSrv),
      p: round1(protein * sSrv),
      c: round1(carbs * sSrv),
      f: round1(fat * sSrv),
      fib: round1(fiber * sSrv),
    },
    per100g: {
      kcal: Math.round(kcal * s100),
      p: round1(protein * s100),
      c: round1(carbs * s100),
      f: round1(fat * s100),
      fib: round1(fiber * s100),
    },
  };
}

export function renderRecipesSubview(container, recipeState, dashboard, uiState, callbacks) {
  const lang = uiState.language;
  const llmEnabled = Boolean(dashboard?.llm_enabled);
  const personId = dashboard?.person?.person_id || null;
  const slots = dashboard?.person?.meal_slots || [{ name: "Lunch" }];
  const recipes = recipeState.recipes || [];
  const showEditor = Boolean(recipeState.showEditor);
  const draft = recipeState.draft || {
    recipe_id: null,
    name: "",
    category: "other",
    servings: 1,
    serving_unit: "serving",
    final_weight_g: "",
    allow_in_planner: false,
    notes: "",
    ingredients: [],
  };

  const renderIngRowsHtml = () => {
    const ings = draft.ingredients || [];
    if (!ings.length) {
      return `<div class="hint-text" style="margin: 6px 0;">${escapeHtml(t(lang, "recipe_ing_search_placeholder"))}</div>`;
    }
    return ings
      .map(
        (ing, idx) => `
        <div class="recipe-ing-row">
          <div style="flex: 1; min-width: 140px;">
            <strong>${escapeHtml(ing.name)}</strong>
            ${ing.food_id ? `<span class="pill good">✓</span>` : `<span class="pill warn">⚠️</span>`}
          </div>
          <div class="controls-row">
            <input type="number" step="0.5" min="0.1" class="rec-ing-qty" data-idx="${idx}" value="${escapeHtml(ing.quantity)}" style="width: 68px;" />
            <input type="text" class="rec-ing-unit" data-idx="${idx}" value="${escapeHtml(ing.unit || "g")}" style="width: 58px;" />
            <select class="rec-ing-state" data-idx="${idx}">
              <option value="ready_to_eat" ${ing.state === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(lang, "state_ready_to_eat"))}</option>
              <option value="raw" ${ing.state === "raw" ? "selected" : ""}>${escapeHtml(t(lang, "state_raw"))}</option>
              <option value="cooked" ${ing.state === "cooked" ? "selected" : ""}>${escapeHtml(t(lang, "state_cooked"))}</option>
              <option value="unspecified" ${!ing.state || ing.state === "unspecified" ? "selected" : ""}>${escapeHtml(t(lang, "state_unspecified"))}</option>
            </select>
            <button type="button" class="icon-danger-btn rec-ing-del" data-idx="${idx}">✕</button>
          </div>
        </div>
      `
      )
      .join("");
  };

  const renderPreviewHtml = () => {
    const p = computeDraftPreview(draft);
    const unitLbl = t(lang, `unit_${draft.serving_unit || "serving"}`);
    return `
      <div class="recipe-preview-box">
        <div>
          <strong>${escapeHtml(t(lang, "recipe_preview_per_serving"))}</strong> (1 ${escapeHtml(unitLbl)} · ${p.gramsPerServing}g):
          <strong>${p.perServing.kcal} kcal</strong>
          · P ${p.perServing.p}g C ${p.perServing.c}g F ${p.perServing.f}g Fib ${p.perServing.fib}g
        </div>
        <div class="meal-item-meta" style="margin-top: 2px;">
          ${escapeHtml(t(lang, "recipe_preview_per_100g"))}: ${p.per100g.kcal} kcal (P ${p.per100g.p}g C ${p.per100g.c}g F ${p.per100g.f}g Fib ${p.per100g.fib}g)
          · ${p.rawTotalG}g raw → ${p.effG}g total
        </div>
      </div>
    `;
  };

  container.innerHTML = `
    <div class="quick-input-row" style="margin-bottom: 10px;">
      <input type="text" id="recipe-search-input" value="${escapeHtml(recipeState.query || "")}" placeholder="${escapeHtml(t(lang, "recipe_search_placeholder"))}" />
      <button type="button" id="recipe-search-btn">${escapeHtml(t(lang, "search_btn"))}</button>
    </div>

    <div class="controls-row" style="margin-bottom: 12px;">
      <button type="button" class="secondary" id="toggle-recipe-editor-btn">${escapeHtml(t(lang, "add_recipe_btn"))}</button>
    </div>

    ${
      showEditor
        ? `
      <div class="meal-card" style="margin-bottom: 12px;">
        <div class="meal-header">
          <span>📖 ${escapeHtml(draft.recipe_id ? t(lang, "edit_recipe_title") : t(lang, "new_recipe_title"))}</span>
          <button type="button" class="secondary" id="cancel-recipe-btn">✕</button>
        </div>

        ${
          llmEnabled
            ? `
          <div class="add-item-box" style="margin-top: 0; margin-bottom: 10px;">
            <div style="font-size: 0.78rem; font-weight: 600; margin-bottom: 4px;">${escapeHtml(t(lang, "recipe_ai_import_title"))}</div>
            <textarea id="recipe-ai-text" rows="2" placeholder="${escapeHtml(t(lang, "recipe_ai_import_placeholder"))}" style="width: 100%; box-sizing: border-box;"></textarea>
            <div class="meal-actions" style="margin-top: 6px;">
              <button type="button" class="secondary" id="recipe-ai-import-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "recipe_ai_import_btn"))}</button>
            </div>
          </div>
        `
            : ""
        }

        <div class="form-grid">
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "recipe_name_label"))}</label>
            <input type="text" id="rec-name" value="${escapeHtml(draft.name || "")}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "recipe_category_label"))}</label>
            <select id="rec-category">
              ${["other", "snack", "protein", "carbs", "dairy", "legumes", "produce"]
                .map(
                  (c) =>
                    `<option value="${c}" ${draft.category === c ? "selected" : ""}>${escapeHtml(t(lang, `cat_${c === "protein" ? "protein" : c}`) || c)}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "recipe_serving_unit_label"))}</label>
            <select id="rec-unit">
              <option value="serving" ${draft.serving_unit === "serving" ? "selected" : ""}>${escapeHtml(t(lang, "unit_serving"))}</option>
              <option value="slice" ${draft.serving_unit === "slice" ? "selected" : ""}>${escapeHtml(t(lang, "unit_slice"))}</option>
              <option value="piece" ${draft.serving_unit === "piece" ? "selected" : ""}>${escapeHtml(t(lang, "unit_piece"))}</option>
            </select>
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "recipe_servings_label"))}</label>
            <input type="number" step="0.5" min="0.5" id="rec-servings" value="${escapeHtml(draft.servings || 1)}" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "recipe_final_weight_label"))}</label>
            <input type="number" step="1" min="1" id="rec-final-weight" placeholder="${escapeHtml(t(lang, "recipe_final_weight_hint"))}" value="${draft.final_weight_g ? escapeHtml(draft.final_weight_g) : ""}" />
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "recipe_notes_label"))}</label>
            <input type="text" id="rec-notes" value="${escapeHtml(draft.notes || "")}" />
          </div>
          <div class="form-field full">
            <label style="display:flex;align-items:center;gap:6px;flex-direction:row;">
              <input type="checkbox" id="rec-allow-planner" ${draft.allow_in_planner ? "checked" : ""} />
              <span>${escapeHtml(t(lang, "recipe_allow_planner_label"))}</span>
            </label>
          </div>
        </div>

        <div class="section-title" style="margin-top: 10px;">${escapeHtml(t(lang, "ingredients_title"))}</div>
        <div id="rec-ing-list">${renderIngRowsHtml()}</div>

        <div class="add-item-box">
          <div class="quick-input-row">
            <input type="text" id="rec-ing-search" placeholder="${escapeHtml(t(lang, "recipe_ing_search_placeholder"))}" />
            <button type="button" class="secondary" id="rec-ing-search-btn">🔍</button>
          </div>
          <div id="rec-ing-results" class="edit-food-results"></div>
        </div>

        <div id="rec-live-preview">${renderPreviewHtml()}</div>

        <div class="meal-actions">
          <button type="button" class="success" id="save-recipe-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "recipe_save_btn"))}</button>
          <button type="button" class="secondary" id="cancel-recipe-btn-2">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `
        : ""
    }

    <div>
      ${
        recipes.length
          ? recipes
              .map((rec, idx) => {
                const isQuickLog = recipeState.quickLogRecipeId === rec.recipe_id;
                const defaultSlot = pickDefaultSlotForNow(slots);
                const unitLbl = t(lang, `unit_${rec.serving_unit || "serving"}`);
                return `
                  <div class="meal-card">
                    <div class="meal-header">
                      <span>
                        📖 ${escapeHtml(rec.name)}
                        <span class="pill">${escapeHtml(t(lang, "recipe_badge"))}</span>
                        ${rec.allow_in_planner ? `<span class="pill good">📅 ${escapeHtml(t(lang, "recipe_planner_badge"))}</span>` : ""}
                      </span>
                      <span>${Math.round(rec.macros_per_serving?.kcal || 0)} kcal / ${escapeHtml(unitLbl)}</span>
                    </div>
                    <div class="meal-item-meta">
                      1 ${escapeHtml(unitLbl)} (${round1(rec.grams_per_serving)}g):
                      P ${round1(rec.macros_per_serving?.protein_g)}g · C ${round1(rec.macros_per_serving?.carbs_g)}g · F ${round1(rec.macros_per_serving?.fat_g)}g · Fib ${round1(rec.macros_per_serving?.fiber_g || 0)}g
                    </div>
                    <div class="meal-item-meta" style="margin-top: 2px;">
                      100g: ${round1(rec.macros_per_100g?.kcal)} kcal · ${escapeHtml(t(lang, "recipe_ingredients_count", { count: (rec.ingredients || []).length }))} · ${round1(rec.effective_weight_g)}g
                    </div>
                    <details style="margin-top: 6px; font-size: 0.8rem;">
                      <summary style="cursor: pointer; color: var(--secondary-text-color);">${escapeHtml(t(lang, "ingredients_title"))}</summary>
                      <div style="margin-top: 4px;">
                        ${(rec.ingredients || [])
                          .map(
                            (ing) => `
                            <div class="meal-item-row">
                              <span><strong>${escapeHtml(ing.display_name)}</strong> (${ing.input_quantity} ${escapeHtml(ing.input_unit)})</span>
                              <span>${round1(ing.grams)}g · ${Math.round(ing.macros?.kcal || 0)} kcal</span>
                            </div>
                          `
                          )
                          .join("")}
                        ${rec.notes ? `<div class="hint-text" style="margin-top: 4px;">💡 ${escapeHtml(rec.notes)}</div>` : ""}
                      </div>
                    </details>
                    ${
                      isQuickLog
                        ? `
                      <div class="controls-row" style="margin-top: 8px;">
                        <input type="number" step="0.5" min="0.25" id="qlog-qty-${escapeHtml(rec.recipe_id)}" value="1" style="width: 68px;" />
                        <select id="qlog-unit-${escapeHtml(rec.recipe_id)}">
                          <option value="${escapeHtml(rec.serving_unit || "serving")}">${escapeHtml(unitLbl)}</option>
                          <option value="g">g</option>
                        </select>
                        <select id="qlog-slot-${escapeHtml(rec.recipe_id)}">
                          ${slots
                            .map(
                              (s) =>
                                `<option value="${escapeHtml(s.name)}" ${s.name === defaultSlot ? "selected" : ""}>${escapeHtml(s.name)}</option>`
                            )
                            .join("")}
                        </select>
                        <button type="button" class="success confirm-qlog-rec-btn" data-idx="${idx}">${escapeHtml(t(lang, "recipe_confirm_log_btn"))}</button>
                        <button type="button" class="secondary cancel-qlog-rec-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
                      </div>
                    `
                        : `
                      <div class="meal-actions">
                        <button type="button" class="success open-qlog-rec-btn" data-id="${escapeHtml(rec.recipe_id)}">${escapeHtml(t(lang, "recipe_log_btn"))}</button>
                        <button type="button" class="secondary edit-rec-btn" data-idx="${idx}">${escapeHtml(t(lang, "edit_btn"))}</button>
                        <button type="button" class="danger del-rec-btn" data-id="${escapeHtml(rec.recipe_id)}">${escapeHtml(t(lang, "delete_btn"))}</button>
                      </div>
                    `
                    }
                  </div>
                `;
              })
              .join("")
          : `<div class="hint-text">${escapeHtml(t(lang, "recipe_no_recipes"))}</div>`
      }
    </div>
  `;

  const triggerRecSearch = () => {
    const q = (container.querySelector("#recipe-search-input")?.value || "").trim();
    callbacks.onSearchRecipes?.(q);
  };
  container.querySelector("#recipe-search-btn")?.addEventListener("click", triggerRecSearch);
  container.querySelector("#recipe-search-input")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      triggerRecSearch();
    }
  });

  container.querySelector("#toggle-recipe-editor-btn")?.addEventListener("click", () => {
    callbacks.onToggleRecipeEditor?.(!showEditor);
  });
  container.querySelector("#cancel-recipe-btn")?.addEventListener("click", () => {
    callbacks.onToggleRecipeEditor?.(false);
  });
  container.querySelector("#cancel-recipe-btn-2")?.addEventListener("click", () => {
    callbacks.onToggleRecipeEditor?.(false);
  });

  const refreshPreview = () => {
    const host = container.querySelector("#rec-live-preview");
    if (host) host.innerHTML = renderPreviewHtml();
  };

  const wireIngredientRowEvents = () => {
    container.querySelectorAll(".rec-ing-qty").forEach((inp) => {
      inp.addEventListener("input", () => {
        const idx = Number(inp.getAttribute("data-idx"));
        if (draft.ingredients[idx]) {
          draft.ingredients[idx].quantity = Number(inp.value || 0);
          refreshPreview();
        }
      });
    });
    container.querySelectorAll(".rec-ing-unit").forEach((inp) => {
      inp.addEventListener("input", () => {
        const idx = Number(inp.getAttribute("data-idx"));
        if (draft.ingredients[idx]) {
          draft.ingredients[idx].unit = (inp.value || "g").trim();
          refreshPreview();
        }
      });
    });
    container.querySelectorAll(".rec-ing-state").forEach((sel) => {
      sel.addEventListener("change", () => {
        const idx = Number(sel.getAttribute("data-idx"));
        if (draft.ingredients[idx]) {
          draft.ingredients[idx].state = sel.value || "unspecified";
          refreshPreview();
        }
      });
    });
    container.querySelectorAll(".rec-ing-del").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-idx"));
        draft.ingredients.splice(idx, 1);
        refreshIngList();
        refreshPreview();
      });
    });
  };

  const refreshIngList = () => {
    const listHost = container.querySelector("#rec-ing-list");
    if (listHost) {
      listHost.innerHTML = renderIngRowsHtml();
      wireIngredientRowEvents();
    }
  };

  if (showEditor) {
    wireIngredientRowEvents();

    const syncField = (selector, prop, castFn = (v) => v) => {
      container.querySelector(selector)?.addEventListener("input", (ev) => {
        draft[prop] = castFn(ev.target.value);
        refreshPreview();
      });
      container.querySelector(selector)?.addEventListener("change", (ev) => {
        draft[prop] = castFn(ev.target.value);
        refreshPreview();
      });
    };
    syncField("#rec-name", "name", (v) => v);
    syncField("#rec-category", "category", (v) => v);
    syncField("#rec-unit", "serving_unit", (v) => v);
    syncField("#rec-servings", "servings", (v) => Number(v || 1));
    syncField("#rec-final-weight", "final_weight_g", (v) => (v ? Number(v) : ""));
    syncField("#rec-notes", "notes", (v) => v);

    container.querySelector("#rec-allow-planner")?.addEventListener("change", (ev) => {
      draft.allow_in_planner = Boolean(ev.target.checked);
    });

    container.querySelector("#recipe-ai-import-btn")?.addEventListener("click", () => {
      const txt = (container.querySelector("#recipe-ai-text")?.value || "").trim();
      if (txt) {
        callbacks.onParseRecipeText?.(txt);
      }
    });

    const ingSearchInput = container.querySelector("#rec-ing-search");
    const ingResultsHost = container.querySelector("#rec-ing-results");
    if (ingSearchInput && ingResultsHost && callbacks.api) {
      let timer = null;
      const runSearch = async () => {
        const q = (ingSearchInput.value || "").trim();
        if (q.length < 2) {
          ingResultsHost.innerHTML = "";
          return;
        }
        try {
          const resp = await callbacks.api.searchFoods(personId, q, false, { limit: 8 });
          const hits = resp?.foods || [];
          if (!hits.length) {
            ingResultsHost.innerHTML = `<div class="hint-text" style="padding:4px;">${escapeHtml(t(lang, "product_not_found"))}</div>`;
            return;
          }
          ingResultsHost.innerHTML = hits
            .map(
              (f, idx) => `
              <button type="button" class="edit-food-option rec-ing-pick" data-hit-idx="${idx}">
                <span>
                  <strong>${escapeHtml(localizedFoodName(f, lang))}</strong>
                  ${f.brand ? ` <span class="pill">${escapeHtml(f.brand)}</span>` : ""}
                </span>
                <span class="meal-item-meta">${Math.round(f.macros_per_100g?.kcal || 0)} kcal · ${escapeHtml(t(lang, `state_${f.preparation_state}`) || f.preparation_state)}</span>
              </button>
            `
            )
            .join("");
          ingResultsHost.querySelectorAll(".rec-ing-pick").forEach((optBtn) => {
            optBtn.addEventListener("click", () => {
              const f = hits[Number(optBtn.getAttribute("data-hit-idx"))];
              if (!f) return;
              const defState = ["raw", "cooked", "ready_to_eat"].includes(f.preparation_state)
                ? f.preparation_state
                : "ready_to_eat";
              draft.ingredients.push({
                food_id: f.food_id,
                name: localizedFoodName(f, lang),
                quantity: 100,
                unit: "g",
                state: defState,
                macros_per_100g: f.macros_per_100g,
                yield_factor: f.yield_factor || 1.0,
                ref_state: f.preparation_state || "ready_to_eat",
                food_portions: f.food_portions || {},
                category: f.category || "other",
              });
              ingSearchInput.value = "";
              ingResultsHost.innerHTML = "";
              refreshIngList();
              refreshPreview();
            });
          });
        } catch (_err) {
          ingResultsHost.innerHTML = "";
        }
      };
      ingSearchInput.addEventListener("input", () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(runSearch, 250);
      });
      container.querySelector("#rec-ing-search-btn")?.addEventListener("click", runSearch);
    }

    container.querySelector("#save-recipe-btn")?.addEventListener("click", () => {
      const cleanName = (draft.name || "").trim();
      const validIngs = (draft.ingredients || []).filter(
        (i) => (i.name || "").trim() && Number(i.quantity) > 0
      );
      if (!cleanName || !validIngs.length) return;
      const finalW = Number(draft.final_weight_g || 0);
      callbacks.onSaveRecipe?.({
        ...(draft.recipe_id ? { recipe_id: draft.recipe_id } : {}),
        name: cleanName,
        category: draft.category || "other",
        servings: Math.max(0.25, Number(draft.servings || 1)),
        serving_unit: draft.serving_unit || "serving",
        ...(finalW > 0 ? { final_weight_g: finalW } : { final_weight_g: null }),
        allow_in_planner: Boolean(draft.allow_in_planner),
        notes: (draft.notes || "").trim(),
        ingredients: validIngs.map((i) => ({
          ...(i.food_id ? { food_id: i.food_id } : {}),
          name: i.name.trim(),
          quantity: Number(i.quantity),
          unit: (i.unit || "g").trim(),
          state: i.state || "unspecified",
        })),
      });
    });
  }

  container.querySelectorAll(".open-qlog-rec-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onToggleQuickLogRecipe?.(btn.getAttribute("data-id"));
    });
  });
  container.querySelectorAll(".cancel-qlog-rec-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onToggleQuickLogRecipe?.(null);
    });
  });
  container.querySelectorAll(".confirm-qlog-rec-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      const rec = recipes[idx];
      if (!rec) return;
      const rid = rec.recipe_id;
      const qty = Number(container.querySelector(`#qlog-qty-${rid}`)?.value || 1);
      const unit = container.querySelector(`#qlog-unit-${rid}`)?.value || rec.serving_unit || "serving";
      const mealSlot = container.querySelector(`#qlog-slot-${rid}`)?.value || "";
      if (qty > 0) {
        callbacks.onQuickLogRecipe?.({
          food_id: rec.food_id,
          name: rec.name,
          quantity: qty,
          unit,
          state: "ready_to_eat",
          meal_slot: mealSlot,
        });
      }
    });
  });

  container.querySelectorAll(".edit-rec-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      if (recipes[idx]) callbacks.onEditRecipe?.(recipes[idx]);
    });
  });
  container.querySelectorAll(".del-rec-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-id");
      if (id) callbacks.onDeleteRecipe?.(id);
    });
  });
}
