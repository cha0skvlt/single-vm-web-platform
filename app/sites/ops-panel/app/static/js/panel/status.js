import { state } from './state.js';
import { renderAllTiles, statusBeaconClass, statusTitle } from './tiles.js';
import { updateStatusFavicon } from './favicon.js';

let pollTimer = null;

function failedCheckDetails(checks) {
  if (!Array.isArray(checks)) return '';
  return checks
    .filter(c => c && c.ok === false)
    .map(c => `${c.name}: ${c.detail || 'failed'}`)
    .join('; ');
}

function paintHeaderChip(chipId, orbId, statusState) {
  const chip = document.getElementById(chipId);
  const orb = document.getElementById(orbId);
  if (!chip || !orb) return;
  orb.className = `status-beacon header-status-orb ${statusBeaconClass(statusState)}`;
  const title = statusTitle(statusState);
  chip.title = title;
  const name = chipId === 'status-prod-chip' ? 'Platform' : 'Peer';
  chip.setAttribute('aria-label', `${name}: ${title}`);
}

function paintHeaderStatus() {
  paintHeaderChip('status-prod-chip', 'status-prod-orb', state.status);
  paintHeaderChip('status-git-chip', 'status-git-orb', state.gitStatus);
}

export async function fetchStatus() {
  try {
    const res = await fetch('/health', { cache: 'no-store', credentials: 'same-origin' });
    if (res.status === 401 || res.redirected) {
      location.replace('/login');
      return;
    }
    if (!res.ok) throw new Error('health ' + res.status);
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      location.replace('/login');
      return;
    }
    const data = await res.json();
    const overall = data.overall === 'ok' ? 'ok' : data.overall === 'down' ? 'down' : 'unknown';
    const checks = Array.isArray(data.checks) ? data.checks : [];
    state.status = {
      overall,
      checks,
      fail2ban: data.fail2ban && typeof data.fail2ban === 'object' ? data.fail2ban : null,
      incidents: Array.isArray(data.incidents) ? data.incidents : [],
      ok: overall === 'ok',
      stale: false,
      detail: failedCheckDetails(checks),
    };
    const git = data.git && typeof data.git === 'object' ? data.git : null;
    const gitChecks = Array.isArray(git?.checks) ? git.checks : [];
    const gitOverall =
      git?.overall === 'ok' ? 'ok' : git?.overall === 'down' ? 'down' : 'unknown';
    state.gitStatus = {
      overall: gitOverall,
      checks: gitChecks,
      ok: gitOverall === 'ok',
      stale: false,
      detail: failedCheckDetails(gitChecks),
    };
  } catch {
    state.status = { overall: 'unknown', checks: [], ok: false, stale: true, detail: '', incidents: [] };
    state.gitStatus = { overall: 'unknown', checks: [], ok: false, stale: true, detail: '' };
  }
  renderAllTiles();
  paintHeaderStatus();
  updateStatusFavicon(state.status);
}

export function startStatusPolling() {
  fetchStatus();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(fetchStatus, 5000);
}
