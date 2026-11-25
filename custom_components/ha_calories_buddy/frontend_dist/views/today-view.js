/** Today View: Calorie ring, macro bars, pending confirmations, meal list, weight log */
import { renderQuickLogBar } from "../components/quick-log-bar.js";
import { t } from "../i18n/index.js";
import { escapeHtml, round1 } from "../utils.js";

export function renderTodayView(container, dashboard, uiState, callbacks) {
  const lang = uiState.language;
  const targets = dashboard.targets || { target_kcal: 2000, protein_g: 150, carbs_g: 200, fat_g: 65, safety_warnings: [] };
  const totals = dashboard.totals?.consumed || { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0 };
  const tdee = dashboard.tdee || { tdee_kcal: 2200, engine_used: "static" };
  const meals = dashboard.meals || [];

  const targetKcal = Math.max(1, Number(targets.target_kcal || 1));
  const consumedKcal = Number(totals.kcal || 0);
  const remainingKcal = Math.round(targetKcal - consumedKcal);
  const pct = Math.min(100, Math.round((consumedKcal / targetKcal) * 100));

  const radius = 48;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (pct / 100) * circumference;
  const ringColor = remainingKcal < 0 ? "var(--error-color, #db4437)" : "var(--primary-color, #03a9f4)";

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
                · ${Math.round(item.macros.kcal)} kcal (P ${round1(item.macros.protein_g)} / C ${round1(item.macros.carbs_g)} / F ${round1(item.macros.fat_g)})
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
          <span>${isPending ? "⚠️" : "🍽️"} ${escapeHtml(meal.meal_slot)}</span>
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
      mealSlots: dashboard.person?.meal_slots || [],
      personId: dashboard.person?.person_id,
      busy: uiState.busy,
    },
    {
      api: callbacks.api,
      onLogText: callbacks.onLogText,
      onLogItem: callbacks.onLogItem,
      onOpenBarcode: callbacks.onOpenBarcode,
    }
  );

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
