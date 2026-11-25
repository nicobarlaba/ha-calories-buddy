# HA Calories Buddy — User & Operator Manual

**Version:** `0.2.2` (Phase 2 — *The Dietitian & Chef* + Fiber, Vault Pagination & HA AI Task)  
**Minimum Home Assistant:** `2026.10.0` (verified on `2026.11.3` / Python `3.14`)  
**Languages:** English (`en`) & Italian (`it`)

---

## Table of Contents

1. [Prerequisites & Compatibility](#1-prerequisites--compatibility)
2. [Installation](#2-installation)
3. [First-Time Setup Wizard](#3-first-time-setup-wizard)
4. [LLM Provider Configuration Guide](#4-llm-provider-configuration-guide)
5. [Biophysical Sensor Mapping (Garmin & Smart Scales)](#5-biophysical-sensor-mapping-garmin--smart-scales)
6. [Physiology vs. Manual Target Modes](#6-physiology-vs-manual-target-modes)
7. [Daily Usage: Custom Lovelace Card](#7-daily-usage-custom-lovelace-card)
8. [Daily Usage: Voice Logging with Home Assistant Assist](#8-daily-usage-voice-logging-with-home-assistant-assist)
9. [Barcode Scanner & Two-Tier Food Vault](#9-barcode-scanner--two-tier-food-vault)
10. [Calendars, Sensors & Automations](#10-calendars-sensors--automations)
11. [Home Assistant Services Reference](#11-home-assistant-services-reference)
12. [Lovelace YAML Mode Setup](#12-lovelace-yaml-mode-setup)
13. [Troubleshooting & FAQ](#13-troubleshooting--faq)
14. [Privacy, AI Disclosure & Medical Disclaimer](#14-privacy-ai-disclosure--medical-disclaimer)

---

## 1. Prerequisites & Compatibility

- **Home Assistant:** `2026.10.0` or newer (compatible with Home Assistant OS, Supervised, Container, and Core).
- **Lovelace Mode:** Both **Storage Mode** (default — Custom Card auto-registers automatically) and **YAML Mode** (manual resource line provided below) are supported.
- **Live Camera Barcode Scanning:** Requires accessing Home Assistant over **HTTPS** (e.g., Nabu Casa, Cloudflare Tunnel, or reverse proxy) or `localhost` with browser `BarcodeDetector` support. Manual EAN/UPC barcode entry works in all environments, including plain HTTP LAN access.
- **LLM Optional:** Natural-language meal parsing requires either a cloud LLM API key or a local LLM server (Ollama / LM Studio). Manual logging, Food Vault search, barcode scanning, sensors, calendars, and physiological target math work **100% offline** with provider set to `none`.

---

## 2. Installation

### Option A — Home Assistant Marketplace / HACS (Recommended)

1. Open **Settings → Marketplace** (or **HACS** on older installs).
2. Open the top-right `⋮` menu → **Custom repositories**.
3. Add repository URL: `https://github.com/nicobarlaba/ha-calories-buddy`
4. Select Category: **Integration**.
5. Click **Add**, find **HA Calories Buddy**, and click **Download / Install**.
6. **Restart Home Assistant** so the integration and its Python runtime dependencies (`pydantic`, `aiosqlite`) are loaded.

### Option B — Manual Installation

1. Copy the `custom_components/ha_calories_buddy` folder into `<ha_config>/custom_components/ha_calories_buddy`.
2. **Restart Home Assistant**.

---

## 3. First-Time Setup Wizard

`ha-calories-buddy` uses a **1 Household Hub + N Person Profiles** architecture:

### Part 1 — Household Hub Setup (First Run)

1. Go to **Settings → Devices & Services → + Add Integration**.
2. Search for **HA Calories Buddy**.
3. On first setup, the wizard opens **Household Hub & AI Configuration** (displaying the *LLM Capability & Privacy Disclosure Notice*).
4. Configure:
   - **Default Language:** `en` (English) or `it` (Italiano).
   - **Default LLM Provider:** Shared across household members unless a person overrides it (see [Section 4](#4-llm-provider-configuration-guide)).
   - **USDA FoodData Central API Key (optional):** Enables US database fallback alongside Open Food Facts.
   - **Household Food Baseline Notes (optional):** Shared baseline household notes/preferences.
5. Submit. If an active LLM provider is selected, HA Calories Buddy performs a live connectivity check before saving.

### Part 2 — Adding Person Profiles (1..N Household Members)

1. Click **+ Add Integration → HA Calories Buddy** again (and repeat once per monitored person).
2. **Step 1 — Person Identity & HA Mapping:**
   - Select the Home Assistant **`person.*`** entity (for example, `person.marco`). If that entity is linked to an HA login user, `Display Name` and `Home Assistant User ID` are auto-detected.
   - Choose `Language`: **Inherit from Hub**, `English`, or `Italiano`.
3. **Step 2 — Biometrics, Goals & TDEE Engine:**
   - Biological Sex, Birth Year, Height (`cm`), Initial Weight (`kg`), optional Body Fat `%` (enables Katch-McArdle BMR).
   - **Target Mode:** `Physiology-Guided` or `Manual / Nutritionist Plan`.
   - **Goal & Weekly Rate:** `lose_weight`, `maintain`, `recomposition`, or `gain_muscle`, plus target weekly weight change (`kg/week`, e.g., `-0.4`).
   - **TDEE Engine:** `static` or `wearable_dynamic` + Activity Level (`PAL`) + Wearable Correction Factor (default `1.0`; e.g., `0.85` to offset wearable overestimation).
4. **Step 3 (Conditional) — Manual / Nutritionist Targets:**
   - Shown only when `Target Mode` is `manual`. Enter daily `kcal`, `protein_g`, `carbs_g`, `fat_g`, plus optional Training Day overrides.
5. **Step 4 — Biophysical Sensors & Household Services:**
   - Optionally map existing HA sensors (see [Section 5](#5-biophysical-sensor-mapping-garmin--smart-scales)).
6. **Step 5 — Per-Person LLM Override (optional):**
   - Leave unchecked to inherit the Hub LLM, or enable to assign a different provider/model to this person.
7. **Step 6 — Meal Schedule & Dietary Constraints:**
   - Customize comma-separated meal slots (e.g., `Colazione, Pranzo, Spuntino, Cena`), dietary regime (`omnivore`, `mediterranean`, `vegetarian`, `vegan`, `pescatarian`, `keto`), allergies, intolerances, and excluded foods.

> **Tip:** Every setting can be edited later without recreating entities via **Settings → Devices & Services → HA Calories Buddy → Configure** (Options Flow).

---

## 4. LLM Provider Configuration Guide

| Provider Value | Endpoint / Auth | Suggested Default Model | Notes |
| :--- | :--- | :--- | :--- |
| `ha_ai_task` | Reuses an existing Home Assistant `ai_task.*` entity (e.g., `ai_task.google_ai_task_3_8`) | Managed in HA | **Recommended** if you already configured Google Generative AI, OpenAI, Anthropic, or Ollama in HA — zero duplicated API keys and no Ollama URL needed. Step 2 shows only the `ai_task.*` selector. |
| `none` | No network / no API key | — | Manual search, paginated Food Vault, barcode logging, and deterministic rebalancing remain 100% active. |
| `openai` | OpenAI API key (`https://api.openai.com/v1`) | `gpt-4o-mini` | Uses native JSON schema + automatic repair retry (default timeout `60s`, configurable). |
| `anthropic` | Anthropic API key (`https://api.anthropic.com`) | `claude-sonnet-4-5` | Uses forced structured tool output (default timeout `60s`, configurable). |
| `gemini` | Google AI Studio API key | `gemini-2.5-flash` | Uses sanitized `responseSchema` JSON mode (default timeout `60s`, configurable). |
| `ollama` | Local URL (default `http://localhost:11434`) | `qwen2.5:14b` | 100% local; default timeout is `180s` (configurable up to `600s` in Step 2) to accommodate full recipe/meal-plan generation. |
| `openai_compatible` | Custom Base URL (default `http://localhost:1234/v1`) + optional key | `local-model` | Works with **LM Studio**, OpenRouter, vLLM, Groq, and LocalAI (default timeout `180s`). |

> **Important:** The LLM **never** calculates final calories or sums macros. It extracts food names, brands, quantities, units, and raw/cooked states into a strict JSON schema; Python resolves nutrition deterministically from the Food Vault and structured databases.

---

## 5. Biophysical Sensor Mapping (Garmin & Smart Scales)

In Step 4 (or **Configure → Mapped Sensors & Services**), link Home Assistant entities using native selectors (just like the Energy Dashboard):

- **Body Weight Sensor (`sensor.*`):** Automatically converts `kg`, `lb`/`lbs`, and `g` to `kg`.
- **Body Fat % Sensor (`sensor.*`):** Accepts percentages (`3%–65%`) and switches BMR calculation to **Katch-McArdle** when valid.
- **Active Calories Burned Sensor (`sensor.*`, e.g., Garmin Connect):** Automatically converts `kcal`, `kJ`, and `Mcal`.
- **Total Calories Burned Sensor (`sensor.*`, e.g., Garmin Connect):** Preferred source for `wearable_dynamic` baseline when available.
- **Daily Steps Sensor (`sensor.*`):** Recorded into daily biometric snapshots.
- **Shopping List (`todo.*`):** Target list entity (supports sharing one household list across multiple persons).
- **Notification Service (e.g., `notify.mobile_app_phone`):** Receives compact meal logging summaries and confirmation alerts.

### Resilience Against Unavailable Sensors

Every night at `23:55` (and whenever you log weight manually), the coordinator persists a `BiometricSnapshot` to SQLite (`.storage/ha_calories_buddy.db`). If a smart scale or wearable entity temporarily goes `unavailable` or `unknown`, HA Calories Buddy safely falls back to the latest valid stored snapshot without breaking targets or sensors.

---

## 6. Physiology vs. Manual Target Modes

### BMR Equations
- **Katch-McArdle** (used automatically when valid Body Fat `%` is present):  
  $\text{BMR} = 370 + 21.6 \times \text{LBM}_{\text{kg}}$
- **Mifflin-St Jeor** (used when Body Fat `%` is unavailable):  
  $\text{BMR} = 10W + 6.25H - 5A + s$ ($s = +5$ male, $-161$ female)

### TDEE Engines (Anti-Double-Counting & Morning Budget Protection)
1. **`static`:** $\text{TDEE} = \text{BMR} \times \text{PAL}$ (`sedentary: 1.2`, `light: 1.375`, `moderate: 1.55`, `very_active: 1.725`, `athlete: 1.9`).
2. **`wearable_dynamic`:**
   - Uses a **7-day rolling average of completed past days** (`total_kcal`, or `BMR + active_kcal × correction_factor`) to establish today's morning baseline—so cumulative wearable sensors starting at `0` at midnight never make your 09:00 AM budget artificially tiny.
   - Only when today's live active expenditure exceeds your 7-day average active burn does the surplus `(today_active - baseline_active) × correction_factor` increase today's budget.
   - Falls back cleanly to the static prior until at least 3 completed past snapshots exist.

### Macro & Fiber Allocation & Safety Guardrails
- Protein is anchored to `1.8–2.0 g/kg` body weight (or automatically anchored to **Lean Body Mass** at `2.0–2.2 g/kg LBM` when body fat exceeds `25%` male / `32%` female).
- Essential fat floor (`≥ 0.65 g/kg`, target `25%` of kcal) is preserved before assigning remaining calories to carbohydrates.
- **Dietary Fiber Target:** Computed automatically at `14g per 1000 kcal` clamped to `[20g, 45g]` in Physiology mode, or explicitly configurable per rest/training day in Manual mode (`fiber_g`, `training_fiber_g`).
- Non-blocking `safety_warnings` (`excessive_deficit`, `low_calorie_floor`, `fat_floor_strained`) appear on `sensor.*_calories_target` and inside the Custom Card if configured rates are overly aggressive.

---

## 7. Daily Usage: Custom Lovelace Card

In standard Home Assistant Storage dashboards, `/ha_calories_buddy/frontend/ha-calories-buddy-card.js?v=0.2.2` is registered automatically on startup.

1. Refresh your browser (`Ctrl+Shift+R` / clear mobile app cache once).
2. Edit a dashboard → **+ Add Card** → search **HA Calories Buddy** (or add YAML `type: custom:ha-calories-buddy-card`).
3. By default, the card detects the signed-in Home Assistant user (`hass.user.id`) and displays their personal diary (a person switcher dropdown appears automatically when multiple profiles exist, or you can pin `person_id` in the visual card editor).

### Card Features (`Today` Tab)
- **Calorie Ring, Fiber Bar & Macro Split Pie Chart:** Live consumed vs. target `kcal`, Protein, Carbs, Fat, and **Fiber**, Remaining/Over-budget pill, TDEE pill, plus a compact **Macro Distribution Donut Chart** ($P \times 4$, $C \times 4$, $F \times 9$, $\text{Fiber} \times 2$) with a **Consumed / Target** toggle.
- **Quick AI Natural-Language Bar & Time-Aware Slot Selector:** Select or accept the time-of-day suggested meal slot pill (`Breakfast`, `Lunch`, `Snack`, `Dinner`), type a meal in plain English or Italian, and press Enter (e.g., *"A pranzo 80g riso basmati Scotti, 1 cucchiaio olio di oliva, 250g lenticchie cotte"*).
- **1-Tap Inline Slot Reassignment:** Each logged meal shows a slot badge (`Breakfast` / `Pranzo` / etc.) that lets you reassign the slot inline without deleting or re-logging.
- **Hybrid Confirmation Banner (`Needs Confirmation`):**
  - High-confidence logs save immediately as **Confirmed**.
  - Items with AI estimation (`llm_estimated`), raw/cooked state ambiguity (`ambiguous_state`), label Atwater mismatches (`atwater_warning`), or unknown units are saved as **Pending Confirmation**.
  - Review pending items inline: adjust grams, unit, or `Raw / Cooked / Ready to eat`, then tap **Confirm** (or **Confirm & Save Food** to promote an AI-estimated item permanently into the Food Vault).
- **Inline Edit, Undo Delete & Quick Weight Entry:** Edit any logged item's quantity/unit/state at any time, undo deletes on the toast banner, and record morning weight (`kg`) directly into SQLite snapshots.

### Meal Planning & Dynamic Rebalancing (`Plan` Tab)
- **Suggest Meal ("What should I eat now?"):** Pick a meal slot, optional craving/hint, and max cook time to generate 1–3 macro-scaled recipe options constrained by your allergies and likes/dislikes.
- **Generate 1–7 Day Plan:** Create an active personalized meal plan with optional **Meal Prep Batch Mode** (batches repeated lunches/dinners to minimize cooking). Every meal is deterministically scaled by Python's bounded algebraic solver to within `±5%` of your daily target.
- **Interactive Daily Controls:** Expand ingredients & preparation steps, swap any single meal (`Swap`), click **Eaten (`✅`)** to promote a planned dish into today's `MealLog` in 1 tap (choosing `1.0x`, `0.75x`, `1.25x`, etc.), rate dishes (`Like`, `Dislike`, `Never Suggest`), or click **Rebalance Today** to adjust dinner/snacks after an unplanned meal (with multi-day smoothing option if over budget).

### Two-Tier Dietary Profile (`Preferences` Tab)
- Manage **Global Household** rules (shared allergens/blacklist) and **Personal Overrides** (`allergy`, `intolerance`, `blacklist`, `dislike`, `like`, `love`, `preferred_brand`) enforced deterministically by HA Calories Buddy's Zero-Trust Hard-Constraint Gate before any AI plan reaches your calendar.

---

## 8. Daily Usage: Voice Logging & Planning with Home Assistant Assist

HA Calories Buddy registers a native Home Assistant Assist `llm.API` (`HA Calories Buddy`) with seven tools:
1. `log_meal(text, meal_slot?, person?)`
2. `correct_last_meal(item_name?, new_quantity?, new_unit?, new_state?, new_meal_slot?, person?)`
3. `get_today_nutrition_summary(person?)`
4. `suggest_meal(meal_slot?, hint?, max_cook_minutes?, count?, person?)`
5. `generate_meal_plan(num_days?, meal_prep_mode?, notes?, person?)`
6. `swap_plan_meal(plan_item_id, instruction?, person?)`
7. `rebalance_today(spread_days?, allow_llm_swap?, person?)`

### Enabling Assist Tools
1. Open **Settings → Voice assistants** and select your Assist pipeline / Conversation Agent (LLM-backed conversation integration).
2. Under **Control Home Assistant / Assist APIs**, enable **HA Calories Buddy**.

### Example Voice Commands
- *"At lunch I had 80g Scotti basmati rice, 1 tablespoon olive oil, and 250g cooked lentils."*
- *"A pranzo ho mangiato 80g di riso basmati Scotti, 1 cucchiaio di olio di oliva e 250g di lenticchie cotte."*
- *"Move my last logged meal to Dinner."* / *"Sposta l'ultimo pasto a Cena."*
- *"Suggest a quick high-protein dinner within my remaining macros today."*
- *"Rebalance my remaining meals for today."*
- On shared smart speakers without an authenticated `user_id`, explicitly name the person: *"Log for Marco: 150g Greek yogurt 0%."*

---

## 9. Barcode Scanner & Paginated Two-Tier Food Vault

### Barcode Scanning
1. Click **Barcode** in the Quick Log bar or inside the **Food Vault** tab.
2. On HTTPS/localhost browsers supporting `BarcodeDetector`, click **Start Camera** and point at an EAN-13, EAN-8, or UPC barcode—or type/paste the barcode manually at any time.
3. HA Calories Buddy searches the local Food Vault first, then queries **Open Food Facts**, runs an **EU/US Atwater sanity check** ($4P + 4C + 9F + 2\text{Fiber}$ vs. declared kcal), and flags discrepancies with an `Atwater Warning` badge before verification.
4. Click **Save to Global Vault** (instantly available to every household member) or **Save & Log Meal** to save and record a portion immediately.

### Two-Tier Food Vault (`Food Vault` Tab)
- **Global Food Vault & Pagination:** Pre-seeded with 68 verified bilingual Italian/English staples (including paired raw & cooked variants with yield factors and fiber), plus every barcode scan or custom food you save. Filter across category chips (`All`, `Protein`, `Carbs`, `Legumes`, `Dairy`, `Produce`, `Fats`, `Other`) and browse the entire catalog with `Previous` / `Next` pagination controls.
- **Personal Overrides:** Click **Edit** on any food to create a per-person override without affecting other family members:
  - Set a **Personal Alias** (e.g., *"My al dente basmati"*).
  - Customize macros (including **Fiber / 100g**) or your personal cooked/raw **Yield Factor**.
  - Define **Custom Portions** using `unit=grams` pairs (e.g., `tbsp=12, bowl=90`).

---

## 10. Calendars, Sensors & Automations

### Unified Multi-Person Calendars
Each Person Profile creates one `calendar` entity (`calendar.calories_buddy_<name>_nutrition_diary_plan`) with a distinct initial color. In Home Assistant's **Calendar** view, all family members appear side-by-side in one shared view with individual color filters, displaying:
- `🍽️ [Slot] kcal — items` (confirmed meals, anchored to slot window if logged outside `±90 min`)
- `⚠️ [Slot] kcal — items` (meals awaiting confirmation)
- `📋 [Plan - Slot] kcal — title` (upcoming planned meals with ingredients & macros in description)
- `📊 consumed / target kcal | P / C / F / Fib` (all-day summary)

### Exposed Sensors (10 per Person)
All entities are grouped under a single device (`Calories Buddy — <Name>`):

| Entity Suffix | Unit | Key Attributes |
| :--- | :--- | :--- |
| `_calories_consumed` | `kcal` | `meals_confirmed`, `by_slot` (includes `fiber_g`) |
| `_calories_target` | `kcal` | `mode`, `goal_type`, `bmr_kcal`, `tdee_kcal`, `safety_warnings` |
| `_calories_remaining` | `kcal` | — |
| `_protein_consumed` | `g` | `target_g`, `remaining_g` |
| `_carbs_consumed` | `g` | `target_g`, `remaining_g` |
| `_fat_consumed` | `g` | `target_g`, `remaining_g` |
| `_fiber_consumed` | `g` | `target_g`, `remaining_g` |
| `_estimated_tdee` | `kcal` | `engine`, `bmr_kcal`, `bmr_formula`, `wearable_baseline_kcal`, `intraday_delta_kcal`, `fallback_to_static`, `days_used`, `weight_kg` |
| `_pending_meal_confirmations` | count | Trigger automations whenever `state > 0` |
| `_calorie_target_adherence` | `%` | — |

### Example Automation (Pending Confirmation Alert)

```yaml
alias: Calories Buddy — Pending Confirmation Reminder
trigger:
  - trigger: numeric_state
    entity_id: sensor.calories_buddy_marco_pending_meal_confirmations
    above: 0
    for:
      minutes: 15
action:
  - action: notify.mobile_app_marco_phone
    data:
      title: "Calories Buddy"
      message: "You have {{ states('sensor.calories_buddy_marco_pending_meal_confirmations') }} meal(s) waiting for confirmation."
```

---

## 11. Home Assistant Services Reference

All services support optional response data (`SupportsResponse.OPTIONAL`) and resolve the person from `person` parameter, calling `context.user_id`, or single-person default.

| Service | Parameters | Purpose |
| :--- | :--- | :--- |
| `ha_calories_buddy.log_meal_text` | `text` (req), `person`, `meal_slot`, `date` | Parse & log a natural-language meal via LLM. |
| `ha_calories_buddy.log_meal_item` | `quantity` (req), `food_id` / `barcode` / `name`, `unit`, `state`, `meal_slot`, `person`, `date` | Deterministic offline/manual item logging. |
| `ha_calories_buddy.confirm_meal` | `log_id` (req), `promote_to_vault`, `person` | Confirm a pending meal and optionally save AI-estimated foods to Vault. |
| `ha_calories_buddy.update_meal_item` | `log_id` (req), `item_id` (req), `quantity` (req), `unit`, `state`, `person` | Update quantity/unit/state and recalculate totals. |
| `ha_calories_buddy.update_meal_slot` | `log_id` (req), `meal_slot` (req), `person` | Reassign a logged meal to a different slot (e.g. `Cena`). |
| `ha_calories_buddy.delete_meal` | `log_id` (req), `person` | Soft-delete a logged meal. |
| `ha_calories_buddy.lookup_barcode` | `barcode` (req), `person` | Look up EAN/UPC in local Vault + Open Food Facts. |
| `ha_calories_buddy.log_weight` | `weight_kg` (req), `person` | Record today's weight snapshot and refresh targets. |
| `ha_calories_buddy.suggest_meal` | `meal_slot`, `hint`, `max_cook_minutes`, `count`, `person` | Propose 1–3 personalized macro-scaled recipes. |
| `ha_calories_buddy.generate_meal_plan` | `start_date`, `num_days`, `meal_prep_mode`, `notes`, `person` | Generate a 1–7 day macro-scaled meal plan. |
| `ha_calories_buddy.swap_plan_meal` | `plan_item_id` (req), `instruction`, `person` | Swap one planned slot with a fresh recipe matching daily budget. |
| `ha_calories_buddy.promote_plan_meal` | `plan_item_id` (req), `scale`, `person` | Log a planned meal directly as confirmed intake. |
| `ha_calories_buddy.rebalance_today` | `date`, `spread_days`, `allow_llm_swap`, `person` | Rescale/swap remaining meals today or smooth over 2–4 days. |
| `ha_calories_buddy.set_preference` | `scope` (req), `target_type` (req), `target_value` (req), `stance` (req), `notes`, `person` | Add/update global or personal preference, dislike, or allergen. |

Example YAML service call:

```yaml
action: ha_calories_buddy.log_meal_item
data:
  person: Marco
  barcode: "8001800001111"
  quantity: 80
  unit: g
  state: raw
  meal_slot: Pranzo
```

---

## 12. Lovelace YAML Mode Setup

If your Home Assistant uses `lovelace: mode: yaml` instead of UI storage mode, add the module resource manually to `configuration.yaml` (or your `resources.yaml`):

```yaml
lovelace:
  mode: yaml
  resources:
    - url: /ha_calories_buddy/frontend/ha-calories-buddy-card.js?v=0.2.2
      type: module
```

Then add the card to any dashboard view:

```yaml
type: custom:ha-calories-buddy-card
title: Calories Buddy
# Optional: omit person_id to auto-match the signed-in HA user
# person_id: person_xxxxxxxxxx
```

---

## 13. Troubleshooting & FAQ

- **Custom Card shows "Custom element doesn't exist: ha-calories-buddy-card":**  
  Hard-refresh your browser (`Ctrl+Shift+R`) or clear cache in the Home Assistant Companion app. Confirm under **Settings → Dashboards → Resources** that `/ha_calories_buddy/frontend/ha-calories-buddy-card.js?v=0.2.2` is listed (if using YAML mode, follow [Section 12](#12-lovelace-yaml-mode-setup)).
- **Camera barcode scanner button is disabled:**  
  Live camera scanning requires HTTPS (or `localhost`) plus browser `BarcodeDetector` support (Chromium/Android WebView; iOS Safari lacks native `BarcodeDetector`). Type or paste the EAN digits directly into the manual lookup input—it works everywhere.
- **Voice logging from a shared smart speaker returns an ambiguity error:**  
  Voice satellites without a linked `user_id` cannot tell household members apart automatically. Include the person's name in your request (*"Log for Giulia..."*).
- **Where is my nutrition data stored? Will Home Assistant purge it after 10 days?**  
  No. All logs, foods, preferences, and biometric snapshots live in `<ha_config>/.storage/ha_calories_buddy.db` (SQLite WAL mode), completely independent of Home Assistant's `recorder` purge window.

---

## 14. Privacy, AI Disclosure & Medical Disclaimer

- **Privacy & Local Control:** API keys are stored in Home Assistant Config Entries and are never logged or exposed over WebSocket endpoints. Using Ollama or LM Studio with local Food Vault entries keeps 100% of your dietary data inside your LAN.
- **LLM Capability Disclaimer:** Structured parsing accuracy depends on the selected model's instruction-following capability. Whenever uncertainty is detected (`llm_estimated`, `ambiguous_state`, `atwater_warning`, `unknown_unit`), HA Calories Buddy routes the meal through hybrid confirmation rather than silently trusting unverified numbers.
- **Medical Disclaimer:** HA Calories Buddy is a personal nutrition and wellness tool and does not replace professional medical advice, diagnosis, or clinical dietetic care.
