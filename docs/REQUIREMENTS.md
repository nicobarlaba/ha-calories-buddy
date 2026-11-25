# ha-calories-buddy — Software Requirements Specification (SRS)

**Version:** 0.1 (Baseline for Review)  
**Date:** 2026-11-25  
**Target Platform:** Home Assistant (Custom Integration via HACS + Auto-registered Lovelace Custom Card)  
**Languages:** English & Italian UI/LLM support (extensible i18n); all code and technical documentation in English.

---

## 1. Product Vision & Scope

`ha-calories-buddy` is a privacy-first, multi-person, AI-assisted **personal dietitian, recipe advisor, and grocery butler** running natively inside Home Assistant.

Unlike standalone calorie trackers, it combines:
1. **Zero-friction multi-modal logging** (Home Assistant Assist voice, free text, manual entry, and barcode scanning) backed by deterministic nutritional math.
2. **A two-tier Food Vault & Preference Memory** (shared household catalog + per-person overrides, brand preferences, taste profile, allergies, and consumption frequency).
3. **Evidence-based physiology & biophysical integration**, reading real entities from Home Assistant (e.g., Garmin Connect, smart scales) using an Energy-Dashboard-style entity mapping UI.
4. **Actionable daily/weekly meal planning** projected directly onto Home Assistant `calendar` entities, dynamic rebalancing when meals deviate from plan, and an editable **shopping list draft** synced to Home Assistant `todo` entities.

### 1.1 Out of Scope (v1–v2)
- Clinical medical diagnosis or management of eating disorders / medical diets requiring physician supervision.
- Full real-time pantry/fridge inventory tracking (replaced intentionally by an editable pre-shopping draft).
- Full micronutrient tracking (vitamins/minerals); focus is strictly on **high-reliability Calories and Macronutrients** (Protein, Carbohydrates, Fat, plus optional Fiber where available).
- Standalone Docker Add-on runtime (distributed as a universal HACS Custom Integration compatible with HA OS, Supervised, Container, and Core).

### 1.2 Important Notices & Disclaimers
- **Nutritional & Medical Disclaimer:** The integration is a personal wellness assistant and does not replace a registered dietitian or physician. Safety guardrails warn against extreme caloric deficits or unsafe macro floors.
- **LLM Capability Disclosure Notice:** During LLM setup (both cloud and local), the UI shall display a clear disclosure explaining that parsing reliability and meal-planning quality depend directly on the chosen model's instruction-following and structured-output capabilities, and that cloud providers incur API costs and send meal/profile context externally whereas local engines (Ollama, LM Studio) keep all data on-premises.

---

## 2. Multi-Person & Household Model

### 2.1 Configuration Hierarchy
- **FR-001 (Shared Household Hub):** The integration shall provide a singleton Household / Global configuration holding:
  - Default LLM provider configuration (shared by all person instances unless overridden).
  - Global Food Vault (nutritional catalog shared across the household).
  - Shared household baseline food preferences/tastes (optional starting point for household members).
  - Default language and optional external food database credentials (e.g., USDA API key).
- **FR-002 (Multi-Person Instantiation):** Users shall be able to create N independent Person profiles (one per monitored individual), each cleanly separated as its own instance in Home Assistant.
- **FR-003 (HA User & Person Mapping):** Every Person profile shall be mapped to a Home Assistant `person.*` entity and linked to its Home Assistant `user_id`.
  - Requests originating from the Custom Card, Home Assistant services, or authenticated Assist sessions shall automatically resolve the active person from `context.user_id`.
  - Requests from unauthenticated sources (e.g., shared Voice Assist Satellites without a `user_id`) shall support explicit person targeting (e.g., *"Log for Marco..."*) or a configurable fallback/satellite mapping.
- **FR-004 (Two-Tier Override Model):**
  - **LLM Settings:** Inherit global LLM config by default, with optional per-person override (different provider/model/language).
  - **Food Vault:** Every person reads from the Global Food Vault, and may store per-person overrides (custom macros, preferred brand for a generic food, custom household portions such as *"my tablespoon of oil = 10g"*, custom cooked/raw yield factor).
  - **Preferences & Constraints:** Household baseline tastes may be defined globally; each person strictly overrides them with their own hard constraints (allergies, intolerances, dietary regime, blacklisted foods) and soft preferences (loved/liked/disliked foods, preferred brands, cooking habits).
- **FR-005 (Language Selection):** Language shall be selectable both globally and per person (affecting LLM responses, meal plans, recipes, and grocery item names), with UI translations supporting at least English (`en`) and Italian (`it`) from day one.

---

## 3. Nutritional Precision & Food Resolution Engine

### 3.1 Nutritional Scope
- **FR-010 (Core Metrics):** Every food item, meal item, daily log, and meal plan shall track:
  - Energy (`kcal`)
  - Protein (`g`)
  - Carbohydrates (`g`)
  - Fat (`g`)
  - Fiber (`g`, optional — stored when available, never blocking).

### 3.2 Deterministic Calculation & Trust Waterfall
The LLM shall **never** perform final arithmetic or invent macros when structured data is available.

- **FR-011 (Food Resolution Waterfall):** For each parsed food item, the engine shall resolve nutritional values in strict priority order:
  1. **Person Food Override** (exact match on barcode, brand+name, or personal alias/preferred brand).
  2. **Global Food Vault** (previously verified/scanned/saved items in local SQLite).
  3. **Structured External Databases:**
     - **Open Food Facts (OFF):** Primary source for barcodes (EAN/UPC) and branded European/international retail products (e.g., *Riso Basmati Scotti*).
     - **USDA FoodData Central / Curated Generic Seed:** Primary source for unbranded whole foods (raw & cooked variants).
  4. **LLM Knowledge Fallback:** Used only when no structured match is found. The resulting item is explicitly tagged `source = llm_estimated` with low confidence so the user can review, adjust, and promote it to the Vault.
- **FR-012 (Atwater Energy Sanity Check):** Whenever a food item is imported (OFF, USDA, LLM fallback, or manual edit), the system shall validate energy consistency against Atwater factors:
  - Account for **EU labeling** (available carbs exclude fiber: $4P + 4C + 9F + 2\text{Fiber}$) vs **US labeling** (total carbs include fiber: $4P + 4(C - \text{Fiber}) + 9F + 2\text{Fiber}$).
  - If declared calories deviate beyond tolerance (default: `> 15%` AND `> 25 kcal/100g`), flag the item with `atwater_warning` and surface it to the user for verification.
- **FR-013 (Raw vs. Cooked Weight & Yield Conversion):**
  - Every food record in the Vault stores its reference `preparation_state` (`raw`, `cooked`, `ready_to_eat`) and an optional `yield_factor` ($\text{cooked\_weight} / \text{raw\_weight}$, e.g., rice `2.5`, dry pasta `2.2`, dried legumes `2.4`, chicken breast `0.75`).
  - When a user logs a weight in a different state than the stored reference (e.g., Vault has *Riso Basmati Scotti* raw dry values, user logs *"200g cooked basmati rice"*), the engine deterministically converts weight using the yield factor before computing macros.
  - When the user omits state (`unspecified`), category defaults apply (e.g., pasta/rice default to `raw` when weight is in a plausible dry range; if state ambiguity creates a high caloric uncertainty — such as *"250g lentils"* — the item is flagged as `ambiguous_state`).
- **FR-014 (Household Measures & Portion Mapping):** Support common units (`g`, `kg`, `ml`, `l`, `tbsp`, `tsp`, `cup`, `slice`, `piece`, `can`, `pack`) resolved via food-specific serving sizes, per-person custom portions, or standard culinary density tables.

### 3.3 Barcode & Food Vault Management
- **FR-015 (Barcode Scanning & Lookup):** Users shall be able to scan product barcodes (camera scanner in the Custom Card over HTTPS/Companion App, plus manual EAN entry fallback) to:
  - Immediately look up the product in the Vault or Open Food Facts.
  - Inspect/correct macros (with Atwater check), optionally set yield factor/serving size, and save/update the Global Food Vault (or personal override).
  - Directly log a consumed quantity of the scanned item into a meal.
- **FR-016 (Vault CRUD & Deduplication):** Users can browse, search, edit, verify, and add custom foods or homemade recurring recipes (composite foods) from the Custom Card.

---

## 4. Multi-Modal Meal Logging & Hybrid Confirmation

- **FR-020 (Voice & Natural Language Input):** Integrate natively with Home Assistant **Assist** (`llm.API` tools + HA services) and text input in the Custom Card.
  - Example input: *"A pranzo ho mangiato 80g di riso basmati Scotti, 1 cucchiaio di olio di oliva, 250g lenticchie cotte"*.
  - The LLM parses the utterance into structured meal items (food name, brand, quantity, unit, state, meal slot, date/time), preserving implicit brand mentions.
- **FR-021 (Manual Logging):** Users can manually search the Food Vault / Open Food Facts, select an item, enter weight/serving and raw/cooked state, and log without invoking the LLM.
- **FR-022 (Hybrid Confirmation Workflow):**
  - **High-confidence log** (all items matched in Vault or verified DB, no severe raw/cooked ambiguity, Atwater valid): **saved immediately** as `confirmed`, returning a concise voice/UI summary (*"Pranzo registrato: 540 kcal — P 22g, C 82g, G 12g"*) plus optional actionable notification for one-tap undo/edit.
  - **Low-confidence / Ambiguous log** (at least one item is `llm_estimated`, has `ambiguous_state`, or failed `atwater_warning`): saved as **`pending_confirmation`** (highlighted prominently in the Custom Card and optionally via voice follow-up / actionable notification). Users can confirm, correct state/grams/macros with 1–2 taps, and optionally persist the corrected food to the Vault.
- **FR-023 (Conversational Corrections & Queries):** Users can edit or delete logged meals via Custom Card or voice/Assist (e.g., *"Correggi le lenticchie del pranzo a 150g"*, *"Quante proteine mi mancano oggi?"*).
- **FR-024 (Plan-to-Log Promotion):** Any planned meal can be marked as `consumed` in one tap (with optional portion adjustments), immediately converting it into a real log entry.

---

## 5. Physiology, Targets & Biophysical Sensor Integration

### 5.1 Entity Mapping (Energy-Dashboard Style)
- **FR-030 (Configurable HA Entity Bindings):** Each Person profile can optionally map existing Home Assistant entities via native `EntitySelector`s:
  - Body weight sensor (`sensor.*`, automatic `kg`/`lb` conversion).
  - Body fat percentage sensor (`sensor.*`, `%`) and/or lean mass sensor.
  - Daily active calories burned sensor (`sensor.*`, e.g., Garmin Connect).
  - Daily total calories burned sensor (`sensor.*`, e.g., Garmin Connect).
  - Daily steps sensor (`sensor.*`).
  - Target shopping list (`todo.*`, shareable across household members).
  - Notification target (`notify.*`, for actionable confirmations/reminders).
- **FR-031 (Resilient Biometric Snapshotting):** Mapped sensor values are periodically snapshotted into local SQLite. If a mapped HA entity becomes `unavailable` or `unknown`, the engine falls back gracefully to the latest stored valid value without breaking targets or sensors.

### 5.2 Target Modes & Scientific Calculations
- **FR-032 (Dual Target Modes):** Each person selects either:
  1. **Physiology-Guided Mode:** Computes BMR, TDEE, caloric target, and macro targets from biometrics and goals.
  2. **Manual / Nutritionist Mode:** User directly enters target `kcal` and `P / C / F` (in grams or percentages), optionally differentiated by **Training Day (ON)** vs **Rest Day (OFF)**.
- **FR-033 (BMR Equations):**
  - **Katch-McArdle** when body fat % is available: $\text{BMR} = 370 + 21.6 \times \text{LBM}_{\text{kg}}$.
  - **Mifflin-St Jeor** when body fat % is unavailable.
- **FR-034 (TDEE Engines — Anti-Double-Counting Design):**
  1. **Static Profile:** $\text{TDEE} = \text{BMR} \times \text{PAL}$ (configurable activity multiplier).
  2. **Wearable-Dynamic (e.g., Garmin):** Uses mapped wearable expenditure **without** applying a redundant PAL multiplier:
     - Uses a **7-day completed-day rolling baseline** for today's morning budget (preventing an artificially tiny budget at 09:00 when cumulative daily wearable calories start near 0), plus an optional intra-day surplus bump only when today's active burn exceeds the rolling baseline.
     - Supports a configurable `wearable_correction_factor` (default `1.0`, tunable e.g. `0.85` to offset wearable overestimation).
  3. **Adaptive TDEE (Phase 3 — MacroFactor-style):** Combines Exponential Moving Average (EMA) body weight trend and rolling caloric intake over 14–28 days (requiring minimum logging & weigh-in adherence thresholds) to converge toward true physiological energy expenditure, damped per week to ignore water/glycogen noise.
- **FR-035 (Goal & Macro Allocation with Safety Guardrails):**
  - Supports goals: `lose_weight`, `maintain`, `recomposition`, `gain_muscle`, with target weight and/or target body fat % and weekly rate (e.g., `-0.25` to `-0.75 kg/week` or `% body weight/week`).
  - Protein anchored to evidence-based ranges (`g/kg` body weight or `g/kg LBM` for higher body fat %), fat anchored above essential minimum (`≥ 0.6–0.8 g/kg`), carbohydrates fill remaining budget.
  - Soft safety warnings if a configured rate produces a deficit `> 25%` of TDEE or drops below conservative physiological floors.

---

## 6. Personal Dietitian, Chef & Shopping Butler

### 6.1 Preference Learning & Personalization ("Friend" Memory)
- **FR-040 (Structured Food Profile):** Tracks per person:
  - **Hard constraints:** allergies, intolerances, dietary pattern (omnivore, vegetarian, vegan, pescatarian, gluten-free, lactose-free, etc.), blacklisted foods/ingredients.
  - **Soft preferences:** explicit ratings (`love`, `like`, `neutral`, `dislike`), implicit frequency & recency scores automatically computed from confirmed meal logs, preferred brands per food category.
  - **Lifestyle constraints:** customizable meal slots (number, names, approximate times, optional caloric split, e.g., 3/4/5 meals or intermittent fasting window), max cooking time per meal slot (e.g., 15 min lunch, 35 min dinner), meal-prep / repetition preference (e.g., cook once eat twice vs daily variety), optional assistant tone/personality.

### 6.2 Meal Planning, Recipes & Dynamic Rebalancing
- **FR-041 (On-Demand Meal & Recipe Advisor):** Users can ask (via Assist or Custom Card): *"What can I eat for dinner to hit my remaining macros?"* The system computes exact remaining `kcal/P/C/F`, queries preferred foods/brands & hard constraints, and returns 1–3 concrete, macro-accurate meal proposals with step-by-step preparation instructions.
- **FR-042 (Daily & Weekly Meal Planner):** Generates a structured 1-day to 7-day meal plan tailored to targets, preferences, cooking time limits, and repetition preference.
  - Every generated meal uses deterministic Food Vault / DB macro calculations (the LLM selects dishes and proportions; the Python engine verifies/scales exact gram weights to match daily kcal/macro targets within tolerance, e.g., `±5%`).
  - Users can regenerate a single slot (e.g., *"Swap Thursday dinner for something with salmon"*) without regenerating the whole week.
  - Feedback buttons (`Like`, `Dislike`, `Never suggest again`) continuously refine the preference profile.
- **FR-043 (Dynamic Rebalancing):**
  - **Intra-day (automatic):** Whenever actual intake deviates from plan (e.g., unplanned lunch pizza), remaining meal budgets for the day recalculate immediately, and the user can trigger a one-tap *"Rebalance remaining meals"* suggestion.
  - **Multi-day (optional):** Users can choose to spread a daily surplus/deficit smoothly across the next 2–4 days of the week instead of starving at dinner.

### 6.3 Calendar Projection & Shopping List Draft
- **FR-044 (Per-Person Home Assistant Calendar):** Each Person profile exposes **one `calendar` entity** (`calendar.ha_calories_buddy_<person_slug>`).
  - Because HA assigns calendar colors per entity, multiple household members appear together in the unified HA Calendar view with automatic distinct colors and per-person visibility toggles.
  - Event types rendered:
    - Planned meals: `📋 [Plan - Lunch] ...`
    - Confirmed logged meals: `🍽️ [Lunch] ...`
    - Pending confirmation meals: `⚠️ [Confirm - Lunch] ...`
    - Daily all-day summary: `📊 1,980 / 2,100 kcal | P 145g C 210g F 62g`
- **FR-045 (Editable Shopping List Draft & HA `todo` Sync):**
  - From any approved/active weekly (or multi-day) meal plan, the system aggregates all required ingredients into normalized purchase quantities, preferring the user's favorite brands (e.g., *Riso Basmati Scotti — 480g*) and grouping by grocery category.
  - Presents an **editable draft** inside the Custom Card where the user can:
    - Uncheck items already available at home.
    - Edit quantities or item names.
    - Manually add spontaneous/extra grocery items.
  - On confirmation, pushes selected items into the person's mapped Home Assistant `todo.*` entity (supporting shared household shopping lists across multiple persons).

---

## 7. LLM Engine & Reliability Requirements

- **FR-050 (Provider Support):** Equal-quality support for:
  - Cloud APIs: **OpenAI**, **Anthropic (Claude)**, **Google Gemini**.
  - Local / Self-hosted: **Ollama** (native API) and **OpenAI-Compatible endpoints** (covering **LM Studio**, vLLM, OpenRouter, Groq, LocalAI, etc.).
- **FR-051 (Structured Output & Validation):** All LLM tasks use small, single-purpose JSON schemas validated strictly via Pydantic/schema checks, with automatic repair retry (up to 2 attempts) on malformed JSON so both frontier cloud models and strong local models behave reliably.
- **FR-052 (Graceful Degradation):** Manual logging, barcode scanning, Food Vault management, calendar viewing, sensor reporting, and deterministic macro calculations must remain **100% operational even if the LLM provider is offline or unconfigured**.

---

## 8. Custom Lovelace Card Requirements

- **FR-060 (Zero-Setup Auto-Registration):** The Custom Card JS bundle (`ha-calories-buddy-card`) is shipped inside the HACS integration and automatically registered as a Lovelace resource.
- **FR-061 (Core Card Views):**
  1. **Today (Dashboard & Log):** Calorie ring, P/C/F progress bars, TDEE/remaining summary, chronological meal list with inline edit/delete and prominent `pending_confirmation` review banner, quick input bar (natural text, manual search, **barcode camera/EAN scanner**).
  2. **Plan & Recipes:** Weekly/daily plan view, recipe instructions modal, one-tap *"Log as eaten"*, *"Swap meal"*, *"Rebalance today"*, *"Generate weekly plan"*.
  3. **Shopping Draft:** Editable aggregated grocery draft with checkboxes, quantity adjustment, freeform add, and *"Send to HA Shopping List"* action.
  4. **Food Vault & Profile:** Search/add/edit global & personal foods, barcode quick-add, hard constraints, preferences/brands, and quick body weight entry.
  5. **Trends:** 7/30/90-day charts comparing caloric intake vs target, macro adherence, body weight EMA, and adaptive TDEE.

---

## 9. Non-Functional Requirements (NFR)

- **NFR-001 (Data Persistence):** All domain data lives in a dedicated local SQLite database inside Home Assistant's persistent config/storage directory (never subject to HA `recorder` 10-day purge), with schema versioning and safe migrations.
- **NFR-002 (Non-Blocking Async Execution):** All network calls (LLM, Open Food Facts, USDA) and SQLite operations must run asynchronously (`aiohttp`, `aiosqlite`) without blocking Home Assistant's event loop.
- **NFR-003 (Clean Code & Modularity):** Core domain logic (`nutrition`, `physiology`, `llm`, `planner`, `storage`) shall be completely decoupled from Home Assistant internals so it can be unit-tested independently via `pytest` in a Python `venv`.
- **NFR-004 (Structured Logging):** Use low-cardinality structured logging throughout (passing dynamic identifiers via structured `extra={...}` fields, never interpolating sensitive API keys or PII into log strings).
- **NFR-005 (Privacy & Security):** API keys stored securely in HA Config Entries (never logged or exposed over WebSocket to the frontend); local-only operation guaranteed when using local LLM + local Vault.
- **NFR-006 (HACS & HA Quality Standards):** Compliant with HACS validation, `hassfest`, PEP 8 / `ruff`, type annotations (`mypy`/strict typing), and complete English/Italian translations.

---

## 10. Phased Delivery Roadmap

| Phase | Milestone Name | Key Deliverables |
| :--- | :--- | :--- |
| **Phase 1 (MVP)** | **The Accurate Food Diary** | Hub + Multi-Person ConfigFlow (HA `person`/`user_id` mapping), SQLite two-tier Food Vault, Atwater & raw/cooked yield engine, Open Food Facts + Barcode lookup, LLM engine (Cloud + Ollama + OpenAI-compatible + disclosure), Assist voice/text logging with hybrid confirmation, Physiology (BMR + Static/Wearable TDEE + Manual mode + Entity mapping), HA `sensor` & `calendar` entities, Custom Card v1 (Today + Quick Input + Barcode Scanner + Vault). |
| **Phase 2** | **The Dietitian & Chef** | Two-tier preference/constraint engine & automatic frequency/brand learning, On-demand recipe/meal suggestions, Daily & Weekly meal planner with deterministic macro-scaling, Single-meal swap, Feedback loop, Intra-day & multi-day rebalancing, Plan-to-Log promotion, Custom Card v2 (Plan & Recipes view). |
| **Phase 3** | **The Butler & Adaptive Engine** | Editable Shopping List Draft + HA `todo.*` sync, Adaptive TDEE engine (EMA weight vs intake), Trends view in Custom Card, optional USDA integration & homemade composite recipes, polish for HACS public release. |
