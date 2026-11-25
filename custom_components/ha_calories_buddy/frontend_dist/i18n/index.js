import { EN } from "./en.js";
import { IT } from "./it.js";

const BUNDLES = { en: EN, it: IT };

export function t(language, key) {
  const lang = language && BUNDLES[language] ? language : "en";
  return BUNDLES[lang][key] || EN[key] || key;
}
