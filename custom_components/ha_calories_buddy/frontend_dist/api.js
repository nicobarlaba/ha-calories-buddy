/** WebSocket API client for ha-calories-buddy Lovelace card v0.3.0 */

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

  addMealItem(personId, logId, itemPayload) {
    return this.call("add_meal_item", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      ...itemPayload,
    });
  }

  updateMealItem(personId, logId, itemId, quantity, unit, state, foodId = null) {
    const payload = {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      item_id: itemId,
      quantity,
      unit,
      state,
    };
    if (foodId) payload.food_id = foodId;
    return this.call("update_meal_item", payload);
  }

  deleteMealItem(personId, logId, itemId) {
    return this.call("delete_meal_item", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      item_id: itemId,
    });
  }

  updateMealSlot(personId, logId, mealSlot) {
    return this.call("update_meal_slot", {
      ...(personId ? { person_id: personId } : {}),
      log_id: logId,
      meal_slot: mealSlot,
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

  searchFoods(personId, query = "", includeExternal = true, opts = {}) {
    const payload = {
      ...(personId ? { person_id: personId } : {}),
      query,
      include_external: includeExternal,
    };
    if (opts.category) payload.category = opts.category;
    if (opts.limit !== undefined) payload.limit = opts.limit;
    if (opts.offset !== undefined) payload.offset = opts.offset;
    return this.call("search_foods", payload);
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

  getPlan(personId, referenceDate = null) {
    const payload = { ...(personId ? { person_id: personId } : {}) };
    if (referenceDate) payload.reference_date = referenceDate;
    return this.call("get_plan", payload);
  }

  generatePlan(personId, opts = {}) {
    return this.call("generate_plan", {
      ...(personId ? { person_id: personId } : {}),
      ...opts,
    });
  }

  suggestMeal(personId, opts = {}) {
    return this.call("suggest_meal", {
      ...(personId ? { person_id: personId } : {}),
      budget_mode: opts.budget_mode || "auto",
      ...opts,
    });
  }

  logSuggestion(personId, suggestion, scale = 1.0, dateStr = null) {
    const payload = {
      ...(personId ? { person_id: personId } : {}),
      suggestion,
      scale,
    };
    if (dateStr) payload.date = dateStr;
    return this.call("log_suggestion", payload);
  }

  swapPlanMeal(personId, planItemId, instruction = "") {
    return this.call("swap_plan_meal", {
      ...(personId ? { person_id: personId } : {}),
      plan_item_id: planItemId,
      instruction,
    });
  }

  promotePlanMeal(personId, planItemId, scale = 1.0) {
    return this.call("promote_plan_meal", {
      ...(personId ? { person_id: personId } : {}),
      plan_item_id: planItemId,
      scale,
    });
  }

  rebalanceToday(personId, opts = {}) {
    return this.call("rebalance_today", {
      ...(personId ? { person_id: personId } : {}),
      ...opts,
    });
  }

  getPreferences(personId) {
    return this.call("get_preferences", {
      ...(personId ? { person_id: personId } : {}),
    });
  }

  upsertPreference(personId, prefPayload) {
    return this.call("upsert_preference", {
      ...(personId ? { person_id: personId } : {}),
      ...prefPayload,
    });
  }

  deletePreference(personId, prefId) {
    return this.call("delete_preference", {
      ...(personId ? { person_id: personId } : {}),
      pref_id: prefId,
    });
  }

  setMealFeedback(personId, title, rating, notes = "") {
    return this.call("set_meal_feedback", {
      ...(personId ? { person_id: personId } : {}),
      title,
      rating,
      notes,
    });
  }

  listRecipes(personId, query = "", opts = {}) {
    const payload = {
      ...(personId ? { person_id: personId } : {}),
      query,
    };
    if (opts.limit !== undefined) payload.limit = opts.limit;
    if (opts.offset !== undefined) payload.offset = opts.offset;
    return this.call("list_recipes", payload);
  }

  saveRecipe(personId, recipePayload) {
    return this.call("save_recipe", {
      ...(personId ? { person_id: personId } : {}),
      ...recipePayload,
    });
  }

  deleteRecipe(personId, recipeId) {
    return this.call("delete_recipe", {
      ...(personId ? { person_id: personId } : {}),
      recipe_id: recipeId,
    });
  }

  parseRecipeText(personId, text) {
    return this.call("parse_recipe_text", {
      ...(personId ? { person_id: personId } : {}),
      text,
    });
  }

  getShoppingDraft(personId, includeCommitted = true) {
    return this.call("get_shopping_draft", {
      ...(personId ? { person_id: personId } : {}),
      include_committed: includeCommitted,
    });
  }

  generateShoppingDraft(personId, opts = {}) {
    return this.call("generate_shopping_draft", {
      ...(personId ? { person_id: personId } : {}),
      ...opts,
    });
  }

  updateShoppingItem(personId, draftId, draftItemId, patch = {}) {
    return this.call("update_shopping_item", {
      ...(personId ? { person_id: personId } : {}),
      draft_id: draftId,
      draft_item_id: draftItemId,
      ...patch,
    });
  }

  addShoppingItem(personId, itemPayload) {
    return this.call("add_shopping_item", {
      ...(personId ? { person_id: personId } : {}),
      ...itemPayload,
    });
  }

  deleteShoppingItem(personId, draftId, draftItemId) {
    return this.call("delete_shopping_item", {
      ...(personId ? { person_id: personId } : {}),
      draft_id: draftId,
      draft_item_id: draftItemId,
    });
  }

  commitShoppingDraft(personId, opts = {}) {
    return this.call("commit_shopping_draft", {
      ...(personId ? { person_id: personId } : {}),
      ...opts,
    });
  }

  getTrends(personId, days = 30) {
    return this.call("get_trends", {
      ...(personId ? { person_id: personId } : {}),
      days,
    });
  }

  getWorkouts(personId) {
    return this.call("get_workouts", {
      ...(personId ? { person_id: personId } : {}),
    });
  }

  saveWorkoutItem(personId, itemPayload) {
    return this.call("save_workout_item", {
      ...(personId ? { person_id: personId } : {}),
      ...itemPayload,
    });
  }

  deleteWorkoutItem(personId, scheduleId) {
    return this.call("delete_workout_item", {
      ...(personId ? { person_id: personId } : {}),
      schedule_id: scheduleId,
    });
  }

  setTrainingDay(personId, opts = {}) {
    return this.call("set_training_day", {
      ...(personId ? { person_id: personId } : {}),
      ...opts,
    });
  }
}
