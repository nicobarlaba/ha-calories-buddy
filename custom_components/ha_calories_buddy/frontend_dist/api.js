/** WebSocket API client for ha-calories-buddy Lovelace card */

export class CaloriesBuddyApi {
  constructor(hass) {
    this._hass = hass;
  }

  setHass(hass) {
    this._hass = hass;
  }

  call(command, payload = {}) {
    return this._hass.callWS({
      type: `ha_calories_buddy/${command}`,
      ...payload,
    });
  }

  listPersons() {
    return this.call("list_persons");
  }

  getDashboard(personId, dateStr = null) {
    const payload = {};
    if (personId) payload.person_id = personId;
    if (dateStr) payload.date = dateStr;
    return this.call("dashboard", payload);
  }

  logText(personId, text, dateStr = null, mealSlot = null) {
    const payload = { text };
    if (personId) payload.person_id = personId;
    if (dateStr) payload.date = dateStr;
    if (mealSlot) payload.meal_slot = mealSlot;
    return this.call("log_text", payload);
  }

  logItem(personId, itemPayload) {
    return this.call("log_item", {
      ...(personId ? { person_id: personId } : {}),
      ...itemPayload,
    });
  }

  confirmMeal(personId, logId, promoteToVault = false) {
    return this.call("confirm_meal", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      promote_to_vault: promoteToVault,
    });
  }

  updateMealItem(personId, logId, itemId, quantity, unit, state) {
    return this.call("update_meal_item", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      item_id: itemId,
      quantity,
      unit,
      state,
    });
  }

  deleteMeal(personId, logId) {
    return this.call("delete_meal", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
    });
  }

  restoreMeal(personId, logId) {
    return this.call("restore_meal", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
    });
  }

  searchFoods(personId, query, includeExternal = true) {
    return this.call("search_foods", {
      ...(personId ? { person_id: personId } : {}),
      query,
      include_external: includeExternal,
    });
  }

  lookupBarcode(personId, barcode) {
    return this.call("lookup_barcode", {
      ...(personId ? { person_id: personId } : {}),
      barcode,
    });
  }

  saveFood(personId, foodPayload) {
    return this.call("save_food", {
      ...(personId ? { person_id: personId } : {}),
      ...foodPayload,
    });
  }

  logWeight(personId, weightKg) {
    return this.call("log_weight", {
      ...(personId ? { person_id: personId } : {}),
      weight_kg: weightKg,
    });
  }
}
