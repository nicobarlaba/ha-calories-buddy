/** HA Calories Buddy Custom Lovelace Card v0.3.0 (Zero-Build Modular ES Web Component) */
import { CaloriesBuddyApi } from "./api.js";
import { openBarcodeScannerModal } from "./components/barcode-scanner.js";
import { t } from "./i18n/index.js";
import { CARD_STYLES } from "./styles.js";
import { escapeHtml } from "./utils.js";
import { renderPlanView } from "./views/plan-view.js";
import { renderPreferencesView } from "./views/preferences-view.js";
import { renderShoppingView } from "./views/shopping-view.js";
import { renderTodayView } from "./views/today-view.js";
import { renderTrendsView } from "./views/trends-view.js";
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
      subTab: "foods",
      query: "",
      category: "all",
      foods: [],
      totalCount: 0,
      offset: 0,
      limit: 20,
      showAddForm: false,
      overrideFood: null,
    };
    this._recipeState = {
      loaded: false,
      query: "",
      recipes: [],
      totalCount: 0,
      showEditor: false,
      editingRecipeId: null,
      draft: null,
      quickLogRecipeId: null,
    };
    this._planState = {
      subTab: "menu",
      plan: null,
      selectedDate: null,
      showGenerateForm: false,
      showSuggestForm: false,
      suggestions: [],
      suggestMeta: null,
      suggestBudgetMode: "auto",
      swappingItemId: null,
    };
    this._shoppingState = {
      loaded: false,
      draft: null,
      todoEntity: null,
      startDate: null,
      endDate: null,
      includeHousehold: false,
      lastCommitResult: null,
    };
    this._workoutState = {
      loaded: false,
      data: null,
      showForm: false,
    };
    this._trendsState = {
      loaded: false,
      days: 14,
      weightMetric: "weight",
      summary: null,
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
      infoToast: null,
      toastLogId: null,
      editingItemId: null,
      editReplacementFood: null,
      addingToLogId: null,
      addSelectedFood: null,
      showManualWeightFallback: false,
      selectedAiSlot: null,
      macroPieMode: null,
    };
    this._initialized = false;
    this._eventUnsub = null;
    this._subscribedConn = null;
    this._refreshTimer = null;
    this._toastTimer = null;
    this._lastSensorStamp = "";
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    this._unsubscribeEvents();
  }

  _showInfoToast(message) {
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._uiState.infoToast = message;
    this._render();
    this._toastTimer = setTimeout(() => {
      this._toastTimer = null;
      this._uiState.infoToast = null;
      this._render();
    }, 4000);
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
      if (
        !key.startsWith("sensor.calories_buddy_") &&
        !key.startsWith("switch.calories_buddy_")
      ) {
        continue;
      }
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
      if (this._activeTab === "plan") {
        if (this._planState.subTab === "workouts") {
          await this._loadWorkouts();
        } else if (this._planState.plan) {
          await this._loadPlan();
        }
      } else if (this._activeTab === "shopping" && this._shoppingState.loaded) {
        await this._loadShopping();
      } else if (this._activeTab === "trends" && this._trendsState.loaded) {
        await this._loadTrends(this._trendsState.days);
      } else if (this._activeTab === "vault" && this._vaultState.subTab === "recipes") {
        await this._loadRecipes(this._recipeState.query);
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

  async _loadRecipes(query = "") {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.listRecipes(this._activePersonId, query, {
        limit: 50,
        offset: 0,
      });
      this._recipeState.loaded = true;
      this._recipeState.query = query;
      this._recipeState.recipes = resp.recipes || [];
      this._recipeState.totalCount = Number(
        resp.total_count ?? this._recipeState.recipes.length
      );
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load recipes";
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

  async _loadShopping() {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.getShoppingDraft(this._activePersonId, true);
      this._shoppingState.loaded = true;
      this._shoppingState.draft = resp.draft || null;
      this._shoppingState.todoEntity =
        resp.todo_entity || this._dashboard?.person?.todo_entity || null;
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load shopping list";
      this._render();
    }
  }

  async _loadWorkouts() {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.getWorkouts(this._activePersonId);
      this._workoutState.loaded = true;
      this._workoutState.data = resp || null;
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load workouts";
      this._render();
    }
  }

  async _loadTrends(days = this._trendsState.days || 14) {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.getTrends(this._activePersonId, days);
      this._trendsState.loaded = true;
      this._trendsState.days = resp?.trends?.days || days;
      this._trendsState.summary = resp?.trends || null;
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Failed to load trends";
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

    const isTrainDay = Boolean(this._dashboard?.workouts?.is_training_day);
    const trainSource = this._dashboard?.workouts?.source || "rest";

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="header">
          <div class="header-title">
            <span class="header-emblem">🥗</span>
            <span>${escapeHtml(cardTitle)}</span>
          </div>
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
            ${
              this._dashboard
                ? `<button type="button" class="chip-btn ${isTrainDay ? "active" : ""}" id="btn-header-training" title="${escapeHtml(t(lang, `training_source_${trainSource}`) || trainSource)}">${isTrainDay ? "🏋️" : "🧘"}</button>`
                : ""
            }
            <button type="button" class="icon-btn ${this._activeTab === "preferences" ? "active" : ""}" id="btn-prefs-tab" title="${escapeHtml(t(lang, "tab_preferences"))}">⚙️</button>
            <button type="button" class="icon-btn" id="btn-refresh-card" title="${escapeHtml(t(lang, "refresh_btn"))}" ${this._uiState.busy ? "disabled" : ""}>↻</button>
          </div>
        </div>

        ${this._uiState.error ? `<div class="error-banner">${escapeHtml(this._uiState.error)}</div>` : ""}

        ${
          this._uiState.infoToast
            ? `<div class="toast success-toast"><span>✅ ${escapeHtml(this._uiState.infoToast)}</span></div>`
            : ""
        }

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
            <button type="button" class="tab-btn ${this._activeTab === "shopping" ? "active" : ""}" data-tab="shopping">${escapeHtml(t(lang, "tab_shopping"))}</button>
            <button type="button" class="tab-btn ${this._activeTab === "vault" ? "active" : ""}" data-tab="vault">${escapeHtml(t(lang, "tab_vault"))}</button>
            <button type="button" class="tab-btn ${this._activeTab === "trends" ? "active" : ""}" data-tab="trends">${escapeHtml(t(lang, "tab_trends"))}</button>
          </div>
          <div id="view-host"></div>
        `
        }
        <div id="barcode-modal-host"></div>
      </ha-card>
    `;

    this.shadowRoot.querySelector("#btn-header-training")?.addEventListener("click", async () => {
      if (!this._api || !this._activePersonId) return;
      await this._withBusy(async () => {
        await this._api.setTrainingDay(this._activePersonId, {
          is_training_day: !isTrainDay,
        });
        await this._loadDashboard();
        if (this._activeTab === "plan" && this._planState.subTab === "workouts") {
          await this._loadWorkouts();
        }
      });
    });

    this.shadowRoot.querySelector("#btn-prefs-tab")?.addEventListener("click", async () => {
      this._activeTab =
        this._activeTab === "preferences" ? "today" : "preferences";
      if (this._activeTab === "preferences" && !this._prefState.loaded) {
        await this._loadPreferences();
      } else {
        this._render();
      }
    });

    this.shadowRoot.querySelector("#btn-refresh-card")?.addEventListener("click", async () => {
      await this._loadDashboard();
      if (this._activeTab === "plan") {
        if (this._planState.subTab === "workouts") {
          await this._loadWorkouts();
        } else {
          await this._loadPlan();
        }
      } else if (this._activeTab === "shopping") {
        await this._loadShopping();
      } else if (this._activeTab === "trends") {
        await this._loadTrends(this._trendsState.days);
      } else if (this._activeTab === "vault") {
        if (this._vaultState.subTab === "recipes") {
          await this._loadRecipes(this._recipeState.query);
        } else {
          await this._loadVault(this._vaultState.query, this._vaultState.category, this._vaultState.offset);
        }
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
      this._shoppingState.loaded = false;
      this._shoppingState.draft = null;
      this._shoppingState.lastCommitResult = null;
      this._workoutState.loaded = false;
      this._workoutState.data = null;
      this._trendsState.loaded = false;
      this._trendsState.summary = null;
      this._prefState.loaded = false;
      this._recipeState.loaded = false;
      this._recipeState.quickLogRecipeId = null;
      await this._loadDashboard();
      if (this._activeTab === "vault") {
        if (this._vaultState.subTab === "recipes") {
          await this._loadRecipes(this._recipeState.query);
        } else {
          await this._loadVault(this._vaultState.query, this._vaultState.category, 0);
        }
      } else if (this._activeTab === "plan") {
        if (this._planState.subTab === "workouts") {
          await this._loadWorkouts();
        } else {
          await this._loadPlan();
        }
      } else if (this._activeTab === "shopping") {
        await this._loadShopping();
      } else if (this._activeTab === "trends") {
        await this._loadTrends(this._trendsState.days);
      } else if (this._activeTab === "preferences") {
        await this._loadPreferences();
      }
    });

    this.shadowRoot.querySelector("#undo-delete-btn")?.addEventListener("click", async () => {
      const rawToast = this._uiState.toastLogId;
      this._uiState.toastLogId = null;
      if (rawToast) {
        const logIds = String(rawToast).split(",").filter(Boolean);
        await this._withBusy(async () => {
          for (const lid of logIds) {
            await this._api.restoreMeal(this._activePersonId, lid);
          }
          await this._loadDashboard();
          if (this._planState.plan) await this._loadPlan();
        });
      }
    });

    this.shadowRoot.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        this._activeTab = btn.getAttribute("data-tab");
        if (this._activeTab === "vault") {
          if (this._vaultState.subTab === "recipes" && !this._recipeState.loaded) {
            await this._loadRecipes(this._recipeState.query);
          } else if (this._vaultState.subTab !== "recipes" && this._vaultState.foods.length === 0) {
            await this._loadVault("", "all", 0);
          } else {
            this._render();
          }
        } else if (this._activeTab === "plan") {
          if (this._planState.subTab === "workouts") {
            await this._loadWorkouts();
          } else {
            await this._loadPlan();
          }
        } else if (this._activeTab === "shopping") {
          await this._loadShopping();
        } else if (this._activeTab === "trends") {
          await this._loadTrends(this._trendsState.days);
        } else if (this._activeTab === "preferences" && !this._prefState.loaded) {
          await this._loadPreferences();
        } else {
          this._render();
        }
      });
    });

    const viewHost = this.shadowRoot.querySelector("#view-host");
    if (!viewHost || !this._dashboard) return;

    const openBarcodeForRecipe = (onPicked) => {
      openBarcodeScannerModal(
        this.shadowRoot.querySelector("#barcode-modal-host"),
        {
          language: lang,
          api: this._api,
          personId: this._activePersonId,
          mealSlots: this._dashboard?.person?.meal_slots || [],
          pickOnly: true,
        },
        {
          onSelectFood: async (food) => {
            if (food) await onPicked?.(food);
          },
        }
      );
    };

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
            } else {
              this._vaultState.foods = [];
            }
            this._showInfoToast(t(this._uiState.language, "saved_global_ok"));
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
        onDeleteMeal: (logIds) =>
          this._withBusy(async () => {
            const ids = Array.isArray(logIds) ? logIds : [logIds];
            for (const lid of ids) {
              await this._api.deleteMeal(this._activePersonId, lid);
            }
            this._uiState.toastLogId = ids.join(",");
            await this._loadDashboard();
          }),
        onUpdateMealSlot: (logIds, mealSlot) =>
          this._withBusy(async () => {
            const ids = Array.isArray(logIds) ? logIds : [logIds];
            for (const lid of ids) {
              await this._api.updateMealSlot(this._activePersonId, lid, mealSlot);
            }
            await this._loadDashboard();
          }),
        onStartEditItem: (itemId) => {
          this._uiState.editingItemId = itemId;
          this._uiState.editReplacementFood = null;
          if (itemId) {
            this._uiState.addingToLogId = null;
            this._uiState.addSelectedFood = null;
          }
          this._render();
        },
        onSelectEditFood: (food) => {
          this._uiState.editReplacementFood = food || null;
          this._render();
        },
        onStartAddItem: (logId) => {
          this._uiState.addingToLogId = logId || null;
          this._uiState.addSelectedFood = null;
          if (logId) {
            this._uiState.editingItemId = null;
            this._uiState.editReplacementFood = null;
          }
          this._render();
        },
        onSelectAddFood: (food) => {
          this._uiState.addSelectedFood = food || null;
          this._render();
        },
        onAddMealItem: (logId, itemPayload) =>
          this._withBusy(async () => {
            await this._api.addMealItem(this._activePersonId, logId, itemPayload);
            this._uiState.addingToLogId = null;
            this._uiState.addSelectedFood = null;
            await this._loadDashboard();
          }),
        onUpdateMealItem: (logId, itemId, qty, unit, state, foodId = null) =>
          this._withBusy(async () => {
            await this._api.updateMealItem(
              this._activePersonId,
              logId,
              itemId,
              qty,
              unit,
              state,
              foodId
            );
            this._uiState.editingItemId = null;
            this._uiState.editReplacementFood = null;
            await this._loadDashboard();
          }),
        onDeleteMealItem: (logId, itemId) =>
          this._withBusy(async () => {
            const resp = await this._api.deleteMealItem(
              this._activePersonId,
              logId,
              itemId
            );
            if (this._uiState.editingItemId === itemId) {
              this._uiState.editingItemId = null;
              this._uiState.editReplacementFood = null;
            }
            if (resp?.meal_deleted) {
              this._uiState.toastLogId = logId;
            }
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
            this._uiState.showManualWeightFallback = false;
            await this._loadDashboard();
          }),
        onToggleManualWeightFallback: () => {
          this._uiState.showManualWeightFallback = !this._uiState.showManualWeightFallback;
          this._render();
        },
      });
    } else if (this._activeTab === "plan") {
      renderPlanView(
        viewHost,
        this._planState,
        this._dashboard,
        this._uiState,
        {
          onSwitchPlanSubTab: async (subTab) => {
            this._planState.subTab = subTab === "workouts" ? "workouts" : "menu";
            if (this._planState.subTab === "workouts") {
              await this._loadWorkouts();
            } else if (!this._planState.plan) {
              await this._loadPlan();
            } else {
              this._render();
            }
          },
          onToggleWorkoutForm: () => {
            this._workoutState.showForm = !this._workoutState.showForm;
            this._render();
          },
          onSetTrainingDay: (opts) =>
            this._withBusy(async () => {
              await this._api.setTrainingDay(this._activePersonId, opts);
              await this._loadDashboard();
              await this._loadWorkouts();
            }),
          onSaveWorkout: (payload) =>
            this._withBusy(async () => {
              await this._api.saveWorkoutItem(this._activePersonId, payload);
              this._workoutState.showForm = false;
              await this._loadDashboard();
              await this._loadWorkouts();
              this._showInfoToast(t(this._uiState.language, "workouts_saved_ok"));
            }),
          onDeleteWorkout: (scheduleId) =>
            this._withBusy(async () => {
              await this._api.deleteWorkoutItem(this._activePersonId, scheduleId);
              await this._loadDashboard();
              await this._loadWorkouts();
              this._showInfoToast(t(this._uiState.language, "workouts_deleted_ok"));
            }),
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
        },
        this._workoutState
      );
    } else if (this._activeTab === "shopping") {
      renderShoppingView(
        viewHost,
        this._shoppingState,
        this._dashboard,
        this._uiState,
        this._persons.length,
        {
          onUpdateRange: (startDate, endDate) => {
            if (startDate !== null) this._shoppingState.startDate = startDate;
            if (endDate !== null) this._shoppingState.endDate = endDate;
          },
          onToggleHousehold: (val) => {
            this._shoppingState.includeHousehold = Boolean(val);
          },
          onGenerateDraft: (opts) =>
            this._withBusy(async () => {
              this._shoppingState.lastCommitResult = null;
              const resp = await this._api.generateShoppingDraft(
                this._activePersonId,
                opts
              );
              this._shoppingState.draft = resp.draft || null;
              this._shoppingState.todoEntity =
                resp.todo_entity || this._shoppingState.todoEntity;
            }),
          onUpdateItem: (draftId, itemId, patch) =>
            this._withBusy(async () => {
              const resp = await this._api.updateShoppingItem(
                this._activePersonId,
                draftId,
                itemId,
                patch
              );
              this._shoppingState.draft = resp.draft || this._shoppingState.draft;
            }),
          onAddItem: (payload) =>
            this._withBusy(async () => {
              const resp = await this._api.addShoppingItem(
                this._activePersonId,
                payload
              );
              this._shoppingState.draft = resp.draft || this._shoppingState.draft;
            }),
          onDeleteItem: (draftId, itemId) =>
            this._withBusy(async () => {
              const resp = await this._api.deleteShoppingItem(
                this._activePersonId,
                draftId,
                itemId
              );
              this._shoppingState.draft = resp.draft || this._shoppingState.draft;
            }),
          onCommitDraft: (draftId) =>
            this._withBusy(async () => {
              const resp = await this._api.commitShoppingDraft(
                this._activePersonId,
                { draft_id: draftId }
              );
              this._shoppingState.draft = resp.draft || this._shoppingState.draft;
              this._shoppingState.lastCommitResult = resp;
            }),
        }
      );
    } else if (this._activeTab === "trends") {
      renderTrendsView(viewHost, this._trendsState, this._dashboard, this._uiState, {
        onChangeDays: async (days) => {
          this._trendsState.days = days;
          await this._loadTrends(days);
        },
        onChangeMetric: (metric) => {
          this._trendsState.weightMetric = metric;
          this._render();
        },
      });
    } else if (this._activeTab === "vault") {
      renderVaultView(
        viewHost,
        this._vaultState,
        this._uiState,
        {
          api: this._api,
          onSwitchVaultSubTab: async (subTab) => {
            this._vaultState.subTab = subTab === "recipes" ? "recipes" : "foods";
            if (this._vaultState.subTab === "recipes" && !this._recipeState.loaded) {
              await this._loadRecipes(this._recipeState.query);
            } else if (
              this._vaultState.subTab === "foods" &&
              this._vaultState.foods.length === 0
            ) {
              await this._loadVault(this._vaultState.query, this._vaultState.category, 0);
            } else {
              this._render();
            }
          },
          onSearchVault: (q, cat = "all", off = 0) => this._loadVault(q, cat, off),
          onOpenBarcode: openBarcode,
          onScanRecipeBarcode: openBarcodeForRecipe,
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
          onSearchRecipes: (q) => this._loadRecipes(q),
          onToggleRecipeEditor: (open) => {
            this._recipeState.showEditor = Boolean(open);
            this._recipeState.editingRecipeId = null;
            this._recipeState.draft = this._recipeState.showEditor
              ? {
                  recipe_id: null,
                  name: "",
                  category: "other",
                  servings: 1,
                  serving_unit: "serving",
                  final_weight_g: "",
                  allow_in_planner: false,
                  notes: "",
                  ingredients: [],
                }
              : null;
            this._render();
          },
          onEditRecipe: (rec) => {
            this._recipeState.editingRecipeId = rec.recipe_id;
            this._recipeState.showEditor = true;
            this._recipeState.quickLogRecipeId = null;
            this._recipeState.draft = {
              recipe_id: rec.recipe_id,
              name: rec.name || "",
              category: rec.category || "other",
              servings: rec.servings || 1,
              serving_unit: rec.serving_unit || "serving",
              final_weight_g: rec.final_weight_g || "",
              allow_in_planner: Boolean(rec.allow_in_planner),
              notes: rec.notes || "",
              ingredients: (rec.ingredients || []).map((ing) => {
                const g = Math.max(1, Number(ing.grams || 100));
                const factor = 100 / g;
                const m = ing.macros || {};
                return {
                  food_id: ing.food_id || null,
                  name: ing.display_name || "",
                  raw_name: null,
                  matched_brand: null,
                  category: "other",
                  food_portions: {},
                  person_portions: {},
                  quantity: ing.input_quantity || g,
                  unit: ing.input_unit || "g",
                  state: ing.input_state || "unspecified",
                  base_quantity: ing.input_quantity || g,
                  grams_estimate: ing.grams || g,
                  ref_state: ing.input_state || "unspecified",
                  yield_factor: 1.0,
                  macros_per_100g: {
                    kcal: (Number(m.kcal) || 0) * factor,
                    protein_g: (Number(m.protein_g) || 0) * factor,
                    carbs_g: (Number(m.carbs_g) || 0) * factor,
                    fat_g: (Number(m.fat_g) || 0) * factor,
                    fiber_g: (Number(m.fiber_g) || 0) * factor,
                  },
                };
              }),
            };
            this._render();
          },
          onParseRecipeText: (text) =>
            this._withBusy(async () => {
              const resp = await this._api.parseRecipeText(this._activePersonId, text);
              const parsed = resp?.draft;
              if (parsed) {
                const cur = this._recipeState.draft || {};
                this._recipeState.draft = {
                  recipe_id: cur.recipe_id || null,
                  name: parsed.name || cur.name || "",
                  category: parsed.category || "other",
                  servings: parsed.servings || 1,
                  serving_unit: parsed.serving_unit || "serving",
                  final_weight_g: parsed.final_weight_g || "",
                  allow_in_planner: Boolean(cur.allow_in_planner),
                  notes: parsed.notes || "",
                  ingredients: (parsed.ingredients || []).map((i) => ({
                    food_id: i.food_id || null,
                    name: i.name || "",
                    raw_name: i.raw_name || null,
                    matched_brand: i.matched_brand || null,
                    category: i.category || "other",
                    food_portions: i.food_portions || {},
                    person_portions: i.person_portions || {},
                    quantity: i.quantity || 100,
                    unit: i.unit || "g",
                    state: i.state || "unspecified",
                    base_quantity: i.quantity || 100,
                    grams_estimate: i.grams_estimate || 100,
                    macros_per_100g: i.macros_per_100g || null,
                    yield_factor: i.yield_factor || 1.0,
                    ref_state: i.ref_state || "ready_to_eat",
                  })),
                };
              }
            }),
          onSaveRecipe: (payload) =>
            this._withBusy(async () => {
              await this._api.saveRecipe(this._activePersonId, payload);
              this._recipeState.showEditor = false;
              this._recipeState.editingRecipeId = null;
              this._recipeState.draft = null;
              this._vaultState.foods = [];
              await this._loadRecipes(this._recipeState.query);
              this._showInfoToast(t(this._uiState.language, "recipe_saved_ok"));
            }),
          onDeleteRecipe: (recipeId) =>
            this._withBusy(async () => {
              await this._api.deleteRecipe(this._activePersonId, recipeId);
              this._vaultState.foods = [];
              await this._loadRecipes(this._recipeState.query);
              await this._loadDashboard();
              this._showInfoToast(t(this._uiState.language, "recipe_deleted_ok"));
            }),
          onToggleQuickLogRecipe: (recipeId) => {
            this._recipeState.quickLogRecipeId =
              this._recipeState.quickLogRecipeId === recipeId ? null : recipeId;
            this._render();
          },
          onQuickLogRecipe: (itemPayload) =>
            this._withBusy(async () => {
              await this._api.logItem(this._activePersonId, itemPayload);
              this._recipeState.quickLogRecipeId = null;
              await this._loadDashboard();
              if (this._planState.plan) await this._loadPlan();
              this._showInfoToast(t(this._uiState.language, "recipe_logged_ok"));
            }),
        },
        this._recipeState,
        this._dashboard
      );
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
