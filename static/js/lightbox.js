import { state } from './state.js';

const lightbox = document.getElementById('lightbox');
const lbImg    = document.getElementById('lightbox-img');

// Lightbox pinch-to-zoom state
let lbScale = 1, lbTx = 0, lbTy = 0, lbDragY = 0;
let lbNatLeft = 0, lbNatTop = 0;
let lbPinchStart = null, lbPanStart = null, lbLastTap = 0;

function lbApplyTransform() {
  lbImg.style.transformOrigin = '0 0';
  lbImg.style.transform = `translate(${lbTx}px,${lbTy}px) scale(${lbScale})`;
  lbImg.style.cursor = lbScale > 1 ? 'grab' : 'zoom-in';
}

function lbReset() {
  lbScale = 1; lbTx = 0; lbTy = 0; lbDragY = 0; lbPinchStart = null; lbPanStart = null;
  lbImg.style.transform = lbImg.style.transformOrigin = lbImg.style.cursor = '';
  lightbox.style.background = '';
}

export function openLightbox(src) {
  lbReset();
  lbImg.src = src;
  lightbox.classList.add('open');
  requestAnimationFrame(() => {
    const r = lbImg.getBoundingClientRect();
    lbNatLeft = r.left; lbNatTop = r.top;
  });
}

export function closeLightbox() { lightbox.classList.remove('open'); lbReset(); }

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
      lbDragY = e.touches[0].clientY - lbPanStart.y;
      lbImg.style.transform = `translateY(${lbDragY}px)`;
      lightbox.style.background = `rgba(0,0,0,${Math.max(0, 0.88 - Math.abs(lbDragY) / 400)})`;
    }
  }
}, { passive: false });

lbImg.addEventListener('touchend', e => {
  lbPinchStart = null;
  if (e.touches.length === 1) {
    const t = e.touches[0];
    lbPanStart = { x: t.clientX, y: t.clientY, tx: lbTx, ty: lbTy };
  } else if (e.touches.length === 0) {
    const dy = lbDragY;
    lbPanStart = null;
    if (lbScale < 1.05 && Math.abs(dy) > 80) {
      closeLightbox();
    } else if (lbScale < 1.05 && dy !== 0) {
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
