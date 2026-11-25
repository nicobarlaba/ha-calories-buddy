/** Today View: Calorie ring, macro bars, SVG macro distribution pie chart, planned today, meal diary */
import { renderQuickLogBar } from "../components/quick-log-bar.js";
import { t } from "../i18n/index.js";
import { computeMacroPieSlices, escapeHtml, parseJsonSafe, round1 } from "../utils.js";

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

  const renderSlotSelect = (meal) => {
    const slotNames = slots.map((s) => s.name);
    const hasCurrent = slotNames.includes(meal.meal_slot);
    return `
      <select class="slot-inline-select meal-slot-select" data-log-id="${escapeHtml(meal.log_id)}">
        ${!hasCurrent ? `<option value="${escapeHtml(meal.meal_slot)}" selected>${escapeHtml(meal.meal_slot)}</option>` : ""}
        ${slotNames
          .map(
            (name) =>
              `<option value="${escapeHtml(name)}" ${meal.meal_slot === name ? "selected" : ""}>${escapeHtml(name)}</option>`
          )
          .join("")}
      </select>
    `;
  };

  const renderMealCard = (meal, isPending) => {
    const editingItemId = uiState.editingItemId;
    const itemsHtml = (meal.items || [])
      .map((item) => {
        const isEditing = editingItemId === item.item_id;
        const warnBadge = item.warning_code
          ? `<span class="pill warn">⚠️ ${escapeHtml(t(lang, `warn_${item.warning_code}`) || item.warning_code)}</span>`
          : "";
        if (isEditing) {
          return `
            <div class="meal-item-row">
              <div style="flex: 1; min-width: 180px;">
                <strong>${escapeHtml(item.display_name)}</strong>
                <div class="controls-row" style="margin-top: 6px;">
                  <input type="number" step="0.5" min="0.1" id="edit-qty-${escapeHtml(item.item_id)}" value="${item.input_quantity}" style="width: 75px;" />
                  <input type="text" id="edit-unit-${escapeHtml(item.item_id)}" value="${escapeHtml(item.input_unit)}" style="width: 70px;" />
                  <select id="edit-state-${escapeHtml(item.item_id)}">
                    <option value="raw" ${item.input_state === "raw" ? "selected" : ""}>${escapeHtml(t(lang, "state_raw"))}</option>
                    <option value="cooked" ${item.input_state === "cooked" ? "selected" : ""}>${escapeHtml(t(lang, "state_cooked"))}</option>
                    <option value="ready_to_eat" ${item.input_state === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(lang, "state_ready_to_eat"))}</option>
                  </select>
                  <button type="button" class="success save-edit-btn" data-log-id="${escapeHtml(meal.log_id)}" data-item-id="${escapeHtml(item.item_id)}">${escapeHtml(t(lang, "save_btn"))}</button>
                  <button type="button" class="secondary cancel-edit-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
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
            <button type="button" class="secondary start-edit-btn" data-item-id="${escapeHtml(item.item_id)}">${escapeHtml(t(lang, "edit_btn"))}</button>
          </div>
        `;
      })
      .join("");

    const hasUnlinked = (meal.items || []).some((i) => !i.food_id);

    return `
      <div class="meal-card ${isPending ? "pending" : ""}">
        <div class="meal-header">
          <span class="controls-row">
            <span>${isPending ? "⚠️" : "🍽️"}</span>
            ${renderSlotSelect(meal)}
          </span>
          <span>${Math.round(meal.total_macros.kcal)} kcal</span>
        </div>
        ${itemsHtml}
        <div class="meal-actions">
          ${isPending ? `<button type="button" class="success confirm-meal-btn" data-log-id="${escapeHtml(meal.log_id)}" data-promote="0">${escapeHtml(t(lang, "confirm_btn"))}</button>` : ""}
          ${isPending && hasUnlinked ? `<button type="button" class="warning confirm-meal-btn" data-log-id="${escapeHtml(meal.log_id)}" data-promote="1">${escapeHtml(t(lang, "confirm_promote_btn"))}</button>` : ""}
          <button type="button" class="secondary delete-meal-btn" data-log-id="${escapeHtml(meal.log_id)}">${escapeHtml(t(lang, "delete_btn"))}</button>
        </div>
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
           ${pendingMeals.map((m) => renderMealCard(m, true)).join("")}`
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
    ${confirmedMeals.length ? confirmedMeals.map((m) => renderMealCard(m, false)).join("") : `<div class="hint-text">${escapeHtml(t(lang, "no_meals_today"))}</div>`}

    <div class="section-title">${escapeHtml(t(lang, "weight_label"))}</div>
    <div class="controls-row">
      <input type="number" step="0.1" min="25" max="350" id="today-weight-input" value="${dashboard.latest_weight_kg ? round1(dashboard.latest_weight_kg) : ""}" style="width: 110px;" />
      <button type="button" class="secondary" id="save-weight-btn">${escapeHtml(t(lang, "log_weight_btn"))}</button>
    </div>
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
      const logId = sel.getAttribute("data-log-id");
      const newSlot = sel.value;
      if (logId && newSlot) {
        callbacks.onUpdateMealSlot?.(logId, newSlot);
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
      callbacks.onDeleteMeal(btn.getAttribute("data-log-id"));
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
      if (qty > 0) {
        callbacks.onUpdateMealItem(logId, itemId, qty, unit, state);
      }
    });
  });

  container.querySelector("#save-weight-btn")?.addEventListener("click", () => {
    const val = Number(container.querySelector("#today-weight-input")?.value || 0);
    if (val > 20) {
      callbacks.onLogWeight(val);
    }
  });
}
