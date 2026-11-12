import { icon } from '../icons.js';
import { refreshStatusFavicon } from './favicon.js';

const THEME_STORAGE_KEY = 'prod-theme';

export function getStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

function updateThemeMeta() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (bg) meta.setAttribute('content', bg);
}

function updateThemeIcon(theme) {
  const themeEl = document.getElementById('theme-icon');
  if (themeEl) {
    themeEl.outerHTML = icon(theme === 'dark' ? 'moon' : 'sun', { size: 14, id: 'theme-icon' });
  }
}

export function applyTheme(theme) {
  const next = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  updateThemeMeta();
  updateThemeIcon(next);
  requestAnimationFrame(() => refreshStatusFavicon());
}

export function initTheme() {
  applyTheme(getStoredTheme());
}

export function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
}
