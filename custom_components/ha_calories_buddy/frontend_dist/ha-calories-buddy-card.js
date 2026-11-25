/** HA Calories Buddy Custom Lovelace Card v0.2.3 (Zero-Build Modular ES Web Component) */
import { CaloriesBuddyApi } from "./api.js";
import { openBarcodeScannerModal } from "./components/barcode-scanner.js";
import { t } from "./i18n/index.js";
import { CARD_STYLES } from "./styles.js";
import { escapeHtml } from "./utils.js";
import { renderPlanView } from "./views/plan-view.js";
import { renderPreferencesView } from "./views/preferences-view.js";
import { renderTodayView } from "./views/today-view.js";
import { renderVaultView } from "./views/vault-view.js";

class HaCaloriesBuddyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._api = null;
    this._persons = [];
    this._activePersonId = null;
    this._activeTab = "today";
    this._dashboard = null;
    this._vaultState = {
      query: "",
      category: "all",
      foods: [],
      totalCount: 0,
      offset: 0,
      limit: 20,
      showAddForm: false,
      overrideFood: null,
    };
    this._planState = {
      plan: null,
      selectedDate: null,
      showGenerateForm: false,
      showSuggestForm: false,
      suggestions: [],
      suggestMeta: null,
      suggestBudgetMode: "auto",
      swappingItemId: null,
    };
    this._prefState = {
      loaded: false,
      context: null,
      entries: [],
      feedback: [],
    };
    this._uiState = {
      language: "en",
      busy: false,
      error: null,
      toastLogId: null,
      editingItemId: null,
      selectedAiSlot: null,
      macroPieMode: null,
    };
    this._initialized = false;
    this._eventUnsub = null;
    this._subscribedConn = null;
    this._refreshTimer = null;
    this._lastSensorStamp = "";
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    this._unsubscribeEvents();
  }

  _unsubscribeEvents() {
    const unsub = this._eventUnsub;
    this._eventUnsub = null;
    this._subscribedConn = null;
    if (typeof unsub === "function") {
      try {
        unsub();
      } catch (_err) {
        // Ignore teardown errors
      }
    } else if (unsub && typeof unsub.then === "function") {
      unsub
        .then((fn) => {
          if (typeof fn === "function") fn();
        })
        .catch(() => {});
    }
  }

  _ensureEventSubscription() {
    const conn = this._hass?.connection;
    if (!conn?.subscribeEvents || this._subscribedConn === conn) return;
    this._unsubscribeEvents();
    this._subscribedConn = conn;
    this._eventUnsub = conn.subscribeEvents((ev) => {
      const pid = ev?.data?.person_id;
      if (!pid || pid === this._activePersonId) {
        this._scheduleLiveRefresh();
      }
    }, "ha_calories_buddy_updated");
  }

  _watchSensorUpdates(hass) {
    const states = hass?.states;
    if (!states) return;
    const parts = [];
    for (const key of Object.keys(states)) {
      if (!key.startsWith("sensor.calories_buddy_")) continue;
      const st = states[key];
      parts.push(`${key}:${st.last_updated || st.last_changed || st.state}`);
    }
    parts.sort();
    const stamp = parts.join("|");
    if (this._initialized && this._lastSensorStamp && stamp !== this._lastSensorStamp) {
      this._scheduleLiveRefresh();
    }
    this._lastSensorStamp = stamp;
  }

  _scheduleLiveRefresh() {
    if (this._uiState.busy) return;
    if (this._refreshTimer) clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(async () => {
      this._refreshTimer = null;
      await this._loadDashboard();
      if (this._activeTab === "plan" && this._planState.plan) {
        await this._loadPlan();
      }
    }, 300);
  }

  setConfig(config) {
    this._config = config || {};
    if (this._config.person_id) {
      this._activePersonId = this._config.person_id;
    }
    this._render();
  }

  set hass(hass) {
    const firstSet = !this._hass;
    this._hass = hass;
    if (!this._api) {
      this._api = new CaloriesBuddyApi(hass);
    } else {
      this._api.setHass(hass);
    }

    if (hass?.language && !this._dashboard?.person?.language) {
      this._uiState.language = hass.language.startsWith("it") ? "it" : "en";
    }

    this._ensureEventSubscription();
    this._watchSensorUpdates(hass);

    if (firstSet && !this._initialized) {
      this._initialized = true;
      this._bootstrap();
    }
  }

  getCardSize() {
    return 7;
  }

  static getConfigElement() {
    return document.createElement("ha-calories-buddy-card-editor");
  }

  static getStubConfig() {
    return { title: "Calories Buddy" };
  }

  async _bootstrap() {
    if (!this._api) return;
    try {
      const resp = await this._api.listPersons();
      this._persons = resp.persons || [];
      if (!this._activePersonId && this._persons.length > 0) {
        const uid = this._hass?.user?.id;
        const matched = this._persons.find((p) => p.ha_user_id === uid);
        this._activePersonId = (matched || this._persons[0]).person_id;
      }
      if (this._activePersonId) {
        await this._loadDashboard();
      } else {
        this._render();
      }
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load persons";
      this._render();
    }
  }

  async _loadDashboard() {
    if (!this._api || !this._activePersonId) return;
    try {
      this._uiState.error = null;
      const data = await this._api.getDashboard(this._activePersonId);
      this._dashboard = data;
      if (data?.person?.language) {
        this._uiState.language = data.person.language;
      }
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load dashboard";
      this._render();
    }
  }

  async _loadVault(query = "", category = this._vaultState.category, offset = 0) {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.searchFoods(
        this._activePersonId,
        query,
        query.length >= 2,
        {
          category: category === "all" ? null : category,
          limit: this._vaultState.limit,
          offset,
        }
      );
      this._vaultState.query = query;
      this._vaultState.category = category || "all";
      this._vaultState.offset = Number(resp.offset || 0);
      this._vaultState.totalCount = Number(resp.total_count ?? (resp.foods || []).length);
      this._vaultState.foods = resp.foods || [];
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Search failed";
      this._render();
    }
  }

  async _loadPlan() {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.getPlan(this._activePersonId);
      this._planState.plan = resp.plan || null;
      if (this._planState.plan?.days?.length && !this._planState.selectedDate) {
        const todayStr = this._dashboard?.date;
        const hasToday = this._planState.plan.days.some((d) => d.plan_date === todayStr);
        this._planState.selectedDate = hasToday
          ? todayStr
          : this._planState.plan.days[0].plan_date;
      }
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load meal plan";
      this._render();
    }
  }

  async _loadPreferences() {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.getPreferences(this._activePersonId);
      this._prefState.loaded = true;
      this._prefState.context = resp.context || null;
      this._prefState.entries = resp.entries || [];
      this._prefState.feedback = resp.feedback || [];
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load preferences";
      this._render();
    }
  }

  async _withBusy(fn) {
    this._uiState.busy = true;
    this._uiState.error = null;
    this._render();
    try {
      await fn();
    } catch (err) {
      this._uiState.error = err?.message || "Action failed";
    } finally {
      this._uiState.busy = false;
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const lang = this._uiState.language;
    const cardTitle = this._config.title || t(lang, "title");

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="header">
          <div class="header-title">🥗 ${escapeHtml(cardTitle)}</div>
          <div class="controls-row">
            ${
              this._persons.length > 1
                ? `<select id="person-select">
                    ${this._persons
                      .map(
                        (p) =>
                          `<option value="${escapeHtml(p.person_id)}" ${p.person_id === this._activePersonId ? "selected" : ""}>${escapeHtml(p.display_name)}</option>`
                      )
                      .join("")}
                  </select>`
                : this._dashboard?.person
                  ? `<span class="pill">${escapeHtml(this._dashboard.person.display_name)}</span>`
                  : ""
            }
            <button type="button" class="chip-btn" id="btn-refresh-card" title="${escapeHtml(t(lang, "refresh_btn"))}" ${this._uiState.busy ? "disabled" : ""}>↻</button>
          </div>
        </div>

        ${this._uiState.error ? `<div class="error-banner">${escapeHtml(this._uiState.error)}</div>` : ""}

        ${
          this._uiState.toastLogId
            ? `<div class="toast">
                <span>${escapeHtml(t(lang, "meal_deleted"))}</span>
                <button type="button" class="secondary" id="undo-delete-btn">${escapeHtml(t(lang, "undo_btn"))}</button>
              </div>`
            : ""
        }

        ${
          !this._persons.length
            ? `<div class="hint-text">${escapeHtml(t(lang, "no_persons"))}</div>`
            : `
          <div class="tabs">
            <button type="button" class="tab-btn ${this._activeTab === "today" ? "active" : ""}" data-tab="today">${escapeHtml(t(lang, "tab_today"))}</button>
            <button type="button" class="tab-btn ${this._activeTab === "plan" ? "active" : ""}" data-tab="plan">${escapeHtml(t(lang, "tab_plan"))}</button>
            <button type="button" class="tab-btn ${this._activeTab === "vault" ? "active" : ""}" data-tab="vault">${escapeHtml(t(lang, "tab_vault"))}</button>
            <button type="button" class="tab-btn ${this._activeTab === "preferences" ? "active" : ""}" data-tab="preferences">${escapeHtml(t(lang, "tab_preferences"))}</button>
          </div>
          <div id="view-host"></div>
        `
        }
        <div id="barcode-modal-host"></div>
      </ha-card>
    `;

    this.shadowRoot.querySelector("#btn-refresh-card")?.addEventListener("click", async () => {
      await this._loadDashboard();
      if (this._activeTab === "plan") {
        await this._loadPlan();
      } else if (this._activeTab === "vault") {
        await this._loadVault(this._vaultState.query, this._vaultState.category, this._vaultState.offset);
      } else if (this._activeTab === "preferences") {
        await this._loadPreferences();
      }
    });

    this.shadowRoot.querySelector("#person-select")?.addEventListener("change", async (ev) => {
      this._activePersonId = ev.target.value;
      this._uiState.editingItemId = null;
      this._planState.plan = null;
      this._planState.suggestions = [];
      this._planState.suggestMeta = null;
      this._prefState.loaded = false;
      await this._loadDashboard();
      if (this._activeTab === "vault") {
        await this._loadVault(this._vaultState.query, this._vaultState.category, 0);
      } else if (this._activeTab === "plan") {
        await this._loadPlan();
      } else if (this._activeTab === "preferences") {
        await this._loadPreferences();
      }
    });

    this.shadowRoot.querySelector("#undo-delete-btn")?.addEventListener("click", async () => {
      const logId = this._uiState.toastLogId;
      this._uiState.toastLogId = null;
      if (logId) {
        await this._withBusy(async () => {
          await this._api.restoreMeal(this._activePersonId, logId);
          await this._loadDashboard();
        });
      }
    });

    this.shadowRoot.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        this._activeTab = btn.getAttribute("data-tab");
        if (this._activeTab === "vault" && this._vaultState.foods.length === 0) {
          await this._loadVault("", "all", 0);
        } else if (this._activeTab === "plan" && !this._planState.plan) {
          await this._loadPlan();
        } else if (this._activeTab === "preferences" && !this._prefState.loaded) {
          await this._loadPreferences();
        } else {
          this._render();
        }
      });
    });

    const viewHost = this.shadowRoot.querySelector("#view-host");
    if (!viewHost || !this._dashboard) return;

    const openBarcode = () => {
      openBarcodeScannerModal(
        this.shadowRoot.querySelector("#barcode-modal-host"),
        {
          language: lang,
          api: this._api,
          personId: this._activePersonId,
          mealSlots: this._dashboard?.person?.meal_slots || [],
        },
        {
          onVaultUpdated: async () => {
            if (this._activeTab === "vault") {
              await this._loadVault(this._vaultState.query, this._vaultState.category, this._vaultState.offset);
            }
          },
          onLogFood: async (itemPayload) => {
            await this._withBusy(async () => {
              await this._api.logItem(this._activePersonId, itemPayload);
              await this._loadDashboard();
            });
          },
        }
      );
    };

    if (this._activeTab === "today") {
      renderTodayView(viewHost, this._dashboard, this._uiState, {
        api: this._api,
        onSelectAiSlot: (slot) => {
          this._uiState.selectedAiSlot = slot;
          this._render();
        },
        onSetMacroPieMode: (mode) => {
          this._uiState.macroPieMode = mode;
          this._render();
        },
        onLogText: (text, mealSlot = null) =>
          this._withBusy(async () => {
            await this._api.logText(this._activePersonId, text, null, mealSlot);
            await this._loadDashboard();
          }),
        onLogItem: (payload) =>
          this._withBusy(async () => {
            await this._api.logItem(this._activePersonId, payload);
            await this._loadDashboard();
          }),
        onOpenBarcode: openBarcode,
        onConfirmMeal: (logId, promote) =>
          this._withBusy(async () => {
            await this._api.confirmMeal(this._activePersonId, logId, promote);
            await this._loadDashboard();
          }),
        onDeleteMeal: (logId) =>
          this._withBusy(async () => {
            await this._api.deleteMeal(this._activePersonId, logId);
            this._uiState.toastLogId = logId;
            await this._loadDashboard();
          }),
        onUpdateMealSlot: (logId, mealSlot) =>
          this._withBusy(async () => {
            await this._api.updateMealSlot(this._activePersonId, logId, mealSlot);
            await this._loadDashboard();
          }),
        onStartEditItem: (itemId) => {
          this._uiState.editingItemId = itemId;
          this._render();
        },
        onUpdateMealItem: (logId, itemId, qty, unit, state) =>
          this._withBusy(async () => {
            await this._api.updateMealItem(this._activePersonId, logId, itemId, qty, unit, state);
            this._uiState.editingItemId = null;
            await this._loadDashboard();
          }),
        onPromotePlanMeal: (planItemId, scale) =>
          this._withBusy(async () => {
            await this._api.promotePlanMeal(this._activePersonId, planItemId, scale);
            await this._loadDashboard();
            if (this._planState.plan) await this._loadPlan();
          }),
        onSwapPlanMeal: (planItemId, instruction) =>
          this._withBusy(async () => {
            await this._api.swapPlanMeal(this._activePersonId, planItemId, instruction);
            await this._loadDashboard();
            if (this._planState.plan) await this._loadPlan();
          }),
        onRebalanceToday: (opts) =>
          this._withBusy(async () => {
            await this._api.rebalanceToday(this._activePersonId, opts);
            await this._loadDashboard();
            if (this._planState.plan) await this._loadPlan();
          }),
        onLogWeight: (weightKg) =>
          this._withBusy(async () => {
            await this._api.logWeight(this._activePersonId, weightKg);
            await this._loadDashboard();
          }),
      });
    } else if (this._activeTab === "plan") {
      renderPlanView(viewHost, this._planState, this._dashboard, this._uiState, {
        onToggleGenerate: () => {
          this._planState.showGenerateForm = !this._planState.showGenerateForm;
          this._render();
        },
        onToggleSuggest: () => {
          this._planState.showSuggestForm = !this._planState.showSuggestForm;
          this._render();
        },
        onSelectDate: (d) => {
          this._planState.selectedDate = d;
          this._planState.swappingItemId = null;
          this._render();
        },
        onGeneratePlan: (opts) =>
          this._withBusy(async () => {
            const resp = await this._api.generatePlan(this._activePersonId, opts);
            this._planState.plan = resp.plan || null;
            this._planState.selectedDate = this._planState.plan?.days?.[0]?.plan_date || null;
            this._planState.showGenerateForm = false;
            await this._loadDashboard();
          }),
        onSuggestMeal: (opts) =>
          this._withBusy(async () => {
            const resp = await this._api.suggestMeal(this._activePersonId, opts);
            this._planState.suggestions = resp.suggestions || [];
            this._planState.suggestBudgetMode = opts.budget_mode || "auto";
            this._planState.suggestMeta = {
              target_macros: resp.target_macros || null,
              budget_mode_used: resp.budget_mode_used || opts.budget_mode || "auto",
              meal_slot: resp.meal_slot || opts.meal_slot || "",
            };
            this._planState.showSuggestForm = false;
          }),
        onLogSuggestion: (sug) =>
          this._withBusy(async () => {
            await this._api.logSuggestion(
              this._activePersonId,
              sug,
              1.0,
              this._dashboard?.date || null
            );
            this._planState.suggestions = [];
            this._planState.suggestMeta = null;
            await this._loadDashboard();
          }),
        onPromotePlanMeal: (planItemId, scale) =>
          this._withBusy(async () => {
            await this._api.promotePlanMeal(this._activePersonId, planItemId, scale);
            await this._loadPlan();
            await this._loadDashboard();
          }),
        onStartSwap: (itemId) => {
          this._planState.swappingItemId = itemId;
          this._render();
        },
        onSwapPlanMeal: (planItemId, instruction) =>
          this._withBusy(async () => {
            await this._api.swapPlanMeal(this._activePersonId, planItemId, instruction);
            this._planState.swappingItemId = null;
            await this._loadPlan();
            await this._loadDashboard();
          }),
        onSetFeedback: (title, rating) =>
          this._withBusy(async () => {
            await this._api.setMealFeedback(this._activePersonId, title, rating);
            this._prefState.loaded = false;
          }),
        onRebalance: (opts) =>
          this._withBusy(async () => {
            await this._api.rebalanceToday(this._activePersonId, opts);
            await this._loadPlan();
            await this._loadDashboard();
          }),
      });
    } else if (this._activeTab === "vault") {
      renderVaultView(viewHost, this._vaultState, this._uiState, {
        onSearchVault: (q, cat = "all", off = 0) => this._loadVault(q, cat, off),
        onOpenBarcode: openBarcode,
        onToggleAddForm: () => {
          this._vaultState.showAddForm = !this._vaultState.showAddForm;
          this._render();
        },
        onSelectOverrideFood: (food) => {
          this._vaultState.overrideFood = food;
          this._render();
        },
        onSaveFood: (payload) =>
          this._withBusy(async () => {
            await this._api.saveFood(this._activePersonId, payload);
            this._vaultState.showAddForm = false;
            this._vaultState.overrideFood = null;
            await this._loadVault(
              this._vaultState.query,
              this._vaultState.category,
              this._vaultState.offset
            );
          }),
      });
    } else if (this._activeTab === "preferences") {
      renderPreferencesView(viewHost, this._prefState, this._uiState, {
        onAddPreference: (payload) =>
          this._withBusy(async () => {
            await this._api.upsertPreference(this._activePersonId, payload);
            await this._loadPreferences();
          }),
        onDeletePreference: (prefId) =>
          this._withBusy(async () => {
            await this._api.deletePreference(this._activePersonId, prefId);
            await this._loadPreferences();
          }),
      });
    }
  }
}

class HaCaloriesBuddyCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  _render() {
    this.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 10px; padding: 8px 0;">
        <label style="font-size: 0.9rem;">
          Title
          <input id="editor-title" type="text" value="${escapeHtml(this._config?.title || "Calories Buddy")}" style="width: 100%; padding: 6px; margin-top: 4px;" />
        </label>
        <label style="font-size: 0.9rem;">
          Pin Person ID (optional, defaults to active HA user)
          <input id="editor-person" type="text" value="${escapeHtml(this._config?.person_id || "")}" style="width: 100%; padding: 6px; margin-top: 4px;" />
        </label>
      </div>
    `;
    const fire = () => {
      const next = {
        ...this._config,
        title: this.querySelector("#editor-title")?.value || "Calories Buddy",
      };
      const pid = (this.querySelector("#editor-person")?.value || "").trim();
      if (pid) next.person_id = pid;
      else delete next.person_id;
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
    };
    this.querySelector("#editor-title")?.addEventListener("change", fire);
    this.querySelector("#editor-person")?.addEventListener("change", fire);
  }
}

if (!customElements.get("ha-calories-buddy-card")) {
  customElements.define("ha-calories-buddy-card", HaCaloriesBuddyCard);
}
if (!customElements.get("ha-calories-buddy-card-editor")) {
  customElements.define("ha-calories-buddy-card-editor", HaCaloriesBuddyCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "ha-calories-buddy-card")) {
  window.customCards.push({
    type: "ha-calories-buddy-card",
    name: "HA Calories Buddy",
    description: "Personal AI Dietitian, Food Diary, Meal Planner, and Barcode Scanner",
  });
}
