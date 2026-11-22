// Multi-face face-detail editor (👥, reached from the 👤 Face detail dialog).
//
// The user paints up to four faces, each on its own coloured mask layer, and
// gives each a prompt. On Apply every painted face's mask is binarised at the
// image's natural size and uploaded (/api/upload-mask); `onApply` then runs the
// server job, which crops a square around each mask, face-details it and
// pastes it back. See ADR/multi-face-detail.md.
import { escapeHtml, FACE_MULTI_SLOTS, FACE_MULTI_COLOURS, FACE_MULTI_MAX_MARGIN,
         faceMultiPayload, clampFaceMultiMargin } from './utils.js';
import { state } from './state.js';
import { addMessage } from './dom.js';
import { sizeCanvasesToImage, brushStroke, createBrushCursor, binaryMaskCanvas,
         canvasPngB64, canvasHasPaint, uploadMask } from './editors.js';

// opts: { defaultPrompt, denoise, onApply({faces, margin, denoise}), onClose(), onSettingsChange() }
export function openMultiFaceEditor(imageUrl, { defaultPrompt, denoise, onApply, onClose, onSettingsChange }) {
  if (document.getElementById('mask-editor-overlay')) { if (onClose) onClose(); return; }

  const dpr = window.devicePixelRatio || 1;
  const changed = () => { if (onSettingsChange) onSettingsChange(); };
  let aborted = false;
  let applied = false;
  let active = 0;
  // Working prompts: the remembered one per slot, else the dialog's prompt.
  const prompts = state.faceMultiPrompts.map(p => p || defaultPrompt || '');

  const overlay = document.createElement('div');
  overlay.id = 'mask-editor-overlay';
  overlay.className = 'face-multi-editor';

  const wrap = document.createElement('div');
  wrap.id = 'mask-editor-wrap';
  const img = document.createElement('img');
  img.src = imageUrl;
  img.draggable = false;
  wrap.appendChild(img);

  // One canvas per face, each painted in its own colour; a transparent input
  // canvas on top takes the pointer and paints into the active face's layer.
  const layers = FACE_MULTI_COLOURS.map(() => {
    const c = document.createElement('canvas');
    c.className = 'face-multi-layer';
    wrap.appendChild(c);
    return c;
  });
  const input = document.createElement('canvas');
  input.id = 'mask-editor-canvas';
  wrap.appendChild(input);
  overlay.appendChild(wrap);

  // Prompt row: a coloured face tag + one textarea showing the active face.
  const promptRow = document.createElement('div');
  promptRow.id = 'mask-editor-prompt-row';
  const promptTag = document.createElement('span');
  promptTag.className = 'face-multi-tag';
  const promptInput = document.createElement('input');
  promptInput.type = 'text';
  promptInput.id = 'mask-editor-prompt';
  promptInput.placeholder = 'Prompt for this face (include its <lora:…>)…';
  promptRow.append(promptTag, promptInput);
  overlay.appendChild(promptRow);

  const actions = document.createElement('div');
  actions.id = 'mask-editor-actions';

  const faceGroup = document.createElement('div');
  faceGroup.className = 'mask-editor-tools';
  const faceBtns = FACE_MULTI_COLOURS.map((colour, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'mask-editor-tool face-multi-btn';
    b.textContent = String(i + 1);
    b.title = `Face ${i + 1} (key ${i + 1})`;
    b.style.setProperty('--face-colour', colour);
    b.addEventListener('click', () => setActive(i));
    faceGroup.appendChild(b);
    return b;
  });

  function slider(labelText, min, max, step, value, fmt) {
    const box = document.createElement('div');
    box.className = 'mask-editor-denoise';
    const label = document.createElement('label');
    label.className = 'mask-editor-denoise-label';
    label.textContent = labelText;
    const el = document.createElement('input');
    el.type = 'range';
    el.className = 'mask-editor-denoise-slider';
    el.min = String(min); el.max = String(max); el.step = String(step);
    el.value = String(value);
    const out = document.createElement('span');
    out.className = 'mask-editor-denoise-value';
    out.textContent = fmt(value);
    el.addEventListener('input', () => { out.textContent = fmt(parseFloat(el.value)); });
    box.append(label, el, out);
    return { box, el };
  }
  const margin = slider('Margin: ', 0, FACE_MULTI_MAX_MARGIN, 1,
                        clampFaceMultiMargin(state.faceMultiMargin), v => `${Math.round(v)}px`);
  margin.box.title = 'Extra context around each face in the square sent to the face-detailer';
  margin.el.addEventListener('change', () => {
    state.faceMultiMargin = clampFaceMultiMargin(margin.el.value);
    changed();
  });
  const den = slider('Denoise: ', 0.01, 1, 0.01, denoise, v => v.toFixed(2));

  const mkBtn = (text, cls = '') => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    b.className = 'mask-editor-btn' + (cls ? ' ' + cls : '');
    return b;
  };
  const clearBtn = mkBtn('Clear face');
  clearBtn.title = 'Wipe the active face’s mask';
  const resetBtn = mkBtn('Reset');
  resetBtn.title = 'Wipe every mask and reset all four prompts';
  const cancelBtn = mkBtn('Cancel');
  const applyBtn = mkBtn('Apply Face Detail', 'mask-editor-btn-primary');
  applyBtn.disabled = true;

  actions.append(faceGroup, margin.box, den.box, clearBtn, resetBtn, cancelBtn, applyBtn);
  overlay.appendChild(actions);
  document.body.appendChild(overlay);

  let cssW = 0, cssH = 0;
  function syncCanvasSize() {
    if (!img.naturalWidth) return;
    ({ cssW, cssH } = sizeCanvasesToImage(img, [...layers, input], dpr));
    applyBtn.disabled = false;
  }
  if (img.complete && img.naturalWidth) syncCanvasSize();
  else img.addEventListener('load', syncCanvasSize, { once: true });

  let brushRadius = 30;
  const cursor = createBrushCursor(overlay);
  cursor.setRadius(brushRadius);
  input.style.cursor = 'none';

  function setActive(i) {
    active = i;
    faceBtns.forEach((b, j) => b.classList.toggle('is-active', j === i));
    const colour = FACE_MULTI_COLOURS[i];
    promptTag.textContent = `Face ${i + 1}`;
    promptTag.style.background = colour;
    promptInput.style.borderColor = colour;
    promptInput.value = prompts[i];
    promptInput.classList.remove('mask-editor-prompt-invalid');
    cursor.el.style.borderColor = colour;
  }

  function markPainted() {
    faceBtns.forEach((b, i) => b.classList.toggle('has-mask', canvasHasPaint(layers[i])));
  }

  promptInput.addEventListener('input', () => {
    prompts[active] = promptInput.value;
    promptInput.classList.remove('mask-editor-prompt-invalid');
    faceBtns[active].classList.remove('is-invalid');
    state.faceMultiPrompts[active] = promptInput.value.trim() || null;
    changed();
  });

  let painting = false;
  let cachedRect = null;
  let last = null;
  const onResize = () => { cachedRect = null; };
  window.addEventListener('resize', onResize);

  function paint(e) {
    if (!painting || !cachedRect) return;
    const x = e.clientX - cachedRect.left;
    const y = e.clientY - cachedRect.top;
    brushStroke(layers[active].getContext('2d'), FACE_MULTI_COLOURS[active], brushRadius, last, x, y);
    last = { x, y };
  }
  function endStroke() {
    if (painting) markPainted();
    painting = false;
    last = null;
  }
  input.addEventListener('pointerdown', e => {
    cachedRect = input.getBoundingClientRect();
    painting = true;
    last = null;
    input.setPointerCapture(e.pointerId);
    paint(e);
  });
  input.addEventListener('pointermove', e => { cursor.moveTo(e.clientX, e.clientY); paint(e); });
  input.addEventListener('pointerenter', () => cursor.show(true));
  input.addEventListener('pointerleave', () => cursor.show(false));
  input.addEventListener('pointerup', endStroke);
  input.addEventListener('pointercancel', endStroke);

  clearBtn.addEventListener('click', () => {
    layers[active].getContext('2d').clearRect(0, 0, cssW, cssH);
    markPainted();
  });
  resetBtn.addEventListener('click', () => {
    for (const c of layers) c.getContext('2d').clearRect(0, 0, cssW, cssH);
    for (let i = 0; i < FACE_MULTI_SLOTS; i++) prompts[i] = defaultPrompt || '';
    state.faceMultiPrompts = state.faceMultiPrompts.map(() => null);
    faceBtns.forEach(b => b.classList.remove('is-invalid'));
    markPainted();
    setActive(0);
    changed();
  });

  function closeEditor() {
    aborted = true;
    window.removeEventListener('resize', onResize);
    document.removeEventListener('keydown', onKey);
    cursor.remove();
    overlay.remove();
    if (!applied && onClose) onClose();
  }
  cancelBtn.addEventListener('click', closeEditor);

  applyBtn.addEventListener('click', () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    const check = faceMultiPayload(layers.map((c, i) => ({ painted: canvasHasPaint(c), prompt: prompts[i] })));
    faceBtns.forEach((b, i) => b.classList.toggle('is-invalid', check.missing.includes(i)));
    if (check.error) {
      if (check.missing.length) {
        setActive(check.missing[0]);
        promptInput.classList.add('mask-editor-prompt-invalid');
        promptInput.focus();
      }
      applyBtn.textContent = check.error;
      setTimeout(() => { if (!aborted) applyBtn.textContent = 'Apply Face Detail'; }, 2000);
      return;
    }
    applyBtn.disabled = true;
    applyBtn.textContent = 'Uploading…';
    const natW = img.naturalWidth, natH = img.naturalHeight;
    Promise.all(check.slots.map(i => uploadMask(canvasPngB64(binaryMaskCanvas(layers[i], natW, natH)))))
      .then(tokens => {
        if (aborted) return;
        const faces = check.slots.map((slot, k) => ({ mask: tokens[k], prompt: prompts[slot].trim() }));
        const payload = { faces, margin: clampFaceMultiMargin(margin.el.value), denoise: parseFloat(den.el.value) };
        state.faceMultiMargin = payload.margin;
        changed();
        applied = true;
        closeEditor();
        Promise.resolve(onApply(payload)).finally(() => { if (onClose) onClose(); });
      })
      .catch(err => {
        if (aborted) return;
        applyBtn.disabled = false;
        applyBtn.textContent = 'Apply Face Detail';
        addMessage('bot', `<span style="color:var(--danger)">⚠ Mask upload failed: ${escapeHtml(err.message)}</span>`);
      });
  });

  function onKey(e) {
    if (e.target === promptInput) {
      if (e.key === 'Escape') promptInput.blur();
      return;
    }
    if (e.key === 'Escape') { closeEditor(); return; }
    if (e.key >= '1' && e.key <= String(FACE_MULTI_SLOTS)) {
      e.preventDefault();
      setActive(Number(e.key) - 1);
      return;
    }
    if (e.key === '[' || e.key === ']') {
      e.preventDefault();
      brushRadius = Math.max(5, Math.min(150, brushRadius + (e.key === ']' ? 5 : -5)));
      cursor.setRadius(brushRadius);
    }
  }
  document.addEventListener('keydown', onKey);

  setActive(0);
}
