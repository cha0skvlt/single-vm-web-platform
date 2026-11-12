import { initTheme, toggleTheme } from './theme.js';
import { loadTileRegistry, renderBoard } from './tiles.js';
import { startStatusPolling } from './status.js';
import { startSitesPolling } from './sites-table.js';
import { updateStatusFavicon } from './favicon.js';

document.getElementById('theme-toggle-btn')?.addEventListener('click', toggleTheme);

async function boot() {
  initTheme();
  updateStatusFavicon(null);

  await loadTileRegistry();
  renderBoard();
  startStatusPolling();
  startSitesPolling();
}

function showBootError(err) {
  console.error('Panel boot failed:', err);
  const board = document.getElementById('monitor-board');
  if (!board) return;
  const msg = err instanceof Error ? err.message : String(err);
  board.innerHTML = `<p class="monitor-tile__meta monitor-tile__meta--stale">Panel failed to load: ${msg}</p>`;
}

boot().catch(showBootError);
