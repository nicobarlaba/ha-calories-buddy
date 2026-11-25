/** Barcode Scanner, HTTP Photo Decoder & Manual EAN Lookup Modal */
import { decodeBarcodeFromImageSource } from "./barcode-decoder.js";
import { t } from "../i18n/index.js";
import { escapeHtml, localizedFoodName, pickDefaultSlotForNow, round1 } from "../utils.js";

export function openBarcodeScannerModal(host, ctx, callbacks) {
  const { language, api, personId, mealSlots } = ctx;
  const slots = mealSlots && mealSlots.length ? mealSlots : [{ name: "Breakfast" }, { name: "Lunch" }, { name: "Snack" }, { name: "Dinner" }];
  const hasNativeDetector = "BarcodeDetector" in window;
  const isSecure = window.isSecureContext || ["localhost", "127.0.0.1"].includes(window.location.hostname);

  let stream = null;
  let rafId = null;
  let scanning = false;
  let statusMsg = "";
  let currentEan = "";
  let foundFood = null;
  let notFound = false;
  let chosenSlot = pickDefaultSlotForNow(slots);

  const stopCamera = () => {
    scanning = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (stream) {
      stream.getTracks().forEach((tr) => tr.stop());
      stream = null;
    }
  };

  const closeModal = () => {
    stopCamera();
    host.innerHTML = "";
  };

  const doLookup = async (ean) => {
    const clean = String(ean || "").trim();
    if (!clean) return;
    currentEan = clean;
    statusMsg = "";
    notFound = false;
    foundFood = null;
    render();
    try {
      const resp = await api.lookupBarcode(personId, clean);
      if (resp?.food) {
        foundFood = resp.food;
      } else {
        notFound = true;
      }
    } catch (err) {
      statusMsg = err?.message || "Lookup failed";
    }
    render();
  };

  const startCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !hasNativeDetector) {
      statusMsg = t(language, "camera_https_hint");
      render();
      return;
    }
    try {
      statusMsg = "";
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      scanning = true;
      render();
      const video = host.querySelector("#barcode-video");
      if (video) {
        video.srcObject = stream;
        await video.play();
      }
      const detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"],
      });
      const tick = async () => {
        if (!scanning || !video || video.readyState < 2) {
          if (scanning) rafId = requestAnimationFrame(tick);
          return;
        }
        try {
          const codes = await detector.detect(video);
          if (codes && codes.length > 0 && codes[0].rawValue) {
            stopCamera();
            await doLookup(codes[0].rawValue);
            return;
          }
        } catch {
          // Ignore single frame decode error
        }
        if (scanning) rafId = requestAnimationFrame(tick);
      };
      rafId = requestAnimationFrame(tick);
    } catch (err) {
      stopCamera();
      statusMsg = err?.message || t(language, "camera_https_hint");
      render();
    }
  };

  const decodePhotoFile = async (file) => {
    if (!file) return;
    stopCamera();
    statusMsg = t(language, "decoding_photo");
    foundFood = null;
    notFound = false;
    render();

    let objectUrl = null;
    try {
      let decoded = null;
      if (typeof createImageBitmap === "function") {
        const bitmap = await createImageBitmap(file);
        try {
          decoded = await decodeBarcodeFromImageSource(bitmap);
        } finally {
          bitmap.close?.();
        }
      } else {
        objectUrl = URL.createObjectURL(file);
        const img = new Image();
        await new Promise((resolve, reject) => {
          img.onload = resolve;
          img.onerror = reject;
          img.src = objectUrl;
        });
        decoded = await decodeBarcodeFromImageSource(img);
      }

      if (decoded) {
        await doLookup(decoded);
      } else {
        statusMsg = t(language, "photo_barcode_not_found");
        render();
      }
    } catch (err) {
      statusMsg = err?.message || t(language, "photo_barcode_not_found");
      render();
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    }
  };

  const render = () => {
    const draft = foundFood || (notFound
      ? {
          food_id: null,
          name: "",
          brand: "",
          barcode_ean: currentEan,
          category: "other",
          preparation_state: "ready_to_eat",
          yield_factor: 1.0,
          atwater_warning: false,
          macros_per_100g: { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 },
        }
      : null);

    host.innerHTML = `
      <div class="modal-backdrop">
        <div class="modal-panel">
          <div class="modal-header">
            <span>📷 ${escapeHtml(t(language, "barcode_modal_title"))}</span>
            <button type="button" class="secondary" id="close-barcode-modal">✕</button>
          </div>

          <div class="controls-row" style="margin-bottom: 10px;">
            <button type="button" id="photo-scan-btn">📸 ${escapeHtml(t(language, "photo_barcode_btn"))}</button>
            <input type="file" id="barcode-photo-input" accept="image/*" capture="environment" style="display: none;" />
            ${
              scanning
                ? `<button type="button" class="danger" id="stop-cam-btn">${escapeHtml(t(language, "stop_camera_btn"))}</button>`
                : `<button type="button" class="secondary" id="start-cam-btn" ${!isSecure || !hasNativeDetector ? "disabled" : ""}>${escapeHtml(t(language, "start_camera_btn"))}</button>`
            }
          </div>

          ${!isSecure || !hasNativeDetector ? `<div class="hint-text" style="margin-bottom: 8px;">${escapeHtml(t(language, "camera_https_hint"))}</div>` : ""}
          ${scanning ? `<video id="barcode-video" class="scanner-video" autoplay muted playsinline></video>` : ""}
          ${statusMsg ? `<div class="error-banner">${escapeHtml(statusMsg)}</div>` : ""}

          <div class="quick-input-row" style="margin-bottom: 10px;">
            <input type="text" id="manual-ean-input" value="${escapeHtml(currentEan)}" placeholder="${escapeHtml(t(language, "ean_placeholder"))}" />
            <button type="button" id="lookup-ean-btn">${escapeHtml(t(language, "lookup_btn"))}</button>
          </div>

          ${notFound ? `<div class="hint-text" style="margin-bottom: 8px;">${escapeHtml(t(language, "product_not_found"))}</div>` : ""}

          ${
            draft
              ? `
            <div class="meal-card">
              ${draft.atwater_warning ? `<div class="pill warn" style="margin-bottom: 8px;">⚠️ ${escapeHtml(t(language, "atwater_warning"))}</div>` : ""}
              <div class="form-grid">
                <div class="form-field full">
                  <label>${escapeHtml(t(language, "food_name_label"))}</label>
                  <input type="text" id="bc-name" value="${escapeHtml(foundFood ? localizedFoodName(foundFood, language) : draft.name)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "brand_label"))}</label>
                  <input type="text" id="bc-brand" value="${escapeHtml(draft.brand || "")}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "state_label"))}</label>
                  <select id="bc-state">
                    <option value="ready_to_eat" ${draft.preparation_state === "ready_to_eat" ? "selected" : ""}>${escapeHtml(t(language, "state_ready_to_eat"))}</option>
                    <option value="raw" ${draft.preparation_state === "raw" ? "selected" : ""}>${escapeHtml(t(language, "state_raw"))}</option>
                    <option value="cooked" ${draft.preparation_state === "cooked" ? "selected" : ""}>${escapeHtml(t(language, "state_cooked"))}</option>
                  </select>
                </div>
                <div class="form-field">
                  <label>kcal (${escapeHtml(t(language, "per_100g"))})</label>
                  <input type="number" step="0.1" id="bc-kcal" value="${round1(draft.macros_per_100g.kcal)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "protein"))} (g)</label>
                  <input type="number" step="0.1" id="bc-p" value="${round1(draft.macros_per_100g.protein_g)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "carbs"))} (g)</label>
                  <input type="number" step="0.1" id="bc-c" value="${round1(draft.macros_per_100g.carbs_g)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "fat"))} (g)</label>
                  <input type="number" step="0.1" id="bc-f" value="${round1(draft.macros_per_100g.fat_g)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "fiber"))} (g)</label>
                  <input type="number" step="0.1" id="bc-fib" value="${round1(draft.macros_per_100g.fiber_g || 0)}" />
                </div>
                <div class="form-field">
                  <label>${escapeHtml(t(language, "quantity_label"))} (g)</label>
                  <input type="number" step="1" min="1" id="bc-qty" value="100" />
                </div>
                <div class="form-field full">
                  <label>${escapeHtml(t(language, "meal_slot_label"))}</label>
                  <div class="quick-actions">
                    ${slots
                      .map(
                        (s) =>
                          `<button type="button" class="chip-btn bc-slot-chip ${chosenSlot === s.name ? "active" : ""}" data-slot="${escapeHtml(s.name)}">${escapeHtml(s.name)}</button>`
                      )
                      .join("")}
                  </div>
                </div>
              </div>
              <div class="meal-actions">
                <button type="button" class="secondary" id="bc-save-global">${escapeHtml(t(language, "save_global_btn"))}</button>
                <button type="button" class="success" id="bc-save-and-log">${escapeHtml(t(language, "save_and_log_btn"))}</button>
              </div>
            </div>
          `
              : ""
          }
        </div>
      </div>
    `;

    host.querySelector("#close-barcode-modal")?.addEventListener("click", closeModal);
    host.querySelector("#photo-scan-btn")?.addEventListener("click", () => {
      host.querySelector("#barcode-photo-input")?.click();
    });
    host.querySelector("#barcode-photo-input")?.addEventListener("change", async (ev) => {
      const input = ev.target;
      const file = input?.files?.[0];
      if (input) input.value = "";
      if (file) {
        await decodePhotoFile(file);
      }
    });
    host.querySelector("#start-cam-btn")?.addEventListener("click", startCamera);
    host.querySelector("#stop-cam-btn")?.addEventListener("click", () => {
      stopCamera();
      render();
    });

    host.querySelector("#lookup-ean-btn")?.addEventListener("click", () => {
      const val = host.querySelector("#manual-ean-input")?.value || "";
      doLookup(val);
    });
    host.querySelector("#manual-ean-input")?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        doLookup(ev.target.value);
      }
    });

    host.querySelectorAll(".bc-slot-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        chosenSlot = chip.getAttribute("data-slot") || chosenSlot;
        render();
      });
    });

    const collectFoodPayload = () => ({
      food_id: draft?.food_id || null,
      name: (host.querySelector("#bc-name")?.value || "").trim() || `Barcode ${currentEan}`,
      brand: (host.querySelector("#bc-brand")?.value || "").trim() || null,
      barcode_ean: currentEan,
      preparation_state: host.querySelector("#bc-state")?.value || "ready_to_eat",
      kcal_per_100g: Number(host.querySelector("#bc-kcal")?.value || 0),
      protein_g_per_100g: Number(host.querySelector("#bc-p")?.value || 0),
      carbs_g_per_100g: Number(host.querySelector("#bc-c")?.value || 0),
      fat_g_per_100g: Number(host.querySelector("#bc-f")?.value || 0),
      fiber_g_per_100g: Number(host.querySelector("#bc-fib")?.value || 0),
      verified: true,
      scope: "global",
    });

    host.querySelector("#bc-save-global")?.addEventListener("click", async () => {
      const saved = await api.saveFood(personId, collectFoodPayload());
      foundFood = saved.food;
      notFound = false;
      await callbacks.onVaultUpdated();
      render();
    });

    host.querySelector("#bc-save-and-log")?.addEventListener("click", async () => {
      const saved = await api.saveFood(personId, collectFoodPayload());
      const qty = Number(host.querySelector("#bc-qty")?.value || 100);
      closeModal();
      await callbacks.onLogFood({
        food_id: saved.food.food_id,
        name: saved.food.name,
        quantity: qty,
        unit: "g",
        state: saved.food.preparation_state,
        meal_slot: chosenSlot,
      });
    });
  };

  render();
}
