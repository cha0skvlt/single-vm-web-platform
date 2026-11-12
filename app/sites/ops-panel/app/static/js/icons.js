/** Lucide inline SVG icons — stroke icons, no CDN. */

const BASE =
  'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"';

const PATHS = {
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sun:
    '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
};

/**
 * @param {string} name
 * @param {{ size?: number, className?: string, id?: string }} [opts]
 */
export function icon(name, opts = {}) {
  const { size = 16, className = '', id = '' } = opts;
  const paths = PATHS[name];
  if (!paths) return '';
  const cls = className ? ` class="icon ${className}"` : ' class="icon"';
  const idAttr = id ? ` id="${id}"` : '';
  return `<svg ${BASE} width="${size}" height="${size}"${cls}${idAttr} aria-hidden="true">${paths}</svg>`;
}
