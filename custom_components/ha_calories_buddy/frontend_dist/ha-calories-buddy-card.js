/** HA Calories Buddy Custom Lovelace Card (Zero-Build Modular ES Web Component) */
import { CaloriesBuddyApi } from "./api.js";
import { openBarcodeScannerModal } from "./components/barcode-scanner.js";
import { t } from "./i18n/index.js";
import { CARD_STYLES } from "./styles.js";
import { escapeHtml } from "./utils.js";
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
    this._vaultState = { query: "", foods: [], showAddForm: false, overrideFood: null };
    this._uiState = { language: "en", busy: false, error: null, toastLogId: null, editingItemId: null };
    this._initialized = false;
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

    if (firstSet && !this._initialized) {
      this._initialized = true;
      this._bootstrap();
    }
  }

  getCardSize() {
    return 6;
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

  async _loadVault(query = "") {
    if (!this._api || !this._activePersonId) return;
    try {
      const resp = await this._api.searchFoods(this._activePersonId, query, query.length >= 2);
      this._vaultState.query = query;
      this._vaultState.foods = resp.foods || [];
      this._render();
    } catch (err) {
      this._uiState.error = err?.message || "Search failed";
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
            <button type="button" class="tab-btn ${this._activeTab === "vault" ? "active" : ""}" data-tab="vault">${escapeHtml(t(lang, "tab_vault"))}</button>
          </div>
          <div id="view-host"></div>
        `
        }
        <div id="barcode-modal-host"></div>
      </ha-card>
    `;

    this.shadowRoot.querySelector("#person-select")?.addEventListener("change", async (ev) => {
      this._activePersonId = ev.target.value;
      this._uiState.editingItemId = null;
      await this._loadDashboard();
      if (this._activeTab === "vault") {
        await this._loadVault(this._vaultState.query);
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
          await this._loadVault("");
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
              await this._loadVault(this._vaultState.query);
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
        onLogText: (text) =>
          this._withBusy(async () => {
            await this._api.logText(this._activePersonId, text);
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
        onLogWeight: (weightKg) =>
          this._withBusy(async () => {
            await this._api.logWeight(this._activePersonId, weightKg);
            await this._loadDashboard();
          }),
      });
    } else if (this._activeTab === "vault") {
      renderVaultView(viewHost, this._vaultState, this._uiState, {
        onSearchVault: (q) => this._loadVault(q),
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
            await this._loadVault(this._vaultState.query);
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
    description: "Personal AI Dietitian, Food Diary, and Barcode Scanner",
  });
}
