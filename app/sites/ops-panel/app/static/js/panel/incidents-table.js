/** Incidents tile — open problems from GET /health → incidents. */

import { state } from './state.js';

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderIncidentsTable(body) {
  const stale = !state.status || state.status.stale;
  if (stale) {
    body.innerHTML =
      '<p class="monitor-tile__meta monitor-tile__meta--stale">Incidents unavailable</p>';
    return;
  }
  const rows = Array.isArray(state.status.incidents) ? state.status.incidents : [];
  if (!rows.length) {
    body.innerHTML = '<p class="monitor-tile__meta">No open incidents</p>';
    return;
  }
  const bodyRows = rows
    .map(row => {
      const sev = row.severity === 'error' ? 'error' : 'warn';
      return (
        '<tr class="inc-row inc-row--' +
        sev +
        '">' +
        `<td class="inc-sev inc-sev--${sev}">${escapeHtml(sev)}</td>` +
        `<td class="inc-title">${escapeHtml(row.title || '')}</td>` +
        `<td class="inc-detail">${escapeHtml(row.detail || '')}</td>` +
        '</tr>'
      );
    })
    .join('');
  body.innerHTML =
    '<table class="tile-table tile-table--incidents">' +
    '<thead><tr><th scope="col">Level</th><th scope="col">What</th><th scope="col">Detail</th></tr></thead>' +
    `<tbody>${bodyRows}</tbody></table>`;
}

export function mountIncidentsTable(body) {
  renderIncidentsTable(body);
}
