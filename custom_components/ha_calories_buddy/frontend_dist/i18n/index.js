import { EN } from "./en.js";
import { IT } from "./it.js";

const BUNDLES = { en: EN, it: IT };

export function t(language, key, vars = null) {
  const lang = language && BUNDLES[language] ? language : "en";
  let text = BUNDLES[lang][key] || EN[key] || key;
  if (vars && typeof vars === "object") {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replaceAll(`{${k}}`, String(v));
    }
  }
  return text;
}
