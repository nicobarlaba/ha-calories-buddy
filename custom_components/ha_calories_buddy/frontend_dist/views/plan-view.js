/** Plan View: 7-day strip, Generate Plan dialog, Suggest Meal advisor, Recipe modal, Swap & Rebalance */
import { t } from "../i18n/index.js";
import { escapeHtml, parseJsonSafe, pickDefaultSlotForNow, round1 } from "../utils.js";

function groupIngredientsByCourse(ingredients) {
  const hasCourse = (ingredients || []).some((ing) => Boolean(ing.course));
  if (!hasCourse) return null;
  const groups = [];
  const byCourse = new Map();
  for (const ing of ingredients || []) {
    const label = (ing.course || "").trim() || "Altro";
    if (!byCourse.has(label)) {
      const bucket = [];
      byCourse.set(label, bucket);
      groups.push([label, bucket]);
    }
    byCourse.get(label).push(ing);
  }
  return groups;
}

function formatCompactIngLabel(ing, lang = "en") {
  const name = String(ing.name || "");
  const st = ing.preparation_state;
  let stateHint = "";
  if (st === "raw" || st === "cooked") {
    const lbl = lang === "it" ? (st === "raw" ? "crudo" : "cotto") : st;
    if (!name.toLowerCase().includes(lbl)) {
      stateHint = ` (${lbl})`;
    }
  }
  return `${escapeHtml(name + stateHint)} ${round1(ing.grams)}g`;
}

function renderCompactIngredientsHtml(ingredients, lang = "en") {
  const groups = groupIngredientsByCourse(ingredients);
  if (!groups) {
    return (ingredients || [])
      .map((i) => formatCompactIngLabel(i, lang))
      .join(" · ");
  }
  return groups
    .map(
      ([course, ings]) =>
        `<div class="course-line"><span class="course-tag">${escapeHtml(course)}</span> ${ings
          .map((i) => formatCompactIngLabel(i, lang))
          .join(" · ")}</div>`
    )
    .join("");
}

function renderRecipeModal(host, lang, item) {
  if (!item) {
    host.innerHTML = "";
    return;
  }
  const ingredients = parseJsonSafe(item.ingredients_json, item.ingredients || []);
  const recipe = parseJsonSafe(item.recipe_json, {
    prep_minutes: item.prep_minutes || 20,
    steps: item.steps || [],
    tips: item.tips || "",
  });

  host.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal-panel">
        <div class="modal-header">
          <span>🍽️ ${escapeHtml(item.title)}</span>
          <button type="button" class="secondary" id="close-recipe-modal">✕</button>
        </div>
        <div class="pills">
          <span class="pill">⏱️ ${Number(recipe.prep_minutes || item.prep_minutes || 20)} min</span>
          <span class="pill">${Math.round(item.macros?.kcal || 0)} kcal</span>
          <span class="pill">P ${round1(item.macros?.protein_g)}g · C ${round1(item.macros?.carbs_g)}g · F ${round1(item.macros?.fat_g)}g · Fib ${round1(item.macros?.fiber_g || 0)}g</span>
        </div>

        <div class="section-title">${escapeHtml(t(lang, "ingredients_title"))}</div>
        <div style="margin-bottom: 10px;">
          ${ingredients
            .map(
              (ing) => `
            <div class="meal-item-row">
              <span>${ing.course ? `<span class="course-tag">${escapeHtml(ing.course)}</span> ` : ""}<strong>${escapeHtml(ing.name)}</strong>${ing.brand ? ` <span class="pill">${escapeHtml(ing.brand)}</span>` : ""}</span>
              <span><strong>${round1(ing.grams)} g</strong> (${escapeHtml(t(lang, `state_${ing.preparation_state}`) || ing.preparation_state)})</span>
            </div>
          `
            )
            .join("")}
        </div>

        ${
          (recipe.steps || []).length
            ? `<div class="section-title">${escapeHtml(t(lang, "recipe_steps_title"))}</div>
               <ol style="padding-left: 20px; margin: 6px 0; font-size: 0.86rem;">
                 ${recipe.steps.map((st) => `<li style="margin-bottom: 4px;">${escapeHtml(st)}</li>`).join("")}
               </ol>`
            : ""
        }
        ${recipe.tips ? `<div class="hint-text" style="margin-top: 8px;">💡 ${escapeHtml(recipe.tips)}</div>` : ""}
      </div>
    </div>
  `;
  host.querySelector("#close-recipe-modal")?.addEventListener("click", () => {
    host.innerHTML = "";
  });
}

export function renderPlanView(container, planState, dashboard, uiState, callbacks) {
  const lang = uiState.language;
  const llmEnabled = Boolean(dashboard?.llm_enabled);
  const slots = dashboard?.person?.meal_slots || [{ name: "Breakfast" }, { name: "Lunch" }, { name: "Snack" }, { name: "Dinner" }];
  const plan = planState.plan || null;
  const days = plan?.days || [];
  const selectedDate = planState.selectedDate || (days[0]?.plan_date || dashboard?.date);
  const activeDay = days.find((d) => d.plan_date === selectedDate) || days[0] || null;
  const suggestions = planState.suggestions || [];
  const suggestMeta = planState.suggestMeta || null;
  const budgetModeVal = planState.suggestBudgetMode || "auto";
  const sugTarget = suggestMeta?.target_macros || suggestions[0]?.target_macros || null;
  const sugModeUsed = suggestMeta?.budget_mode_used || suggestions[0]?.budget_mode || "auto";
  const sugSlotLabel = suggestMeta?.meal_slot || suggestions[0]?.meal_slot || "";

  container.innerHTML = `
    <div class="controls-row" style="margin-bottom: 10px;">
      <button type="button" id="toggle-gen-plan-btn" ${!llmEnabled || uiState.busy ? "disabled" : ""}>📅 ${escapeHtml(t(lang, "generate_plan_btn"))}</button>
      <button type="button" class="secondary" id="toggle-suggest-btn" ${!llmEnabled || uiState.busy ? "disabled" : ""}>🍳 ${escapeHtml(t(lang, "suggest_meal_btn"))}</button>
      ${
        activeDay
          ? `<button type="button" class="secondary" id="plan-rebalance-btn" ${uiState.busy ? "disabled" : ""}>⚖️ ${escapeHtml(t(lang, "rebalance_today_btn"))}</button>
             <button type="button" class="secondary" id="plan-smooth-btn" ${uiState.busy ? "disabled" : ""}>📆 ${escapeHtml(t(lang, "smooth_multiday_btn"))}</button>`
          : ""
      }
    </div>

    ${!llmEnabled ? `<div class="hint-text" style="margin-bottom: 8px;">${escapeHtml(t(lang, "llm_disabled_hint"))}</div>` : ""}

    ${
      planState.showGenerateForm
        ? `
      <div class="meal-card" style="margin-bottom: 12px;">
        <div class="form-grid">
          <div class="form-field">
            <label>${escapeHtml(t(lang, "plan_days_label"))}</label>
            <input type="number" min="1" max="7" id="gen-days" value="7" />
          </div>
          <div class="form-field" style="justify-content: flex-end;">
            <label style="display:flex;align-items:center;gap:6px;">
              <input type="checkbox" id="gen-meal-prep" />
              <span>${escapeHtml(t(lang, "meal_prep_label"))}</span>
            </label>
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "plan_notes_label"))}</label>
            <input type="text" id="gen-notes" placeholder="${escapeHtml(t(lang, "swap_instruction_placeholder"))}" />
          </div>
        </div>
        <div class="meal-actions">
          <button type="button" class="success" id="submit-gen-plan-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "generate_now_btn"))}</button>
          <button type="button" class="secondary" id="cancel-gen-plan-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `
        : ""
    }

    ${
      planState.showSuggestForm
        ? `
      <div class="meal-card" style="margin-bottom: 12px;">
        <div class="form-grid">
          <div class="form-field">
            <label>${escapeHtml(t(lang, "meal_slot_label"))}</label>
            <select id="sug-slot">
              ${slots
                .map(
                  (s) =>
                    `<option value="${escapeHtml(s.name)}" ${s.name === pickDefaultSlotForNow(slots) ? "selected" : ""}>${escapeHtml(s.name)}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "max_time_label"))}</label>
            <input type="number" min="5" max="120" step="5" id="sug-max-min" value="25" />
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "budget_mode_label"))}</label>
            <select id="sug-budget-mode">
              <option value="auto" ${budgetModeVal === "auto" ? "selected" : ""}>${escapeHtml(t(lang, "budget_mode_auto"))}</option>
              <option value="remaining" ${budgetModeVal === "remaining" ? "selected" : ""}>${escapeHtml(t(lang, "budget_mode_remaining"))}</option>
              <option value="slot" ${budgetModeVal === "slot" ? "selected" : ""}>${escapeHtml(t(lang, "budget_mode_slot"))}</option>
            </select>
          </div>
          <div class="form-field full">
            <input type="text" id="sug-hint" placeholder="${escapeHtml(t(lang, "suggest_hint_placeholder"))}" />
          </div>
        </div>
        <div class="meal-actions">
          <button type="button" class="success" id="submit-suggest-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "suggest_meal_btn"))}</button>
          <button type="button" class="secondary" id="cancel-suggest-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `
        : ""
    }

    ${
      suggestions.length
        ? `<div class="section-title">🍳 ${escapeHtml(
            t(lang, "suggest_options_banner", {
              count: suggestions.length,
              slot: sugSlotLabel || t(lang, "meal_slot_label"),
            })
          )}</div>
           ${
             sugTarget
               ? `<div class="suggest-banner">
                    <span>🎯 ${escapeHtml(t(lang, "target"))}: <strong>${Math.round(sugTarget.kcal)} kcal</strong> (P ${round1(sugTarget.protein_g)}g · C ${round1(sugTarget.carbs_g)}g · F ${round1(sugTarget.fat_g)}g)</span>
                    <span class="pill">${escapeHtml(t(lang, `budget_used_${sugModeUsed}`) || sugModeUsed)}</span>
                  </div>`
               : ""
           }
           ${suggestions
             .map(
               (sug, idx) => `
             <div class="meal-card planned">
               <div class="meal-header">
                 <span>
                   ${escapeHtml(sug.title)}
                   <span class="pill">⏱️ ${sug.prep_minutes}m</span>
                   ${sug.macro_fit_warning ? `<span class="pill warn">⚠️ ${escapeHtml(t(lang, "fit_warning_badge"))}</span>` : ""}
                 </span>
                 <span>${Math.round(sug.macros.kcal)} kcal</span>
               </div>
               <div class="meal-item-meta">
                 P ${round1(sug.macros.protein_g)}g · C ${round1(sug.macros.carbs_g)}g · F ${round1(sug.macros.fat_g)}g · Fib ${round1(sug.macros.fiber_g || 0)}g
               </div>
               <div class="meal-item-meta" style="margin-top:4px;">
                 ${renderCompactIngredientsHtml(sug.ingredients || [], lang)}
               </div>
               <div class="meal-actions">
                 <button type="button" class="secondary view-sug-recipe-btn" data-idx="${idx}">${escapeHtml(t(lang, "recipe_btn"))}</button>
                 <button type="button" class="success log-sug-btn" data-idx="${idx}">${escapeHtml(t(lang, "log_as_eaten_btn"))}</button>
               </div>
             </div>
           `
             )
             .join("")}`
        : ""
    }

    ${
      days.length
        ? `
      <div class="day-strip">
        ${days
          .map(
            (d) => `
          <button type="button" class="day-pill ${d.plan_date === activeDay?.plan_date ? "active" : ""}" data-date="${escapeHtml(d.plan_date)}">
            <div><strong>${escapeHtml(d.plan_date.slice(5))}</strong></div>
            <div>${Math.round(d.target_macros.kcal)} kcal</div>
          </button>
        `
          )
          .join("")}
      </div>
      ${
        activeDay
          ? `
        <div class="pills">
          <span class="pill">🎯 ${Math.round(activeDay.target_macros.kcal)} kcal · P ${round1(activeDay.target_macros.protein_g)}g · C ${round1(activeDay.target_macros.carbs_g)}g · F ${round1(activeDay.target_macros.fat_g)}g · Fib ${round1(activeDay.target_macros.fiber_g || 0)}g</span>
        </div>
        ${(activeDay.items || [])
          .map((item, idx) => {
            const ings = parseJsonSafe(item.ingredients_json, []);
            const isConsumed = item.status === "consumed";
            const isCovered = item.status === "planned" && Boolean(item.covered_by_log);
            const isInactive = isConsumed || isCovered;
            const isSwapping = planState.swappingItemId === item.plan_item_id;
            const statusIcon = isConsumed ? "✅" : isCovered ? "🔄" : "📋";
            const statusBadge = isConsumed
              ? `<span class="pill good">${escapeHtml(t(lang, "consumed_badge"))}</span>`
              : isCovered
                ? `<span class="pill">${escapeHtml(t(lang, "replaced_by_log_badge"))}</span>`
                : "";
            return `
              <div class="meal-card ${isCovered ? "covered" : isConsumed ? "" : "planned"}">
                <div class="meal-header">
                  <span>
                    ${statusIcon} [${escapeHtml(item.meal_slot)}] ${escapeHtml(item.title)}
                    ${statusBadge}
                    ${item.macro_fit_warning ? `<span class="pill warn">⚠️ ${escapeHtml(t(lang, "fit_warning_badge"))}</span>` : ""}
                  </span>
                  <span>${Math.round(item.macros.kcal)} kcal</span>
                </div>
                <div class="meal-item-meta">
                  ⏱️ ${item.prep_minutes}m · P ${round1(item.macros.protein_g)}g C ${round1(item.macros.carbs_g)}g F ${round1(item.macros.fat_g)}g Fib ${round1(item.macros.fiber_g || 0)}g
                </div>
                <div class="meal-item-meta" style="margin-top:4px;">
                  ${renderCompactIngredientsHtml(ings, lang)}
                </div>
                ${
                  isSwapping
                    ? `
                  <div class="quick-input-row" style="margin-top:8px;">
                    <input type="text" id="swap-instr-${escapeHtml(item.plan_item_id)}" placeholder="${escapeHtml(t(lang, "swap_instruction_placeholder"))}" />
                    <button type="button" class="success confirm-swap-btn" data-id="${escapeHtml(item.plan_item_id)}">${escapeHtml(t(lang, "swap_btn"))}</button>
                    <button type="button" class="secondary cancel-swap-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
                  </div>
                `
                    : `
                  <div class="meal-actions">
                    <button type="button" class="secondary view-plan-recipe-btn" data-idx="${idx}">${escapeHtml(t(lang, "recipe_btn"))}</button>
                    ${!isInactive ? `<button type="button" class="success promote-plan-btn" data-id="${escapeHtml(item.plan_item_id)}">${escapeHtml(t(lang, "log_as_eaten_btn"))}</button>` : ""}
                    ${!isInactive && llmEnabled ? `<button type="button" class="secondary start-swap-btn" data-id="${escapeHtml(item.plan_item_id)}">${escapeHtml(t(lang, "swap_btn"))}</button>` : ""}
                    <button type="button" class="chip-btn fb-btn" data-title="${escapeHtml(item.title)}" data-rating="like">👍</button>
                    <button type="button" class="chip-btn fb-btn" data-title="${escapeHtml(item.title)}" data-rating="dislike">👎</button>
                    <button type="button" class="chip-btn fb-btn" data-title="${escapeHtml(item.title)}" data-rating="never_suggest">🚫</button>
                  </div>
                `
                }
              </div>
            `;
          })
          .join("")}
      `
          : ""
      }
    `
        : `<div class="hint-text">${escapeHtml(t(lang, "no_plan_yet"))}</div>`
    }

    <div id="recipe-modal-host"></div>
  `;

  container.querySelector("#toggle-gen-plan-btn")?.addEventListener("click", callbacks.onToggleGenerate);
  container.querySelector("#cancel-gen-plan-btn")?.addEventListener("click", callbacks.onToggleGenerate);
  container.querySelector("#toggle-suggest-btn")?.addEventListener("click", callbacks.onToggleSuggest);
  container.querySelector("#cancel-suggest-btn")?.addEventListener("click", callbacks.onToggleSuggest);

  container.querySelector("#submit-gen-plan-btn")?.addEventListener("click", () => {
    const numDays = Number(container.querySelector("#gen-days")?.value || 7);
    const mealPrep = Boolean(container.querySelector("#gen-meal-prep")?.checked);
    const notes = (container.querySelector("#gen-notes")?.value || "").trim();
    callbacks.onGeneratePlan({ num_days: numDays, meal_prep_mode: mealPrep, notes });
  });

  container.querySelector("#submit-suggest-btn")?.addEventListener("click", () => {
    const slot = container.querySelector("#sug-slot")?.value || "";
    const maxMin = Number(container.querySelector("#sug-max-min")?.value || 25);
    const budgetMode = container.querySelector("#sug-budget-mode")?.value || "auto";
    const hint = (container.querySelector("#sug-hint")?.value || "").trim();
    callbacks.onSuggestMeal({
      meal_slot: slot,
      max_cook_minutes: maxMin,
      budget_mode: budgetMode,
      hint,
    });
  });

  container.querySelectorAll(".day-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onSelectDate(btn.getAttribute("data-date"));
    });
  });

  container.querySelectorAll(".view-plan-recipe-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      const item = activeDay?.items?.[idx];
      renderRecipeModal(container.querySelector("#recipe-modal-host"), lang, item);
    });
  });

  container.querySelectorAll(".view-sug-recipe-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      renderRecipeModal(container.querySelector("#recipe-modal-host"), lang, suggestions[idx]);
    });
  });

  container.querySelectorAll(".log-sug-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      const sug = suggestions[idx];
      if (sug) callbacks.onLogSuggestion(sug);
    });
  });

  container.querySelectorAll(".promote-plan-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onPromotePlanMeal(btn.getAttribute("data-id"), 1.0);
    });
  });

  container.querySelectorAll(".start-swap-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onStartSwap(btn.getAttribute("data-id"));
    });
  });
  container.querySelectorAll(".cancel-swap-btn").forEach((btn) => {
    btn.addEventListener("click", () => callbacks.onStartSwap(null));
  });
  container.querySelectorAll(".confirm-swap-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-id");
      const instr = (container.querySelector(`#swap-instr-${id}`)?.value || "").trim();
      callbacks.onSwapPlanMeal(id, instr);
    });
  });

  container.querySelectorAll(".fb-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onSetFeedback(
        btn.getAttribute("data-title"),
        btn.getAttribute("data-rating")
      );
    });
  });

  container.querySelector("#plan-rebalance-btn")?.addEventListener("click", () => {
    callbacks.onRebalance({ date: activeDay?.plan_date });
  });
  container.querySelector("#plan-smooth-btn")?.addEventListener("click", () => {
    callbacks.onRebalance({ date: activeDay?.plan_date, spread_days: 3 });
  });
}
