/** FAIL2BAN — summary + recent bans fed by /health → fail2ban payload. */

import { state } from './state.js';

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderJailNames(names) {
  if (!Array.isArray(names) || !names.length) return '';
  return `<span class="f2b-stat__names">${names.map(n => escapeHtml(n)).join(', ')}</span>`;
}

function renderRecent(recent) {
  if (!Array.isArray(recent) || !recent.length) {
    return '<li class="f2b-recent__item monitor-tile__meta">No active bans</li>';
  }
    return recent
    .map(row => {
      const ip = escapeHtml(row.ip);
      const jail = escapeHtml(row.jail);
      const ago = escapeHtml(row.ago);
      return `<li class="f2b-recent__item"><span class="f2b-recent__ip">${ip}</span> — ${jail} <span class="f2b-recent__ago">(${ago})</span></li>`;
    })
    .join('');
}

function renderRecentTitle(recent, bannedNow) {
  if (bannedNow > 0) return 'Active bans';
  return 'Recent bans';
}

export function renderFail2banTable(body) {
  const fb = state.status?.fail2ban;
  const stale = state.status?.stale || !fb || fb.reachable === false;

  if (stale) {
    body.innerHTML = '<p class="monitor-tile__meta monitor-tile__meta--stale">Fail2ban metrics unavailable</p>';
    return;
  }

  const banned = escapeHtml(fb.banned_now ?? 0);
  const jails = escapeHtml(fb.active_jails ?? 0);
  const jailNames = renderJailNames(fb.jail_names);
  const recentTitle = renderRecentTitle(fb.recent, fb.banned_now ?? 0);

  body.innerHTML = `
    <div class="f2b-stats">
      <p class="f2b-stat">Banned now: <strong class="f2b-stat__value">${banned}</strong> IPs</p>
      <p class="f2b-stat">Active jails: <strong class="f2b-stat__value">${jails}</strong>${jailNames ? ` ${jailNames}` : ''}</p>
    </div>
    <p class="f2b-recent__title">${escapeHtml(recentTitle)}</p>
    <ul class="f2b-recent">${renderRecent(fb.recent)}</ul>`;
}

export function mountFail2banTable(body) {
  renderFail2banTable(body);
}
