import { state } from './state.js';
import { scrollBottom } from './dom.js';
import { openLightbox, enterFauxFs, exitFauxFs } from './lightbox.js';
import { isVideoUrl } from './utils.js';

const IMAGE_HOLD_MS = 3000;

export function createSlideshow(bubble, images) {
  let idx = 0;
  let timer = null;
  let paused = false;

  bubble.innerHTML = `
    <div class="slideshow">
      <div class="ss-img-wrap">
        <img class="ss-image" src="" alt="Generated image">
        <video class="ss-video" playsinline preload="metadata" style="display:none"></video>
      </div>
      <div class="ss-controls">
        <button class="ss-btn ss-prev">&#8249;</button>
        <button class="ss-btn ss-pause" title="Pause">&#9646;&#9646;</button>
        <button class="ss-btn ss-delete" title="Delete image">&#128465;&#xFE0E;</button>
        <span class="ss-counter"></span>
        <button class="ss-btn ss-next">&#8250;</button>
        <button class="ss-fullscreen-btn" title="Toggle fullscreen">&#x26F6;</button>
      </div>
      <div class="ss-bar-wrap"><div class="ss-bar"></div></div>
    </div>`;

  const img      = bubble.querySelector('.ss-image');
  const video    = bubble.querySelector('.ss-video');
  const counter  = bubble.querySelector('.ss-counter');
  const bar      = bubble.querySelector('.ss-bar');
  const wrap     = bubble.querySelector('.ss-img-wrap');
  const pauseBtn = bubble.querySelector('.ss-pause');

  // Tap-to-toggle works without native controls and avoids tangling with the
  // wrap's click/swipe handlers below.
  video.muted = true;
  video.loop = false;
  video.controls = false;

  function setBarDuration(ms) {
    bar.style.animationDuration = `${Math.max(50, ms)}ms`;
  }

  function restartBar() {
    bar.style.animation = 'none';
    bar.offsetWidth;
    bar.style.animation = paused ? 'none' : '';
  }

  let videoMetaHandler = null;
  let videoEndedHandler = null;

  function detachVideoHandlers() {
    if (videoMetaHandler) {
      video.removeEventListener('loadedmetadata', videoMetaHandler);
      videoMetaHandler = null;
    }
    if (videoEndedHandler) {
      video.removeEventListener('ended', videoEndedHandler);
      videoEndedHandler = null;
    }
  }

  function stopVideo() {
    detachVideoHandlers();
    if (!video.paused) video.pause();
    // Drop the src so the decoder releases the file and the element doesn't
    // keep buffering off-screen while the next slide is showing.
    video.removeAttribute('src');
    video.load();
  }

  function show(i) {
    idx = ((i % images.length) + images.length) % images.length;
    const url = images[idx];
    counter.textContent = `${idx + 1} / ${images.length}`;
    clearTimeout(timer);

    if (isVideoUrl(url)) {
      detachVideoHandlers();
      img.removeAttribute('src');
      img.style.display = 'none';
      video.style.display = '';
      video.src = url;
      // Start the bar with a placeholder duration; refine once we know the
      // clip length so the bar tracks playback.
      setBarDuration(IMAGE_HOLD_MS);
      restartBar();
      videoMetaHandler = () => {
        if (Number.isFinite(video.duration) && video.duration > 0) {
          setBarDuration(video.duration * 1000);
          restartBar();
        }
      };
      videoEndedHandler = () => { if (!paused) show(idx + 1); };
      video.addEventListener('loadedmetadata', videoMetaHandler);
      video.addEventListener('ended', videoEndedHandler);
      if (!paused) {
        const p = video.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      }
    } else {
      stopVideo();
      video.style.display = 'none';
      img.style.display = '';
      img.src = url;
      setBarDuration(IMAGE_HOLD_MS);
      restartBar();
      if (!paused) timer = setTimeout(() => show(idx + 1), IMAGE_HOLD_MS);
    }
    scrollBottom();
  }

  function currentUrl() { return images[idx]; }

  function navigate(dir) { show(idx + dir); }

  let deleting = false;
  function deleteCurrent() {
    if (deleting || !images.length) return;
    deleting = true;
    const filename = images[idx].split('/').pop();
    fetch('/api/images/' + encodeURIComponent(filename), { method: 'DELETE' })
      .then(r => r.json().then(data => {
        if (!r.ok) throw new Error(data.error || 'Delete failed');
        images.splice(idx, 1);
        if (!images.length) {
          clearTimeout(timer);
          stopVideo();
          if (isFsActive()) fsBtn.click();
          bubble.innerHTML = 'All images deleted.';
          if (state.activeSlideshowCtrl === ctrl) state.activeSlideshowCtrl = null;
          return;
        }
        show(idx);
      }))
      .catch(err => {
        counter.textContent = '⚠ ' + err.message;
        clearTimeout(timer);
        if (!paused) timer = setTimeout(() => show(idx + 1), IMAGE_HOLD_MS);
      })
      .finally(() => { deleting = false; });
  }

  function togglePause() {
    paused = !paused;
    pauseBtn.innerHTML = paused ? '&#9654;' : '&#9646;&#9646;';
    pauseBtn.title     = paused ? 'Play' : 'Pause';
    const showingVideo = isVideoUrl(currentUrl());
    if (paused) {
      clearTimeout(timer);
      bar.style.animationPlayState = 'paused';
      if (showingVideo && !video.paused) video.pause();
    } else {
      bar.style.animationPlayState = 'running';
      if (showingVideo) {
        const p = video.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } else {
        timer = setTimeout(() => show(idx + 1), IMAGE_HOLD_MS);
      }
    }
  }

  bubble.querySelector('.ss-prev').addEventListener('click', () => navigate(-1));
  bubble.querySelector('.ss-next').addEventListener('click', () => navigate(1));
  bubble.querySelector('.ss-delete').addEventListener('click', deleteCurrent);
  pauseBtn.addEventListener('click', togglePause);

  const fsBtn = bubble.querySelector('.ss-fullscreen-btn');
  const ssEl  = bubble.querySelector('.slideshow');
  const nativeFs = !!(document.fullscreenEnabled ?? document.webkitFullscreenEnabled);

  function isFsActive() {
    return document.fullscreenElement === ssEl
        || document.webkitFullscreenElement === ssEl
        || ssEl.classList.contains('ss-faux-fullscreen');
  }
  function syncFsState() {
    const isFs = isFsActive();
    ssEl.classList.toggle('ss-fs', isFs);
    fsBtn.innerHTML = isFs ? '&#x2715;' : '&#x26F6;';
    fsBtn.title     = isFs ? 'Exit fullscreen' : 'Toggle fullscreen';
  }
  fsBtn.addEventListener('click', () => {
    if (nativeFs) {
      if (document.fullscreenElement === ssEl || document.webkitFullscreenElement === ssEl) {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      } else {
        (ssEl.requestFullscreen || ssEl.webkitRequestFullscreen).call(ssEl);
      }
    } else {
      const entering = !ssEl.classList.contains('ss-faux-fullscreen');
      ssEl.classList.toggle('ss-faux-fullscreen', entering);
      entering ? enterFauxFs(ssEl) : exitFauxFs(ssEl);
      syncFsState();
    }
  });
  document.addEventListener('fullscreenchange', syncFsState);
  document.addEventListener('webkitfullscreenchange', syncFsState);

  let touchStartX = 0, touchStartY = 0, suppressClick = false;
  wrap.addEventListener('touchstart', e => {
    if (e.touches.length !== 1) return;
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, { passive: true });
  wrap.addEventListener('touchend', e => {
    if (e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    suppressClick = true;
    if (Math.abs(dx) > 40) { navigate(dx < 0 ? 1 : -1); }
    else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
      const url = currentUrl();
      if (!isVideoUrl(url)) openLightbox(url, images.slice());
    }
  });
  wrap.addEventListener('click', () => {
    if (suppressClick) { suppressClick = false; return; }
    const url = currentUrl();
    if (isVideoUrl(url)) {
      // Tap a video to toggle play/pause without leaving the slideshow — the
      // lightbox can't render videos, so opening it would just show a broken
      // image icon.
      if (video.paused) {
        const p = video.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } else {
        video.pause();
      }
    } else {
      openLightbox(url, images.slice());
    }
  });

  show(0);
  const ctrl = { navigate, deleteCurrent };
  return ctrl;
}
