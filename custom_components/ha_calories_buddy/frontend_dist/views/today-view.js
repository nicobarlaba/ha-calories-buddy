/** Today View: Calorie ring, macro bars, SVG macro distribution pie chart, planned today, meal diary */
import { renderQuickLogBar } from "../components/quick-log-bar.js";
import { t } from "../i18n/index.js";
import {
  computeMacroPieSlices,
  escapeHtml,
  localizedFoodName,
  parseJsonSafe,
  round1,
} from "../utils.js";

function renderMacroPieSvg(pieData) {
  const r = 34;
  const c = 2 * Math.PI * r;
  if (pieData.totalKcal <= 0) {
    return `
      <svg width="88" height="88" viewBox="0 0 88 88">
        <circle cx="44" cy="44" r="${r}" fill="none" stroke="var(--secondary-background-color, rgba(127,127,127,0.22))" stroke-width="14" />
      </svg>
    `;
  }
  let offset = 0;
  const circles = pieData.slices
    .filter((s) => s.exactShare > 0.005)
    .map((s) => {
      const len = s.exactShare * c;
      const el = `<circle cx="44" cy="44" r="${r}" fill="none" stroke="${s.color}" stroke-width="14" stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 44 44)" />`;
      offset += len;
      return el;
    })
    .join("");
  return `<svg width="88" height="88" viewBox="0 0 88 88">${circles}</svg>`;
}

export function renderTodayView(container, dashboard, uiState, callbacks) {
  const lang = uiState.language;
  const targets = dashboard.targets || { target_kcal: 2000, protein_g: 150, carbs_g: 200, fat_g: 65, fiber_g: 28, safety_warnings: [] };
  const totals = dashboard.totals?.consumed || { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 };
  const tdee = dashboard.tdee || { tdee_kcal: 2200, engine_used: "static" };
  const meals = dashboard.meals || [];
  const slots = dashboard.person?.meal_slots || [];
  const plannedToday = (dashboard.planned_today || []).filter((p) => p.status === "planned");

  const targetKcal = Math.max(1, Number(targets.target_kcal || 1));
  const consumedKcal = Number(totals.kcal || 0);
  const remainingKcal = Math.round(targetKcal - consumedKcal);
  const pct = Math.min(100, Math.round((consumedKcal / targetKcal) * 100));

  const radius = 48;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (pct / 100) * circumference;
  const ringColor = remainingKcal < 0 ? "var(--error-color, #db4437)" : "var(--primary-color, #03a9f4)";

  const pieMode = uiState.macroPieMode || (consumedKcal > 0 ? "consumed" : "target");
  const pieSource =
    pieMode === "target"
      ? {
          protein_g: targets.protein_g,
          carbs_g: targets.carbs_g,
          fat_g: targets.fat_g,
          fiber_g: targets.fiber_g || 0,
        }
      : totals;
  const pieData = computeMacroPieSlices(pieSource);

  const pendingMeals = meals.filter((m) => m.status === "pending_confirmation");
  const confirmedMeals = meals.filter((m) => m.status === "confirmed");

  const slotOrder = new Map(slots.map((s, idx) => [s.name, idx]));
  const confirmedGroupsMap = new Map();
  for (const meal of confirmedMeals) {
    const key = meal.meal_slot || "Meal";
    if (!confirmedGroupsMap.has(key)) {
      confirmedGroupsMap.set(key, {
        meal_slot: key,
        logIds: [],
        primaryLogId: meal.log_id,
        items: [],
        total_macros: { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 },
      });
    }
    const grp = confirmedGroupsMap.get(key);
    grp.logIds.push(meal.log_id);
    grp.total_macros.kcal += Number(meal.total_macros?.kcal || 0);
    grp.total_macros.protein_g += Number(meal.total_macros?.protein_g || 0);
    grp.total_macros.carbs_g += Number(meal.total_macros?.carbs_g || 0);
    grp.total_macros.fat_g += Number(meal.total_macros?.fat_g || 0);
    grp.total_macros.fiber_g += Number(meal.total_macros?.fiber_g || 0);
    for (const item of meal.items || []) {
      grp.items.push({ ...item, _logId: item.log_id || meal.log_id });
    }
  }
  const confirmedGroups = Array.from(confirmedGroupsMap.values()).sort((a, b) => {
    const ia = slotOrder.has(a.meal_slot) ? slotOrder.get(a.meal_slot) : 999;
    const ib = slotOrder.has(b.meal_slot) ? slotOrder.get(b.meal_slot) : 999;
    return ia - ib;
  });

  const renderMacroBar = (label, val, maxVal, color) => {
    const p = maxVal > 0 ? Math.min(100, Math.round((val / maxVal) * 100)) : 0;
    return `
      <div class="macro-row">
        <div class="macro-label">
          <span>${escapeHtml(label)}</span>
          <span><strong>${round1(val)}g</strong> / ${round1(maxVal)}g</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${p}%; background:${color};"></div>
        </div>
      </div>
    `;
  };

  const renderSlotSelect = (mealSlot, logIds) => {
    const slotNames = slots.map((s) => s.name);
    const hasCurrent = slotNames.includes(mealSlot);
    const joinedIds = (logIds || []).join(",");
    return `
      <select class="slot-inline-select meal-slot-select" data-log-ids="${escapeHtml(joinedIds)}">
        ${!hasCurrent ? `<option value="${escapeHtml(mealSlot)}" selected>${escapeHtml(mealSlot)}</option>` : ""}
        ${slotNames
          .map(
            (name) =>
              `<option value="${escapeHtml(name)}" ${mealSlot === name ? "selected" : ""}>${escapeHtml(name)}</option>`
          )
          .join("")}
      </select>
    `;
  };

  const renderAddItemPanel = (targetLogId) => {
    const selFood = uiState.addSelectedFood || null;
    const selState =
      selFood && ["raw", "cooked", "ready_to_eat"].includes(selFood.preparation_state)
        ? selFood.preparation_state
        : "ready_to_eat";
    const safeId = escapeHtml(targetLogId);
    return `
      <div class="add-item-box">
        ${
          selFood
            ? `<div class="controls-row" style="margin-bottom: 6px;">
                 <strong>${escapeHtml(localizedFoodName(selFood, lang))}</strong>
                 ${selFood.brand ? `<span class="pill">${escapeHtml(selFood.brand)}</span>` : ""}
                 <span class="pill good">${Math.round(selFood.macros_per_100g?.kcal || 0)} kcal/100g</span>
                 <button type="button" class="chip-btn" id="clear-add-food-btn">✕</button>
               </div>`
            : ""
        }
        <div class="quick-input-row">
          <input type="text" id="add-food-search-${safeId}" placeholder="${escapeHtml(t(lang, "add_item_placeholder"))}" />
          <button type="button" class="secondary add-food-search-btn" data-log-id="${safeId}">🔍</button>
        </div>
        <div id="add-food-results-${safeId}" class="edit-food-results"></div>
        <div class="controls-row" style="margin-top: 6px;">
          <input type="number" step="0.5" min="0.1" id="add-qty-${safeId}" value="100" style="width: 75px;" />
          <input type="text" id="add-unit-${safeId}" value="g" style="width: 65px;" />
          <select id="add-state-${safeId}">
            <option value="ready_to_eat" ${selState === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(lang, "state_ready_to_eat"))}</option>
            <option value="raw" ${selState === "raw" ? "selected" : ""}>${escapeHtml(t(lang, "state_raw"))}</option>
            <option value="cooked" ${selState === "cooked" ? "selected" : ""}>${escapeHtml(t(lang, "state_cooked"))}</option>
          </select>
          <button type="button" class="success confirm-add-item-btn" data-log-id="${safeId}">${escapeHtml(t(lang, "add_item_submit_btn"))}</button>
          <button type="button" class="secondary cancel-add-item-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `;
  };

  const renderMealGroupCard = (mealOrGroup, isPending) => {
    const editingItemId = uiState.editingItemId;
    const primaryLogId = isPending ? mealOrGroup.log_id : mealOrGroup.primaryLogId;
    const logIds = isPending ? [mealOrGroup.log_id] : mealOrGroup.logIds;
    const itemsList = mealOrGroup.items || [];
    const totalMacros = mealOrGroup.total_macros || { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 };
    const isAddingHere = uiState.addingToLogId === primaryLogId;

    const itemsHtml = itemsList
      .map((item) => {
        const itemLogId = item._logId || item.log_id || primaryLogId;
        const isEditing = editingItemId === item.item_id;
        const warnBadge = item.warning_code
          ? `<span class="pill warn">⚠️ ${escapeHtml(t(lang, `warn_${item.warning_code}`) || item.warning_code)}</span>`
          : "";
        if (isEditing) {
          const repFood = uiState.editReplacementFood || null;
          const activeName = repFood ? localizedFoodName(repFood, lang) : item.display_name;
          const activeBrand = repFood ? repFood.brand : item.brand;
          const activeState = repFood && ["raw", "cooked", "ready_to_eat"].includes(repFood.preparation_state)
            ? repFood.preparation_state
            : item.input_state;
          return `
            <div class="meal-item-row">
              <div style="flex: 1; min-width: 210px;">
                <div class="controls-row">
                  <strong>${escapeHtml(activeName)}</strong>
                  ${activeBrand ? `<span class="pill">${escapeHtml(activeBrand)}</span>` : ""}
                  ${
                    repFood
                      ? `<span class="pill good">${escapeHtml(t(lang, "replaced_badge"))}</span>
                         <button type="button" class="chip-btn" id="clear-rep-food-btn">✕</button>`
                      : ""
                  }
                </div>
                <div class="quick-input-row" style="margin-top: 6px;">
                  <input type="text" id="edit-food-search-${escapeHtml(item.item_id)}" placeholder="${escapeHtml(t(lang, "replace_food_placeholder"))}" />
                  <button type="button" class="secondary edit-food-search-btn" data-item-id="${escapeHtml(item.item_id)}">🔍</button>
                </div>
                <div id="edit-food-results-${escapeHtml(item.item_id)}" class="edit-food-results"></div>
                <div class="controls-row" style="margin-top: 6px;">
                  <input type="number" step="0.5" min="0.1" id="edit-qty-${escapeHtml(item.item_id)}" value="${item.input_quantity}" style="width: 75px;" />
                  <input type="text" id="edit-unit-${escapeHtml(item.item_id)}" value="${escapeHtml(item.input_unit)}" style="width: 70px;" />
                  <select id="edit-state-${escapeHtml(item.item_id)}">
                    <option value="raw" ${activeState === "raw" ? "selected" : ""}>${escapeHtml(t(lang, "state_raw"))}</option>
                    <option value="cooked" ${activeState === "cooked" ? "selected" : ""}>${escapeHtml(t(lang, "state_cooked"))}</option>
                    <option value="ready_to_eat" ${activeState === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(lang, "state_ready_to_eat"))}</option>
                  </select>
                  <button type="button" class="success save-edit-btn" data-log-id="${escapeHtml(itemLogId)}" data-item-id="${escapeHtml(item.item_id)}">${escapeHtml(t(lang, "save_btn"))}</button>
                  <button type="button" class="secondary cancel-edit-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
                  <button type="button" class="danger remove-item-btn" data-log-id="${escapeHtml(itemLogId)}" data-item-id="${escapeHtml(item.item_id)}">${escapeHtml(t(lang, "remove_item_btn"))}</button>
                </div>
              </div>
            </div>
          `;
        }
        return `
          <div class="meal-item-row">
            <div>
              <span><strong>${escapeHtml(item.display_name)}</strong></span>
              ${item.brand ? `<span class="pill">${escapeHtml(item.brand)}</span>` : ""}
              ${warnBadge}
              <div class="meal-item-meta">
                ${item.input_quantity} ${escapeHtml(item.input_unit)} (${escapeHtml(t(lang, `state_${item.input_state}`) || item.input_state)})
                · ${Math.round(item.macros.kcal)} kcal (P ${round1(item.macros.protein_g)} / C ${round1(item.macros.carbs_g)} / F ${round1(item.macros.fat_g)} / Fib ${round1(item.macros.fiber_g || 0)})
              </div>
            </div>
            <div class="controls-row">
              <button type="button" class="secondary start-edit-btn" data-item-id="${escapeHtml(item.item_id)}">${escapeHtml(t(lang, "edit_btn"))}</button>
              <button type="button" class="icon-danger-btn remove-item-btn" title="${escapeHtml(t(lang, "remove_item_btn"))}" data-log-id="${escapeHtml(itemLogId)}" data-item-id="${escapeHtml(item.item_id)}">✕</button>
            </div>
          </div>
        `;
      })
      .join("");

    const hasUnlinked = itemsList.some((i) => !i.food_id);

    return `
      <div class="meal-card ${isPending ? "pending" : ""}">
        <div class="meal-header">
          <span class="controls-row">
            <span>${isPending ? "⚠️" : "🍽️"}</span>
            ${renderSlotSelect(mealOrGroup.meal_slot, logIds)}
          </span>
          <span>${Math.round(totalMacros.kcal)} kcal</span>
        </div>
        <div class="meal-item-meta" style="margin-bottom: 4px;">
          P ${round1(totalMacros.protein_g)}g · C ${round1(totalMacros.carbs_g)}g · F ${round1(totalMacros.fat_g)}g · Fib ${round1(totalMacros.fiber_g || 0)}g
        </div>
        ${itemsHtml}
        ${isAddingHere ? renderAddItemPanel(primaryLogId) : ""}
        <div class="meal-actions">
          ${isPending ? `<button type="button" class="success confirm-meal-btn" data-log-id="${escapeHtml(primaryLogId)}" data-promote="0">${escapeHtml(t(lang, "confirm_btn"))}</button>` : ""}
          ${isPending && hasUnlinked ? `<button type="button" class="warning confirm-meal-btn" data-log-id="${escapeHtml(primaryLogId)}" data-promote="1">${escapeHtml(t(lang, "confirm_promote_btn"))}</button>` : ""}
          <button type="button" class="secondary start-add-item-btn" data-log-id="${escapeHtml(primaryLogId)}">${escapeHtml(t(lang, "add_item_btn"))}</button>
          <button type="button" class="secondary delete-meal-btn" data-log-ids="${escapeHtml(logIds.join(","))}">${escapeHtml(t(lang, "delete_btn"))}</button>
        </div>
      </div>
    `;
  };

  const bio = dashboard.biometrics || null;
  const hasMappedBio = Boolean(bio && (bio.weight_entity_id || bio.body_fat_entity_id));
  const weightInputVal = (bio?.weight_kg ?? dashboard.latest_weight_kg)
    ? round1(bio?.weight_kg ?? dashboard.latest_weight_kg)
    : "";
  const renderBiometricsSection = () => {
    if (!hasMappedBio) {
      return `
        <div class="section-title">${escapeHtml(t(lang, "weight_label"))}</div>
        <div class="controls-row">
          <input type="number" step="0.1" min="25" max="350" id="today-weight-input" value="${weightInputVal}" style="width: 110px;" />
          <button type="button" class="secondary" id="save-weight-btn">${escapeHtml(t(lang, "log_weight_btn"))}</button>
        </div>
      `;
    }
    const weightBadge = bio.weight_entity_id
      ? bio.weight_sensor_available
        ? `<span class="pill good">📡 ${escapeHtml(bio.weight_entity_name || t(lang, "sensor_source_badge"))}</span>`
        : `<span class="pill warn">⚠️ ${escapeHtml(t(lang, "sensor_unavailable_badge"))}</span>`
      : `<span class="pill">${escapeHtml(t(lang, "weight_label"))}</span>`;
    const bfBadge = bio.body_fat_entity_id
      ? bio.body_fat_sensor_available
        ? `<span class="pill good">📡 ${escapeHtml(bio.body_fat_entity_name || t(lang, "sensor_source_badge"))}</span>`
        : `<span class="pill warn">⚠️ ${escapeHtml(t(lang, "sensor_unavailable_badge"))}</span>`
      : "";
    const needsFallback = !bio.weight_entity_id || !bio.weight_sensor_available;
    return `
      <div class="section-title">${escapeHtml(t(lang, "biometrics_title"))}</div>
      <div class="biometrics-card">
        <div class="biometrics-grid">
          <div class="bio-stat">
            <span class="bio-stat-label">${escapeHtml(t(lang, "weight_stat_label"))}</span>
            <span class="bio-stat-val">${round1(bio.weight_kg)} kg</span>
            <div>${weightBadge}</div>
          </div>
          ${
            bio.body_fat_pct !== null && bio.body_fat_pct !== undefined
              ? `<div class="bio-stat">
                   <span class="bio-stat-label">${escapeHtml(t(lang, "body_fat_label"))}</span>
                   <span class="bio-stat-val">${round1(bio.body_fat_pct)} %</span>
                   ${bfBadge ? `<div>${bfBadge}</div>` : ""}
                 </div>`
              : ""
          }
          ${
            bio.lean_mass_kg !== null && bio.lean_mass_kg !== undefined
              ? `<div class="bio-stat">
                   <span class="bio-stat-label">${escapeHtml(t(lang, "lean_mass_label"))}</span>
                   <span class="bio-stat-val">${round1(bio.lean_mass_kg)} kg</span>
                 </div>`
              : ""
          }
        </div>
        ${
          needsFallback
            ? `<div>
                 <button type="button" class="chip-btn" id="toggle-manual-weight-btn">${escapeHtml(t(lang, "manual_weight_fallback_btn"))}</button>
               </div>
               ${
                 uiState.showManualWeightFallback
                   ? `<div class="controls-row">
                        <input type="number" step="0.1" min="25" max="350" id="today-weight-input" value="${weightInputVal}" style="width: 110px;" />
                        <button type="button" class="secondary" id="save-weight-btn">${escapeHtml(t(lang, "log_weight_btn"))}</button>
                      </div>`
                   : ""
               }`
            : ""
        }
      </div>
    `;
  };

  container.innerHTML = `
    <div id="quick-log-host"></div>

    <div class="summary-grid">
      <div class="ring-wrap">
        <svg width="116" height="116" viewBox="0 0 116 116">
          <circle cx="58" cy="58" r="${radius}" fill="none" stroke="var(--secondary-background-color, rgba(127,127,127,0.18))" stroke-width="10" />
          <circle
            cx="58" cy="58" r="${radius}"
            fill="none"
            stroke="${ringColor}"
            stroke-width="10"
            stroke-linecap="round"
            stroke-dasharray="${circumference}"
            stroke-dashoffset="${dashOffset}"
            transform="rotate(-90 58 58)"
          />
        </svg>
        <div class="ring-center">
          <div class="ring-kcal">${Math.round(consumedKcal)}</div>
          <div class="ring-sub">/ ${Math.round(targetKcal)} kcal</div>
        </div>
      </div>

      <div class="macro-bars">
        ${renderMacroBar(t(lang, "protein"), totals.protein_g, targets.protein_g, "#42a5f5")}
        ${renderMacroBar(t(lang, "carbs"), totals.carbs_g, targets.carbs_g, "#ffa726")}
        ${renderMacroBar(t(lang, "fat"), totals.fat_g, targets.fat_g, "#ab47bc")}
        ${renderMacroBar(t(lang, "fiber"), totals.fiber_g || 0, targets.fiber_g || 28, "#66bb6a")}
      </div>
    </div>

    <div class="macro-pie-card">
      <div class="macro-pie-left">
        ${renderMacroPieSvg(pieData)}
        <div>
          <div style="font-size: 0.82rem; font-weight: 600; margin-bottom: 6px;">${escapeHtml(t(lang, "macro_split_title"))}</div>
          <div class="macro-legend">
            ${pieData.slices
              .map(
                (s) => `
              <div class="legend-item">
                <span class="legend-dot" style="background:${s.color};"></span>
                <span>${escapeHtml(t(lang, s.key))}: <strong>${s.pct}%</strong> (${round1(s.grams)}g)</span>
              </div>
            `
              )
              .join("")}
          </div>
        </div>
      </div>
      <div class="controls-row">
        <button type="button" class="chip-btn pie-mode-btn ${pieMode === "consumed" ? "active" : ""}" data-mode="consumed">${escapeHtml(t(lang, "consumed"))}</button>
        <button type="button" class="chip-btn pie-mode-btn ${pieMode === "target" ? "active" : ""}" data-mode="target">${escapeHtml(t(lang, "target"))}</button>
      </div>
    </div>

    <div class="pills">
      <span class="pill ${remainingKcal >= 0 ? "good" : "warn"}">
        ${escapeHtml(remainingKcal >= 0 ? t(lang, "remaining") : t(lang, "over_budget"))}: <strong>${Math.abs(remainingKcal)} kcal</strong>
      </span>
      <span class="pill">
        ${escapeHtml(t(lang, "tdee"))}: <strong>${Math.round(tdee.tdee_kcal)} kcal</strong>
      </span>
      ${(targets.safety_warnings || []).map((w) => `<span class="pill warn">⚠️ ${escapeHtml(w)}</span>`).join("")}
    </div>

    ${
      pendingMeals.length
        ? `<div class="section-title">⚠️ ${escapeHtml(t(lang, "pending_title"))} (${pendingMeals.length})</div>
           ${pendingMeals.map((m) => renderMealGroupCard(m, true)).join("")}`
        : ""
    }

    ${
      plannedToday.length
        ? `<div class="section-title" style="display:flex;justify-content:space-between;align-items:center;">
             <span>📋 ${escapeHtml(t(lang, "planned_today_title"))}</span>
             <button type="button" class="chip-btn" id="today-rebalance-btn">⚖️ ${escapeHtml(t(lang, "rebalance_today_btn"))}</button>
           </div>
           ${plannedToday
             .map((p) => {
               const ings = parseJsonSafe(p.ingredients_json, []);
               const preview = ings.slice(0, 3).map((i) => `${i.name} ${Math.round(i.grams)}g`).join(" · ");
               return `
                 <div class="meal-card planned">
                   <div class="meal-header">
                     <span>📋 [${escapeHtml(p.meal_slot)}] ${escapeHtml(p.title)}</span>
                     <span>${Math.round(p.macros.kcal)} kcal</span>
                   </div>
                   <div class="meal-item-meta">${escapeHtml(preview)} · P ${round1(p.macros.protein_g)}g C ${round1(p.macros.carbs_g)}g F ${round1(p.macros.fat_g)}g</div>
                   <div class="meal-actions">
                     <button type="button" class="success today-promote-btn" data-plan-item-id="${escapeHtml(p.plan_item_id)}" data-scale="1.0">${escapeHtml(t(lang, "log_as_eaten_btn"))}</button>
                     <button type="button" class="secondary today-promote-btn" data-plan-item-id="${escapeHtml(p.plan_item_id)}" data-scale="0.75">0.75×</button>
                     <button type="button" class="secondary today-promote-btn" data-plan-item-id="${escapeHtml(p.plan_item_id)}" data-scale="1.25">1.25×</button>
                     <button type="button" class="secondary today-swap-btn" data-plan-item-id="${escapeHtml(p.plan_item_id)}">${escapeHtml(t(lang, "swap_btn"))}</button>
                   </div>
                 </div>
               `;
             })
             .join("")}`
        : ""
    }

    <div class="section-title">${escapeHtml(t(lang, "todays_meals"))}</div>
    ${confirmedGroups.length ? confirmedGroups.map((g) => renderMealGroupCard(g, false)).join("") : `<div class="hint-text">${escapeHtml(t(lang, "no_meals_today"))}</div>`}

    ${renderBiometricsSection()}
  `;

  renderQuickLogBar(
    container.querySelector("#quick-log-host"),
    {
      language: lang,
      llmEnabled: Boolean(dashboard.llm_enabled),
      mealSlots: slots,
      personId: dashboard.person?.person_id,
      busy: uiState.busy,
      selectedAiSlot: uiState.selectedAiSlot || null,
    },
    {
      api: callbacks.api,
      onLogText: callbacks.onLogText,
      onLogItem: callbacks.onLogItem,
      onOpenBarcode: callbacks.onOpenBarcode,
      onSelectAiSlot: callbacks.onSelectAiSlot,
    }
  );

  container.querySelectorAll(".pie-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onSetMacroPieMode?.(btn.getAttribute("data-mode"));
    });
  });

  container.querySelectorAll(".meal-slot-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const rawIds = sel.getAttribute("data-log-ids") || "";
      const logIds = rawIds.split(",").filter(Boolean);
      const newSlot = sel.value;
      if (logIds.length && newSlot) {
        callbacks.onUpdateMealSlot?.(logIds, newSlot);
      }
    });
  });

  container.querySelectorAll(".today-promote-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const itemId = btn.getAttribute("data-plan-item-id");
      const scale = Number(btn.getAttribute("data-scale") || 1.0);
      callbacks.onPromotePlanMeal?.(itemId, scale);
    });
  });

  container.querySelectorAll(".today-swap-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const itemId = btn.getAttribute("data-plan-item-id");
      callbacks.onSwapPlanMeal?.(itemId, "");
    });
  });

  container.querySelector("#today-rebalance-btn")?.addEventListener("click", () => {
    callbacks.onRebalanceToday?.({});
  });

  container.querySelectorAll(".confirm-meal-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const logId = btn.getAttribute("data-log-id");
      const promote = btn.getAttribute("data-promote") === "1";
      callbacks.onConfirmMeal(logId, promote);
    });
  });

  container.querySelectorAll(".delete-meal-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rawIds = btn.getAttribute("data-log-ids") || "";
      const logIds = rawIds.split(",").filter(Boolean);
      if (logIds.length) {
        callbacks.onDeleteMeal(logIds);
      }
    });
  });

  container.querySelectorAll(".start-add-item-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onStartAddItem?.(btn.getAttribute("data-log-id"));
    });
  });

  container.querySelectorAll(".cancel-add-item-btn").forEach((btn) => {
    btn.addEventListener("click", () => callbacks.onStartAddItem?.(null));
  });

  container.querySelector("#clear-add-food-btn")?.addEventListener("click", () => {
    callbacks.onSelectAddFood?.(null);
  });

  container.querySelectorAll(".confirm-add-item-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const logId = btn.getAttribute("data-log-id");
      const qty = Number(container.querySelector(`#add-qty-${logId}`)?.value || 0);
      const unit = (container.querySelector(`#add-unit-${logId}`)?.value || "g").trim();
      const state = container.querySelector(`#add-state-${logId}`)?.value || "unspecified";
      const chosen = uiState.addSelectedFood || null;
      const typed = (container.querySelector(`#add-food-search-${logId}`)?.value || "").trim();
      if (qty > 0 && (chosen || typed.length >= 2)) {
        callbacks.onAddMealItem?.(logId, {
          ...(chosen ? { food_id: chosen.food_id, name: localizedFoodName(chosen, lang) } : { name: typed }),
          quantity: qty,
          unit,
          state,
        });
      }
    });
  });

  container.querySelectorAll(".start-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onStartEditItem(btn.getAttribute("data-item-id"));
    });
  });

  container.querySelectorAll(".cancel-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => callbacks.onStartEditItem(null));
  });

  container.querySelectorAll(".save-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const logId = btn.getAttribute("data-log-id");
      const itemId = btn.getAttribute("data-item-id");
      const qty = Number(container.querySelector(`#edit-qty-${itemId}`)?.value || 0);
      const unit = (container.querySelector(`#edit-unit-${itemId}`)?.value || "g").trim();
      const state = container.querySelector(`#edit-state-${itemId}`)?.value || "unspecified";
      const foodId = uiState.editReplacementFood?.food_id || null;
      if (qty > 0) {
        callbacks.onUpdateMealItem(logId, itemId, qty, unit, state, foodId);
      }
    });
  });

  container.querySelectorAll(".remove-item-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const logId = btn.getAttribute("data-log-id");
      const itemId = btn.getAttribute("data-item-id");
      if (logId && itemId) {
        callbacks.onDeleteMealItem?.(logId, itemId);
      }
    });
  });

  container.querySelector("#clear-rep-food-btn")?.addEventListener("click", () => {
    callbacks.onSelectEditFood?.(null);
  });

  const wireVaultLookup = (searchInput, triggerBtn, resultsHost, onChoose) => {
    if (!searchInput || !resultsHost || !callbacks.api) return;
    let searchTimer = null;
    const runFoodSearch = async () => {
      const q = (searchInput.value || "").trim();
      if (q.length < 2) {
        resultsHost.innerHTML = "";
        return;
      }
      try {
        const resp = await callbacks.api.searchFoods(
          dashboard.person?.person_id,
          q,
          false,
          { limit: 8 }
        );
        const hits = resp?.foods || [];
        if (!hits.length) {
          resultsHost.innerHTML = `<div class="hint-text" style="padding:4px;">${escapeHtml(t(lang, "product_not_found"))}</div>`;
          return;
        }
        resultsHost.innerHTML = hits
          .map(
            (f, idx) => `
          <button type="button" class="edit-food-option" data-hit-idx="${idx}">
            <span>
              <strong>${escapeHtml(localizedFoodName(f, lang))}</strong>
              ${f.brand ? ` <span class="pill">${escapeHtml(f.brand)}</span>` : ""}
            </span>
            <span class="meal-item-meta">${Math.round(f.macros_per_100g?.kcal || 0)} kcal · ${escapeHtml(t(lang, `state_${f.preparation_state}`) || f.preparation_state)}</span>
          </button>
        `
          )
          .join("");
        resultsHost.querySelectorAll(".edit-food-option").forEach((optBtn) => {
          optBtn.addEventListener("click", () => {
            const chosen = hits[Number(optBtn.getAttribute("data-hit-idx"))];
            if (chosen) onChoose(chosen);
          });
        });
      } catch (_err) {
        resultsHost.innerHTML = "";
      }
    };
    searchInput.addEventListener("input", () => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(runFoodSearch, 250);
    });
    triggerBtn?.addEventListener("click", runFoodSearch);
  };

  if (uiState.editingItemId && callbacks.api) {
    const eid = uiState.editingItemId;
    wireVaultLookup(
      container.querySelector(`#edit-food-search-${eid}`),
      container.querySelector(".edit-food-search-btn"),
      container.querySelector(`#edit-food-results-${eid}`),
      (chosen) => callbacks.onSelectEditFood?.(chosen)
    );
  }

  if (uiState.addingToLogId && callbacks.api) {
    const aid = uiState.addingToLogId;
    wireVaultLookup(
      container.querySelector(`#add-food-search-${aid}`),
      container.querySelector(".add-food-search-btn"),
      container.querySelector(`#add-food-results-${aid}`),
      (chosen) => callbacks.onSelectAddFood?.(chosen)
    );
  }

  container.querySelector("#toggle-manual-weight-btn")?.addEventListener("click", () => {
    callbacks.onToggleManualWeightFallback?.();
  });

  container.querySelector("#save-weight-btn")?.addEventListener("click", () => {
    const val = Number(container.querySelector("#today-weight-input")?.value || 0);
    if (val > 20) {
      callbacks.onLogWeight(val);
    }
  });
}
