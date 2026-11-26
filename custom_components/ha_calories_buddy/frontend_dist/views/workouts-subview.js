/** Workouts Subview (inside Plan -> Menu | Workouts): Training Day switch, schedule, Garmin Calendar & Activities */
import { t } from "../i18n/index.js";
import { escapeHtml, round1 } from "../utils.js";

const ACTIVITY_TYPES = [
  "strength",
  "cardio",
  "running",
  "cycling",
  "hiit",
  "mobility",
  "sport",
  "other",
];

const ACTIVITY_ICONS = {
  strength: "🏋️",
  cardio: "💓",
  running: "🏃",
  cycling: "🚴",
  hiit: "⚡",
  mobility: "🧘",
  sport: "⚽",
  other: "🏅",
};

export function renderWorkoutsSubview(
  container,
  workoutState,
  dashboard,
  uiState,
  callbacks
) {
  const lang = uiState.language;
  const info = workoutState.data || {};
  const isTraining = Boolean(
    info.is_training_day ?? dashboard?.workouts?.is_training_day
  );
  const source =
    info.training_day_source || dashboard?.workouts?.source || "rest";
  const hasManualOverride = Boolean(info.has_manual_override);
  const schedule = info.schedule || [];
  const calendarWorkouts = info.calendar_workouts || [];
  const recentActivities = info.recent_activities || [];
  const targets = dashboard?.targets || {};

  const enabledByDay = new Map();
  for (let d = 0; d < 7; d++) enabledByDay.set(d, []);
  for (const item of schedule) {
    if (item.enabled && enabledByDay.has(item.weekday)) {
      enabledByDay.get(item.weekday).push(item);
    }
  }

  container.innerHTML = `
    <div class="training-banner ${isTraining ? "is-training" : ""}">
      <div>
        <div style="font-size: 1rem; font-weight: 700; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          <span>${isTraining ? "🏋️" : "🧘"} ${escapeHtml(t(lang, isTraining ? "training_day_badge" : "rest_day_badge"))}</span>
          <span class="pill">${escapeHtml(t(lang, `training_source_${source}`) || source)}</span>
          ${
            info.has_manual_training_targets && isTraining
              ? `<span class="pill good">${escapeHtml(t(lang, "training_targets_on_badge"))}</span>`
              : ""
          }
        </div>
        <div class="hint-text tabular" style="margin-top: 4px;">
          ${escapeHtml(t(lang, "target"))}: <strong>${Math.round(targets.target_kcal || 0)} kcal</strong> · P ${round1(targets.protein_g)}g · C ${round1(targets.carbs_g)}g · F ${round1(targets.fat_g)}g
        </div>
      </div>
      <div class="controls-row">
        <button type="button" class="${isTraining ? "success" : "secondary"}" id="wrk-toggle-on" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "training_set_on_btn"))}</button>
        <button type="button" class="${!isTraining ? "warning" : "secondary"}" id="wrk-toggle-off" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "training_set_off_btn"))}</button>
        ${
          hasManualOverride
            ? `<button type="button" class="chip-btn" id="wrk-reset-auto" ${uiState.busy ? "disabled" : ""}>↺ ${escapeHtml(t(lang, "training_reset_auto_btn"))}</button>`
            : ""
        }
      </div>
    </div>

    <div class="section-title" style="display:flex;justify-content:space-between;align-items:center;">
      <span>🗓️ ${escapeHtml(t(lang, "workouts_weekly_title"))}</span>
      <button type="button" class="chip-btn" id="wrk-toggle-form">${escapeHtml(t(lang, "workouts_add_btn"))}</button>
    </div>

    <div class="workout-day-strip">
      ${[0, 1, 2, 3, 4, 5, 6]
        .map((wd) => {
          const dayItems = enabledByDay.get(wd) || [];
          const has = dayItems.length > 0;
          return `
            <div class="workout-day-cell ${has ? "has-workout" : ""}" title="${escapeHtml(dayItems.map((i) => i.title).join(", "))}">
              <div>${escapeHtml(t(lang, `wday_${wd}`))}</div>
              <div>${has ? dayItems.map((i) => ACTIVITY_ICONS[i.activity_type] || "🏋️").join("") : "—"}</div>
            </div>
          `;
        })
        .join("")}
    </div>

    ${
      workoutState.showForm
        ? `
      <div class="meal-card" style="margin-bottom: 12px;">
        <div class="form-grid">
          <div class="form-field">
            <label>${escapeHtml(t(lang, "workouts_weekday_label"))}</label>
            <select id="wrk-weekday">
              ${[0, 1, 2, 3, 4, 5, 6]
                .map((wd) => `<option value="${wd}">${escapeHtml(t(lang, `wday_${wd}`))}</option>`)
                .join("")}
            </select>
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "workouts_type_label"))}</label>
            <select id="wrk-type">
              ${ACTIVITY_TYPES.map((tp) => `<option value="${tp}">${ACTIVITY_ICONS[tp]} ${escapeHtml(t(lang, `wtype_${tp}`) || tp)}</option>`).join("")}
            </select>
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "workouts_title_label"))}</label>
            <input type="text" id="wrk-title" placeholder="es. Upper Body / Corsa 10km" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "workouts_duration_label"))}</label>
            <input type="number" id="wrk-duration" min="5" max="480" value="60" />
          </div>
          <div class="form-field">
            <label>${escapeHtml(t(lang, "workouts_time_label"))}</label>
            <input type="text" id="wrk-time" value="18:00" />
          </div>
          <div class="form-field full">
            <label>${escapeHtml(t(lang, "workouts_kcal_label"))} (${lang === "it" ? "opzionale" : "optional"})</label>
            <input type="number" id="wrk-kcal" min="0" max="5000" step="10" placeholder="350" />
          </div>
        </div>
        <div class="meal-actions">
          <button type="button" class="success" id="wrk-save-btn" ${uiState.busy ? "disabled" : ""}>${escapeHtml(t(lang, "workouts_save_btn"))}</button>
          <button type="button" class="secondary" id="wrk-cancel-btn">${escapeHtml(t(lang, "cancel_btn"))}</button>
        </div>
      </div>
    `
        : ""
    }

    ${
      !schedule.length
        ? `<div class="hint-text" style="margin-bottom: 12px;">${escapeHtml(t(lang, "workouts_empty"))}</div>`
        : schedule
            .map(
              (item) => `
          <div class="food-list-item">
            <div>
              <div>
                <strong>${escapeHtml(t(lang, `wday_${item.weekday}`))} ${escapeHtml(item.time_of_day)}</strong> ·
                ${ACTIVITY_ICONS[item.activity_type] || "🏋️"} ${escapeHtml(item.title)}
                ${!item.enabled ? `<span class="pill warn">OFF</span>` : ""}
              </div>
              <div class="meal-item-meta">
                ${escapeHtml(t(lang, `wtype_${item.activity_type}`) || item.activity_type)} · ${Number(item.duration_min)} min
                ${item.estimated_kcal ? ` · ~${Math.round(item.estimated_kcal)} kcal` : ""}
              </div>
            </div>
            <div class="controls-row">
              <button type="button" class="chip-btn wrk-toggle-enabled ${item.enabled ? "active" : ""}" data-id="${escapeHtml(item.schedule_id)}">${item.enabled ? "ON" : "OFF"}</button>
              <button type="button" class="icon-danger-btn wrk-delete-btn" data-id="${escapeHtml(item.schedule_id)}">✕</button>
            </div>
          </div>
        `
            )
            .join("")
    }

    ${
      calendarWorkouts.length
        ? `<div class="section-title">📅 ${escapeHtml(t(lang, "workouts_calendar_title"))}</div>
           ${calendarWorkouts
             .map(
               (cw) => `
             <div class="food-list-item">
               <div>
                 <strong>${escapeHtml(cw.summary)}</strong>
                 <div class="meal-item-meta">${escapeHtml(String(cw.start).slice(0, 16).replace("T", " "))}${cw.description ? ` · ${escapeHtml(cw.description.slice(0, 80))}` : ""}</div>
               </div>
             </div>
           `
             )
             .join("")}`
        : ""
    }

    <div class="section-title">⌚ ${escapeHtml(t(lang, "workouts_recent_title"))}</div>
    ${
      !recentActivities.length
        ? `<div class="hint-text">${escapeHtml(t(lang, "workouts_no_activities"))}</div>`
        : recentActivities
            .slice(0, 6)
            .map(
              (act) => `
          <div class="food-list-item">
            <div>
              <strong>${escapeHtml(act.name)}</strong>
              <div class="meal-item-meta">
                ${escapeHtml(act.activity_type)}
                ${act.duration_min ? ` · ${round1(act.duration_min)} min` : ""}
                ${act.distance_km ? ` · ${act.distance_km} km` : ""}
                ${act.start_time ? ` · ${escapeHtml(String(act.start_time).slice(0, 16).replace("T", " "))}` : ""}
              </div>
            </div>
            ${act.calories ? `<span class="pill tabular">${Math.round(act.calories)} kcal</span>` : ""}
          </div>
        `
            )
            .join("")
    }
  `;

  container.querySelector("#wrk-toggle-on")?.addEventListener("click", () => {
    callbacks.onSetTrainingDay({ is_training_day: true });
  });
  container.querySelector("#wrk-toggle-off")?.addEventListener("click", () => {
    callbacks.onSetTrainingDay({ is_training_day: false });
  });
  container.querySelector("#wrk-reset-auto")?.addEventListener("click", () => {
    callbacks.onSetTrainingDay({ clear_override: true });
  });

  container.querySelector("#wrk-toggle-form")?.addEventListener("click", callbacks.onToggleWorkoutForm);
  container.querySelector("#wrk-cancel-btn")?.addEventListener("click", callbacks.onToggleWorkoutForm);

  container.querySelector("#wrk-save-btn")?.addEventListener("click", () => {
    const title = (container.querySelector("#wrk-title")?.value || "").trim();
    if (!title) return;
    const kcalRaw = container.querySelector("#wrk-kcal")?.value;
    callbacks.onSaveWorkout({
      weekday: Number(container.querySelector("#wrk-weekday")?.value || 0),
      activity_type: container.querySelector("#wrk-type")?.value || "strength",
      title,
      duration_min: Number(container.querySelector("#wrk-duration")?.value || 60),
      time_of_day: (container.querySelector("#wrk-time")?.value || "18:00").trim(),
      estimated_kcal: kcalRaw ? Number(kcalRaw) : null,
      enabled: true,
    });
  });

  container.querySelectorAll(".wrk-toggle-enabled").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-id");
      const found = schedule.find((s) => s.schedule_id === id);
      if (found) {
        callbacks.onSaveWorkout({
          schedule_id: found.schedule_id,
          weekday: found.weekday,
          title: found.title,
          activity_type: found.activity_type,
          duration_min: found.duration_min,
          estimated_kcal: found.estimated_kcal,
          time_of_day: found.time_of_day,
          notes: found.notes,
          enabled: !found.enabled,
        });
      }
    });
  });

  container.querySelectorAll(".wrk-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-id");
      if (id) callbacks.onDeleteWorkout(id);
    });
  });
}
