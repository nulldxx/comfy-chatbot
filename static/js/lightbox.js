import { state } from './state.js';
import { isVideoUrl } from './utils.js';
import { deleteImageFile, removeImageFromChat } from './dom.js';

const lightbox = document.getElementById('lightbox');
const lbImg    = document.getElementById('lightbox-img');
const lbStatus = document.getElementById('lightbox-status');

// Lightbox pinch-to-zoom state
let lbScale = 1, lbTx = 0, lbTy = 0, lbDragY = 0, lbDragX = 0;
let lbNatLeft = 0, lbNatTop = 0;
let lbPinchStart = null, lbPanStart = null, lbLastTap = 0;

// The ordered collection this lightbox is browsing (session images, or the
// review grid it was opened from) plus the index of the current image, so
// arrow keys / horizontal swipes can move to the neighbouring image.
let lbCollection = [], lbIndex = -1;

// Normalise a URL for matching: an <img>.src is absolute
// (http://host/images/x.png) while collection entries are relative
// (/images/x.png). Compare by pathname so both forms line up.
const srcKey = u => { try { return new URL(u, location.href).pathname; } catch { return u; } };

function lbRecomputeOrigin() {
  requestAnimationFrame(() => {
    const r = lbImg.getBoundingClientRect();
    lbNatLeft = r.left; lbNatTop = r.top;
  });
}

function lbApplyTransform() {
  lbImg.style.transformOrigin = '0 0';
  lbImg.style.transform = `translate(${lbTx}px,${lbTy}px) scale(${lbScale})`;
  lbImg.style.cursor = lbScale > 1 ? 'grab' : 'zoom-in';
}

function lbReset() {
  lbScale = 1; lbTx = 0; lbTy = 0; lbDragY = 0; lbDragX = 0; lbPinchStart = null; lbPanStart = null;
  lbImg.style.transform = lbImg.style.transformOrigin = lbImg.style.cursor = '';
  lightbox.style.background = '';
}

function lbShow(i) {
  lbIndex = i;
  lbReset();
  lbImg.src = lbCollection[i];
  lbRecomputeOrigin();
}

export function openLightbox(src, collection = state.sessionImages) {
  lbCollection = Array.isArray(collection) && collection.length ? collection : [src];
  lbIndex = lbCollection.findIndex(u => srcKey(u) === srcKey(src));
  if (lbIndex < 0) lbIndex = 0;
  lbSetStatus('');
  lbReset();
  lbImg.src = src;
  lightbox.classList.add('open');
  lbRecomputeOrigin();
}

// Move to the next (dir=1) / previous (dir=-1) image in the collection,
// wrapping around at the ends and skipping video entries (the lightbox can't
// render video). No-op if there's nothing else to show.
export function navigateLightbox(dir) {
  const n = lbCollection.length;
  if (n < 2 || lbIndex < 0) return;
  let i = lbIndex;
  for (let step = 0; step < n; step++) {
    i = (i + dir + n) % n;
    if (i === lbIndex) break;
    if (!isVideoUrl(lbCollection[i])) { lbShow(i); return; }
  }
}

let lbStatusTimer = null;
function lbSetStatus(text) {
  if (!lbStatus) return;
  clearTimeout(lbStatusTimer);
  lbStatus.textContent = text;
  lbStatus.classList.toggle('open', !!text);
  if (text) lbStatusTimer = setTimeout(() => lbSetStatus(''), 4000);
}

// Delete the image currently on screen from the output folder, then move to
// the next one in the collection (closing the lightbox when none is left).
// The lightbox may be sitting on top of the view that opened it, so the other
// views holding this image are told to forget it too — otherwise a review grid
// or slideshow keeps a thumb/slide pointing at a file that no longer exists.
let lbDeleting = false;
export function deleteCurrentLightboxImage() {
  if (lbDeleting || !isLightboxOpen()) return;
  const url = lbCollection[lbIndex];
  if (!url || isVideoUrl(url)) return;

  lbDeleting = true;
  lbSetStatus('Deleting…');
  deleteImageFile(url).then(() => {
    lbSetStatus('');
    // Collection entries are relative (/images/x.png) and match the chat's src
    // attributes as-is; only the single-image fallback (an absolute <img>.src)
    // needs reducing to a pathname first.
    removeImageFromChat(/^https?:/i.test(url) ? srcKey(url) : url);
    document.querySelectorAll('.review-thumb').forEach(cell => {
      if (cell._forgetImage && srcKey(cell.dataset.url) === srcKey(url)) cell._forgetImage();
    });
    if (state.activeSlideshowCtrl && state.activeSlideshowCtrl.forget) {
      state.activeSlideshowCtrl.forget(url);
    }

    // removeImageFromChat already splices state.sessionImages, so only drop the
    // entry when this collection is a separate copy (e.g. a review grid's).
    const at = lbIndex;
    const i  = lbCollection.indexOf(url);
    if (i !== -1) lbCollection.splice(i, 1);
    // Land on whatever moved into this slot, wrapping past the end.
    for (let k = 0; k < lbCollection.length; k++) {
      const j = (at + k) % lbCollection.length;
      if (!isVideoUrl(lbCollection[j])) { lbShow(j); return; }
    }
    closeLightbox();
  }).catch(err => {
    lbSetStatus('Delete failed: ' + err.message);
  }).finally(() => { lbDeleting = false; });
}

export function isLightboxOpen() { return lightbox.classList.contains('open'); }

export function closeLightbox() { lightbox.classList.remove('open'); lbSetStatus(''); lbReset(); }

lbImg.addEventListener('touchstart', e => {
  if (e.touches.length === 2) {
    e.preventDefault();
    lbPanStart = null;
    const [t0, t1] = [e.touches[0], e.touches[1]];
    lbPinchStart = {
      dist:  Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY),
      scale: lbScale, tx: lbTx, ty: lbTy,
      mx: (t0.clientX + t1.clientX) / 2,
      my: (t0.clientY + t1.clientY) / 2,
    };
  } else if (e.touches.length === 1) {
    const now = Date.now(), t = e.touches[0];
    if (now - lbLastTap < 300) {
      e.preventDefault();
      lbLastTap = 0;
      if (lbScale > 1) { lbReset(); }
      else {
        const s = 2.5;
        lbTx = (t.clientX - lbNatLeft) * (1 - s);
        lbTy = (t.clientY - lbNatTop)  * (1 - s);
        lbScale = s;
        lbApplyTransform();
      }
    } else {
      lbLastTap = now;
      lbPanStart = { x: t.clientX, y: t.clientY, tx: lbTx, ty: lbTy };
    }
  }
}, { passive: false });

lbImg.addEventListener('touchmove', e => {
  e.preventDefault();
  if (e.touches.length === 2 && lbPinchStart) {
    const [t0, t1] = [e.touches[0], e.touches[1]];
    const dist     = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
    const newScale = Math.max(1, Math.min(6, lbPinchStart.scale * dist / lbPinchStart.dist));
    const ratio    = newScale / lbPinchStart.scale;
    lbScale = newScale;
    lbTx = (lbPinchStart.mx - lbNatLeft) * (1 - ratio) + lbPinchStart.tx * ratio;
    lbTy = (lbPinchStart.my - lbNatTop)  * (1 - ratio) + lbPinchStart.ty * ratio;
    lbApplyTransform();
  } else if (e.touches.length === 1 && lbPanStart) {
    if (lbScale > 1) {
      lbTx = lbPanStart.tx + e.touches[0].clientX - lbPanStart.x;
      lbTy = lbPanStart.ty + e.touches[0].clientY - lbPanStart.y;
      lbApplyTransform();
    } else {
      lbDragX = e.touches[0].clientX - lbPanStart.x;
      lbDragY = e.touches[0].clientY - lbPanStart.y;
      if (Math.abs(lbDragX) > Math.abs(lbDragY)) {
        // Horizontal drag — previewing a swipe to the prev/next image.
        lbImg.style.transform = `translateX(${lbDragX}px)`;
        lightbox.style.background = '';
      } else {
        // Vertical drag — swipe down to dismiss, dimming the backdrop.
        lbImg.style.transform = `translateY(${lbDragY}px)`;
        lightbox.style.background = `rgba(0,0,0,${Math.max(0, 0.88 - Math.abs(lbDragY) / 400)})`;
      }
    }
  }
}, { passive: false });

lbImg.addEventListener('touchend', e => {
  lbPinchStart = null;
  if (e.touches.length === 1) {
    const t = e.touches[0];
    lbPanStart = { x: t.clientX, y: t.clientY, tx: lbTx, ty: lbTy };
  } else if (e.touches.length === 0) {
    const dy = lbDragY, dx = lbDragX;
    lbPanStart = null;
    if (lbScale < 1.05 && Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      // Swipe-left = next, swipe-right = prev (matches the slideshow).
      navigateLightbox(dx < 0 ? 1 : -1);
    } else if (lbScale < 1.05 && Math.abs(dy) > 80) {
      closeLightbox();
    } else if (lbScale < 1.05 && (dy !== 0 || dx !== 0)) {
      lbImg.style.transition = 'transform 0.2s ease-out';
      lightbox.style.transition = 'background 0.2s ease-out';
      lbReset();
      setTimeout(() => { lbImg.style.transition = ''; lightbox.style.transition = ''; }, 220);
    } else if (lbScale < 1.05) {
      lbReset();
    }
  }
});

export function enterFauxFs(el) {
  state.fauxFullscreenEls.add(el);
  document.body.style.overflow = 'hidden';
}

export function exitFauxFs(el) {
  state.fauxFullscreenEls.delete(el);
  if (state.fauxFullscreenEls.size === 0) document.body.style.overflow = '';
}
