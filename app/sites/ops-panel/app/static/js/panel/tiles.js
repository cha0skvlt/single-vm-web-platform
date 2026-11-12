import { state } from './state.js';
import { mountSitesTable } from './sites-table.js';
import { mountFail2banTable } from './fail2ban-table.js';
import { mountIncidentsTable } from './incidents-table.js';

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function statusBeaconClass(s) {
  if (!s || s.stale) return 'status-beacon--stale';
  if (s.overall === 'ok' || s.ok) return 'status-beacon--ok';
  if (s.overall === 'down') return 'status-beacon--alarm';
  return 'status-beacon--stale';
}

export function statusTitle(s) {
  if (!s || s.stale) return 'Health endpoint unreachable';
  if (s.detail) return s.detail;
  if (s.overall === 'ok' || s.ok) return 'All health checks passed';
  return 'One or more health checks failed';
}

function renderTable(body, def) {
  if (def.variant === 'sites' || def.id === 'sites') {
    mountSitesTable(body);
    return;
  }
  if (def.variant === 'fail2ban' || def.id === 'fail2ban') {
    mountFail2banTable(body);
    return;
  }
  if (def.variant === 'incidents' || def.id === 'incidents') {
    mountIncidentsTable(body);
    return;
  }
  body.innerHTML = '<p class="monitor-tile__meta">Unknown table</p>';
}

const RENDERERS = {
  table: renderTable,
};

function buildTileElement(def) {
  const el = document.createElement('article');
  el.className = 'card monitor-tile tile--table';
  el.dataset.tileId = def.id;

  el.innerHTML = `
    <div class="monitor-tile__head">
      <span class="monitor-tile__title">${escapeHtml(def.title)}</span>
    </div>
    <div class="monitor-tile__body" data-tile-body></div>`;

  const renderer = RENDERERS[def.kind];
  const body = el.querySelector('[data-tile-body]');
  if (renderer && body) renderer(body, def);
  return el;
}

export function renderBoard() {
  const board = document.getElementById('monitor-board');
  if (!board) return;
  board.replaceChildren();
  for (const def of state.registry) {
    board.appendChild(buildTileElement(def));
  }
}

export function renderAllTiles() {
  const board = document.getElementById('monitor-board');
  if (!board) return;
  for (const def of state.registry) {
    if (def.id === 'sites' || def.variant === 'sites') continue;
    const el = board.querySelector(`[data-tile-id="${def.id}"]`);
    const body = el?.querySelector('[data-tile-body]');
    const renderer = RENDERERS[def.kind];
    if (body && renderer) renderer(body, def);
  }
}

export async function loadTileRegistry() {
  const res = await fetch('/tiles.json', { cache: 'no-store', credentials: 'same-origin' });
  if (!res.ok) throw new Error(`tiles.json HTTP ${res.status}`);
  const data = await res.json();
  if (!data || !Array.isArray(data.tiles) || data.tiles.length === 0) {
    throw new Error('tiles.json: empty or invalid');
  }
  state.registry = data.tiles;
}
