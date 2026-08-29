// Custom right-click menu for generated images and videos.
//
// The .img-wrap hover buttons ran out of edges long ago (twelve of them, pinned to
// every corner), while the browser's own context menu sat unused over every piece of
// gallery media. This reclaims it for Save / Copy / Copy seed.
//
// Attached ONCE, delegated on document, rather than wired per render site: the
// /images/ URL is already the identity key everywhere in this app, so a single
// listener covers chat bubbles, the /review-all and sequence-review grids
// (grids.js), the slideshow and the lightbox — including the renderers that bypass
// appendChatImage. Gating on the /images/ prefix leaves the native menu in place
// over mask/crop editor canvases, /references thumbs and /references-file/ previews.

import { state } from './state.js';
import { isVideoUrl, clampMenuPosition, escapeHtml } from './utils.js';
import { addMessage } from './dom.js';

// Copying an image (as opposed to its address) needs navigator.clipboard.write,
// which browsers expose only in a secure context — HTTPS or localhost. The
// appliance is normally reached over plain HTTP on a LAN address, where the API is
// simply absent, so the row becomes "Copy image address" instead of offering
// something that silently fails.
function canCopyImages() {
  return !!(window.isSecureContext && navigator.clipboard && navigator.clipboard.write);
}

let openMenu = null;

export function closeMediaMenu() {
  if (openMenu) { openMenu(); openMenu = null; }
}

// --- actions ---------------------------------------------------------------

function saveMedia(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = url.split('/').pop();
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Chrome and Firefox accept only image/png in a ClipboardItem, but the gallery also
// holds .webp/.jpg/.gif (IMAGE_EXTS in config.py), so anything else is transcoded
// through a canvas first.
async function asPngBlob(url) {
  const blob = await fetch(url).then(r => {
    if (!r.ok) throw new Error('fetch failed');
    return r.blob();
  });
  if (blob.type === 'image/png') return blob;
  const bmp = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  canvas.getContext('2d').drawImage(bmp, 0, 0);
  bmp.close();
  return await new Promise((resolve, reject) => {
    canvas.toBlob(b => b ? resolve(b) : reject(new Error('encode failed')), 'image/png');
  });
}

// The ClipboardItem is built from a PROMISE, not an awaited blob, so that write()
// is called synchronously inside the click handler — Safari discards the user
// gesture otherwise. Older Firefox rejects the promise form; the caller falls back
// to copying the address.
function copyImage(url) {
  return navigator.clipboard.write([
    new ClipboardItem({ 'image/png': asPngBlob(url) }),
  ]);
}

function copyAddress(url) {
  return navigator.clipboard.writeText(new URL(url, window.location.href).href);
}

// --- menu ------------------------------------------------------------------

function openMediaMenu(url, x, y) {
  closeMediaMenu();

  const menu = document.createElement('div');
  menu.className = 'media-menu';
  // Measured before it is placed, so it can be clamped into the viewport.
  menu.style.visibility = 'hidden';

  const head = document.createElement('div');
  head.className = 'media-menu-head';
  head.textContent = url.split('/').pop();
  menu.appendChild(head);

  const dismiss = () => {
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('mousedown', onOutside, true);
    window.removeEventListener('scroll', dismiss, true);
    window.removeEventListener('resize', dismiss);
    window.removeEventListener('blur', dismiss);
    menu.remove();
    openMenu = null;
  };
  const onKey = e => { if (e.key === 'Escape') { e.preventDefault(); dismiss(); } };
  const onOutside = e => { if (!menu.contains(e.target)) dismiss(); };

  // `run` keeps the menu up just long enough to show the outcome, so a copy that
  // failed says so instead of vanishing as if it had worked.
  function row(label, onClick, { enabled = true } = {}) {
    const btn = document.createElement('button');
    btn.className = 'media-menu-item';
    btn.textContent = label;
    btn.disabled = !enabled;
    btn.addEventListener('click', () => onClick(btn));
    menu.appendChild(btn);
    return btn;
  }

  function run(btn, promise, okLabel) {
    btn.disabled = true;
    Promise.resolve(promise)
      .then(() => { btn.textContent = okLabel; setTimeout(dismiss, 600); })
      .catch(() => { btn.textContent = 'Failed'; btn.classList.add('media-menu-fail');
                     setTimeout(dismiss, 1200); });
  }

  const video = isVideoUrl(url);

  row(video ? 'Save video' : 'Save image', () => { saveMedia(url); dismiss(); });

  if (!video && canCopyImages()) {
    row('Copy image', btn => run(
      btn,
      // Older Firefox rejects a promise-valued ClipboardItem; the address is a
      // useful answer rather than a dead end.
      copyImage(url).catch(() => copyAddress(url)),
      'Copied ✓'));
  } else {
    row(video ? 'Copy video address' : 'Copy image address',
        btn => run(btn, copyAddress(url), 'Copied ✓'));
  }

  // Fetched on open so the row can say up front whether there is a seed to take,
  // rather than failing after the click.
  const seedRow = row('Copy seed…', () => {}, { enabled: false });
  fetch('/api/image-seed/' + encodeURIComponent(url.split('/').pop()))
    .then(r => r.ok ? r.json() : Promise.reject(new Error('unavailable')))
    .then(data => {
      if (data.seed == null) { seedRow.textContent = 'No seed recorded'; return; }
      const shown = data.seed.length > 12 ? data.seed.slice(0, 12) + '…' : data.seed;
      seedRow.textContent = 'Copy seed ' + shown;
      seedRow.title = data.seed;
      seedRow.disabled = false;
      seedRow.onclick = () => {
        // Same one-shot pin /getseed sets: consumed by the next t2i / i2v / t2v
        // run in runGeneration, then cleared. Kept a string all the way to the
        // server — a 64-bit seed does not survive a JS Number.
        state.reuseSeed = data.seed;
        dismiss();
        addMessage('bot', `🎲 The next generation (t2i, i2v or t2v) will reuse seed ` +
          `<code>${escapeHtml(data.seed)}</code> from ` +
          `<code>${escapeHtml(url.split('/').pop())}</code>. ` +
          `It reverts to random seeds afterwards.`);
      };
    })
    .catch(() => { seedRow.textContent = 'Seed unavailable'; });

  document.body.appendChild(menu);
  const pos = clampMenuPosition(x, y, menu.offsetWidth, menu.offsetHeight,
                                window.innerWidth, window.innerHeight);
  menu.style.left = pos.left + 'px';
  menu.style.top = pos.top + 'px';
  menu.style.visibility = '';

  // Registered after this event finishes, or the very right-click that opened the
  // menu would close it again.
  setTimeout(() => {
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onOutside, true);
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('blur', dismiss);
  }, 0);

  openMenu = dismiss;
}

export function initMediaMenu() {
  document.addEventListener('contextmenu', e => {
    // Shift+right-click is the standard escape hatch back to the browser's own
    // menu — which for a <video> still owns playback speed and picture-in-picture.
    if (e.shiftKey) return;
    const el = e.target.closest && e.target.closest('img, video');
    if (!el) return;
    const url = el.getAttribute('src');
    if (!url || !url.startsWith('/images/')) return;
    e.preventDefault();
    openMediaMenu(url, e.clientX, e.clientY);
  });
}
