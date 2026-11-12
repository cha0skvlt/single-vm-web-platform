/** SITES-1 — table tile fed by GET /sites (Traefik routers + probes). */

import { state } from './state.js';

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function statusClass(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'up') return 'site-status site-status--up';
  if (s === 'down') return 'site-status site-status--down';
  return 'site-status site-status--unknown';
}

function renderSitesTable(body) {
  const sites = state.sites;
  if (!sites || sites.stale) {
    body.innerHTML =
      '<table class="tile-table tile-table--sites">' +
      '<thead><tr><th scope="col">Domain</th><th scope="col">Status</th></tr></thead>' +
      '<tbody><tr><td colspan="2" class="monitor-tile__meta monitor-tile__meta--stale">Loading sites…</td></tr>' +
      '</tbody></table>';
    return;
  }
  if (!sites.rows.length) {
    body.innerHTML =
      '<table class="tile-table tile-table--sites">' +
      '<thead><tr><th scope="col">Domain</th><th scope="col">Status</th></tr></thead>' +
      '<tbody><tr><td colspan="2" class="monitor-tile__meta">No routed domains</td></tr>' +
      '</tbody></table>';
    return;
  }
  const rows = sites.rows
    .map(row => {
      const domain = escapeHtml(row.domain);
      const url = escapeHtml(row.url);
      const status = escapeHtml(row.status);
      return (
        '<tr>' +
        `<td><a class="site-domain-link" href="${url}" target="_blank" rel="noopener noreferrer">${domain}</a></td>` +
        `<td><span class="${statusClass(row.status)}">${status}</span></td>` +
        '</tr>'
      );
    })
    .join('');
  body.innerHTML =
    '<table class="tile-table tile-table--sites">' +
    '<thead><tr><th scope="col">Domain</th><th scope="col">Status</th></tr></thead>' +
    `<tbody>${rows}</tbody></table>`;
}

export function mountSitesTable(body) {
  renderSitesTable(body);
}

export function renderSitesTiles() {
  const board = document.getElementById('monitor-board');
  if (!board) return;
  for (const def of state.registry) {
    if (def.id !== 'sites' && def.variant !== 'sites') continue;
    const el = board.querySelector(`[data-tile-id="${def.id}"]`);
    const body = el?.querySelector('[data-tile-body]');
    if (body) renderSitesTable(body);
  }
}

export async function fetchSites() {
  try {
    const res = await fetch('/sites', { cache: 'no-store', credentials: 'same-origin' });
    if (res.status === 401 || res.redirected) {
      location.replace('/login');
      return;
    }
    if (!res.ok) throw new Error('sites ' + res.status);
    const data = await res.json();
    state.sites = {
      rows: Array.isArray(data) ? data : [],
      stale: false,
    };
  } catch {
    state.sites = { rows: [], stale: true };
  }
  renderSitesTiles();
}

export function startSitesPolling() {
  fetchSites();
  window.setInterval(fetchSites, 5000);
}
