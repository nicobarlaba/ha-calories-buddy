/** Shared HTML escaping, slot inference, localization, and macro pie math */

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
