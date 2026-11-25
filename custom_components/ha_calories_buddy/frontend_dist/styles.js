/** Shared Shadow DOM stylesheet matching Home Assistant light/dark themes */

export const CARD_STYLES = `
  :host {
    display: block;
    font-family: var(--paper-font-body1_-_font-family, inherit);
    color: var(--primary-text-color);
  }
  ha-card {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0, 0, 0, 0.08));
    padding: 16px;
    overflow: hidden;
  }
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .header-title {
    font-size: 1.15rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .controls-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  select, input, button {
    font-family: inherit;
    font-size: 0.9rem;
  }
  select, input[type="text"], input[type="number"] {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.25));
    border-radius: 8px;
    padding: 7px 10px;
    outline: none;
    box-sizing: border-box;
  }
  select:focus, input:focus {
    border-color: var(--primary-color, #03a9f4);
  }
  button {
    cursor: pointer;
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 500;
    background: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
    transition: opacity 0.15s ease;
  }
  button:hover:not(:disabled) {
    opacity: 0.9;
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  button.secondary {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.14));
    color: var(--primary-text-color);
  }
  button.success {
    background: var(--success-color, #4caf50);
    color: #fff;
  }
  button.warning {
    background: var(--warning-color, #ff9800);
    color: #fff;
  }
  button.danger {
    background: var(--error-color, #db4437);
    color: #fff;
  }
  .tabs {
    display: flex;
    gap: 6px;
    border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    padding-bottom: 8px;
    margin-bottom: 14px;
  }
  .tab-btn {
    background: transparent;
    color: var(--secondary-text-color);
    padding: 6px 12px;
    border-radius: 8px;
  }
  .tab-btn.active {
    background: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
  }
  .summary-grid {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 16px;
    align-items: center;
    margin-bottom: 14px;
  }
  @media (max-width: 480px) {
    .summary-grid {
      grid-template-columns: 1fr;
    }
  }
  .ring-wrap {
    position: relative;
    width: 116px;
    height: 116px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .ring-center {
    position: absolute;
    text-align: center;
    line-height: 1.2;
  }
  .ring-kcal {
    font-size: 1.2rem;
    font-weight: 700;
  }
  .ring-sub {
    font-size: 0.72rem;
    color: var(--secondary-text-color);
  }
  .macro-bars {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .macro-row {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .macro-label {
    display: flex;
    justify-content: space-between;
    font-size: 0.82rem;
  }
  .bar-track {
    width: 100%;
    height: 8px;
    border-radius: 4px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.16));
    overflow: hidden;
  }
  .bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.25s ease;
  }
  .pills {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .pill {
    font-size: 0.78rem;
    padding: 4px 9px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    color: var(--primary-text-color);
  }
  .pill.warn {
    background: rgba(255, 152, 0, 0.18);
    color: var(--warning-color, #e65100);
  }
  .pill.good {
    background: rgba(76, 175, 80, 0.16);
    color: var(--success-color, #2e7d32);
  }
  .section-title {
    font-size: 0.92rem;
    font-weight: 600;
    margin: 14px 0 8px;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .quick-bar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 12px;
    padding: 10px;
    border-radius: 10px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
  }
  .quick-input-row {
    display: flex;
    gap: 8px;
  }
  .quick-input-row input {
    flex: 1;
    min-width: 0;
  }
  .quick-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  .hint-text {
    font-size: 0.78rem;
    color: var(--secondary-text-color);
  }
  .meal-card {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    border-radius: 10px;
    padding: 10px 12px;
    margin-bottom: 8px;
  }
  .meal-card.pending {
    border-color: var(--warning-color, #ff9800);
    background: rgba(255, 152, 0, 0.06);
  }
  .meal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-weight: 600;
    font-size: 0.92rem;
  }
  .meal-item-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    border-top: 1px dashed var(--divider-color, rgba(127, 127, 127, 0.16));
    font-size: 0.84rem;
    flex-wrap: wrap;
  }
  .meal-item-meta {
    color: var(--secondary-text-color);
    font-size: 0.78rem;
  }
  .meal-actions {
    display: flex;
    gap: 6px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 999;
    padding: 16px;
  }
  .modal-panel {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    color: var(--primary-text-color);
    border-radius: 12px;
    padding: 16px;
    width: min(520px, 100%);
    max-height: 90vh;
    overflow-y: auto;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
  }
  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    font-weight: 600;
    font-size: 1.05rem;
  }
  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    margin-bottom: 10px;
  }
  .form-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 0.8rem;
  }
  .form-field.full {
    grid-column: 1 / -1;
  }
  video.scanner-video {
    width: 100%;
    max-height: 240px;
    border-radius: 8px;
    background: #000;
    object-fit: cover;
    margin-bottom: 8px;
  }
  .toast {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.16));
    margin-bottom: 10px;
    font-size: 0.86rem;
  }
  .error-banner {
    padding: 8px 12px;
    border-radius: 8px;
    background: rgba(219, 68, 55, 0.14);
    color: var(--error-color, #db4437);
    margin-bottom: 10px;
    font-size: 0.85rem;
  }
  .food-list-item {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
`;
