/** Trends View: 7/30/90d Native SVG Charts, Expected vs Actual KPI & Adaptive TDEE */
import { t } from "../i18n/index.js";
import { escapeHtml, round1 } from "../utils.js";

function renderCaloriesSvg(points) {
  const w = 600;
  const h = 178;
  const padL = 38;
  const padR = 12;
  const padT = 14;
  const padB = 26;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const n = Math.max(1, points.length);

  const vals = points.flatMap((p) =>
    [p.kcal_consumed, p.kcal_target].filter((v) => typeof v === "number" && v > 0)
  );
  const maxVal = Math.max(1200, vals.length ? Math.max(...vals) * 1.1 : 2500);
  const yOf = (v) => padT + plotH - (Math.max(0, Math.min(maxVal, v)) / maxVal) * plotH;
  const stepX = plotW / n;
  const barW = Math.max(3, Math.min(20, stepX * 0.68));

  const gridLines = [0.25, 0.5, 0.75, 1.0].map((frac) => {
    const val = Math.round(maxVal * frac);
    const y = yOf(val).toFixed(1);
    return `
      <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="var(--divider-color, rgba(127,127,127,0.18))" stroke-width="1" />
      <text x="${padL - 4}" y="${Number(y) + 3}" text-anchor="end" fill="var(--secondary-text-color)" font-size="9">${val}</text>
    `;
  });

  const bars = points
    .map((p, idx) => {
      const cx = padL + idx * stepX + stepX / 2;
      const x = cx - barW / 2;
      const kcal = Number(p.kcal_consumed || 0);
      const tgt = Number(p.kcal_target || 0);
      let fill = "var(--secondary-background-color, rgba(127,127,127,0.22))";
      if (p.is_logged && kcal > 0) {
        if (tgt > 0 && kcal > tgt * 1.1) {
          fill = "var(--warning-color, #ff9800)";
        } else if (tgt > 0 && Math.abs(kcal - tgt) <= tgt * 0.1) {
          fill = "var(--success-color, #4caf50)";
        } else {
          fill = "var(--primary-color, #03a9f4)";
        }
      }
      const barH = kcal > 0 ? Math.max(2, padT + plotH - yOf(kcal)) : 1.5;
      const y = kcal > 0 ? yOf(kcal) : padT + plotH - 1.5;
      return `
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="2" fill="${fill}">
          <title>${escapeHtml(p.point_date)}: ${Math.round(kcal)} kcal${tgt ? ` / ${Math.round(tgt)} kcal` : ""}</title>
        </rect>
      `;
    })
    .join("");

  const targetCoords = points
    .map((p, idx) =>
      p.kcal_target && p.kcal_target > 0
        ? `${(padL + idx * stepX + stepX / 2).toFixed(1)},${yOf(p.kcal_target).toFixed(1)}`
        : null
    )
    .filter(Boolean);

  const labelIdx = [0, Math.floor((n - 1) / 2), n - 1].filter(
    (v, i, a) => a.indexOf(v) === i && points[v]
  );
  const xLabels = labelIdx
    .map((idx) => {
      const cx = padL + idx * stepX + stepX / 2;
      return `<text x="${cx.toFixed(1)}" y="${h - 6}" text-anchor="middle" fill="var(--secondary-text-color)" font-size="9">${escapeHtml(String(points[idx].point_date).slice(5))}</text>`;
    })
    .join("");

  return `
    <svg class="chart-svg" viewBox="0 0 ${w} ${h}">
      ${gridLines.join("")}
      ${bars}
      ${
        targetCoords.length >= 2
          ? `<polyline fill="none" stroke="var(--error-color, #db4437)" stroke-width="1.6" stroke-dasharray="4 3" points="${targetCoords.join(" ")}" />`
          : ""
      }
      ${xLabels}
    </svg>
  `;
}

function renderLineChartSvg(points, seriesConfig) {
  const w = 600;
  const h = 176;
  const padL = 42;
  const padR = 14;
  const padT = 14;
  const padB = 26;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const n = Math.max(1, points.length);

  const numericVals = [];
  for (const p of points) {
    for (const key of seriesConfig.valueKeys) {
      const v = p[key];
      if (typeof v === "number" && Number.isFinite(v)) numericVals.push(v);
    }
  }
  if (
    typeof seriesConfig.goalLine === "number" &&
    Number.isFinite(seriesConfig.goalLine)
  ) {
    numericVals.push(seriesConfig.goalLine);
  }
  if (!numericVals.length) {
    return `<div class="hint-text" style="padding: 12px 0;">—</div>`;
  }

  let minV = Math.min(...numericVals);
  let maxV = Math.max(...numericVals);
  if (Math.abs(maxV - minV) < 0.5) {
    minV -= 1.0;
    maxV += 1.0;
  } else {
    const pad = (maxV - minV) * 0.14;
    minV -= pad;
    maxV += pad;
  }
  const yOf = (v) =>
    padT + plotH - ((v - minV) / Math.max(0.01, maxV - minV)) * plotH;
  const xOf = (idx) =>
    n <= 1 ? padL + plotW / 2 : padL + (idx / (n - 1)) * plotW;

  const yTicks = [0, 0.5, 1].map((frac) => {
    const v = minV + (maxV - minV) * frac;
    const y = yOf(v).toFixed(1);
    return `
      <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="var(--divider-color, rgba(127,127,127,0.18))" stroke-width="1" />
      <text x="${padL - 5}" y="${Number(y) + 3}" text-anchor="end" fill="var(--secondary-text-color)" font-size="9">${round1(v)}</text>
    `;
  });

  const rawDots = seriesConfig.rawKey
    ? points
        .map((p, idx) => {
          const val = p[seriesConfig.rawKey];
          if (typeof val !== "number" || !Number.isFinite(val)) return "";
          return `
            <circle cx="${xOf(idx).toFixed(1)}" cy="${yOf(val).toFixed(1)}" r="3" fill="${seriesConfig.rawColor}" opacity="0.55">
              <title>${escapeHtml(p.point_date)}: ${round1(val)} ${escapeHtml(seriesConfig.unit)}</title>
            </circle>
          `;
        })
        .join("")
    : "";

  const lineCoords = points
    .map((p, idx) => {
      const val = p[seriesConfig.lineKey];
      if (typeof val !== "number" || !Number.isFinite(val)) return null;
      return `${xOf(idx).toFixed(1)},${yOf(val).toFixed(1)}`;
    })
    .filter(Boolean);

  const lineDots = points
    .map((p, idx) => {
      const val = p[seriesConfig.lineKey];
      if (typeof val !== "number" || !Number.isFinite(val)) return "";
      return `
        <circle cx="${xOf(idx).toFixed(1)}" cy="${yOf(val).toFixed(1)}" r="3.2" fill="${seriesConfig.lineColor}">
          <title>${escapeHtml(p.point_date)}: ${round1(val)} ${escapeHtml(seriesConfig.unit)}</title>
        </circle>
      `;
    })
    .join("");

  const labelIdx = [0, Math.floor((n - 1) / 2), n - 1].filter(
    (v, i, a) => a.indexOf(v) === i && points[v]
  );
  const xLabels = labelIdx
    .map(
      (idx) =>
        `<text x="${xOf(idx).toFixed(1)}" y="${h - 6}" text-anchor="middle" fill="var(--secondary-text-color)" font-size="9">${escapeHtml(String(points[idx].point_date).slice(5))}</text>`
    )
    .join("");

  const goalY =
    typeof seriesConfig.goalLine === "number" && Number.isFinite(seriesConfig.goalLine)
      ? yOf(seriesConfig.goalLine).toFixed(1)
      : null;

  return `
    <svg class="chart-svg" viewBox="0 0 ${w} ${h}">
      ${yTicks.join("")}
      ${
        goalY !== null
          ? `<line x1="${padL}" y1="${goalY}" x2="${w - padR}" y2="${goalY}" stroke="var(--success-color, #4caf50)" stroke-width="1.5" stroke-dasharray="5 3" />`
          : ""
      }
      ${rawDots}
      ${
        lineCoords.length >= 2
          ? `<polyline fill="none" stroke="${seriesConfig.lineColor}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="${lineCoords.join(" ")}" />`
          : ""
      }
      ${lineDots}
      ${xLabels}
    </svg>
  `;
}

export function renderTrendsView(
  container,
  trendsState,
  dashboard,
  uiState,
  callbacks
) {
  const lang = uiState.language;
  const days = trendsState.days || 30;
  const summary = trendsState.summary || null;
  const metric = trendsState.weightMetric || "weight";

  const eva = summary?.expected_vs_actual || {};
  const points = summary?.points || [];
  const hasBfData = points.some(
    (p) => typeof p.body_fat_pct === "number" || typeof p.body_fat_ema_pct === "number"
  );
  const hasTdeeData =
    points.filter((p) => typeof p.tdee_kcal === "number" && p.tdee_kcal > 0).length >= 2;
  const tdee = dashboard?.tdee || {};

  const statusTone =
    eva.status === "on_track"
      ? "good"
      : eva.status === "insufficient_data"
        ? ""
        : "warn";

  const activeMetricCfg =
    metric === "body_fat"
      ? {
          rawKey: "body_fat_pct",
          lineKey: "body_fat_ema_pct",
          valueKeys: ["body_fat_pct", "body_fat_ema_pct"],
          rawColor: "#ffa726",
          lineColor: "#ef6c00",
          unit: "%",
          goalLine: eva.target_body_fat_pct,
        }
      : metric === "lean_mass"
        ? {
            rawKey: null,
            lineKey: "lean_mass_kg",
            valueKeys: ["lean_mass_kg"],
            rawColor: "#26a69a",
            lineColor: "#26a69a",
            unit: "kg",
            goalLine: null,
          }
        : {
            rawKey: "weight_kg",
            lineKey: "weight_ema_kg",
            valueKeys: ["weight_kg", "weight_ema_kg"],
            rawColor: "#90a4ae",
            lineColor: "var(--primary-color, #03a9f4)",
            unit: "kg",
            goalLine: eva.target_weight_kg,
          };

  container.innerHTML = `
    <div class="subtab-bar">
      <button type="button" class="subtab-btn trends-days-btn ${days === 7 ? "active" : ""}" data-days="7">${escapeHtml(t(lang, "trends_7d"))}</button>
      <button type="button" class="subtab-btn trends-days-btn ${days === 30 ? "active" : ""}" data-days="30">${escapeHtml(t(lang, "trends_30d"))}</button>
      <button type="button" class="subtab-btn trends-days-btn ${days === 90 ? "active" : ""}" data-days="90">${escapeHtml(t(lang, "trends_90d"))}</button>
    </div>

    ${
      !summary
        ? `<div class="hint-text">${escapeHtml(t(lang, "loading"))}</div>`
        : `
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label">${escapeHtml(t(lang, "trends_kpi_pace"))}</div>
          <div class="kpi-val">
            ${eva.actual_weekly_rate_kg !== null && eva.actual_weekly_rate_kg !== undefined ? `${eva.actual_weekly_rate_kg > 0 ? "+" : ""}${eva.actual_weekly_rate_kg} kg/wk` : "—"}
          </div>
          <div class="kpi-sub">
            <span class="pill ${statusTone}" style="padding:2px 7px;font-size:0.72rem;">${escapeHtml(t(lang, `trends_status_${eva.status}`) || eva.status)}</span>
            · ${escapeHtml(t(lang, "trends_goal_label"))}: ${eva.expected_weekly_rate_kg > 0 ? "+" : ""}${eva.expected_weekly_rate_kg} kg/wk
          </div>
          ${
            eva.eta_date
              ? `<div class="kpi-sub">${escapeHtml(t(lang, "trends_eta_label"))}: <strong>${escapeHtml(eva.eta_date)}</strong> (${eva.eta_weeks}w)</div>`
              : eva.target_weight_kg
                ? `<div class="kpi-sub">${escapeHtml(t(lang, "trends_goal_label"))}: ${eva.target_weight_kg} kg</div>`
                : ""
          }
        </div>

        <div class="kpi-card">
          <div class="kpi-label">${escapeHtml(t(lang, "trends_kpi_tdee"))}</div>
          <div class="kpi-val">${Math.round(tdee.tdee_kcal || summary.adaptive_tdee_kcal || 0)} kcal</div>
          <div class="kpi-sub">
            ${summary.adaptive_confidence ? `<span class="pill" style="padding:2px 7px;font-size:0.72rem;">${escapeHtml(t(lang, `trends_conf_${summary.adaptive_confidence}`) || summary.adaptive_confidence)}</span>` : escapeHtml(summary.engine_used)}
            ${summary.two_compartment_used ? `<span class="pill good" style="padding:2px 7px;font-size:0.72rem;">${escapeHtml(t(lang, "adaptive_two_comp_badge"))}</span>` : ""}
          </div>
          ${
            summary.wearable_multiplier
              ? `<div class="kpi-sub">Wearable factor: <strong>${summary.wearable_multiplier}×</strong></div>`
              : ""
          }
        </div>

        <div class="kpi-card">
          <div class="kpi-label">${escapeHtml(t(lang, "trends_kpi_adherence"))}</div>
          <div class="kpi-val">${Math.round(summary.avg_kcal)} kcal</div>
          <div class="kpi-sub">${summary.logged_days}/${summary.total_days}d (${summary.logging_adherence_pct}%) · Target ${Math.round(summary.avg_target_kcal)} kcal</div>
        </div>
      </div>

      ${
        summary.logged_days === 0 && !points.some((p) => p.weight_kg !== null)
          ? `<div class="hint-text" style="margin-bottom: 12px;">${escapeHtml(t(lang, "trends_no_data"))}</div>`
          : ""
      }

      <div class="chart-card">
        <div class="chart-header">
          <span>🔥 ${escapeHtml(t(lang, "trends_kcal_chart_title"))}</span>
          <span class="hint-text">${Math.round(summary.kcal_adherence_pct)}% ${escapeHtml(t(lang, "target"))}</span>
        </div>
        ${renderCaloriesSvg(points)}
      </div>

      <div class="chart-card">
        <div class="chart-header">
          <span>⚖️ ${escapeHtml(t(lang, "trends_weight_chart_title"))}</span>
          <div class="controls-row">
            <button type="button" class="chip-btn trends-metric-btn ${metric === "weight" ? "active" : ""}" data-metric="weight">${escapeHtml(t(lang, "trends_metric_weight"))}</button>
            ${
              hasBfData
                ? `<button type="button" class="chip-btn trends-metric-btn ${metric === "body_fat" ? "active" : ""}" data-metric="body_fat">${escapeHtml(t(lang, "trends_metric_bf"))}</button>
                   <button type="button" class="chip-btn trends-metric-btn ${metric === "lean_mass" ? "active" : ""}" data-metric="lean_mass">${escapeHtml(t(lang, "trends_metric_lean"))}</button>`
                : ""
            }
          </div>
        </div>
        ${renderLineChartSvg(points, activeMetricCfg)}
        <div class="hint-text" style="display:flex;gap:12px;margin-top:4px;">
          ${activeMetricCfg.rawKey ? `<span>● ${escapeHtml(t(lang, "trends_raw_label"))}</span>` : ""}
          <span>━ ${escapeHtml(t(lang, "trends_ema_label"))}</span>
          ${activeMetricCfg.goalLine ? `<span>┄ ${escapeHtml(t(lang, "trends_goal_label"))} (${activeMetricCfg.goalLine}${activeMetricCfg.unit})</span>` : ""}
        </div>
      </div>

      ${
        hasTdeeData
          ? `<div class="chart-card">
               <div class="chart-header">
                 <span>⚡ ${escapeHtml(t(lang, "trends_tdee_chart_title"))}</span>
               </div>
               ${renderLineChartSvg(points, {
                 rawKey: null,
                 lineKey: "tdee_kcal",
                 valueKeys: ["tdee_kcal"],
                 rawColor: "#ab47bc",
                 lineColor: "#ab47bc",
                 unit: "kcal",
                 goalLine: null,
               })}
             </div>`
          : ""
      }

      <div class="chart-card">
        <div class="chart-header">
          <span>📊 ${escapeHtml(t(lang, "trends_macros_avg_title"))}</span>
        </div>
        <div class="kpi-grid" style="margin-bottom: 0;">
          <div class="kpi-card">
            <div class="kpi-label">${escapeHtml(t(lang, "protein"))}</div>
            <div class="kpi-val">${round1(summary.avg_protein_g)}g</div>
            <div class="kpi-sub">/ ${round1(summary.target_protein_g)}g</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${escapeHtml(t(lang, "carbs"))}</div>
            <div class="kpi-val">${round1(summary.avg_carbs_g)}g</div>
            <div class="kpi-sub">/ ${round1(summary.target_carbs_g)}g</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${escapeHtml(t(lang, "fat"))}</div>
            <div class="kpi-val">${round1(summary.avg_fat_g)}g</div>
            <div class="kpi-sub">/ ${round1(summary.target_fat_g)}g</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${escapeHtml(t(lang, "fiber"))}</div>
            <div class="kpi-val">${round1(summary.avg_fiber_g)}g</div>
            <div class="kpi-sub">/ ${round1(summary.target_fiber_g)}g</div>
          </div>
        </div>
      </div>
    `
    }
  `;

  container.querySelectorAll(".trends-days-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const nextDays = Number(btn.getAttribute("data-days") || 30);
      callbacks.onChangeDays(nextDays);
    });
  });

  container.querySelectorAll(".trends-metric-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      callbacks.onChangeMetric(btn.getAttribute("data-metric") || "weight");
    });
  });
}
