/** Shared Shadow DOM stylesheet matching Home Assistant light/dark themes (v0.3.0) */

export const CARD_STYLES = `
  :host {
    display: block;
    font-family: var(--paper-font-body1_-_font-family, inherit);
    color: var(--primary-text-color);
  }
  ha-card {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--ha-card-border-radius, 14px);
    box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0, 0, 0, 0.08));
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
    font-size: 1.12rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .header-emblem {
    width: 24px;
    height: 24px;
    border-radius: 7px;
    background: linear-gradient(135deg, #03a9f4, #26a69a);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.85rem;
    flex-shrink: 0;
  }
  .controls-row {
    display: flex;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
  }
  .icon-btn {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    color: var(--primary-text-color);
    border-radius: 999px;
    width: 32px;
    height: 32px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.95rem;
  }
  .icon-btn.active {
    background: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
  }
  .tabular {
    font-variant-numeric: tabular-nums;
  }
  select, input, button, textarea {
    font-family: inherit;
    font-size: 0.9rem;
  }
  select, input[type="text"], input[type="number"], input[type="date"], textarea {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.25));
    border-radius: 8px;
    padding: 7px 10px;
    outline: none;
    box-sizing: border-box;
  }
  select:focus, input:focus, textarea:focus {
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
  .chip-btn {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    color: var(--primary-text-color);
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 0.78rem;
  }
  .chip-btn.active {
    background: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
  }
  .tabs {
    display: flex;
    gap: 4px;
    padding: 4px;
    border-radius: 11px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.1));
    margin-bottom: 14px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .tab-btn {
    flex: 1 0 auto;
    background: transparent;
    color: var(--secondary-text-color);
    padding: 7px 10px;
    border-radius: 8px;
    font-size: 0.84rem;
    font-weight: 600;
    white-space: nowrap;
    text-align: center;
  }
  .tab-btn.active {
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
  }
  .summary-grid {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 16px;
    align-items: center;
    margin-bottom: 12px;
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
    gap: 7px;
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
  .macro-pie-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .macro-pie-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .macro-legend {
    display: grid;
    grid-template-columns: repeat(2, auto);
    gap: 5px 14px;
    font-size: 0.8rem;
  }
  .legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .legend-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
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
  .pill.bad {
    background: rgba(219, 68, 55, 0.15);
    color: var(--error-color, #db4437);
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
  .meal-card.planned {
    border-style: dashed;
    border-color: var(--primary-color, #03a9f4);
    background: rgba(3, 169, 244, 0.04);
  }
  .meal-card.covered {
    opacity: 0.68;
    border-style: solid;
    border-color: var(--divider-color, rgba(127, 127, 127, 0.3));
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.05));
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
  .slot-inline-select {
    padding: 3px 8px;
    font-size: 0.84rem;
    font-weight: 600;
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
    align-items: center;
  }
  .day-strip {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 6px;
    margin-bottom: 10px;
  }
  .day-pill {
    flex: 0 0 auto;
    min-width: 72px;
    padding: 6px 8px;
    border-radius: 10px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.25));
    background: transparent;
    color: var(--primary-text-color);
    text-align: center;
    font-size: 0.78rem;
  }
  .day-pill.active {
    background: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
    border-color: transparent;
  }
  .pagination-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--divider-color, rgba(127, 127, 127, 0.16));
    flex-wrap: wrap;
    font-size: 0.82rem;
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
    width: min(540px, 100%);
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
  .suggest-banner {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 8px;
    background: rgba(3, 169, 244, 0.08);
    border: 1px solid rgba(3, 169, 244, 0.24);
    margin-bottom: 10px;
    font-size: 0.82rem;
  }
  .course-line {
    margin-top: 3px;
    line-height: 1.45;
  }
  .course-tag {
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.16));
    color: var(--primary-text-color);
    margin-right: 4px;
  }
  .icon-danger-btn {
    padding: 4px 8px;
    border-radius: 6px;
    background: rgba(219, 68, 55, 0.12);
    color: var(--error-color, #db4437);
    font-weight: 600;
    font-size: 0.78rem;
  }
  .edit-food-results {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: 4px;
    max-height: 170px;
    overflow-y: auto;
  }
  .edit-food-option {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    width: 100%;
    text-align: left;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.25));
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 0.8rem;
  }
  .toast.success-toast {
    background: rgba(76, 175, 80, 0.14);
    border: 1px solid rgba(76, 175, 80, 0.35);
    color: var(--success-color, #2e7d32);
  }
  .add-item-box {
    border: 1px dashed var(--divider-color, rgba(127, 127, 127, 0.35));
    border-radius: 8px;
    padding: 8px 10px;
    margin-top: 8px;
    background: rgba(127, 127, 127, 0.04);
  }
  .biometrics-card {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
    border-radius: 10px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .biometrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 8px;
  }
  .bio-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 0.82rem;
  }
  .bio-stat-label {
    font-size: 0.72rem;
    color: var(--secondary-text-color);
  }
  .bio-stat-val {
    font-size: 1rem;
    font-weight: 700;
  }
  .subtab-bar {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
    padding: 4px;
    border-radius: 10px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.1));
  }
  .subtab-btn {
    flex: 1;
    padding: 7px 10px;
    border-radius: 8px;
    background: transparent;
    color: var(--secondary-text-color);
    font-weight: 600;
    font-size: 0.84rem;
    border: none;
  }
  .subtab-btn.active {
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  }
  .recipe-preview-box {
    margin-top: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(76, 175, 80, 0.09);
    border: 1px solid rgba(76, 175, 80, 0.3);
    font-size: 0.82rem;
  }
  .recipe-ing-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    padding: 5px 0;
    border-top: 1px dashed var(--divider-color, rgba(127, 127, 127, 0.2));
    flex-wrap: wrap;
    font-size: 0.82rem;
  }
  .training-banner {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding: 12px;
    border-radius: 11px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .training-banner.is-training {
    border-color: rgba(76, 175, 80, 0.45);
    background: rgba(76, 175, 80, 0.08);
  }
  .aisle-group {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    border-radius: 10px;
    padding: 8px 12px;
    margin-bottom: 10px;
  }
  .aisle-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
    margin-bottom: 6px;
  }
  .shopping-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-top: 1px dashed var(--divider-color, rgba(127, 127, 127, 0.16));
    font-size: 0.86rem;
    flex-wrap: wrap;
  }
  .shopping-row.unchecked {
    opacity: 0.52;
    text-decoration: line-through;
  }
  .shopping-row-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1 1 180px;
    min-width: 0;
  }
  .shopping-row-right {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 8px;
    margin-bottom: 12px;
  }
  .kpi-card {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    border-radius: 10px;
    padding: 10px 12px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.05));
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .kpi-label {
    font-size: 0.74rem;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
  }
  .kpi-val {
    font-size: 1.18rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .kpi-sub {
    font-size: 0.76rem;
    color: var(--secondary-text-color);
  }
  .chart-card {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    border-radius: 10px;
    padding: 10px 12px;
    margin-bottom: 10px;
  }
  .chart-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    flex-wrap: wrap;
    font-size: 0.88rem;
    font-weight: 600;
  }
  .chart-svg {
    width: 100%;
    height: auto;
    display: block;
    overflow: visible;
  }
  .workout-day-strip {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 4px;
    margin-bottom: 10px;
  }
  .workout-day-cell {
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
    border-radius: 8px;
    padding: 5px 4px;
    text-align: center;
    font-size: 0.72rem;
    background: transparent;
    color: var(--primary-text-color);
  }
  .workout-day-cell.has-workout {
    border-color: rgba(76, 175, 80, 0.55);
    background: rgba(76, 175, 80, 0.12);
    font-weight: 600;
  }
  @media (max-width: 520px) {
    ha-card {
      padding: 12px;
    }
    .form-grid {
      grid-template-columns: 1fr;
    }
    .macro-legend {
      grid-template-columns: 1fr;
    }
  }
`;
