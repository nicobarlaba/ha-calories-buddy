/** Shared HTML escaping, slot inference, localization, recipe badges, unit options, and macro pie math */
import { t } from "./i18n/index.js";

export function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function round1(val) {
  return Math.round((Number(val) || 0) * 10) / 10;
}

export function localizedFoodName(food, language = "en") {
  if (!food) return "";
  if (food.preferred_alias) return food.preferred_alias;
  if (language === "it" && food.name_it) return food.name_it;
  return food.name || "";
}

function parseSlotMinutes(slot) {
  const approx = slot?.approx_time;
  if (approx && approx !== "12:30" && approx.includes(":")) {
    const [h, m] = approx.split(":").map(Number);
    if (!Number.isNaN(h) && !Number.isNaN(m)) return h * 60 + m;
  }
  const n = (slot?.name || "").toLowerCase();
  if (n.includes("colaz") || n.includes("break")) return 8 * 60;
  if (n.includes("pranz") || n.includes("lunch")) return 13 * 60;
  if (n.includes("spunt") || n.includes("snack") || n.includes("merend")) return 16 * 60 + 30;
  if (n.includes("cen") || n.includes("din") || n.includes("supper")) return 20 * 60;
  return 13 * 60;
}

export function pickDefaultSlotForNow(slots) {
  if (!slots || !slots.length) return "Lunch";
  const now = new Date();
  const nowMins = now.getHours() * 60 + now.getMinutes();
  let best = slots[0].name;
  let bestDist = 24 * 60;
  for (const s of slots) {
    const d = Math.abs(nowMins - parseSlotMinutes(s));
    if (d < bestDist) {
      bestDist = d;
      best = s.name;
    }
  }
  return best;
}

export function computeMacroPieSlices(macros) {
  const pG = Math.max(0, Number(macros?.protein_g || 0));
  const cG = Math.max(0, Number(macros?.carbs_g || 0));
  const fG = Math.max(0, Number(macros?.fat_g || 0));
  const fibG = Math.max(0, Number(macros?.fiber_g || 0));

  const pKcal = pG * 4;
  const cKcal = cG * 4;
  const fKcal = fG * 9;
  const fibKcal = fibG * 2;
  const totalKcal = pKcal + cKcal + fKcal + fibKcal;

  const raw = [
    { key: "protein", grams: pG, kcal: pKcal, color: "#42a5f5" },
    { key: "carbs", grams: cG, kcal: cKcal, color: "#ffa726" },
    { key: "fat", grams: fG, kcal: fKcal, color: "#ab47bc" },
    { key: "fiber", grams: fibG, kcal: fibKcal, color: "#66bb6a" },
  ];

  return {
    totalKcal,
    slices: raw.map((item) => ({
      ...item,
      pct: totalKcal > 0 ? Math.round((item.kcal / totalKcal) * 100) : 0,
      exactShare: totalKcal > 0 ? item.kcal / totalKcal : 0,
    })),
  };
}

export function parseJsonSafe(raw, fallback) {
  if (!raw) return fallback;
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function isRecipeItem(item) {
  if (!item) return false;
  return (
    item.source === "recipe" ||
    String(item.food_id || "").startsWith("recipe_")
  );
}

export function renderRecipeBadge(lang = "en") {
  const label = t(lang, "recipe_badge") || "Recipe";
  return `<span class="pill recipe-pill" title="${escapeHtml(label)}"><svg class="moleskine-ico" viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="1.5" width="10.5" height="13" rx="1.8" fill="#4a3525" stroke="currentColor" stroke-width="0.9"/><line x1="4.6" y1="1.8" x2="4.6" y2="14.2" stroke="rgba(255,255,255,0.35)" stroke-width="0.8"/><line x1="10.7" y1="1.7" x2="10.7" y2="14.3" stroke="#f0a500" stroke-width="1.35"/><path d="M7.5 14v1.8l1-.6 1 .6V14z" fill="#f0a500"/></svg><span>${escapeHtml(label)}</span></span>`;
}

export const CATEGORY_UNIT_DEFAULTS_G = {
  oil: { tbsp: 10.0, tsp: 4.0 },
  dairy: { cup: 240.0, tbsp: 15.0, tsp: 5.0, pack: 150.0 },
  egg: { piece: 55.0 },
  bread: { slice: 35.0, piece: 60.0 },
  legumes: { can: 240.0, tbsp: 20.0 },
  fish: { can: 56.0, piece: 150.0 },
  poultry: { piece: 150.0, slice: 20.0 },
  fruit: { piece: 150.0 },
  vegetables: { piece: 120.0 },
  nuts: { tbsp: 15.0, tsp: 5.0, piece: 3.0 },
  snack: { tbsp: 18.0, tsp: 6.0, serving: 30.0 },
  beverage: { cup: 200.0, pack: 200.0 },
  pasta: { serving: 80.0 },
  rice: { serving: 80.0 },
  grains: { serving: 80.0 },
  oats: { tbsp: 10.0, serving: 40.0 },
  other: { tbsp: 15.0, tsp: 5.0, cup: 200.0, serving: 100.0 },
};

const UNIT_LABEL_KEYS = {
  g: "unit_g",
  ml: "unit_ml",
  tbsp: "unit_tbsp",
  tsp: "unit_tsp",
  cup: "unit_cup",
  piece: "unit_piece",
  slice: "unit_slice",
  serving: "unit_serving",
  can: "unit_can",
  pack: "unit_pack",
};

const LIQUID_OR_SEMILIQUID_CATS = new Set(["oil", "dairy", "beverage"]);

export function getFoodUnitGrams(unit, food) {
  const u = String(unit || "g").trim().toLowerCase();
  if (u === "g") return 1;
  if (u === "kg") return 1000;
  if (u === "oz") return 28.3495;
  if (u === "lb") return 453.592;
  const cat = String(food?.category || "other").toLowerCase();
  if (u === "ml") return cat === "oil" ? 0.92 : 1.0;
  if (u === "l") return cat === "oil" ? 920.0 : 1000.0;
  if (food?.person_portions && typeof food.person_portions[u] === "number") {
    return food.person_portions[u];
  }
  if (food?.food_portions && typeof food.food_portions[u] === "number") {
    return food.food_portions[u];
  }
  const catPortions = CATEGORY_UNIT_DEFAULTS_G[cat] || {};
  if (typeof catPortions[u] === "number") return catPortions[u];
  if (typeof CATEGORY_UNIT_DEFAULTS_G.other[u] === "number") {
    return CATEGORY_UNIT_DEFAULTS_G.other[u];
  }
  return 100;
}

export function estimateGramsForFoodUnit(qty, unit, food) {
  const n = Number(qty || 0);
  if (n <= 0) return 0;
  return n * getFoodUnitGrams(unit, food);
}

export function formatUnitLabel(unit, gramsPerUnit, lang = "en") {
  const u = String(unit || "g").trim().toLowerCase();
  const key = UNIT_LABEL_KEYS[u];
  const base = key ? t(lang, key) : u;
  if (u === "g" || u === "ml" || !gramsPerUnit) return base;
  return `${base} (${round1(gramsPerUnit)}g)`;
}

export function getUnitOptionsForFood(food, lang = "en", currentUnit = null) {
  const optionsMap = new Map();
  const addOpt = (u, grams) => {
    const clean = String(u || "").trim().toLowerCase();
    if (!clean || optionsMap.has(clean)) return;
    optionsMap.set(clean, {
      value: clean,
      gramsPerUnit: Number(grams || 0),
      label: formatUnitLabel(clean, grams, lang),
    });
  };

  if (food?.person_portions) {
    for (const [u, g] of Object.entries(food.person_portions)) addOpt(u, g);
  }
  if (food?.food_portions) {
    for (const [u, g] of Object.entries(food.food_portions)) addOpt(u, g);
  }

  const cat = String(food?.category || "").toLowerCase();
  if (cat && CATEGORY_UNIT_DEFAULTS_G[cat]) {
    for (const [u, g] of Object.entries(CATEGORY_UNIT_DEFAULTS_G[cat])) {
      addOpt(u, g);
    }
  }

  addOpt("g", 1);
  if (LIQUID_OR_SEMILIQUID_CATS.has(cat) || currentUnit === "ml") {
    addOpt("ml", cat === "oil" ? 0.92 : 1.0);
  }

  if (!cat) {
    addOpt("piece", 55);
    addOpt("tbsp", 12);
    addOpt("tsp", 5);
    addOpt("slice", 30);
    addOpt("serving", 100);
  }

  if (currentUnit) {
    const cleanCur = String(currentUnit).trim().toLowerCase();
    if (cleanCur && !optionsMap.has(cleanCur)) {
      addOpt(cleanCur, getFoodUnitGrams(cleanCur, food));
    }
  }
  return Array.from(optionsMap.values());
}

export function renderUnitSelectOptions(options, selectedUnit = "g") {
  const normSel = String(selectedUnit || "g").trim().toLowerCase();
  return options
    .map(
      (opt) =>
        `<option value="${escapeHtml(opt.value)}" ${opt.value === normSel ? "selected" : ""}>${escapeHtml(opt.label)}</option>`
    )
    .join("");
}

export function getDefaultUnitAndQty(food, { preferGramsForBulk = false } = {}) {
  if (!food) return { unit: "g", quantity: 100 };
  const cat = String(food.category || "").toLowerCase();
  const merged = { ...(food.food_portions || {}), ...(food.person_portions || {}) };
  const keys = Object.keys(merged);

  if (isRecipeItem(food)) {
    const recUnit = keys.find((k) => ["slice", "piece", "serving"].includes(k)) || "serving";
    return { unit: recUnit, quantity: 1 };
  }
  if (cat === "egg") {
    return { unit: "piece", quantity: 1 };
  }
  if (preferGramsForBulk) {
    if (cat === "fruit" && merged.piece) return { unit: "piece", quantity: 1 };
    return { unit: "g", quantity: 100 };
  }
  if (cat === "oil") {
    return { unit: merged.tbsp || cat === "oil" ? "tbsp" : "g", quantity: 1 };
  }
  if (cat === "beverage" && merged.cup) {
    return { unit: "cup", quantity: 1 };
  }
  for (const preferred of ["piece", "slice", "can", "pack", "cup", "tbsp", "serving"]) {
    if (typeof merged[preferred] === "number" && merged[preferred] > 0) {
      return { unit: preferred, quantity: 1 };
    }
  }
  return { unit: "g", quantity: 100 };
}
