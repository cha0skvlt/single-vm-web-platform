import { state } from './state.js';

const FAVICON_SIZE = 32;

/** Same states as header Status Prod (overall health). */
export function beaconKindFromStatus(status) {
  if (!status || status.stale) return 'stale';
  if (status.overall === 'ok' || status.ok) return 'ok';
  if (status.overall === 'down') return 'alarm';
  return 'stale';
}

let probe;

/** Computed styles from the real status-beacon (theme-aware). */
function beaconComputed(kind) {
  if (!probe) {
    probe = document.createElement('div');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.cssText =
      'position:fixed;left:-9999px;top:0;width:var(--beacon-size);height:var(--beacon-size);pointer-events:none;animation:none!important';
    document.body.appendChild(probe);
  }
  probe.className = `status-beacon status-beacon--${kind === 'ok' ? 'ok' : kind === 'alarm' ? 'alarm' : 'stale'}`;
  probe.style.animation = 'none';
  return getComputedStyle(probe);
}

function parseColor(input) {
  const scratch = document.createElement('canvas');
  const ctx = scratch.getContext('2d');
  if (!ctx || !input) return 'rgba(0,0,0,0)';
  ctx.fillStyle = input;
  return ctx.fillStyle;
}

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function pxToken(name, fallback) {
  const n = parseFloat(token(name));
  return Number.isFinite(n) ? n : fallback;
}

/** Outer glow only (shadow extends outside solid core). */
function fillOrbWithGlow(ctx, cx, cy, r, core, glow, glowSoft) {
  if (glowSoft) {
    ctx.save();
    ctx.shadowColor = glowSoft;
    ctx.shadowBlur = pxToken('--space-4', 16) * 0.55;
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  if (glow) {
    ctx.save();
    ctx.shadowColor = glow;
    ctx.shadowBlur = pxToken('--space-2', 8) * 0.65;
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
}

/** Specular spot — same position as .status-beacon radial-gradient (38% 32%). */
function applySpecular(ctx, size, cx, cy, r) {
  const cs = beaconComputed('ok');
  const bgImg = cs.backgroundImage;
  const hx = size * 0.38;
  const hy = size * 0.32;
  const hi = ctx.createRadialGradient(hx, hy, 0, cx, cy, r * 0.95);
  if (bgImg && bgImg !== 'none') {
    hi.addColorStop(0, 'rgba(255,255,255,0.4)');
    hi.addColorStop(0.42, 'rgba(255,255,255,0)');
  } else {
    hi.addColorStop(0, 'rgba(255,255,255,0.28)');
    hi.addColorStop(0.42, 'rgba(255,255,255,0)');
  }
  ctx.save();
  ctx.globalCompositeOperation = 'source-atop';
  ctx.fillStyle = hi;
  ctx.fillRect(0, 0, size, size);
  ctx.restore();
}

function drawBeacon(ctx, size, kind) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.4375;
  ctx.clearRect(0, 0, size, size);

  if (kind === 'stale') {
    const cs = beaconComputed('stale');
    const core = parseColor(cs.backgroundColor);
    const alpha = parseFloat(cs.opacity) || 0.35;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
    return;
  }

  if (kind === 'ok') {
    const core = parseColor(beaconComputed('ok').backgroundColor || token('--beacon-ok-core'));
    const glow = parseColor(token('--beacon-ok-glow'));
    const glowSoft = parseColor(token('--beacon-ok-glow-soft'));
    fillOrbWithGlow(ctx, cx, cy, r, core, glow, glowSoft);
    applySpecular(ctx, size, cx, cy, r);
    return;
  }

  if (kind === 'alarm') {
    const core = parseColor(beaconComputed('alarm').backgroundColor);
    const glow = parseColor(token('--beacon-alarm-glow'));
    const halo = parseColor(token('--beacon-alarm-halo'));
    ctx.save();
    ctx.shadowColor = halo;
    ctx.shadowBlur = pxToken('--space-6', 32) * 0.45;
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    ctx.save();
    ctx.shadowColor = glow;
    ctx.shadowBlur = pxToken('--space-3', 12) * 0.5;
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    applySpecular(ctx, size, cx, cy, r);
  }
}

let canvas;

function faviconCanvas() {
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.width = FAVICON_SIZE;
    canvas.height = FAVICON_SIZE;
  }
  return canvas;
}

function setFaviconHref(dataUrl) {
  let link = document.getElementById('status-favicon');
  if (!link) {
    link = document.createElement('link');
    link.id = 'status-favicon';
    link.rel = 'icon';
    link.type = 'image/png';
    link.sizes = '32x32';
    document.head.appendChild(link);
  }
  link.href = dataUrl;
}

/** @param {import('./state.js').state['status']} status */
export function updateStatusFavicon(status) {
  const kind = beaconKindFromStatus(status);
  const c = faviconCanvas();
  const ctx = c.getContext('2d');
  if (!ctx) return;
  drawBeacon(ctx, FAVICON_SIZE, kind);
  setFaviconHref(c.toDataURL('image/png'));
}

export function refreshStatusFavicon() {
  updateStatusFavicon(state.status);
}
