// ---------------------------------------------------------------------------
// /privacy — mask inline media until hovered
// ---------------------------------------------------------------------------
// A screen shield, not access control: a `privacy-mode` class on <body> lets CSS
// (chat.css) cover every .img-wrap / .review-thumb / .composite-cell with an
// opaque panel that lifts on hover. One class rather than per-render wiring, so
// media rendered later is covered with no extra work. Per browser, in
// localStorage — it's about who can see this screen, not about the chat — and
// templates/index.html applies the same key before any media paints.

export const PRIVACY_STORAGE_KEY = 'privacy-mode';
const PRIVACY_CLASS = 'privacy-mode';

// Pure, for testing: storage can be missing or throw (private window, blocked
// site data), and either way means "off".
export function readPrivacyMode(storage) {
  try {
    return !!storage && storage.getItem(PRIVACY_STORAGE_KEY) === '1';
  } catch (e) {
    return false;
  }
}

export function isPrivacyMode() {
  return document.body.classList.contains(PRIVACY_CLASS);
}

export function setPrivacyMode(on, { persist = true } = {}) {
  document.body.classList.toggle(PRIVACY_CLASS, !!on);
  if (!persist) return;
  try { localStorage.setItem(PRIVACY_STORAGE_KEY, on ? '1' : '0'); } catch (e) {}
}

export function initPrivacyMode() {
  let storage = null;
  try { storage = localStorage; } catch (e) {}
  setPrivacyMode(readPrivacyMode(storage), { persist: false });
}
