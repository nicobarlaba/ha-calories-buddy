/** Shopping View ("The Butler"): Plan-derived editable grocery draft + HA todo.* sync */
import { t } from "../i18n/index.js";
import { escapeHtml, round1 } from "../utils.js";

const AISLE_ORDER = [
  "produce",
  "meat_fish",
  "dairy_eggs",
  "pantry",
  "beverages_snacks",
  "other",
];

const AISLE_ICONS = {
  produce: "🥬",
  meat_fish: "🥩",
  dairy_eggs: "🥛",
  pantry: "🌾",
  beverages_snacks: "☕",
  other: "🧺",
};

function addDaysIso(baseIso, deltaDays) {
  const base = baseIso ? new Date(`${baseIso}T12:00:00`) : new Date();
  base.setDate(base.getDate() + deltaDays);
  return base.toISOString().slice(0, 10);
}

export function renderShoppingView(
  container,
  shoppingState,
  dashboard,
  uiState,
  personsCount,
  callbacks
) {
  const lang = uiState.language;
  const todayIso = dashboard?.date || new Date().toISOString().slice(0, 10);
  const startDate = shoppingState.startDate || todayIso;
  const endDate = shoppingState.endDate || addDaysIso(startDate, 6);
  const includeHousehold = Boolean(shoppingState.includeHousehold);
  const draft = shoppingState.draft || null;
  const todoEntity =
    shoppingState.todoEntity || dashboard?.person?.todo_entity || null;
  const items = draft?.items || [];
  const checkedCount = items.filter((it) => it.checked).length;

  const grouped = new Map();
  for (const aisle of AISLE_ORDER) grouped.set(aisle, []);
  for (const item of items) {
    const key = AISLE_ORDER.includes(item.aisle) ? item.aisle : "other";
    grouped.get(key).push(item);
  }

  container.innerHTML = `
    <div class="quick-bar">
      <div class="form-grid" style="margin-bottom: 4px;">
        <div class="form-field">
          <label>${escapeHtml(t(lang, "shopping_range_start"))}</label>
          <input type="date" id="shop-start-date" value="${escapeHtml(startDate)}" />
        </div>
        <div class="form-field">
          <label>${escapeHtml(t(lang, "shopping_range_end"))}</label>
          <input type="date" id="shop-end-date" value="${escapeHtml(endDate)}" />
        </div>
      </div>
      <div class="controls-row" style="justify-content: space-between;">
        ${
          personsCount > 1
            ? `<label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;cursor:pointer;">
                 <input type="checkbox" id="shop-merge-household" ${includeHousehold ? "checked" : ""} />
                 <span>${escapeHtml(t(lang, "shopping_merge_household"))}</span>
               </label>`
            : `<span></span>`
        }
        <button type="button" id="shop-generate-btn" ${uiState.busy ? "disabled" : ""}>🛒 ${escapeHtml(t(lang, "shopping_generate_btn"))}</button>
      </div>
    </div>

    ${
      shoppingState.lastCommitResult
        ? `<div class="toast success-toast">
             <span>✅ ${escapeHtml(
               t(lang, "shopping_committed_ok", {
                 added: shoppingState.lastCommitResult.added_count ?? 0,
                 skipped: shoppingState.lastCommitResult.skipped_duplicates_count ?? 0,
               })
             )}</span>
           </div>`
        : ""
    }

    <div class="pills" style="justify-content: space-between; align-items: center;">
      <div class="controls-row">
        ${
          draft
            ? `<span class="pill ${draft.status === "committed" ? "good" : ""}">
                 ${escapeHtml(t(lang, `shopping_status_${draft.status}`) || draft.status)} · ${checkedCount}/${items.length}
               </span>`
            : ""
        }
        ${
          todoEntity
            ? `<span class="pill">${escapeHtml(t(lang, "shopping_todo_target", { entity: todoEntity }))}</span>`
            : `<span class="pill warn">${escapeHtml(t(lang, "shopping_todo_not_configured"))}</span>`
        }
      </div>
      ${
        draft && checkedCount > 0 && todoEntity
          ? `<button type="button" class="success" id="shop-commit-btn" ${uiState.busy ? "disabled" : ""}>📤 ${escapeHtml(t(lang, "shopping_push_todo_btn"))} (${checkedCount})</button>`
          : ""
      }
    </div>

    <div class="quick-input-row" style="margin-bottom: 12px;">
      <input type="text" id="shop-add-label" placeholder="${escapeHtml(t(lang, "shopping_add_placeholder"))}" />
      <input type="number" step="0.5" min="0.1" id="shop-add-qty" value="1" style="width: 72px; flex: 0 0 auto;" />
      <input type="text" id="shop-add-unit" value="${lang === "it" ? "pz" : "pcs"}" style="width: 58px; flex: 0 0 auto;" />
      <button type="button" class="secondary" id="shop-add-item-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "shopping_add_btn"))}</button>
    </div>

    ${
      !draft || !items.length
        ? `<div class="hint-text">${escapeHtml(t(lang, "shopping_no_draft"))}</div>`
        : AISLE_ORDER.map((aisle) => {
            const list = grouped.get(aisle) || [];
            if (!list.length) return "";
            const icon = AISLE_ICONS[aisle] || "🧺";
            return `
              <div class="aisle-group">
                <div class="aisle-header">
                  <span>${icon} ${escapeHtml(t(lang, `aisle_${aisle}`) || aisle)}</span>
                  <span>${list.filter((i) => i.checked).length}/${list.length}</span>
                </div>
                ${list
                  .map(
                    (it) => `
                  <div class="shopping-row ${it.checked ? "" : "unchecked"}">
                    <label class="shopping-row-left" style="cursor:pointer;">
                      <input type="checkbox" class="shop-item-check" data-item-id="${escapeHtml(it.draft_item_id)}" ${it.checked ? "checked" : ""} />
                      <span>
                        <strong>${escapeHtml(it.label)}</strong>
                        ${it.brand ? ` <span class="pill">${escapeHtml(it.brand)}</span>` : ""}
                      </span>
                    </label>
                    <div class="shopping-row-right">
                      <input type="number" step="1" min="0.1" class="shop-item-qty tabular" data-item-id="${escapeHtml(it.draft_item_id)}" value="${round1(it.quantity)}" style="width: 68px; padding: 4px 6px;" />
                      <input type="text" class="shop-item-unit" data-item-id="${escapeHtml(it.draft_item_id)}" value="${escapeHtml(it.unit)}" style="width: 48px; padding: 4px 6px;" />
                      <button type="button" class="icon-danger-btn shop-item-del" data-item-id="${escapeHtml(it.draft_item_id)}" title="${escapeHtml(t(lang, "delete_btn"))}">✕</button>
                    </div>
                  </div>
                `
                  )
                  .join("")}
              </div>
            `;
          }).join("")
    }
  `;

  container.querySelector("#shop-start-date")?.addEventListener("change", (ev) => {
    callbacks.onUpdateRange(ev.target.value, null);
  });
  container.querySelector("#shop-end-date")?.addEventListener("change", (ev) => {
    callbacks.onUpdateRange(null, ev.target.value);
  });
  container.querySelector("#shop-merge-household")?.addEventListener("change", (ev) => {
    callbacks.onToggleHousehold(Boolean(ev.target.checked));
  });

  container.querySelector("#shop-generate-btn")?.addEventListener("click", () => {
    const sDate = container.querySelector("#shop-start-date")?.value || startDate;
    const eDate = container.querySelector("#shop-end-date")?.value || endDate;
    const merge = Boolean(container.querySelector("#shop-merge-household")?.checked);
    callbacks.onGenerateDraft({
      start_date: sDate,
      end_date: eDate,
      include_household: merge,
    });
  });

  container.querySelector("#shop-commit-btn")?.addEventListener("click", () => {
    if (draft?.draft_id) {
      callbacks.onCommitDraft(draft.draft_id);
    }
  });

  container.querySelector("#shop-add-item-btn")?.addEventListener("click", () => {
    const label = (container.querySelector("#shop-add-label")?.value || "").trim();
    const qty = Number(container.querySelector("#shop-add-qty")?.value || 1);
    const unit = (container.querySelector("#shop-add-unit")?.value || "pcs").trim();
    if (label && qty > 0) {
      callbacks.onAddItem({
        draft_id: draft?.draft_id || undefined,
        label,
        quantity: qty,
        unit,
      });
    }
  });

  container.querySelectorAll(".shop-item-check").forEach((chk) => {
    chk.addEventListener("change", () => {
      const id = chk.getAttribute("data-item-id");
      if (draft?.draft_id && id) {
        callbacks.onUpdateItem(draft.draft_id, id, { checked: Boolean(chk.checked) });
      }
    });
  });

  container.querySelectorAll(".shop-item-qty").forEach((inp) => {
    inp.addEventListener("change", () => {
      const id = inp.getAttribute("data-item-id");
      const val = Number(inp.value || 0);
      if (draft?.draft_id && id && val > 0) {
        callbacks.onUpdateItem(draft.draft_id, id, { quantity: val });
      }
    });
  });

  container.querySelectorAll(".shop-item-unit").forEach((inp) => {
    inp.addEventListener("change", () => {
      const id = inp.getAttribute("data-item-id");
      const val = (inp.value || "").trim();
      if (draft?.draft_id && id && val) {
        callbacks.onUpdateItem(draft.draft_id, id, { unit: val });
      }
    });
  });

  container.querySelectorAll(".shop-item-del").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-item-id");
      if (draft?.draft_id && id) {
        callbacks.onDeleteItem(draft.draft_id, id);
      }
    });
  });
}
