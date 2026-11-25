/** Quick Meal Log Bar + Manual Search Modal with smart slot selection */
import { t } from "../i18n/index.js";
import { escapeHtml, localizedFoodName, pickDefaultSlotForNow, round1 } from "../utils.js";

export function renderQuickLogBar(container, state, callbacks) {
  const { language, llmEnabled, mealSlots, busy, selectedAiSlot } = state;
  const slots = mealSlots && mealSlots.length ? mealSlots : [{ name: "Breakfast" }, { name: "Lunch" }, { name: "Snack" }, { name: "Dinner" }];

  container.innerHTML = `
    <div class="quick-bar">
      <div class="quick-input-row">
        <input
          type="text"
          id="quick-text-input"
          placeholder="${escapeHtml(t(language, "quick_input_placeholder"))}"
          ${!llmEnabled || busy ? "disabled" : ""}
        />
        <button id="quick-ai-btn" ${!llmEnabled || busy ? "disabled" : ""}>
          ${escapeHtml(t(language, "log_ai_btn"))}
        </button>
      </div>
      ${
        llmEnabled
          ? `<div class="quick-actions">
              <span class="hint-text">${escapeHtml(t(language, "meal_slot_label"))}:</span>
              <button type="button" class="chip-btn ai-slot-chip ${!selectedAiSlot ? "active" : ""}" data-slot="">${escapeHtml(t(language, "auto_slot_option"))}</button>
              ${slots
                .map(
                  (s) =>
                    `<button type="button" class="chip-btn ai-slot-chip ${selectedAiSlot === s.name ? "active" : ""}" data-slot="${escapeHtml(s.name)}">${escapeHtml(s.name)}</button>`
                )
                .join("")}
            </div>`
          : `<div class="hint-text">${escapeHtml(t(language, "llm_disabled_hint"))}</div>`
      }
      <div class="quick-actions">
        <button type="button" class="secondary" id="open-manual-btn">${escapeHtml(t(language, "manual_log_btn"))}</button>
        <button type="button" class="secondary" id="open-barcode-btn">📷 ${escapeHtml(t(language, "barcode_btn"))}</button>
      </div>
      <div id="manual-modal-host"></div>
    </div>
  `;

  const input = container.querySelector("#quick-text-input");
  const aiBtn = container.querySelector("#quick-ai-btn");
  const submitAi = () => {
    const val = (input?.value || "").trim();
    if (val) {
      callbacks.onLogText(val, state.selectedAiSlot || null);
      input.value = "";
    }
  };
  aiBtn?.addEventListener("click", submitAi);
  input?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      submitAi();
    }
  });

  container.querySelectorAll(".ai-slot-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const slot = chip.getAttribute("data-slot") || null;
      callbacks.onSelectAiSlot?.(slot);
    });
  });

  container.querySelector("#open-barcode-btn")?.addEventListener("click", () => {
    callbacks.onOpenBarcode();
  });

  container.querySelector("#open-manual-btn")?.addEventListener("click", () => {
    openManualSearchModal(
      container.querySelector("#manual-modal-host"),
      { language, slots, api: callbacks.api, personId: state.personId },
      callbacks.onLogItem
    );
  });
}

function openManualSearchModal(host, ctx, onLogItem) {
  const { language, slots, api, personId } = ctx;
  let searchResults = [];
  let selectedFood = null;
  let chosenSlot = pickDefaultSlotForNow(slots);

  const renderModal = () => {
    host.innerHTML = `
      <div class="modal-backdrop">
        <div class="modal-panel">
          <div class="modal-header">
            <span>${escapeHtml(t(language, "manual_modal_title"))}</span>
            <button type="button" class="secondary" id="close-manual-modal">✕</button>
          </div>
          <div class="quick-input-row" style="margin-bottom: 10px;">
            <input type="text" id="manual-search-q" placeholder="${escapeHtml(t(language, "search_foods_placeholder"))}" />
            <button type="button" id="manual-search-go">${escapeHtml(t(language, "search_btn"))}</button>
          </div>
          <div id="manual-search-list" style="max-height: 200px; overflow-y: auto; margin-bottom: 10px;">
            ${searchResults
              .map(
                (f, idx) => `
                <div class="food-list-item">
                  <div>
                    <strong>${escapeHtml(localizedFoodName(f, language))}</strong>
                    ${f.brand ? `<span class="pill">${escapeHtml(f.brand)}</span>` : ""}
                    <div class="meal-item-meta">
                      ${round1(f.macros_per_100g.kcal)} kcal | P ${round1(f.macros_per_100g.protein_g)}g C ${round1(f.macros_per_100g.carbs_g)}g F ${round1(f.macros_per_100g.fat_g)}g (${escapeHtml(t(language, "per_100g"))})
                    </div>
                  </div>
                  <button type="button" class="secondary pick-food-btn" data-idx="${idx}">+</button>
                </div>
              `
              )
              .join("")}
          </div>
          ${
            selectedFood
              ? `
              <div class="meal-card">
                <div style="font-weight: 600; margin-bottom: 8px;">${escapeHtml(localizedFoodName(selectedFood, language))}</div>
                <div class="quick-actions" style="margin-bottom: 8px;">
                  ${slots
                    .map(
                      (s) =>
                        `<button type="button" class="chip-btn manual-slot-chip ${chosenSlot === s.name ? "active" : ""}" data-slot="${escapeHtml(s.name)}">${escapeHtml(s.name)}</button>`
                    )
                    .join("")}
                </div>
                <div class="form-grid">
                  <div class="form-field">
                    <label>${escapeHtml(t(language, "state_label"))}</label>
                    <select id="manual-state">
                      <option value="raw" ${selectedFood.preparation_state === "raw" ? "selected" : ""}>${escapeHtml(t(language, "state_raw"))}</option>
                      <option value="cooked" ${selectedFood.preparation_state === "cooked" ? "selected" : ""}>${escapeHtml(t(language, "state_cooked"))}</option>
                      <option value="ready_to_eat" ${selectedFood.preparation_state === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(language, "state_ready_to_eat"))}</option>
                    </select>
                  </div>
                  <div class="form-field">
                    <label>${escapeHtml(t(language, "quantity_label"))}</label>
                    <input type="number" step="0.5" min="0.1" id="manual-qty" value="100" />
                  </div>
                  <div class="form-field">
                    <label>${escapeHtml(t(language, "unit_label"))}</label>
                    <input type="text" id="manual-unit" value="g" />
                  </div>
                </div>
                <button type="button" class="success" id="confirm-manual-log">${escapeHtml(t(language, "log_item_btn"))}</button>
              </div>
            `
              : ""
          }
        </div>
      </div>
    `;

    host.querySelector("#close-manual-modal")?.addEventListener("click", () => {
      host.innerHTML = "";
    });

    const runSearch = async () => {
      const q = (host.querySelector("#manual-search-q")?.value || "").trim();
      if (!q) return;
      const resp = await api.searchFoods(personId, q, true, { limit: 25, offset: 0 });
      searchResults = resp.foods || [];
      selectedFood = searchResults[0] || null;
      renderModal();
      const qInput = host.querySelector("#manual-search-q");
      if (qInput) qInput.value = q;
    };

    host.querySelector("#manual-search-go")?.addEventListener("click", runSearch);
    host.querySelector("#manual-search-q")?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        runSearch();
      }
    });

    host.querySelectorAll(".pick-food-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-idx"));
        selectedFood = searchResults[idx] || null;
        renderModal();
      });
    });

    host.querySelectorAll(".manual-slot-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        chosenSlot = chip.getAttribute("data-slot") || chosenSlot;
        renderModal();
      });
    });

    host.querySelector("#confirm-manual-log")?.addEventListener("click", async () => {
      if (!selectedFood) return;
      const qty = Number(host.querySelector("#manual-qty")?.value || 100);
      const unit = (host.querySelector("#manual-unit")?.value || "g").trim();
      const state = host.querySelector("#manual-state")?.value || "unspecified";
      host.innerHTML = "";
      await onLogItem({
        food_id: selectedFood.food_id,
        name: localizedFoodName(selectedFood, language),
        quantity: qty,
        unit,
        state,
        meal_slot: chosenSlot,
      });
    });
  };

  renderModal();
}
