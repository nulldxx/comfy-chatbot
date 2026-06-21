import {
  escapeHtml, isVideoUrl, applyReplacements, deriveFaceDetailPrompt,
  buildVideoPrompt, i2vTooltip, reorderList,
} from './utils.js';
import { state } from './state.js';
import { messagesEl, sendBtn, addMessage, scrollBottom, createMediaElement, deleteImageFile, removeImageFromChat } from './dom.js';
import { openLightbox } from './lightbox.js';
import { openMaskEditor } from './editors.js';

export function renderSequenceReview(bubble, seq, { runGeneration }) {
  bubble.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'seq-review-header';
  header.textContent = `Last ${seq.video ? 'video ' : ''}sequence — ${seq.items.length} prompt(s). Press ▶ to generate an image from a prompt.`;
  bubble.appendChild(header);

  const list = document.createElement('div');
  list.className = 'seq-review';

  seq.items.forEach((item, idx) => {
    const row = document.createElement('div');
    row.className = 'seq-review-row';

    const play = document.createElement('button');
    play.className = 'seq-review-play';
    play.title = 'Generate an image from this prompt';
    play.textContent = '▶';
    play.addEventListener('click', () => {
      if (play.disabled || sendBtn.disabled) return;
      play.disabled = true;
      const opts = seq.video
        ? { videoMeta: { action: item.action || '', audio: item.audio || '' } }
        : {};
      addMessage('user', escapeHtml(item.prompt), item.prompt);
      runGeneration(item.prompt, '', null, opts).finally(() => { play.disabled = false; });
    });

    const body = document.createElement('div');
    body.className = 'seq-review-body';

    const promptEl = document.createElement('div');
    promptEl.className = 'seq-review-prompt';
    promptEl.textContent = `${idx + 1}. ${item.prompt}`;
    body.appendChild(promptEl);

    if (seq.video && item.action) {
      const a = document.createElement('div');
      a.className = 'seq-review-meta';
      a.innerHTML = `<span class="seq-review-label">Action:</span> `;
      a.appendChild(document.createTextNode(item.action));
      body.appendChild(a);
    }
    if (seq.video && item.audio) {
      const a = document.createElement('div');
      a.className = 'seq-review-meta';
      a.innerHTML = `<span class="seq-review-label">Audio:</span> `;
      a.appendChild(document.createTextNode(item.audio));
      body.appendChild(a);
    }

    row.appendChild(play);
    row.appendChild(body);
    list.appendChild(row);
  });

  bubble.appendChild(list);
}

// Renders a responsive grid of the given image URLs into `bubble`. Tapping a
// thumb opens the lightbox; the trash button deletes from the output folder.
export function renderReviewGrid(bubble, urls, { runFaceDetail, runUpscale, runImage2Image, runDoOver, runImage2Video, runInpaint }) {
  bubble.innerHTML = '';
  const grid = document.createElement('div');
  grid.className = 'review-grid';

  urls.forEach(url => {
    const cell = document.createElement('div');
    cell.className = 'review-thumb';

    const isVideo = isVideoUrl(url);
    const media = createMediaElement(url);
    if (isVideo) {
      // Match the /composite-videos-session preview: show the first frame so the
      // cell isn't a blank black square, and start muted so native controls play
      // without surprising the user.
      media.muted = true;
      media.preload = 'metadata';
    } else {
      media.addEventListener('click', () => openLightbox(url));
    }

    const del = document.createElement('button');
    del.className = 'img-del review-del';
    del.title = 'Delete image';
    del.innerHTML = '&#128465;&#xFE0E;';
    del.addEventListener('click', e => {
      e.stopPropagation();
      if (del.disabled) return;
      del.disabled = true;
      deleteImageFile(url)
        .then(() => {
          removeImageFromChat(url);
          cell.remove();
          if (!grid.children.length) {
            bubble.innerHTML = 'All session images deleted.';
          }
        })
        .catch(err => {
          del.disabled = false;
          del.innerHTML = '&#9888;&#xFE0E;';
          del.title = 'Delete failed: ' + err.message + ' — click to retry';
          setTimeout(() => {
            del.innerHTML = '&#128465;&#xFE0E;';
            del.title = 'Delete image';
          }, 3000);
        });
    });

    if (isVideo) {
      cell.appendChild(media);
      cell.appendChild(del);
      grid.appendChild(cell);
      return;
    }

    const face = document.createElement('button');
    face.className = 'img-face review-face';
    face.title = 'Run face detail';
    face.innerHTML = '&#128100;&#xFE0E;';
    face.addEventListener('click', e => {
      e.stopPropagation();
      if (face.disabled || sendBtn.disabled) return;
      const prompt = state.lastFaceDetailPrompt || deriveFaceDetailPrompt(state.imagePrompts[url]);
      if (!prompt) {
        addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
        return;
      }
      face.disabled = true;
      addMessage('user', 'Face detail: ' + escapeHtml(prompt));
      runFaceDetail(prompt, url).finally(() => { face.disabled = false; });
    });

    const up = document.createElement('button');
    up.className = 'img-up review-up';
    up.title = 'Upscale image';
    up.textContent = '↑';
    up.addEventListener('click', e => {
      e.stopPropagation();
      if (up.disabled || sendBtn.disabled) return;
      up.disabled = true;
      addMessage('user', 'Upscale image');
      runUpscale(url).finally(() => { up.disabled = false; });
    });

    const ri2i = document.createElement('button');
    ri2i.className = 'img-i2i review-i2i';
    ri2i.title = 'Image to image';
    ri2i.innerHTML = '&#127912;&#xFE0E;';
    ri2i.addEventListener('click', e => {
      e.stopPropagation();
      if (ri2i.disabled || sendBtn.disabled) return;
      let prompt;
      if (state.image2imageOverridePrompt) {
        prompt = state.image2imageOverridePrompt;
      } else {
        const orig = state.imagePrompts[url];
        if (!orig) {
          addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        prompt = applyReplacements(orig, state.image2imageReplacements);
      }
      ri2i.disabled = true;
      addMessage('user', 'Image2image: ' + escapeHtml(prompt), prompt);
      runImage2Image(prompt, url).finally(() => { ri2i.disabled = false; });
    });

    const rredo = document.createElement('button');
    rredo.className = 'img-redo review-redo';
    rredo.title = 'Regenerate this image';
    rredo.innerHTML = '&#x21BA;&#xFE0E;';
    rredo.addEventListener('click', e => {
      e.stopPropagation();
      if (rredo.disabled || sendBtn.disabled) return;
      rredo.disabled = true;
      runDoOver(url).finally(() => { rredo.disabled = false; });
    });

    const rinpaint = document.createElement('button');
    rinpaint.className = 'img-inpaint review-inpaint';
    rinpaint.title = 'Inpaint';
    rinpaint.textContent = '🩹';
    rinpaint.addEventListener('click', e => {
      e.stopPropagation();
      if (rinpaint.disabled || sendBtn.disabled) return;
      openMaskEditor(url, null, { onInpaint: runInpaint });
    });

    const ri2v = document.createElement('button');
    ri2v.className = 'img-i2v review-i2v';
    ri2v.title = i2vTooltip(state.imageVideoMeta[url]);
    ri2v.innerHTML = '&#127916;&#xFE0E;';
    ri2v.addEventListener('click', e => {
      e.stopPropagation();
      if (ri2v.disabled || sendBtn.disabled) return;
      let prompt;
      if (state.image2videoOverridePrompt) {
        prompt = state.image2videoOverridePrompt;
      } else {
        const orig = state.imagePrompts[url];
        if (!orig) {
          addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2video-set-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        prompt = buildVideoPrompt(applyReplacements(orig, state.image2videoReplacements), state.imageVideoMeta[url], state.currentVideoSettings.audio);
      }
      ri2v.disabled = true;
      addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
      runImage2Video(prompt, url).finally(() => { ri2v.disabled = false; });
    });

    cell.appendChild(media);
    cell.appendChild(del);
    cell.appendChild(face);
    cell.appendChild(up);
    cell.appendChild(rredo);
    cell.appendChild(ri2i);
    cell.appendChild(rinpaint);
    cell.appendChild(ri2v);
    grid.appendChild(cell);
  });

  bubble.appendChild(grid);
  scrollBottom();
}

// Renders a draggable grid of videos for /composite-videos-session.
// The ✓ button calls compositeVideos(orderedUrls) which is injected from chat.js.
export function renderCompositeGrid(bubble, urls, { compositeVideos }) {
  bubble.innerHTML = '';

  const hint = document.createElement('div');
  hint.className = 'composite-hint';
  hint.textContent = 'Drag the ⠿ handle to reorder, play each to preview, then press ✓ to join them into one clip.';
  bubble.appendChild(hint);

  let order = urls.slice();
  const cells = new Map();

  const grid = document.createElement('div');
  grid.className = 'composite-grid';

  function renderBadges() {
    order.forEach((url, i) => {
      const cell = cells.get(url);
      if (cell) cell.querySelector('.composite-order').textContent = String(i + 1);
    });
  }

  function relayout() {
    order.forEach(url => grid.appendChild(cells.get(url)));
    renderBadges();
  }

  function cellFromPoint(x, y) {
    for (const cell of cells.values()) {
      const r = cell.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return cell;
    }
    return null;
  }

  let dragging = null;
  let draggingUrl = null;

  function attachDragHandle(handle, cell) {
    handle.addEventListener('pointerdown', e => {
      e.preventDefault();
      dragging = cell;
      draggingUrl = cell.dataset.url;
      cell.classList.add('composite-dragging');
      try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    });
    handle.addEventListener('pointermove', e => {
      if (!dragging) return;
      e.preventDefault();
      const target = cellFromPoint(e.clientX, e.clientY);
      if (target && target !== dragging) {
        const from = order.indexOf(draggingUrl);
        const to   = order.indexOf(target.dataset.url);
        if (from !== -1 && to !== -1) {
          order = reorderList(order, from, to);
          relayout();
        }
      }
    });
    const end = e => {
      if (!dragging) return;
      dragging.classList.remove('composite-dragging');
      dragging = null; draggingUrl = null;
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  order.forEach(url => {
    const cell = document.createElement('div');
    cell.className = 'composite-cell';
    cell.dataset.url = url;

    const video = createMediaElement(url);
    video.muted = true;
    video.preload = 'metadata';

    const badge = document.createElement('span');
    badge.className = 'composite-order';

    const handle = document.createElement('button');
    handle.className = 'composite-handle';
    handle.title = 'Drag to reorder';
    handle.setAttribute('aria-label', 'Drag to reorder');
    handle.textContent = '⠿';

    cell.appendChild(video);
    cell.appendChild(badge);
    cell.appendChild(handle);
    cells.set(url, cell);
    grid.appendChild(cell);
    attachDragHandle(handle, cell);
  });

  renderBadges();
  bubble.appendChild(grid);

  const controls = document.createElement('div');
  controls.className = 'composite-controls';
  const go = document.createElement('button');
  go.className = 'composite-go';
  go.title = 'Composite these videos into one';
  go.textContent = '✓';
  go.addEventListener('click', () => {
    if (go.disabled) return;
    go.disabled = true;
    compositeVideos(order.slice()).finally(() => { go.disabled = false; });
  });
  controls.appendChild(go);
  bubble.appendChild(controls);

  scrollBottom();
}
