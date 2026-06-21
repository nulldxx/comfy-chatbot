import { escapeHtml } from './utils.js';
import { state } from './state.js';
import { addMessage } from './dom.js';

// Opens a full-screen mask editor over `imageUrl`. The user paints a translucent
// yellow mask; on "Apply Inpaint" the mask is exported as a binary PNG, uploaded
// to the server, and onInpaint() is called. `imgWrap` is the source .img-wrap
// for the in-place comparison slider (null in the review-grid case).
export function openMaskEditor(imageUrl, imgWrap, { onInpaint, onRemove }) {
  if (document.getElementById('mask-editor-overlay')) return;

  // Capture the prompt at editor-open time so a concurrent /inpainting-prompt
  // command can't silently change what gets submitted.
  const capturedPrompt = state.lastInpaintingPrompt;

  let aborted = false;
  const dpr = window.devicePixelRatio || 1;

  const overlay = document.createElement('div');
  overlay.id = 'mask-editor-overlay';

  const wrap = document.createElement('div');
  wrap.id = 'mask-editor-wrap';

  const img = document.createElement('img');
  img.src = imageUrl;
  img.draggable = false;

  // Drawing layer (colored pen strokes) sits between the image and the mask so the
  // yellow mask always renders on top.
  const drawCanvas = document.createElement('canvas');
  drawCanvas.id = 'mask-editor-draw-canvas';

  const canvas = document.createElement('canvas');
  canvas.id = 'mask-editor-canvas';

  wrap.appendChild(img);
  wrap.appendChild(drawCanvas);
  wrap.appendChild(canvas);
  overlay.appendChild(wrap);

  const promptRow = document.createElement('div');
  promptRow.id = 'mask-editor-prompt-row';
  const promptLabel = document.createElement('label');
  promptLabel.textContent = 'Prompt:';
  promptLabel.className = 'mask-editor-prompt-label';
  const promptInput = document.createElement('input');
  promptInput.type = 'text';
  promptInput.id = 'mask-editor-prompt';
  promptInput.value = capturedPrompt || '';
  promptInput.placeholder = 'Describe what to inpaint…';
  promptLabel.htmlFor = 'mask-editor-prompt';
  promptInput.addEventListener('input', () => promptInput.classList.remove('mask-editor-prompt-invalid'));
  promptRow.appendChild(promptLabel);
  promptRow.appendChild(promptInput);
  overlay.appendChild(promptRow);

  const actions = document.createElement('div');
  actions.id = 'mask-editor-actions';

  const toolGroup = document.createElement('div');
  toolGroup.className = 'mask-editor-tools';

  const maskToolBtn = document.createElement('button');
  maskToolBtn.type = 'button';
  maskToolBtn.className = 'mask-editor-tool';
  maskToolBtn.textContent = '🩹';
  maskToolBtn.title = 'Mask tool — paint the area to inpaint';

  const penToolBtn = document.createElement('button');
  penToolBtn.type = 'button';
  penToolBtn.className = 'mask-editor-tool';
  penToolBtn.textContent = '✏️';
  penToolBtn.title = 'Pen tool — draw a colour hint for the inpainter';

  const colorPicker = document.createElement('input');
  colorPicker.type = 'color';
  colorPicker.id = 'mask-editor-color';
  colorPicker.value = '#ff3b30';
  colorPicker.title = 'Pen colour';

  let removalMode = false;
  let removeToolBtn = null;
  if (onRemove) {
    removeToolBtn = document.createElement('button');
    removeToolBtn.type = 'button';
    removeToolBtn.className = 'mask-editor-tool';
    removeToolBtn.textContent = '✂';
    removeToolBtn.title = 'Removal mode — paint over the object to erase it';
  }

  toolGroup.appendChild(maskToolBtn);
  toolGroup.appendChild(penToolBtn);
  toolGroup.appendChild(colorPicker);
  if (removeToolBtn) toolGroup.appendChild(removeToolBtn);

  const clearBtn = document.createElement('button');
  clearBtn.textContent = 'Clear';
  clearBtn.className = 'mask-editor-btn';

  const cancelBtn = document.createElement('button');
  cancelBtn.textContent = 'Cancel';
  cancelBtn.className = 'mask-editor-btn';

  const denoiseControl = document.createElement('div');
  denoiseControl.className = 'mask-editor-denoise';
  const denoiseLabel = document.createElement('label');
  denoiseLabel.textContent = 'Denoise: ';
  denoiseLabel.className = 'mask-editor-denoise-label';
  const denoiseSlider = document.createElement('input');
  denoiseSlider.type = 'range';
  denoiseSlider.min = '0.01';
  denoiseSlider.max = '1';
  denoiseSlider.step = '0.01';
  denoiseSlider.value = state.currentDenoise.inpaint.toFixed(2);
  denoiseSlider.className = 'mask-editor-denoise-slider';
  const denoiseValue = document.createElement('span');
  denoiseValue.className = 'mask-editor-denoise-value';
  denoiseValue.textContent = state.currentDenoise.inpaint.toFixed(2);
  denoiseSlider.addEventListener('input', () => {
    denoiseValue.textContent = parseFloat(denoiseSlider.value).toFixed(2);
    canvas.style.opacity = denoiseSlider.value;
  });
  denoiseControl.appendChild(denoiseLabel);
  denoiseControl.appendChild(denoiseSlider);
  denoiseControl.appendChild(denoiseValue);

  const applyBtn = document.createElement('button');
  applyBtn.textContent = 'Apply Inpaint';
  applyBtn.className = 'mask-editor-btn mask-editor-btn-primary';
  applyBtn.disabled = true;

  actions.appendChild(toolGroup);
  actions.appendChild(denoiseControl);
  actions.appendChild(clearBtn);
  actions.appendChild(cancelBtn);
  actions.appendChild(applyBtn);
  overlay.appendChild(actions);
  document.body.appendChild(overlay);

  const ctx = canvas.getContext('2d');
  const drawCtx = drawCanvas.getContext('2d');

  let cssW = 0, cssH = 0;

  function syncCanvasSize() {
    if (!img.naturalWidth) return;
    const rect = img.getBoundingClientRect();
    cssW = rect.width;
    cssH = rect.height;
    for (const c of [canvas, drawCanvas]) {
      c.width  = Math.round(rect.width  * dpr);
      c.height = Math.round(rect.height * dpr);
      c.style.width  = rect.width  + 'px';
      c.style.height = rect.height + 'px';
      c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    canvas.style.opacity = denoiseSlider.value;
    applyBtn.disabled = false;
  }

  if (img.complete && img.naturalWidth) {
    syncCanvasSize();
  } else {
    img.addEventListener('load', syncCanvasSize, { once: true });
  }

  let painting = false;
  let cachedRect = null;
  let lastX = null, lastY = null;
  let brushRadius = 30;

  let tool = 'mask';
  let penColor = colorPicker.value;

  const cursorEl = document.createElement('div');
  cursorEl.style.cssText = 'position:fixed;border-radius:50%;border:2px solid rgba(255,255,255,0.85);box-shadow:0 0 0 1px rgba(0,0,0,0.5);pointer-events:none;transform:translate(-50%,-50%);display:none;z-index:10001';
  overlay.appendChild(cursorEl);

  function updateCursorSize() {
    const cssR = brushRadius / dpr;
    cursorEl.style.width  = cssR * 2 + 'px';
    cursorEl.style.height = cssR * 2 + 'px';
  }
  updateCursorSize();
  canvas.style.cursor = 'none';

  function setRemovalMode(active) {
    removalMode = active;
    if (removeToolBtn) removeToolBtn.classList.toggle('is-active', active);
    applyBtn.textContent = active ? 'Apply Removal' : 'Apply Inpaint';
    promptRow.style.display = active ? 'none' : '';
    promptInput.classList.remove('mask-editor-prompt-invalid');
  }

  function setTool(name) {
    tool = name;
    maskToolBtn.classList.toggle('is-active', name === 'mask');
    penToolBtn.classList.toggle('is-active', name === 'pen');
    cursorEl.style.borderColor = name === 'pen' ? penColor : 'rgba(255,255,255,0.85)';
  }
  setTool('mask');

  penToolBtn.addEventListener('click', () => setTool('pen'));
  maskToolBtn.addEventListener('click', () => setTool('mask'));
  if (removeToolBtn) {
    removeToolBtn.addEventListener('click', () => setRemovalMode(!removalMode));
  }
  colorPicker.addEventListener('input', () => {
    penColor = colorPicker.value;
    setTool('pen');
  });

  const onResize = () => { cachedRect = null; };
  window.addEventListener('resize', onResize);

  // Sweep a thick round-capped line from the previous point for gap-free strokes.
  function stroke(targetCtx, color, x, y) {
    targetCtx.fillStyle = color;
    if (lastX !== null) {
      targetCtx.strokeStyle = color;
      targetCtx.lineWidth = brushRadius * 2;
      targetCtx.lineCap = 'round';
      targetCtx.lineJoin = 'round';
      targetCtx.beginPath();
      targetCtx.moveTo(lastX, lastY);
      targetCtx.lineTo(x, y);
      targetCtx.stroke();
    }
    targetCtx.beginPath();
    targetCtx.arc(x, y, brushRadius, 0, Math.PI * 2);
    targetCtx.fill();
  }

  function paint(e) {
    if (!painting || !cachedRect) return;
    const x = e.clientX - cachedRect.left;
    const y = e.clientY - cachedRect.top;
    if (tool === 'pen') {
      stroke(drawCtx, penColor, x, y);
    } else {
      stroke(ctx, 'rgba(255, 220, 0, 1.0)', x, y);
    }
    lastX = x; lastY = y;
  }

  function endStroke() { painting = false; lastX = lastY = null; }

  canvas.addEventListener('pointerdown', e => {
    cachedRect = canvas.getBoundingClientRect();
    painting = true;
    lastX = lastY = null;
    canvas.setPointerCapture(e.pointerId);
    paint(e);
  });
  canvas.addEventListener('pointermove', e => {
    cursorEl.style.left = e.clientX + 'px';
    cursorEl.style.top  = e.clientY + 'px';
    cursorEl.style.display = 'block';
    paint(e);
  });
  canvas.addEventListener('pointerenter', () => { cursorEl.style.display = 'block'; });
  canvas.addEventListener('pointerleave', () => { cursorEl.style.display = 'none'; });
  canvas.addEventListener('pointerup',     endStroke);
  canvas.addEventListener('pointercancel', endStroke);

  clearBtn.addEventListener('click', () => {
    (tool === 'pen' ? drawCtx : ctx).clearRect(0, 0, cssW, cssH);
  });

  function closeEditor() {
    aborted = true;
    window.removeEventListener('resize', onResize);
    document.removeEventListener('keydown', onKey);
    cursorEl.remove();
    overlay.remove();
  }

  cancelBtn.addEventListener('click', closeEditor);

  applyBtn.addEventListener('click', () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    if (!removalMode && !promptInput.value.trim()) {
      promptInput.focus();
      promptInput.classList.add('mask-editor-prompt-invalid');
      return;
    }
    applyBtn.disabled = true;
    applyBtn.textContent = 'Uploading…';

    const src = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const binary = new ImageData(canvas.width, canvas.height);
    for (let i = 0; i < src.data.length; i += 4) {
      const v = src.data[i + 3] > 0 ? 255 : 0;
      binary.data[i] = binary.data[i + 1] = binary.data[i + 2] = v;
      binary.data[i + 3] = 255;
    }
    const binaryCanvas = document.createElement('canvas');
    binaryCanvas.width  = canvas.width;
    binaryCanvas.height = canvas.height;
    binaryCanvas.getContext('2d').putImageData(binary, 0, 0);

    const offscreen = document.createElement('canvas');
    offscreen.width  = img.naturalWidth;
    offscreen.height = img.naturalHeight;
    const offCtx = offscreen.getContext('2d');
    // Nearest-neighbour scaling keeps the mask strictly black/white.
    offCtx.imageSmoothingEnabled = false;
    offCtx.drawImage(binaryCanvas, 0, 0, offscreen.width, offscreen.height);

    const b64 = offscreen.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');

    const drawn = drawCtx.getImageData(0, 0, drawCanvas.width, drawCanvas.height).data;
    let hasDrawing = false;
    for (let i = 3; i < drawn.length; i += 4) {
      if (drawn[i] > 0) { hasDrawing = true; break; }
    }

    function uploadDrawing() {
      if (!hasDrawing) return Promise.resolve(null);
      const comp = document.createElement('canvas');
      comp.width  = img.naturalWidth;
      comp.height = img.naturalHeight;
      const compCtx = comp.getContext('2d');
      compCtx.imageSmoothingEnabled = true;
      compCtx.drawImage(img, 0, 0, comp.width, comp.height);
      compCtx.drawImage(drawCanvas, 0, 0, comp.width, comp.height);
      const drawB64 = comp.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');
      return fetch('/api/upload-inpaint-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: drawB64 }),
      })
        .then(r => r.json())
        .then(d => { if (d.error) throw new Error(d.error); return d.token; });
    }

    Promise.all([
      fetch('/api/upload-mask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: b64 }),
      }).then(r => r.json()).then(d => { if (d.error) throw new Error(d.error); return d.token; }),
      uploadDrawing(),
    ])
    .then(([maskToken, drawToken]) => {
      if (aborted) return;
      const capturedDenoise = parseFloat(denoiseSlider.value);
      const finalPrompt = promptInput.value.trim();
      const isRemoval = removalMode;
      closeEditor();
      if (isRemoval) {
        addMessage('user', finalPrompt ? `Remove object (hint: ${escapeHtml(finalPrompt)})` : 'Remove object');
        onRemove(imageUrl, maskToken, imgWrap, finalPrompt, capturedDenoise, b64, drawToken);
      } else {
        state.lastInpaintingPrompt = finalPrompt || null;
        addMessage('user', `Inpaint: ${escapeHtml(finalPrompt)}`);
        onInpaint(imageUrl, maskToken, imgWrap, finalPrompt, capturedDenoise, b64, drawToken);
      }
    })
    .catch(err => {
      if (aborted) return;
      applyBtn.disabled = false;
      applyBtn.textContent = removalMode ? 'Apply Removal' : 'Apply Inpaint';
      addMessage('bot', `<span style="color:#f87171">⚠ Mask upload failed: ${escapeHtml(err.message)}</span>`);
    });
  });

  function onKey(e) {
    if (e.target === promptInput) {
      if (e.key === 'Escape') promptInput.blur();
      return;
    }
    if (e.key === 'Escape') { closeEditor(); return; }
    if (e.key === '[' || e.key === ']') {
      e.preventDefault();
      brushRadius = Math.max(5, Math.min(150, brushRadius + (e.key === ']' ? 5 : -5)));
      updateCursorSize();
    }
  }
  document.addEventListener('keydown', onKey);
}

// Opens a mask editor over `newUrl` that lets the user paint which parts of
// image 2 to keep. On "Apply", composites image 1 and image 2 client-side,
// uploads the result via /api/save-image, and calls onComposite(compositeUrl).
export function openCompositeEditor(oldUrl, newUrl, onComposite) {
  if (document.getElementById('mask-editor-overlay')) return;

  let aborted = false;
  const dpr = window.devicePixelRatio || 1;

  const overlay = document.createElement('div');
  overlay.id = 'mask-editor-overlay';

  const wrap = document.createElement('div');
  wrap.id = 'mask-editor-wrap';

  const img = document.createElement('img');
  img.src = newUrl;
  img.draggable = false;

  const canvas = document.createElement('canvas');
  canvas.id = 'mask-editor-canvas';

  wrap.appendChild(img);
  wrap.appendChild(canvas);
  overlay.appendChild(wrap);

  const actions = document.createElement('div');
  actions.id = 'mask-editor-actions';

  const hint = document.createElement('div');
  hint.style.cssText = 'color:#94a3b8;font-size:0.8rem;white-space:normal;flex:1 1 100%;order:-1;margin-bottom:2px';
  hint.textContent = 'Paint over the face(s) from ② to keep — unpainted areas will use ①';

  const clearBtn = document.createElement('button');
  clearBtn.textContent = 'Clear';
  clearBtn.className = 'mask-editor-btn';

  const cancelBtn = document.createElement('button');
  cancelBtn.textContent = 'Cancel';
  cancelBtn.className = 'mask-editor-btn';

  const applyBtn = document.createElement('button');
  applyBtn.textContent = 'Apply Composite';
  applyBtn.className = 'mask-editor-btn mask-editor-btn-primary';
  applyBtn.disabled = true;

  actions.appendChild(hint);
  actions.appendChild(clearBtn);
  actions.appendChild(cancelBtn);
  actions.appendChild(applyBtn);
  overlay.appendChild(actions);
  document.body.appendChild(overlay);

  const ctx = canvas.getContext('2d');
  let cssW = 0, cssH = 0;

  function syncCanvasSize() {
    if (!img.naturalWidth) return;
    const rect = img.getBoundingClientRect();
    cssW = rect.width;
    cssH = rect.height;
    canvas.width  = Math.round(rect.width  * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas.style.width  = rect.width  + 'px';
    canvas.style.height = rect.height + 'px';
    canvas.style.opacity = '0.6';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    applyBtn.disabled = false;
  }

  if (img.complete && img.naturalWidth) {
    syncCanvasSize();
  } else {
    img.addEventListener('load', syncCanvasSize, { once: true });
  }

  let painting = false;
  let cachedRect = null;
  let lastX = null, lastY = null;
  let brushRadius = 30;

  const cursorEl = document.createElement('div');
  cursorEl.style.cssText = 'position:fixed;border-radius:50%;border:2px solid rgba(255,255,255,0.85);box-shadow:0 0 0 1px rgba(0,0,0,0.5);pointer-events:none;transform:translate(-50%,-50%);display:none;z-index:10001';
  overlay.appendChild(cursorEl);

  function updateCursorSize() {
    const cssR = brushRadius / dpr;
    cursorEl.style.width  = cssR * 2 + 'px';
    cursorEl.style.height = cssR * 2 + 'px';
  }
  updateCursorSize();
  canvas.style.cursor = 'none';

  const onResize = () => { cachedRect = null; };
  window.addEventListener('resize', onResize);

  function paint(e) {
    if (!painting || !cachedRect) return;
    const x = e.clientX - cachedRect.left;
    const y = e.clientY - cachedRect.top;
    ctx.fillStyle = 'rgba(255, 220, 0, 1.0)';
    if (lastX !== null) {
      ctx.strokeStyle = 'rgba(255, 220, 0, 1.0)';
      ctx.lineWidth = brushRadius * 2;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(lastX, lastY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(x, y, brushRadius, 0, Math.PI * 2);
    ctx.fill();
    lastX = x; lastY = y;
  }

  function endStroke() { painting = false; lastX = lastY = null; }

  canvas.addEventListener('pointerdown', e => {
    cachedRect = canvas.getBoundingClientRect();
    painting = true;
    lastX = lastY = null;
    canvas.setPointerCapture(e.pointerId);
    paint(e);
  });
  canvas.addEventListener('pointermove', e => {
    cursorEl.style.left = e.clientX + 'px';
    cursorEl.style.top  = e.clientY + 'px';
    cursorEl.style.display = 'block';
    paint(e);
  });
  canvas.addEventListener('pointerenter', () => { cursorEl.style.display = 'block'; });
  canvas.addEventListener('pointerleave', () => { cursorEl.style.display = 'none'; });
  canvas.addEventListener('pointerup',     endStroke);
  canvas.addEventListener('pointercancel', endStroke);

  clearBtn.addEventListener('click', () => ctx.clearRect(0, 0, cssW, cssH));

  function closeEditor() {
    aborted = true;
    window.removeEventListener('resize', onResize);
    document.removeEventListener('keydown', onKey);
    cursorEl.remove();
    overlay.remove();
  }

  cancelBtn.addEventListener('click', closeEditor);

  applyBtn.addEventListener('click', () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    applyBtn.disabled = true;
    applyBtn.textContent = 'Compositing…';

    const src = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const binary = new ImageData(canvas.width, canvas.height);
    for (let i = 0; i < src.data.length; i += 4) {
      const v = src.data[i + 3] > 0 ? 255 : 0;
      binary.data[i] = binary.data[i + 1] = binary.data[i + 2] = v;
      binary.data[i + 3] = 255;
    }
    const binaryCanvas = document.createElement('canvas');
    binaryCanvas.width  = canvas.width;
    binaryCanvas.height = canvas.height;
    binaryCanvas.getContext('2d').putImageData(binary, 0, 0);

    const natW = img.naturalWidth;
    const natH = img.naturalHeight;

    const maskCanvas = document.createElement('canvas');
    maskCanvas.width  = natW;
    maskCanvas.height = natH;
    const maskCtx = maskCanvas.getContext('2d');
    maskCtx.imageSmoothingEnabled = false;
    maskCtx.drawImage(binaryCanvas, 0, 0, natW, natH);
    const maskData = maskCtx.getImageData(0, 0, natW, natH);

    const img2Canvas = document.createElement('canvas');
    img2Canvas.width  = natW;
    img2Canvas.height = natH;
    const img2Ctx = img2Canvas.getContext('2d');
    img2Ctx.drawImage(img, 0, 0, natW, natH);
    const img2Data = img2Ctx.getImageData(0, 0, natW, natH);

    const img1El = new Image();
    img1El.onload = () => {
      const img1Canvas = document.createElement('canvas');
      img1Canvas.width  = natW;
      img1Canvas.height = natH;
      const img1Ctx = img1Canvas.getContext('2d');
      img1Ctx.drawImage(img1El, 0, 0, natW, natH);
      const img1Data = img1Ctx.getImageData(0, 0, natW, natH);

      for (let i = 0; i < img1Data.data.length; i += 4) {
        if (maskData.data[i] > 128) {
          img1Data.data[i]     = img2Data.data[i];
          img1Data.data[i + 1] = img2Data.data[i + 1];
          img1Data.data[i + 2] = img2Data.data[i + 2];
          img1Data.data[i + 3] = img2Data.data[i + 3];
        }
      }
      img1Ctx.putImageData(img1Data, 0, 0);

      const b64 = img1Canvas.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');

      fetch('/api/save-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: b64 }),
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) throw new Error(data.error);
        if (aborted) return;
        closeEditor();
        onComposite(data.url);
      })
      .catch(err => {
        if (aborted) return;
        applyBtn.disabled = false;
        applyBtn.textContent = 'Apply Composite';
        addMessage('bot', `<span style="color:#f87171">⚠ Composite failed: ${escapeHtml(err.message)}</span>`);
      });
    };
    img1El.onerror = () => {
      if (aborted) return;
      applyBtn.disabled = false;
      applyBtn.textContent = 'Apply Composite';
      addMessage('bot', '<span style="color:#f87171">⚠ Failed to load original image for compositing.</span>');
    };
    img1El.src = oldUrl;
  });

  function onKey(e) {
    if (e.key === 'Escape') { closeEditor(); return; }
    if (e.key === '[' || e.key === ']') {
      e.preventDefault();
      brushRadius = Math.max(5, Math.min(150, brushRadius + (e.key === ']' ? 5 : -5)));
      updateCursorSize();
    }
  }
  document.addEventListener('keydown', onKey);
}

// Builds a before/after comparison slider for a face-detail or upscale result.
// `oldUrl` shows on the left, `newUrl` on the right; dragging the handle wipes
// between them. onAccept/onReject handle the ✓/✗ buttons. The optional
// onComposite enables a 🩹 button for selective compositing.
export function buildComparisonSlider(oldUrl, newUrl, onAccept, onReject, onComposite) {
  const container = document.createElement('div');
  container.className = 'ba-container';

  const slider = document.createElement('div');
  slider.className = 'ba-slider';

  const before = document.createElement('img');
  before.className = 'ba-before';
  before.src = oldUrl;
  before.alt = 'Original image';

  const after = document.createElement('img');
  after.className = 'ba-after';
  after.src = newUrl;
  after.alt = 'Processed image';

  const handle = document.createElement('div');
  handle.className = 'ba-handle';

  const setPos = pct => {
    pct = Math.max(0, Math.min(100, pct));
    after.style.clipPath = `inset(0 0 0 ${pct}%)`;
    handle.style.left = pct + '%';
  };
  setPos(50);

  const posFromEvent = e => {
    const rect = slider.getBoundingClientRect();
    return ((e.clientX - rect.left) / rect.width) * 100;
  };
  let dragging = false;
  slider.addEventListener('pointerdown', e => {
    dragging = true;
    slider.setPointerCapture(e.pointerId);
    setPos(posFromEvent(e));
  });
  slider.addEventListener('pointermove', e => {
    if (dragging) setPos(posFromEvent(e));
  });
  const endDrag = e => {
    if (!dragging) return;
    dragging = false;
    try { slider.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  slider.addEventListener('pointerup', endDrag);
  slider.addEventListener('pointercancel', endDrag);

  const label1 = document.createElement('div');
  label1.className = 'ba-label ba-label-1';
  label1.textContent = '1';
  const label2 = document.createElement('div');
  label2.className = 'ba-label ba-label-2';
  label2.textContent = '2';

  slider.appendChild(before);
  slider.appendChild(after);
  slider.appendChild(handle);
  slider.appendChild(label1);
  slider.appendChild(label2);

  const actions = document.createElement('div');
  actions.className = 'ba-actions';

  const pick1 = document.createElement('button');
  pick1.className = 'ba-pick ba-pick-1';
  pick1.title = 'Keep image 1 (original)';
  pick1.textContent = '1';

  const pick2 = document.createElement('button');
  pick2.className = 'ba-pick ba-pick-2';
  pick2.title = 'Keep image 2 (edited)';
  pick2.textContent = '2';

  let settled = false;
  pick1.addEventListener('click', () => {
    if (settled) return;
    settled = true;
    pick1.disabled = pick2.disabled = true;
    onReject(container);
  });
  pick2.addEventListener('click', () => {
    if (settled) return;
    settled = true;
    pick1.disabled = pick2.disabled = true;
    onAccept(container);
  });

  const maximizeBtn = document.createElement('button');
  maximizeBtn.className = 'ba-maximize-btn';
  maximizeBtn.title = 'Maximise comparison';
  maximizeBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>`;

  maximizeBtn.addEventListener('click', () => {
    const overlay = document.createElement('div');
    overlay.className = 'ba-overlay';

    const modalSlider = document.createElement('div');
    modalSlider.className = 'ba-slider';

    const mBefore = document.createElement('img');
    mBefore.className = 'ba-before';
    mBefore.src = oldUrl;
    mBefore.alt = 'Original image';

    const mAfter = document.createElement('img');
    mAfter.className = 'ba-after';
    mAfter.src = newUrl;
    mAfter.alt = 'Processed image';

    const mHandle = document.createElement('div');
    mHandle.className = 'ba-handle';

    const mSetPos = pct => {
      pct = Math.max(0, Math.min(100, pct));
      mAfter.style.clipPath = `inset(0 0 0 ${pct}%)`;
      mHandle.style.left = pct + '%';
    };
    mSetPos(50);

    let mDragging = false;
    modalSlider.addEventListener('pointerdown', e => {
      mDragging = true;
      modalSlider.setPointerCapture(e.pointerId);
      const rect = modalSlider.getBoundingClientRect();
      mSetPos(((e.clientX - rect.left) / rect.width) * 100);
    });
    modalSlider.addEventListener('pointermove', e => {
      if (!mDragging) return;
      const rect = modalSlider.getBoundingClientRect();
      mSetPos(((e.clientX - rect.left) / rect.width) * 100);
    });
    const mEndDrag = e => {
      if (!mDragging) return;
      mDragging = false;
      try { modalSlider.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    modalSlider.addEventListener('pointerup', mEndDrag);
    modalSlider.addEventListener('pointercancel', mEndDrag);

    const mLabel1 = document.createElement('div');
    mLabel1.className = 'ba-label ba-label-1';
    mLabel1.textContent = '1';
    const mLabel2 = document.createElement('div');
    mLabel2.className = 'ba-label ba-label-2';
    mLabel2.textContent = '2';

    modalSlider.append(mBefore, mAfter, mHandle, mLabel1, mLabel2);

    const aspect = (before.naturalWidth || 1) / (before.naturalHeight || 1);
    const maxW = window.innerWidth - 32;
    const maxH = window.innerHeight - 120;
    modalSlider.style.width = Math.min(maxW, maxH * aspect) + 'px';

    const header = document.createElement('div');
    header.className = 'ba-overlay-header';

    const mPick1 = document.createElement('button');
    mPick1.className = 'ba-pick ba-pick-1';
    mPick1.textContent = '1';
    mPick1.title = 'Keep image 1 (original)';

    const mPick2 = document.createElement('button');
    mPick2.className = 'ba-pick ba-pick-2';
    mPick2.textContent = '2';
    mPick2.title = 'Keep image 2 (edited)';

    const closeBtn = document.createElement('button');
    closeBtn.className = 'ba-overlay-close';
    closeBtn.textContent = '×';
    closeBtn.title = 'Close';

    if (settled) { mPick1.disabled = mPick2.disabled = true; }

    const dismiss = () => overlay.remove();
    closeBtn.addEventListener('click', dismiss);
    mPick1.addEventListener('click', () => { dismiss(); pick1.click(); });
    mPick2.addEventListener('click', () => { dismiss(); pick2.click(); });

    header.append(mPick1, mPick2);

    if (onComposite) {
      const mMaskBtn = document.createElement('button');
      mMaskBtn.className = 'ba-composite-btn';
      mMaskBtn.textContent = '🩹';
      mMaskBtn.title = 'Selective composite — paint which parts of ② to keep';
      if (settled) mMaskBtn.disabled = true;
      mMaskBtn.addEventListener('click', () => {
        dismiss();
        if (settled) return;
        openCompositeEditor(oldUrl, newUrl, compositeUrl => {
          if (settled) return;
          settled = true;
          pick1.disabled = pick2.disabled = true;
          onComposite(compositeUrl, container);
        });
      });
      header.append(mMaskBtn);
    }

    header.append(closeBtn);
    overlay.append(header, modalSlider);
    document.body.appendChild(overlay);

    const onKey = e => {
      if (e.key === 'Escape') { dismiss(); document.removeEventListener('keydown', onKey); }
    };
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', e => { if (e.target === overlay) dismiss(); });
  });

  actions.appendChild(pick1);
  actions.appendChild(pick2);

  if (onComposite) {
    const maskBtn = document.createElement('button');
    maskBtn.className = 'ba-composite-btn';
    maskBtn.textContent = '🩹';
    maskBtn.title = 'Selective composite — paint which parts of ② to keep';
    maskBtn.addEventListener('click', () => {
      if (settled) return;
      openCompositeEditor(oldUrl, newUrl, compositeUrl => {
        if (settled) return;
        settled = true;
        pick1.disabled = pick2.disabled = maskBtn.disabled = true;
        onComposite(compositeUrl, container);
      });
    });
    actions.appendChild(maskBtn);
  }

  actions.appendChild(maximizeBtn);

  container.appendChild(slider);
  container.appendChild(actions);
  return container;
}
