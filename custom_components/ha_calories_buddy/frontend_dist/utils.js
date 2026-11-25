/** Shared HTML escaping and formatting helpers for Custom Card components */

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
