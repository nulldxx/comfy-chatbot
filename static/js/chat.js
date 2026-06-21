import {
  escapeHtml, parseJsonResponse, expandAliases, applyReplacements,
  deriveFaceDetailPrompt, isVideoUrl, DEFAULT_VIDEO_SETTINGS,
  buildVideoPrompt, i2vTooltip,
} from './utils.js';
import { state, DEFAULT_DENOISE } from './state.js';
import {
  messagesEl, inputEl, sendBtn, slashAcEl,
  scrollBottom, addMessage, createMediaElement,
  deleteImageFile, removeImageFromChat,
} from './dom.js';
import { openLightbox, closeLightbox } from './lightbox.js';
import {
  updateSlashAc, hideSlashAc, selectSlashAcItem, tryExpandAlias,
  renderSlashAc, getAcState, setAcFocused, tabCompleteSlashAc,
} from './autocomplete.js';
import { openMaskEditor, buildComparisonSlider } from './editors.js';
import { makeCommandHandler } from './commands.js';

// ---------------------------------------------------------------------------
// LoRA catalogue and alias catalogue — populated from server on load
// ---------------------------------------------------------------------------

fetch('/api/loras')
  .then(r => r.json())
  .then(loras => {
    state.LORAS = loras.map(entry => {
      const name     = typeof entry === 'string' ? entry : entry.name;
      const strength = typeof entry === 'string' ? 1.0  : (entry.strength ?? 1.0);
      return { name, strength, label: name.split('/').pop().replace(/\.safetensors$/i, '') };
    });
  })
  .catch(() => {});

fetch('/api/aliases')
  .then(r => r.json())
  .then(data => { if (data && typeof data === 'object') state.ALIASES = data; })
  .catch(() => {});

// ---------------------------------------------------------------------------
// Header status
// ---------------------------------------------------------------------------

function updateHeaderStatus() {
  const srv = state.currentServer  ? state.currentServer.name  : DEFAULT_SERVER;
  const wf  = state.currentWorkflow ? state.currentWorkflow     : DEFAULT_WORKFLOW;
  document.getElementById('header-status').textContent = `${srv}  ·  ${wf}`;
}
updateHeaderStatus();

// ---------------------------------------------------------------------------
// Input: auto-resize + alias expansion + slash autocomplete
// ---------------------------------------------------------------------------

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
  tryExpandAlias();
  updateSlashAc();
});

// ---------------------------------------------------------------------------
// Keyboard handlers
// ---------------------------------------------------------------------------

document.addEventListener('keydown', e => {
  if (!state.activeSlideshowCtrl) return;
  if (document.activeElement === inputEl) return;
  if (e.key === 'ArrowLeft')  { e.preventDefault(); state.activeSlideshowCtrl.navigate(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); state.activeSlideshowCtrl.navigate(1); }
  if (e.key === 'Delete')     { e.preventDefault(); state.activeSlideshowCtrl.deleteCurrent(); }
});

inputEl.addEventListener('keydown', e => {
  if (slashAcEl.classList.contains('open')) {
    const { acFocused, acMode, acMatches } = getAcState();
    if (e.key === 'Escape') { e.preventDefault(); hideSlashAc(); return; }
    if (e.key === 'Tab') {
      e.preventDefault();
      tabCompleteSlashAc();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setAcFocused(Math.min(acFocused + 1, acMatches.length - 1));
      renderSlashAc();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (acFocused <= 0) { setAcFocused(-1); renderSlashAc(); return; }
      setAcFocused(acFocused - 1);
      renderSlashAc();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && (acFocused >= 0 || acMode === 'lora')) {
      e.preventDefault();
      selectSlashAcItem(acFocused >= 0 ? acFocused : 0);
      return;
    }
  }

  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); return; }

  if (e.key === 'ArrowUp') {
    if (state.history.length === 0) return;
    e.preventDefault();
    if (state.historyIdx === -1) state.savedDraft = inputEl.value;
    state.historyIdx = Math.min(state.historyIdx + 1, state.history.length - 1);
    inputEl.value = state.history[state.historyIdx];
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    return;
  }

  if (e.key === 'ArrowDown') {
    if (state.historyIdx === -1) return;
    e.preventDefault();
    state.historyIdx--;
    inputEl.value = state.historyIdx === -1 ? state.savedDraft : state.history[state.historyIdx];
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    return;
  }
});

sendBtn.addEventListener('click', sendMessage);

// Lightbox: click image to open, click close or outside to close
document.addEventListener('click', e => {
  if (e.target.tagName === 'IMG' && e.target.closest('.bubble') && !e.target.closest('.slideshow')) {
    openLightbox(e.target.src);
  }
});
document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
document.getElementById('lightbox').addEventListener('click', e => {
  if (e.target === document.getElementById('lightbox')) closeLightbox();
});

// Tap a user bubble to re-edit that prompt
messagesEl.addEventListener('click', e => {
  if (e.target.tagName === 'IMG') return;
  const bubble = e.target.closest('.message.user .bubble');
  if (!bubble || !bubble.dataset.prompt) return;
  const text = bubble.dataset.prompt;
  inputEl.value = text;
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
  state.historyIdx = -1;
  state.savedDraft = '';
  inputEl.focus();
  inputEl.setSelectionRange(text.length, text.length);
  inputEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

// ---------------------------------------------------------------------------
// Drag-and-drop image import
// ---------------------------------------------------------------------------

(function setupImageDrop() {
  const overlay = document.getElementById('drop-overlay');
  let dragDepth = 0;

  const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');

  document.addEventListener('dragenter', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    overlay.classList.add('open');
  });
  document.addEventListener('dragover', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  document.addEventListener('dragleave', e => {
    if (!hasFiles(e)) return;
    dragDepth--;
    if (dragDepth <= 0) { dragDepth = 0; overlay.classList.remove('open'); }
  });
  document.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    overlay.classList.remove('open');
    const files = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith('image/'));
    if (!files.length) return;
    files.forEach(importDroppedImage);
  });
})();

function importDroppedImage(file) {
  const bubble = addMessage('bot', `<div class="status-text">Importing <code>${escapeHtml(file.name)}</code>…</div>`);
  const fd = new FormData();
  fd.append('file', file);
  fetch('/api/import-image', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      bubble.innerHTML = '';
      state.sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not import image: ${escapeHtml(err.message)}</span>`;
    });
}

function extractLastFrame(url) {
  const bubble = addMessage('bot', '<div class="status-text">Cutting last frame…</div>');
  return fetch('/api/extract-last-frame', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      bubble.innerHTML = '';
      if (state.imageVideoMeta[url]) state.imageVideoMeta[data.url] = { ...state.imageVideoMeta[url] };
      if (state.imagePrompts[url]) state.imagePrompts[data.url] = state.imagePrompts[url];
      state.sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not cut last frame: ${escapeHtml(err.message)}</span>`;
    });
}

// ---------------------------------------------------------------------------
// Video / image metadata editor (pencil overlay on generated images)
// ---------------------------------------------------------------------------

function openVideoMetaEditor(url, wrap) {
  const meta = state.imageVideoMeta[url] || {};

  const box = document.createElement('div');
  box.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';

  const mkRow = (label, value, placeholder, multiline) => {
    const row = document.createElement('div');
    row.style.cssText = `display:flex;${multiline ? 'align-items:flex-start' : 'align-items:center'};gap:8px;font-size:0.85rem;color:#cbd5e1`;
    const lbl = document.createElement('span');
    lbl.textContent = label + ':';
    lbl.style.cssText = `min-width:64px;color:#94a3b8${multiline ? ';padding-top:4px' : ''}`;
    const input = document.createElement(multiline ? 'textarea' : 'input');
    if (!multiline) input.type = 'text';
    input.value = value || '';
    input.placeholder = placeholder;
    input.style.cssText = `flex:1;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:4px 6px;font-size:0.85rem${multiline ? ';resize:vertical;min-height:48px;font-family:inherit' : ''}`;
    row.appendChild(lbl); row.appendChild(input);
    box.appendChild(row);
    return input;
  };

  const promptInput = mkRow('Prompt', state.imagePrompts[url], 'image generation prompt', true);
  const actionInput = mkRow('Action', meta.action, 'what happens in the video');
  const audioInput  = mkRow('Audio',  meta.audio,  'sounds / dialogue');

  const refreshTooltip = () => {
    const i2v = wrap && wrap.querySelector('.img-i2v');
    if (i2v) i2v.title = i2vTooltip(state.imageVideoMeta[url]);
  };

  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
  const applyBtn = document.createElement('button');
  applyBtn.textContent = 'Apply';
  applyBtn.className = 'sel-btn';
  applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
  const clearBtn = document.createElement('button');
  clearBtn.textContent = 'Clear';
  clearBtn.className = 'sel-btn';
  clearBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';

  applyBtn.addEventListener('click', () => {
    const prompt = promptInput.value.trim();
    const action = actionInput.value.trim();
    const audio  = audioInput.value.trim();
    if (prompt) state.imagePrompts[url] = prompt; else delete state.imagePrompts[url];
    if (action || audio) {
      state.imageVideoMeta[url] = { action, audio };
    } else {
      delete state.imageVideoMeta[url];
    }
    refreshTooltip();
    addMessage('bot', `Metadata set — Prompt <strong style="color:#a78bfa">${escapeHtml(prompt || '—')}</strong> · Action <strong style="color:#a78bfa">${escapeHtml(action || '—')}</strong> · Audio <strong style="color:#a78bfa">${escapeHtml(audio || '—')}</strong>.`);
    scrollBottom();
  });
  clearBtn.addEventListener('click', () => {
    delete state.imagePrompts[url];
    delete state.imageVideoMeta[url];
    refreshTooltip();
    addMessage('bot', 'Metadata cleared.');
    scrollBottom();
  });
  btnRow.appendChild(applyBtn);
  btnRow.appendChild(clearBtn);
  box.appendChild(btnRow);

  const bubble = addMessage('bot', '<strong>Edit metadata</strong> <span style="color:#475569">(prompt · image2video action / audio)</span>').parentElement.querySelector('.bubble');
  bubble.appendChild(box);
  scrollBottom();
}

// ---------------------------------------------------------------------------
// Image2image pre-run dialog
// ---------------------------------------------------------------------------

function showI2IDialog(wrap, defaultPrompt, defaultDenoise) {
  return new Promise((resolve, reject) => {
    const overlay = document.createElement('div');
    overlay.className = 'img-i2i-dialog';

    const card = document.createElement('div');
    card.className = 'img-i2i-dialog-card';

    const title = document.createElement('div');
    title.className = 'img-i2i-dialog-title';
    title.textContent = 'Image to image';

    const promptLabel = document.createElement('label');
    promptLabel.className = 'img-i2i-dialog-label';
    promptLabel.textContent = 'Prompt';

    const promptEl = document.createElement('textarea');
    promptEl.className = 'img-i2i-dialog-prompt';
    promptEl.value = defaultPrompt;

    const denoiseLabel = document.createElement('label');
    denoiseLabel.className = 'img-i2i-dialog-label';
    denoiseLabel.textContent = 'Denoise';

    const denoiseRow = document.createElement('div');
    denoiseRow.className = 'img-i2i-dialog-denoise-row';

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.className = 'img-i2i-dialog-slider';
    slider.min = '0'; slider.max = '1'; slider.step = '0.01';
    slider.value = String(defaultDenoise);

    const denoiseVal = document.createElement('span');
    denoiseVal.className = 'img-i2i-dialog-denoise-val';
    denoiseVal.textContent = defaultDenoise.toFixed(2);

    slider.addEventListener('input', () => {
      denoiseVal.textContent = parseFloat(slider.value).toFixed(2);
    });

    denoiseRow.append(slider, denoiseVal);

    const actions = document.createElement('div');
    actions.className = 'img-i2i-dialog-actions';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'img-i2i-dialog-btn';
    cancelBtn.type = 'button';
    cancelBtn.textContent = 'Cancel';

    const okBtn = document.createElement('button');
    okBtn.className = 'img-i2i-dialog-btn img-i2i-dialog-btn-ok';
    okBtn.type = 'button';
    okBtn.textContent = 'OK';

    const dismiss = () => overlay.remove();

    cancelBtn.addEventListener('click', () => { dismiss(); reject(); });
    okBtn.addEventListener('click', () => {
      dismiss();
      resolve({ prompt: promptEl.value.trim(), denoise: parseFloat(slider.value) });
    });
    promptEl.addEventListener('keydown', e => {
      if (e.key === 'Escape') { dismiss(); reject(); }
    });

    actions.append(cancelBtn, okBtn);
    card.append(title, promptLabel, promptEl, denoiseLabel, denoiseRow, actions);
    overlay.appendChild(card);
    wrap.appendChild(overlay);

    promptEl.focus();
    promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
  });
}

// ---------------------------------------------------------------------------
// Runner functions (face-detail, upscale, image2image, image2video, inpaint, do-over)
// ---------------------------------------------------------------------------

function runFaceDetail(prompt, image, imgWrap) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration(prompt, '', null, {
    face: { image, workflow: state.currentFaceWorkflow || DEFAULT_FACE_WORKFLOW },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runUpscale(image, imgWrap) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration('', '', null, {
    upscale: { image, workflow: state.currentUpscaleWorkflow || DEFAULT_UPSCALE_WORKFLOW },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runImage2Image(prompt, image, imgWrap, denoiseOverride) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration(prompt, '', null, {
    image2image: { image, workflow: state.currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW, denoiseOverride },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runImage2Video(prompt, image) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  const lastFrame = (state.lastFrameUrl && state.lastFrameUrl !== image) ? state.lastFrameUrl : null;
  return runGeneration(prompt, '', null, {
    image2video: { image, lastFrame, workflow: state.currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW },
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runInpaint(image, mask, imgWrap, prompt, denoise, maskB64, drawToken) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration(prompt || '', '', null, {
    inpaint: { image, mask, workflow: state.currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW, denoise, maskB64, prompt, drawToken },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runRemove(image, mask, imgWrap, _prompt, _denoise, maskB64) {
  state.iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration('', '', null, {
    removal: { image, mask, workflow: state.currentRemovalWorkflow || DEFAULT_REMOVAL_WORKFLOW, maskB64 },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runDoOver(url, imgWrap) {
  const prompt = state.imagePrompts[url] || '';
  return runGeneration(prompt, '', null, { replaceWrap: imgWrap, preserveMtimeFrom: url });
}

// ---------------------------------------------------------------------------
// Last-frame toggle (🎞 button for image2video end-frame interpolation)
// ---------------------------------------------------------------------------

function lastFrameButtonTitle(url) {
  return url === state.lastFrameUrl
    ? 'This image is the image2video end frame — click to unset'
    : 'Use this image as the image2video end frame (last frame)';
}

function refreshLastFrameButtons() {
  document.querySelectorAll('.img-lastframe').forEach(b => {
    b.classList.toggle('active', state.lastFrameUrl !== null && b.dataset.url === state.lastFrameUrl);
    b.title = lastFrameButtonTitle(b.dataset.url);
  });
}

function makeLastFrameButton(url, extraClass) {
  const btn = document.createElement('button');
  btn.className = 'img-lastframe' + (extraClass ? ' ' + extraClass : '');
  btn.dataset.url = url;
  btn.innerHTML = '&#127902;&#xFE0E;';
  btn.title = lastFrameButtonTitle(url);
  if (url === state.lastFrameUrl) btn.classList.add('active');
  btn.addEventListener('click', e => {
    e.stopPropagation();
    state.lastFrameUrl = (state.lastFrameUrl === url) ? null : url;
    refreshLastFrameButtons();
  });
  return btn;
}

// ---------------------------------------------------------------------------
// appendChatImage — renders a generated image/video with its action buttons
// ---------------------------------------------------------------------------

function appendChatImage(container, url) {
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';

  const media = createMediaElement(url, { autoplay: true });

  if (isVideoUrl(url)) {
    const del = document.createElement('button');
    del.className = 'img-del';
    del.title = 'Delete video';
    del.innerHTML = '&#128465;&#xFE0E;';
    del.addEventListener('click', e => {
      e.stopPropagation();
      if (del.disabled) return;
      del.disabled = true;
      deleteImageFile(url)
        .then(() => removeImageFromChat(url))
        .catch(err => {
          del.disabled = false;
          del.innerHTML = '&#9888;&#xFE0E;';
          del.title = 'Delete failed: ' + err.message + ' — click to retry';
          setTimeout(() => {
            del.innerHTML = '&#128465;&#xFE0E;';
            del.title = 'Delete video';
          }, 3000);
        });
    });

    const cut = document.createElement('button');
    cut.className = 'img-cut';
    cut.title = 'Cut last frame into the chat as an image';
    cut.innerHTML = '&#9986;&#xFE0E;';
    cut.addEventListener('click', e => {
      e.stopPropagation();
      if (cut.disabled) return;
      cut.disabled = true;
      extractLastFrame(url).finally(() => { cut.disabled = false; });
    });

    wrap.appendChild(media);
    wrap.appendChild(del);
    wrap.appendChild(cut);
    container.appendChild(wrap);
    return;
  }

  const img = media;

  const face = document.createElement('button');
  face.className = 'img-face';
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
    runFaceDetail(prompt, url, wrap).finally(() => { face.disabled = false; });
  });

  const up = document.createElement('button');
  up.className = 'img-up';
  up.title = 'Upscale image';
  up.textContent = '↑';
  up.addEventListener('click', e => {
    e.stopPropagation();
    if (up.disabled || sendBtn.disabled) return;
    up.disabled = true;
    runUpscale(url, wrap).finally(() => { up.disabled = false; });
  });

  const del = document.createElement('button');
  del.className = 'img-del';
  del.title = 'Delete image';
  del.innerHTML = '&#128465;&#xFE0E;';
  del.addEventListener('click', e => {
    e.stopPropagation();
    if (del.disabled) return;
    del.disabled = true;
    deleteImageFile(url)
      .then(() => removeImageFromChat(url))
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

  const redo = document.createElement('button');
  redo.className = 'img-redo';
  redo.title = 'Regenerate this image';
  redo.innerHTML = '&#x21BA;&#xFE0E;';
  redo.addEventListener('click', e => {
    e.stopPropagation();
    if (redo.disabled || sendBtn.disabled) return;
    redo.disabled = true;
    runDoOver(url, wrap).finally(() => { redo.disabled = false; });
  });

  const i2i = document.createElement('button');
  i2i.className = 'img-i2i';
  i2i.title = 'Image to image';
  i2i.innerHTML = '&#127912;&#xFE0E;';
  i2i.addEventListener('click', e => {
    e.stopPropagation();
    if (i2i.disabled || sendBtn.disabled) return;
    let defaultPrompt;
    if (state.image2imageOverridePrompt) {
      defaultPrompt = state.image2imageOverridePrompt;
    } else {
      const orig = state.imagePrompts[url];
      if (!orig) {
        addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
        return;
      }
      defaultPrompt = applyReplacements(orig, state.image2imageReplacements);
    }
    showI2IDialog(wrap, defaultPrompt, state.currentDenoise.image2image)
      .then(({ prompt, denoise }) => {
        i2i.disabled = true;
        addMessage('user', 'Image2image: ' + escapeHtml(prompt), prompt);
        runImage2Image(prompt, url, wrap, denoise).finally(() => { i2i.disabled = false; });
      })
      .catch(() => {}); // cancelled
  });

  const inpaintBtn = document.createElement('button');
  inpaintBtn.className = 'img-inpaint';
  inpaintBtn.title = 'Inpaint';
  inpaintBtn.textContent = '🩹';
  inpaintBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (inpaintBtn.disabled || sendBtn.disabled) return;
    openMaskEditor(url, wrap, { onInpaint: runInpaint, onRemove: runRemove });
  });

  const i2v = document.createElement('button');
  i2v.className = 'img-i2v';
  i2v.title = i2vTooltip(state.imageVideoMeta[url]);
  i2v.innerHTML = '&#127916;&#xFE0E;';
  i2v.addEventListener('click', e => {
    e.stopPropagation();
    if (i2v.disabled || sendBtn.disabled) return;
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
    i2v.disabled = true;
    addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
    runImage2Video(prompt, url).finally(() => { i2v.disabled = false; });
  });

  const editMeta = document.createElement('button');
  editMeta.className = 'img-edit-meta';
  editMeta.title = 'Edit metadata (prompt / action / audio)';
  editMeta.innerHTML = '&#9998;&#xFE0E;';
  editMeta.addEventListener('click', e => {
    e.stopPropagation();
    openVideoMetaEditor(url, wrap);
  });

  const lastframe = makeLastFrameButton(url);

  wrap.appendChild(img);
  wrap.appendChild(del);
  wrap.appendChild(face);
  wrap.appendChild(up);
  wrap.appendChild(redo);
  wrap.appendChild(i2i);
  wrap.appendChild(inpaintBtn);
  wrap.appendChild(i2v);
  wrap.appendChild(lastframe);
  wrap.appendChild(editMeta);

  const maskCtx = state.imageMasks[url];
  if (maskCtx) {
    const reinpaint = document.createElement('button');
    reinpaint.className = 'img-reinpaint';
    reinpaint.title = 'Re-run inpaint with the same mask';
    reinpaint.innerHTML = '&#x21BB;&#xFE0E;';
    reinpaint.addEventListener('click', e => {
      e.stopPropagation();
      if (reinpaint.disabled || sendBtn.disabled) return;
      reinpaint.disabled = true;
      fetch('/api/upload-mask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: maskCtx.maskB64 }),
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) throw new Error(data.error);
        addMessage('user', `Inpaint: ${escapeHtml(maskCtx.prompt || '')}`);
        runInpaint(url, data.token, wrap, maskCtx.prompt, maskCtx.denoise, maskCtx.maskB64);
      })
      .catch(err => {
        reinpaint.disabled = false;
        addMessage('bot', `<span style="color:#f87171">⚠ Mask upload failed: ${escapeHtml(err.message)}</span>`);
      });
    });
    wrap.appendChild(reinpaint);
  }

  container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// compositeVideos — joins an ordered list of video URLs into one clip
// ---------------------------------------------------------------------------

function compositeVideos(orderedUrls) {
  const bubble = addMessage('bot', '<div class="status-text">Compositing videos…</div>');
  return fetch('/api/composite-videos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls: orderedUrls }),
  })
    .then(parseJsonResponse)
    .then(data => {
      if (data.error) throw new Error(data.error);
      bubble.innerHTML = '';
      state.sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not composite videos: ${escapeHtml(err.message)}</span>`;
    });
}

// ---------------------------------------------------------------------------
// Session save / restore
// ---------------------------------------------------------------------------

function captureSessionMessages() {
  const messages = [];
  messagesEl.querySelectorAll('.message').forEach(msg => {
    const role = msg.classList.contains('user') ? 'user' : 'bot';
    const bubble = msg.querySelector('.bubble');
    if (!bubble) return;
    if (role === 'user') {
      const prompt = bubble.dataset.prompt;
      if (prompt) messages.push({ role: 'user', prompt });
    } else {
      const images = [...bubble.querySelectorAll('.img-wrap img, .img-wrap video')].map(m => m.getAttribute('src'));
      if (images.length) {
        const statusEl = bubble.querySelector('.status-text');
        const text = statusEl ? statusEl.textContent.trim() : '';
        messages.push({ role: 'bot', images, text });
      }
    }
  });
  return messages;
}

function restoreSession(data) {
  state.history.length = 0;
  state.historyIdx = -1;
  state.savedDraft = '';
  state.sessionImages.length = 0;
  for (const k of Object.keys(state.imagePrompts)) delete state.imagePrompts[k];
  for (const k of Object.keys(state.imageVideoMeta)) delete state.imageVideoMeta[k];
  state.lastSequence = null;
  state.fauxFullscreenEls.clear();
  document.body.style.overflow = '';
  messagesEl.innerHTML = '';

  const s = data.settings || {};
  if (s.server              !== undefined) state.currentServer             = s.server;
  if (s.workflow            !== undefined) state.currentWorkflow           = s.workflow;
  if (s.faceWorkflow        !== undefined) state.currentFaceWorkflow       = s.faceWorkflow;
  if (s.upscaleWorkflow     !== undefined) state.currentUpscaleWorkflow    = s.upscaleWorkflow;
  if (s.image2imageWorkflow !== undefined) state.currentImage2ImageWorkflow = s.image2imageWorkflow;
  if (s.image2videoWorkflow !== undefined) state.currentImage2VideoWorkflow = s.image2videoWorkflow;
  if (s.inpaintingWorkflow  !== undefined) state.currentInpaintingWorkflow  = s.inpaintingWorkflow;
  if (s.resolution          !== undefined) state.currentResolution         = s.resolution;
  if (s.generationSteps     !== undefined) state.currentGenerationSteps    = s.generationSteps;
  if (s.iterations          !== undefined) state.iterations                = s.iterations;
  if (s.sequenceReplacements    !== undefined) state.sequenceReplacements    = s.sequenceReplacements;
  if (s.image2imageReplacements !== undefined) state.image2imageReplacements = s.image2imageReplacements;
  if (s.image2imageOverridePrompt !== undefined) state.image2imageOverridePrompt = s.image2imageOverridePrompt;
  if (s.image2videoReplacements !== undefined) state.image2videoReplacements = s.image2videoReplacements;
  if (s.image2videoOverridePrompt !== undefined) state.image2videoOverridePrompt = s.image2videoOverridePrompt;
  if (s.lastFaceDetailPrompt    !== undefined) state.lastFaceDetailPrompt    = s.lastFaceDetailPrompt;
  if (s.lastInpaintingPrompt    !== undefined) state.lastInpaintingPrompt    = s.lastInpaintingPrompt;
  if (s.currentDenoise          !== undefined) state.currentDenoise          = { ...DEFAULT_DENOISE, ...s.currentDenoise };
  if (s.videoSettings           !== undefined) state.currentVideoSettings    = { ...DEFAULT_VIDEO_SETTINGS, ...s.videoSettings };
  if (s.videoLock               !== undefined) state.videoLock               = s.videoLock;
  state.iterationsFromSequence = false;
  updateHeaderStatus();

  for (const url of (data.sessionImages || [])) state.sessionImages.push(url);
  Object.assign(state.imagePrompts, data.imagePrompts || {});
  Object.assign(state.imageVideoMeta, data.imageVideoMeta || {});
  state.lastSequence = data.lastSequence || null;
  for (const entry of (data.promptHistory || [])) state.history.push(entry);

  const validImages = new Set(data.sessionImages || []);
  for (const msg of (data.messages || [])) {
    if (msg.role === 'user') {
      addMessage('user', escapeHtml(msg.prompt), msg.prompt);
    } else if (msg.role === 'bot' && msg.images && msg.images.length) {
      const bubble = addMessage('bot', msg.text ? `<div class="status-text">${escapeHtml(msg.text)}</div>` : '');
      msg.images.forEach(url => { if (validImages.has(url)) appendChatImage(bubble, url); });
      if (!bubble.querySelector('.img-wrap') && !bubble.textContent.trim()) {
        bubble.parentElement.remove();
      }
    }
  }
}

// ---------------------------------------------------------------------------
// sendMessage
// ---------------------------------------------------------------------------

function sendMessage() {
  let raw = inputEl.value.trim();

  if (state.pendingConfirm) {
    if (!raw) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    state.historyIdx = -1;
    state.savedDraft = '';
    addMessage('user', escapeHtml(raw), null);
    const cb = state.pendingConfirm;
    state.pendingConfirm = null;
    cb(raw);
    return;
  }

  if (!raw && state.history.length) raw = state.history[0];
  if (!raw || sendBtn.disabled) return;

  if (raw.startsWith('/')) {
    inputEl.value = '';
    inputEl.style.height = 'auto';
    if (state.history[0] !== raw) state.history.unshift(raw);
    state.historyIdx = -1;
    state.savedDraft = '';
    handleSlashCommand(raw);
    return;
  }

  if (state.history[0] !== raw) state.history.unshift(raw);
  state.historyIdx = -1;
  state.savedDraft = '';

  if (state.iterationsFromSequence) {
    state.iterations = 1;
    state.iterationsFromSequence = false;
  }

  raw = expandAliases(raw, state.ALIASES);

  addMessage('user', escapeHtml(raw), raw);
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;

  (async () => {
    for (let i = 0; i < state.iterations; i++) {
      const label = state.iterations > 1 ? ` (${i + 1}/${state.iterations})` : '';
      const ok = await runGeneration(raw, label);
      if (!ok) break;
    }
    sendBtn.disabled = false;
  })();
}

// ---------------------------------------------------------------------------
// runSequenceJob — runs a Grok prompt-expansion job over SSE
// ---------------------------------------------------------------------------

function runSequenceJob(endpoint, master, count, statusBubble) {
  return new Promise(resolve => {
    const statusText = statusBubble.querySelector('.status-text');

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'cancel-btn';
    cancelBtn.title = 'Cancel this job';
    cancelBtn.textContent = '✕';
    cancelBtn.disabled = true;
    statusBubble.appendChild(cancelBtn);

    const fail = message => {
      cancelBtn.remove();
      statusBubble.innerHTML = `<span style="color:#f87171">⚠ ${escapeHtml(message)}</span>`;
      resolve(null);
    };

    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: master, count, replacements: state.sequenceReplacements }),
    })
    .then(parseJsonResponse)
    .then(data => {
      if (data.error) throw new Error(data.error);

      cancelBtn.disabled = false;
      cancelBtn.addEventListener('click', () => {
        cancelBtn.disabled = true;
        if (statusText) statusText.textContent = 'Cancelling…';
        fetch('/api/cancel/' + data.job_id, { method: 'POST' }).catch(() => {});
      });

      const es = new EventSource(`/api/progress/${data.job_id}`);
      es.onmessage = e => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'progress') {
          if (statusText) statusText.textContent = msg.message;
          scrollBottom();
        } else if (msg.type === 'done') {
          es.close();
          cancelBtn.remove();
          resolve(msg);
        } else if (msg.type === 'cancelled') {
          es.close();
          fail('Cancelled');
        } else if (msg.type === 'error') {
          es.close();
          fail(msg.message);
        }
      };
      es.onerror = () => { es.close(); fail('Connection lost'); };
    })
    .catch(err => fail(err.message));
  });
}

// ---------------------------------------------------------------------------
// runGeneration — the core generation loop
// ---------------------------------------------------------------------------

function runGeneration(raw, label, workflowOverride, opts = {}) {
  return new Promise(resolve => {
  const face = opts.face || null;
  const upscale = opts.upscale || null;
  const image2image = opts.image2image || null;
  const image2video = opts.image2video || null;
  const inpaint = opts.inpaint || null;
  const removal = opts.removal || null;
  const videoMeta = opts.videoMeta || null;
  const replaceWrap = opts.replaceWrap || null;
  const sliderReplace = opts.sliderReplace || null;
  const preserveMtimeFrom = opts.preserveMtimeFrom || null;
  const inPlaceWrap = sliderReplace || replaceWrap;
  const job = face || upscale || image2image || image2video || inpaint || removal;
  const endpoint = face ? '/api/face-detail'
                 : upscale ? '/api/upscale'
                 : image2image ? '/api/image2image'
                 : image2video ? '/api/image2video'
                 : inpaint ? '/api/inpaint'
                 : removal ? '/api/remove'
                 : '/api/generate';
  const botBubble = addMessage('bot', `
    <div class="status-text" id="status-line">Connecting…${label}</div>
    <div class="dots"><span></span><span></span><span></span></div>
    <div class="progress-bar-wrap"><div class="progress-bar"></div></div>
  `);

  const statusLine = botBubble.querySelector('#status-line');
  const dotsEl     = botBubble.querySelector('.dots');
  const barWrap    = botBubble.querySelector('.progress-bar-wrap');

  const startTime = Date.now();

  if (inPlaceWrap && inPlaceWrap.parentNode) {
    const srcMessage = inPlaceWrap.closest('.message');
    if (srcMessage) srcMessage.after(botBubble.parentElement);
    inPlaceWrap.scrollIntoView({ block: 'center' });
  }

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'cancel-btn';
  cancelBtn.title = 'Cancel this job';
  cancelBtn.textContent = '✕';
  cancelBtn.disabled = true;
  botBubble.appendChild(cancelBtn);

  const wf = job ? job.workflow : (workflowOverride || state.currentWorkflow);
  fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt: raw,
      ...(state.currentServer ? { server: state.currentServer.address, server_os: state.currentServer.os } : {}),
      ...(wf ? { workflow: wf } : {}),
      ...(!job && state.currentResolution ? { width: state.currentResolution.width, height: state.currentResolution.height } : {}),
      ...(!job && state.currentGenerationSteps !== null ? { steps: state.currentGenerationSteps } : {}),
      ...(job ? { image: job.image } : {}),
      ...(inpaint ? { mask: inpaint.mask } : {}),
      ...(inpaint && inpaint.drawToken ? { draw_token: inpaint.drawToken } : {}),
      ...(removal ? { mask: removal.mask } : {}),
      ...(face        ? { denoise: state.currentDenoise.face } : {}),
      ...(upscale     ? { denoise: state.currentDenoise.upscale } : {}),
      ...(image2image ? { denoise: image2image.denoiseOverride != null ? image2image.denoiseOverride : state.currentDenoise.image2image } : {}),
      ...(image2video ? { duration: state.currentVideoSettings.duration, frames: state.currentVideoSettings.frames, fps: state.currentVideoSettings.fps, video_width: state.currentVideoSettings.width, video_height: state.currentVideoSettings.height } : {}),
      ...(image2video && image2video.lastFrame ? { last_frame: image2video.lastFrame } : {}),
      ...(inpaint ? { denoise: inpaint.denoise != null ? inpaint.denoise : state.currentDenoise.inpaint } : {}),
      ...(preserveMtimeFrom ? { preserve_mtime_from: preserveMtimeFrom } : {}),
    }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) throw new Error(data.error);

    cancelBtn.disabled = false;
    cancelBtn.addEventListener('click', () => {
      cancelBtn.disabled = true;
      statusLine.textContent = 'Cancelling…' + label;
      fetch('/api/cancel/' + data.job_id, { method: 'POST' }).catch(() => {});
    });

    const es = new EventSource(`/api/progress/${data.job_id}`);

    es.onmessage = e => {
      const msg = JSON.parse(e.data);

      if (msg.type === 'progress') {
        statusLine.textContent = msg.message + label;
        if (!inPlaceWrap) scrollBottom();

      } else if (msg.type === 'done') {
        es.close();
        dotsEl.remove();
        barWrap.remove();
        cancelBtn.remove();
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        statusLine.textContent = `Done — ${msg.images.length} result(s) in ${elapsed}s${label}`;
        const originPrompt = job ? (state.imagePrompts[job.image] || '') : (raw || '');
        const originVideoMeta = videoMeta || (job ? state.imageVideoMeta[job.image] : null);

        if (sliderReplace && sliderReplace.parentNode && msg.images.length === 1) {
          const oldUrl = sliderReplace.querySelector('img').getAttribute('src');
          const newUrl = msg.images[0];
          const onAccept = sliderEl => {
            deleteImageFile(oldUrl).catch(() => {});
            const idx = state.sessionImages.indexOf(oldUrl);
            if (idx !== -1) state.sessionImages.splice(idx, 1, newUrl);
            else state.sessionImages.push(newUrl);
            delete state.imagePrompts[oldUrl];
            delete state.imageVideoMeta[oldUrl];
            if (originPrompt) state.imagePrompts[newUrl] = originPrompt;
            if (originVideoMeta) state.imageVideoMeta[newUrl] = originVideoMeta;
            delete state.imageMasks[oldUrl];
            if (inpaint && inpaint.maskB64) {
              state.imageMasks[newUrl] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
            }
            if (removal && removal.maskB64) {
              state.imageMasks[newUrl] = { maskB64: removal.maskB64, isRemoval: true };
            }
            const tmp = document.createElement('div');
            appendChatImage(tmp, newUrl);
            sliderEl.replaceWith(tmp.firstChild);
          };
          const onReject = sliderEl => {
            deleteImageFile(newUrl).catch(() => {});
            if (inpaint && inpaint.maskB64) {
              state.imageMasks[oldUrl] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
            }
            if (removal && removal.maskB64) {
              state.imageMasks[oldUrl] = { maskB64: removal.maskB64, isRemoval: true };
            }
            const tmp = document.createElement('div');
            appendChatImage(tmp, oldUrl);
            sliderEl.replaceWith(tmp.firstChild);
          };
          const onComposite = face ? (compositeUrl, sliderEl) => {
            deleteImageFile(oldUrl).catch(() => {});
            deleteImageFile(newUrl).catch(() => {});
            const idx = state.sessionImages.indexOf(oldUrl);
            if (idx !== -1) state.sessionImages.splice(idx, 1, compositeUrl);
            else state.sessionImages.push(compositeUrl);
            delete state.imagePrompts[oldUrl];
            delete state.imageMasks[oldUrl];
            delete state.imageVideoMeta[oldUrl];
            if (originPrompt) state.imagePrompts[compositeUrl] = originPrompt;
            if (originVideoMeta) state.imageVideoMeta[compositeUrl] = originVideoMeta;
            const tmp = document.createElement('div');
            appendChatImage(tmp, compositeUrl);
            sliderEl.replaceWith(tmp.firstChild);
          } : null;
          sliderReplace.replaceWith(buildComparisonSlider(oldUrl, newUrl, onAccept, onReject, onComposite));
          botBubble.parentElement.remove();
          resolve(true);
          return;
        }

        msg.images.forEach((url, i) => {
          if (originPrompt) state.imagePrompts[url] = originPrompt;
          if (originVideoMeta) state.imageVideoMeta[url] = originVideoMeta;
          if (inpaint && inpaint.maskB64) {
            state.imageMasks[url] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
          }
          if (i === 0 && replaceWrap && replaceWrap.parentNode) {
            const oldImg = replaceWrap.querySelector('img');
            const oldSrc = oldImg ? oldImg.getAttribute('src') : null;
            if (oldSrc) {
              deleteImageFile(oldSrc).catch(() => {});
              delete state.imagePrompts[oldSrc];
              delete state.imageVideoMeta[oldSrc];
            }
            const oldIdx = oldSrc ? state.sessionImages.indexOf(oldSrc) : -1;
            if (oldIdx !== -1) state.sessionImages.splice(oldIdx, 1, url);
            else state.sessionImages.push(url);
            const tmp = document.createElement('div');
            appendChatImage(tmp, url);
            replaceWrap.replaceWith(tmp.firstChild);
          } else {
            state.sessionImages.push(url);
            appendChatImage(botBubble, url);
          }
        });
        if (replaceWrap && msg.images.length === 1) {
          botBubble.parentElement.remove();
        } else {
          scrollBottom();
        }
        resolve(true);

      } else if (msg.type === 'cancelled') {
        es.close();
        dotsEl.remove();
        barWrap.remove();
        cancelBtn.remove();
        statusLine.textContent = 'Cancelled' + label;
        if (!inPlaceWrap) scrollBottom();
        resolve(false);

      } else if (msg.type === 'error') {
        es.close();
        dotsEl.remove();
        barWrap.remove();
        cancelBtn.remove();
        statusLine.textContent = '';
        botBubble.innerHTML += `<span style="color:#f87171">⚠ ${escapeHtml(msg.message)}</span>`;
        if (!inPlaceWrap) scrollBottom();
        resolve(false);
      }
    };

    es.onerror = () => {
      es.close();
      if (dotsEl.parentNode) dotsEl.remove();
      if (barWrap.parentNode) barWrap.remove();
      cancelBtn.remove();
      botBubble.innerHTML += `<span style="color:#f87171">⚠ Connection lost — check server logs.</span>`;
      resolve(false);
    };
  })
  .catch(err => {
    if (dotsEl.parentNode) dotsEl.remove();
    if (barWrap.parentNode) barWrap.remove();
    cancelBtn.remove();
    statusLine.textContent = '';
    botBubble.innerHTML += `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`;
    resolve(false);
  });
  });
}

// ---------------------------------------------------------------------------
// Wire up the command handler (after all deps are defined)
// ---------------------------------------------------------------------------

const { handleSlashCommand } = makeCommandHandler({
  runGeneration,
  runFaceDetail,
  runUpscale,
  runImage2Image,
  runImage2Video,
  runInpaint,
  runDoOver,
  captureSessionMessages,
  restoreSession,
  updateHeaderStatus,
  compositeVideos,
  runSequenceJob,
  appendChatImage,
});
