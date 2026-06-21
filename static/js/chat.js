import { escapeHtml, fuzzyScore, parseJsonResponse, expandAliases, applyReplacements, deriveFaceDetailPrompt, isVideoUrl,
         DEFAULT_VIDEO_SETTINGS, VIDEO_LIMITS, fmtDuration, clampVideo, recomputeVideo, buildVideoPrompt, i2vTooltip, reorderList } from './utils.js';

// Builds the DOM element for a generated result: a <video> for video outputs,
// otherwise an <img>. Both carry alt text and the same class so existing CSS
// applies. Videos get inline controls + loop; `autoplay` (muted) is used for a
// single inline result but skipped in grids to avoid many simultaneous plays.
function createMediaElement(url, { autoplay = false } = {}) {
  if (isVideoUrl(url)) {
    const video = document.createElement('video');
    video.src = url;
    video.controls = true;
    video.loop = true;
    video.playsInline = true;
    if (autoplay) { video.muted = true; video.autoplay = true; }
    return video;
  }
  const img = document.createElement('img');
  img.src = url;
  img.alt = 'Generated image';
  return img;
}

const messagesEl = document.getElementById('messages');
const inputEl    = document.getElementById('prompt-input');
const sendBtn    = document.getElementById('send-btn');
const lightbox   = document.getElementById('lightbox');
const lbImg      = document.getElementById('lightbox-img');

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
function openLightbox(src) {
  lbReset();
  lbImg.src = src;
  lightbox.classList.add('open');
  requestAnimationFrame(() => {
    const r = lbImg.getBoundingClientRect();
    lbNatLeft = r.left; lbNatTop = r.top;
  });
}
function closeLightbox() { lightbox.classList.remove('open'); lbReset(); }

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
    // finger count dropped from 2→1: transition pinch into pan
    const t = e.touches[0];
    lbPanStart = { x: t.clientX, y: t.clientY, tx: lbTx, ty: lbTy };
  } else if (e.touches.length === 0) {
    const dy = lbDragY;
    lbPanStart = null;
    if (lbScale < 1.05 && Math.abs(dy) > 80) {
      closeLightbox();
    } else if (lbScale < 1.05 && dy !== 0) {
      // Snap back with animation
      lbImg.style.transition = 'transform 0.2s ease-out';
      lightbox.style.transition = 'background 0.2s ease-out';
      lbReset();
      setTimeout(() => { lbImg.style.transition = ''; lightbox.style.transition = ''; }, 220);
    } else if (lbScale < 1.05) {
      lbReset();
    }
  }
});
const slashAcEl  = document.getElementById('slash-ac');

// Tracks which slideshow elements are currently in faux-fullscreen so that
// body overflow is only unlocked when the last one exits.
const fauxFullscreenEls = new Set();
function enterFauxFs(el) {
  fauxFullscreenEls.add(el);
  document.body.style.overflow = 'hidden';
}
function exitFauxFs(el) {
  fauxFullscreenEls.delete(el);
  if (fauxFullscreenEls.size === 0) document.body.style.overflow = '';
}

// ---------------------------------------------------------------------------
// Slash-command autocomplete
// ---------------------------------------------------------------------------

const SLASH_COMMANDS = [
  { cmd: '/addserver',  desc: 'add a server  (name host:port:os)',  args: ' ' },
  { cmd: '/alias-create', desc: 'create or update a prompt text alias  (<from> <to>)', args: ' ' },
  { cmd: '/alias-list',   desc: 'list all defined prompt text aliases',                args: ''  },
  { cmd: '/archive-all',     desc: 'archive every image and video to the encrypted volume (optional folder name)', args: ' ' },
  { cmd: '/archive-session', desc: 'archive all images and videos from this session (optional folder name)',       args: ' ' },
  { cmd: '/archive-today',   desc: 'archive images and videos generated today (optional folder name)',             args: ' ' },
  { cmd: '/clear',       desc: 'clear visible chat (keeps settings, prompt history & session images)',  args: ''  },
  { cmd: '/session-load',    desc: 'load a previously saved session',                args: ''  },
  { cmd: '/session-new',    desc: 'start a new session (resets all settings)',       args: '' },
  { cmd: '/session-save',   desc: 'save the current session (no name: pick one to overwrite)', args: ' ' },
  { cmd: '/session-summary', desc: 'show active settings (workflow, replacements, etc.)', args: '' },
  { cmd: '/delete',         desc: 'delete the last generated image',              args: '' },
  { cmd: '/delete-all',     desc: 'delete every image in the output folder',       args: '' },
  { cmd: '/delete-session', desc: 'delete all images from this session',           args: '' },
  { cmd: '/delete-today',   desc: 'delete every image generated today',            args: '' },
  { cmd: '/face-detail',       desc: 'face-detail the last N images (default 1)', args: ' ' },
  { cmd: '/face-detail-prompt', desc: 'set the prompt the face-detail icons use', args: ' ' },
  { cmd: '/face-detail-prompt-reset', desc: 'clear the override; derive prompts again', args: '' },
  { cmd: '/face-detail-session', desc: 'face-detail every image from this session', args: '' },
  { cmd: '/face-detail-workflow', desc: 'choose a face-detailer workflow',       args: ''  },
  { cmd: '/help',       desc: 'show available commands',            args: ''  },
  { cmd: '/image2image', desc: 'image2image the last N images (default 1)', args: ' ' },
  { cmd: '/image2image-replacement', desc: 'add a find→replace for prompt-less /image2image', args: ' ' },
  { cmd: '/image2image-replacement-reset', desc: 'clear all image2image replacements', args: '' },
  { cmd: '/image2image-set-prompt', desc: 'set an override prompt for prompt-less /image2image', args: ' ' },
  { cmd: '/image2image-set-prompt-reset', desc: 'clear the image2image override prompt', args: '' },
  { cmd: '/image2image-workflow', desc: 'choose an image2image workflow',     args: ''  },
  { cmd: '/image2video', desc: 'image2video the last N images (default 1)', args: ' ' },
  { cmd: '/image2video-replacement', desc: 'add a find→replace for prompt-less /image2video', args: ' ' },
  { cmd: '/image2video-replacement-reset', desc: 'clear all image2video replacements', args: '' },
  { cmd: '/image2video-set-prompt', desc: 'set an override prompt for prompt-less /image2video', args: ' ' },
  { cmd: '/image2video-set-prompt-reset', desc: 'clear the image2video override prompt', args: '' },
  { cmd: '/image2video-workflow', desc: 'choose an image2video workflow',     args: ''  },
  { cmd: '/inpaint-workflow',  desc: 'choose an inpainting workflow',        args: ''  },
  { cmd: '/inpainting-prompt', desc: 'set the prompt used by the inpaint button', args: ' ' },
  { cmd: '/denoise', desc: 'set denoise defaults for face-detail, image2image, inpainting, upscale', args: '' },
  { cmd: '/generation-steps', desc: 'override steps for generation workflows (e.g. 20)', args: ' ' },
  { cmd: '/iterations', desc: 'set images generated per prompt',    args: ' ' },
  { cmd: '/composite-videos-session', desc: 'drag to reorder this session\'s videos, then ✓ to join them into one', args: '' },
  { cmd: '/lora',       desc: 'fuzzy-find a LoRA to insert',        args: ' ' },
  { cmd: '/multi',      desc: 'generate images for multiple prompts (one per line)', args: '\n' },
  { cmd: '/purge',      desc: 'free GPU memory on active server',   args: ''  },
  { cmd: '/resolution', desc: 'set output resolution (e.g. 640x480 or phone)', args: ' ' },
  { cmd: '/review',         desc: 'grid of the last N images, oldest first', args: ' ' },
  { cmd: '/review-all',     desc: 'grid of every image (tap to view, trash to delete)', args: '' },
  { cmd: '/review-session', desc: 'grid of this session\'s images (tap to view, trash to delete)', args: '' },
  { cmd: '/review-today',   desc: 'grid of today\'s images (tap to view, trash to delete)', args: '' },
  { cmd: '/sequence',   desc: 'generate a prompt sequence from a master prompt (Grok)', args: ' ' },
  { cmd: '/video-sequence', desc: 'like /sequence, plus per-shot action & audio for video (Grok)', args: ' ' },
  { cmd: '/sequence-review', desc: 'show the last sequence\'s prompts in a grid; ▶ to generate one', args: '' },
  { cmd: '/sequence-replacement', desc: 'add a find→replace applied to Grok prompts', args: ' ' },
  { cmd: '/server',     desc: 'choose a ComfyUI server',            args: ''  },
  { cmd: '/slideshow',         desc: 'browse the last N images, oldest first',  args: ' ' },
  { cmd: '/slideshow-all',     desc: 'browse every image, oldest first',       args: '' },
  { cmd: '/slideshow-reverse', desc: 'browse every image, newest first',       args: '' },
  { cmd: '/slideshow-session', desc: 'browse this session\'s images',          args: '' },
  { cmd: '/slideshow-today',   desc: 'browse today\'s images, oldest first',   args: '' },
  { cmd: '/upload',     desc: 'upload a new workflow JSON file',    args: ''  },
  { cmd: '/video-settings', desc: 'set video duration, frames, fps & audio (lock one, the others follow)', args: '' },
  { cmd: '/upscale',    desc: 'upscale the last N images (default 1, no prompt)', args: ' ' },
  { cmd: '/workflow',   desc: 'choose a workflow template',         args: ''  },
  { cmd: '/workflow-iterate', desc: 'run a prompt against several workflows', args: ' ' },
];

// LoRA catalogue for the /lora fuzzy finder
let LORAS = [];
fetch('/api/loras')
  .then(r => r.json())
  .then(loras => {
    LORAS = loras.map(entry => {
      const name     = typeof entry === 'string' ? entry : entry.name;
      const strength = typeof entry === 'string' ? 1.0  : (entry.strength ?? 1.0);
      return { name, strength, label: name.split('/').pop().replace(/\.safetensors$/i, '') };
    });
  })
  .catch(() => {});

// Prompt alias catalogue — word → expansion, loaded from the server.
// Persisted in the encrypted output volume as aliases.json.
let ALIASES = {};
fetch('/api/aliases')
  .then(r => r.json())
  .then(data => { if (data && typeof data === 'object') ALIASES = data; })
  .catch(() => {});

// Expand a word-for-word alias in `text`.  Called at send time so any alias
// that slipped past the real-time expansion (e.g. no trailing space) is still
// caught.  Split on runs of whitespace so separators are preserved.
// Try to expand the word that the user just finished typing (detected by a
// trailing space or newline at the cursor position).  Replaces the word
// in-place in the textarea so the expansion is visible immediately.
function tryExpandAlias() {
  if (!Object.keys(ALIASES).length) return;
  const val    = inputEl.value;
  const cursor = inputEl.selectionStart;
  if (cursor === 0) return;
  const sep = val[cursor - 1];
  if (sep !== ' ' && sep !== '\n') return;
  const before = val.slice(0, cursor - 1);
  const m = before.match(/(\S+)$/);
  if (!m) return;
  const word      = m[1];
  if (word.startsWith('/')) return;   // never expand slash command names/args
  const expansion = ALIASES[word];
  if (expansion === undefined) return;
  const wordStart = cursor - 1 - word.length;
  inputEl.value   = val.slice(0, wordStart) + expansion + val.slice(cursor - 1);
  const newCursor = wordStart + expansion.length + 1;
  inputEl.setSelectionRange(newCursor, newCursor);
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
}

// Matches a "/lora" token (optionally followed by a space and a query)
// ending at the caret, anywhere in the prompt.
const LORA_TRIGGER_RE = /(?:^|\s)(\/lora(?: (\S*))?)$/i;

let acMatches = [];
let acFocused = -1;
let acMode = 'cmd';          // 'cmd' | 'lora'
let loraTriggerStart = -1;   // index in input where the /lora token begins

function renderSlashAc() {
  slashAcEl.innerHTML = acMatches.map((c, i) =>
    `<div class="slash-ac-item${i === acFocused ? ' ac-focused' : ''}" data-idx="${i}">` +
    (acMode === 'lora'
      ? `<span class="slash-ac-cmd">${escapeHtml(c.label)}</span>` +
        `<span class="slash-ac-desc">strength ${c.strength}</span>`
      : `<span class="slash-ac-cmd">${c.cmd}</span>` +
        `<span class="slash-ac-desc">${c.desc}</span>`) +
    `</div>`
  ).join('');
  slashAcEl.classList.add('open');
  const focused = slashAcEl.querySelector('.ac-focused');
  if (focused) focused.scrollIntoView({ block: 'nearest' });
}

function hideSlashAc() {
  slashAcEl.classList.remove('open');
  slashAcEl.innerHTML = '';
  acMatches = [];
  acFocused = -1;
  acMode = 'cmd';
  loraTriggerStart = -1;
}

function updateSlashAc() {
  // /lora fuzzy finder takes priority and works anywhere in the prompt
  const before = inputEl.value.slice(0, inputEl.selectionStart);
  const m = before.match(LORA_TRIGGER_RE);
  if (m && LORAS.length > 0) {
    const query = m[2] || '';
    acMode = 'lora';
    loraTriggerStart = before.length - m[1].length;
    acMatches = LORAS
      .map(l => ({ ...l, score: Math.max(fuzzyScore(query, l.label), fuzzyScore(query, l.name)) }))
      .filter(l => l.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8);
    if (acMatches.length === 0) { hideSlashAc(); return; }
    acFocused = -1;
    renderSlashAc();
    return;
  }

  acMode = 'cmd';
  const val = inputEl.value;
  if (!val.startsWith('/') || val.includes(' ')) { hideSlashAc(); return; }
  const typed = val.toLowerCase();
  acMatches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(typed));
  if (acMatches.length === 0 || (acMatches.length === 1 && acMatches[0].cmd === typed)) {
    hideSlashAc(); return;
  }
  acFocused = -1;
  renderSlashAc();
}

function selectSlashAcItem(idx) {
  const c = acMatches[idx];
  if (!c) return;
  if (acMode === 'lora') {
    const tag   = `<lora:${c.name}:${c.strength}> `;
    const caret = inputEl.selectionStart;
    inputEl.value = inputEl.value.slice(0, loraTriggerStart) + tag + inputEl.value.slice(caret);
    const pos = loraTriggerStart + tag.length;
    hideSlashAc();
    inputEl.focus();
    inputEl.setSelectionRange(pos, pos);
  } else {
    inputEl.value = c.cmd + c.args;
    hideSlashAc();
    inputEl.focus();
    updateSlashAc();  // selecting /lora immediately opens the LoRA finder
  }
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
}

slashAcEl.addEventListener('click', e => {
  const item = e.target.closest('.slash-ac-item');
  if (item) selectSlashAcItem(parseInt(item.dataset.idx, 10));
});

// Named resolution presets
const RESOLUTION_PRESETS = {
  ipad:    { width: 2048, height: 2732, label: 'iPad Pro portrait (2048×2732)'   },
  hd:      { width: 1280, height:  720, label: 'HD 720p (1280×720)'              },
  fhd:     { width: 1920, height: 1080, label: 'Full HD 1080p (1920×1080)'       },
  square:  { width: 1024, height: 1024, label: 'Square (1024×1024)'              },
};

// Current selections (null = use backend default)
let currentServer     = null;  // {address, os, name}
let currentWorkflow   = null;  // string
let currentFaceWorkflow = null;  // string — face-detailer workflow the face icons use
let currentUpscaleWorkflow = null; // string — upscaler workflow for /upscale
let currentImage2ImageWorkflow = null; // string — image2image workflow for /image2image
let currentImage2VideoWorkflow = null; // string — image2video workflow for /image2video
let currentInpaintingWorkflow = null; // string — inpainting workflow for the 🩹 button
let lastFaceDetailPrompt = null; // global override set by /face-detail-prompt; takes priority over per-image derivation
let lastInpaintingPrompt = null; // set via /inpainting-prompt; required before the 🩹 button works
let currentResolution = { width: 1365, height: 768 };  // {width, height} or null (null = workflow default); defaults to 16:9
let currentGenerationSteps = null; // integer or null (null = workflow default)
const DEFAULT_DENOISE = { face: 0.35, image2image: 0.30, inpaint: 0.45, upscale: 0.15 };
let currentDenoise = { ...DEFAULT_DENOISE };
// Video output settings for image2video workflows (<DURATION>/<FRAMES>/<FPS>).
// The three are interdependent (frames = duration × fps); the math lives in
// utils.js. `videoLock` names the one held constant when editing the others.
let currentVideoSettings = { ...DEFAULT_VIDEO_SETTINGS };
let videoLock = 'fps';  // 'duration' | 'frames' | 'fps'
let iterations        = 1;     // images generated per prompt (set via /iterations)
let iterationsFromSequence = false; // true while `iterations` is borrowed as a /sequence count; reset to 1 on the next non-sequence prompt
let sequenceReplacements = []; // [from, to] pairs applied to /sequence prompts
// The most recent /sequence or /video-sequence result, for /sequence-review.
// null until one runs; { video: bool, items: [{ prompt, action, audio }] }
// (action/audio are empty strings for a plain /sequence).
let lastSequence = null;
let image2imageReplacements = []; // [from, to] pairs applied to the original generation prompt for prompt-less /image2image
let image2imageOverridePrompt = null; // set via /image2image-set-prompt; overrides the per-image original prompt for prompt-less /image2image and the 🎨 button
let image2videoReplacements = []; // [from, to] pairs applied to the original generation prompt for prompt-less /image2video
let image2videoOverridePrompt = null; // set via /image2video-set-prompt; overrides the per-image original prompt for prompt-less /image2video and the 🎬 button

// Auto-purge of idle GPU memory is handled server-side (see app.py),
// so it fires even after the browser is closed.

function updateHeaderStatus() {
  const srv = currentServer  ? currentServer.name  : DEFAULT_SERVER;
  const wf  = currentWorkflow ? currentWorkflow     : DEFAULT_WORKFLOW;
  document.getElementById('header-status').textContent = `${srv}  ·  ${wf}`;
}
updateHeaderStatus();

// Auto-resize textarea + alias expansion + slash autocomplete
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
  tryExpandAlias();
  updateSlashAc();
});

// Active slideshow controller (keyboard navigation target)
let activeSlideshowCtrl = null;

document.addEventListener('keydown', e => {
  if (!activeSlideshowCtrl) return;
  if (document.activeElement === inputEl) return;
  if (e.key === 'ArrowLeft')  { e.preventDefault(); activeSlideshowCtrl.navigate(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); activeSlideshowCtrl.navigate(1); }
  if (e.key === 'Delete')     { e.preventDefault(); activeSlideshowCtrl.deleteCurrent(); }
});

// Prompt history (shell-style up/down navigation)
const history = [];
let historyIdx = -1;   // -1 = at the live draft
let savedDraft = '';   // preserves unsent text when browsing history

// When a command needs y/n confirmation, it parks a callback here. The next
// message the user sends is treated as the answer instead of a prompt.
let pendingConfirm = null;

inputEl.addEventListener('keydown', e => {
  // Slash-command autocomplete navigation takes priority
  if (slashAcEl.classList.contains('open')) {
    if (e.key === 'Escape') { e.preventDefault(); hideSlashAc(); return; }
    if (e.key === 'Tab') {
      e.preventDefault();
      selectSlashAcItem(acFocused >= 0 ? acFocused : 0);
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      acFocused = Math.min(acFocused + 1, acMatches.length - 1);
      renderSlashAc();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (acFocused <= 0) { acFocused = -1; renderSlashAc(); return; }
      acFocused--;
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
    if (history.length === 0) return;
    e.preventDefault();
    if (historyIdx === -1) savedDraft = inputEl.value;
    historyIdx = Math.min(historyIdx + 1, history.length - 1);
    inputEl.value = history[historyIdx];
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    return;
  }

  if (e.key === 'ArrowDown') {
    if (historyIdx === -1) return;
    e.preventDefault();
    historyIdx--;
    inputEl.value = historyIdx === -1 ? savedDraft : history[historyIdx];
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    return;
  }
});

sendBtn.addEventListener('click', sendMessage);

// Lightbox
document.addEventListener('click', e => {
  if (e.target.tagName === 'IMG' && e.target.closest('.bubble') && !e.target.closest('.slideshow')) {
    openLightbox(e.target.src);
  }
});
document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
lightbox.addEventListener('click', e => { if (e.target === lightbox) closeLightbox(); });

// ---------------------------------------------------------------------------
// Drag-and-drop image import: drop an image from outside the app to copy it
// into the current session. The file is uploaded as-is, gets a permanent
// /images/ URL, and is rendered (and tracked in sessionImages) like any other
// generated image so it works with do-over, review, slideshow, etc.
// ---------------------------------------------------------------------------
(function setupImageDrop() {
  const overlay = document.getElementById('drop-overlay');
  let dragDepth = 0;

  // Only react to drags that carry files (not internal text/image drags).
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
      sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not import image: ${escapeHtml(err.message)}</span>`;
    });
}

// Cuts the last frame out of a generated video (server-side, via ffmpeg) and
// drops it at the bottom of the chat as a normal generated image, so it can be
// edited, do-over'd or fed back into image2video for last-frame continuity.
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
      // Inherit the source video's metadata so the extracted frame carries the
      // same action/audio when fed back into image2video (last-frame continuity).
      if (imageVideoMeta[url]) imageVideoMeta[data.url] = { ...imageVideoMeta[url] };
      // Also inherit the source video's generation prompt so the "Edit metadata"
      // editor pre-populates Prompt with whatever produced the original video.
      if (imagePrompts[url]) imagePrompts[data.url] = imagePrompts[url];
      sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not cut last frame: ${escapeHtml(err.message)}</span>`;
    });
}

// Inline editor (pencil overlay) for an image's metadata: its generation prompt
// (imagePrompts[url]) plus the image2video action/audio (imageVideoMeta[url]).
// The prompt drives do-over / image2image / image2video; action & audio are
// folded into the image2video prompt by buildVideoPrompt().
function openVideoMetaEditor(url, wrap) {
  const meta = imageVideoMeta[url] || {};

  const box = document.createElement('div');
  box.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';

  // `multiline` rows use a textarea (the prompt can be long); the rest are single-line.
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

  const promptInput = mkRow('Prompt', imagePrompts[url], 'image generation prompt', true);
  const actionInput = mkRow('Action', meta.action, 'what happens in the video');
  const audioInput  = mkRow('Audio',  meta.audio,  'sounds / dialogue');

  const refreshTooltip = () => {
    const i2v = wrap && wrap.querySelector('.img-i2v');
    if (i2v) i2v.title = i2vTooltip(imageVideoMeta[url]);
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
    if (prompt) imagePrompts[url] = prompt; else delete imagePrompts[url];
    if (action || audio) {
      imageVideoMeta[url] = { action, audio };
    } else {
      delete imageVideoMeta[url];
    }
    refreshTooltip();
    addMessage('bot', `Metadata set — Prompt <strong style="color:#a78bfa">${escapeHtml(prompt || '—')}</strong> · Action <strong style="color:#a78bfa">${escapeHtml(action || '—')}</strong> · Audio <strong style="color:#a78bfa">${escapeHtml(audio || '—')}</strong>.`);
    scrollBottom();
  });
  clearBtn.addEventListener('click', () => {
    delete imagePrompts[url];
    delete imageVideoMeta[url];
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

// Tap a user bubble to re-edit that prompt
messagesEl.addEventListener('click', e => {
  if (e.target.tagName === 'IMG') return;
  const bubble = e.target.closest('.message.user .bubble');
  if (!bubble || !bubble.dataset.prompt) return;
  const text = bubble.dataset.prompt;
  inputEl.value = text;
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
  historyIdx = -1;
  savedDraft = '';
  inputEl.focus();
  inputEl.setSelectionRange(text.length, text.length);
  inputEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

function scrollBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

function addMessage(role, contentHtml, rawText) {
  const wrap   = document.createElement('div');
  wrap.className = `message ${role}`;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? 'You' : 'AI';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = contentHtml;
  if (role === 'user' && rawText != null) {
    bubble.dataset.prompt = rawText;
    const icon = document.createElement('span');
    icon.className = 'edit-icon';
    icon.textContent = '✏';
    bubble.appendChild(icon);
  }
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollBottom();
  return bubble;
}

// ---------------------------------------------------------------------------
// Slash commands
// ---------------------------------------------------------------------------

// Shared selection bubble for /workflow and /face-detail-workflow: fetches a
// list of workflow names, renders one button each (ticking the current one),
// and reports the choice back via onSelect.
function renderWorkflowPicker({ url, title, loadingText, failLabel, emptyMsg, current, setMsg, onSelect }) {
  const bubble = addMessage('bot', `<div class="status-text">${loadingText}</div>`).parentElement.querySelector('.bubble');
  fetch(url).then(r => r.json()).then(workflows => {
    if (!workflows.length && emptyMsg) {
      bubble.innerHTML = emptyMsg;
      return;
    }
    let html = `<strong>${title}</strong><div class="sel-list">`;
    workflows.forEach(wf => {
      const isCur = wf === current;
      html += `<button class="sel-btn${isCur ? ' current' : ''}" data-wf="${escapeHtml(wf)}">
                 ${escapeHtml(wf)}${isCur ? ' <span style="color:#7c3aed">✓</span>' : ''}
               </button>`;
    });
    html += '</div>';
    bubble.innerHTML = html;
    bubble.querySelectorAll('.sel-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const wf = btn.dataset.wf;
        onSelect(wf);
        bubble.innerHTML = `${setMsg} <strong style="color:#a78bfa">${escapeHtml(wf)}</strong>`;
      });
    });
    scrollBottom();
  }).catch(() => { bubble.innerHTML = `<span style="color:#f87171">Failed to load ${failLabel}.</span>`; });
}

// Renders the list of saved sessions into a new bot bubble. Each row is a
// clickable button (invokes onSelect with the session name and the bubble)
// plus a trash icon that deletes the saved session. Shared by /session-load
// (click = restore) and /session-save with no name (click = overwrite).
function renderSessionPicker({ headerHtml, onSelect }) {
  const bubble = addMessage('bot', '<div class="status-text">Loading saved sessions…</div>').parentElement.querySelector('.bubble');
  fetch('/api/sessions')
  .then(parseJsonResponse)
  .then(sessions => {
    if (!sessions.length) {
      bubble.innerHTML = 'No saved sessions yet. Use <code>/session-save &lt;name&gt;</code> to save one.';
      scrollBottom();
      return;
    }
    const list = document.createElement('div');
    list.innerHTML = headerHtml;
    const selList = document.createElement('div');
    selList.className = 'sel-list';

    sessions.forEach(s => {
      const date = s.saved_at ? new Date(s.saved_at).toLocaleDateString() : '';
      const row = document.createElement('div');
      row.className = 'sel-row';

      const btn = document.createElement('button');
      btn.className = 'sel-btn';
      btn.dataset.name = s.name;
      btn.innerHTML = `<span>${escapeHtml(s.name)}</span>
        <span style="color:#475569;font-size:0.8em">${s.image_count} image(s)${date ? ' · ' + date : ''}</span>`;
      btn.addEventListener('click', () => onSelect(btn.dataset.name, bubble));

      const delBtn = document.createElement('button');
      delBtn.className = 'sel-del-btn';
      delBtn.title = 'Delete session';
      delBtn.innerHTML = '🗑';
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const name = btn.dataset.name;
        delBtn.disabled = true;
        delBtn.style.opacity = '0.4';
        fetch('/api/sessions/' + encodeURIComponent(name), { method: 'DELETE' })
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          row.remove();
          if (!selList.querySelector('.sel-row')) {
            bubble.innerHTML = 'No saved sessions yet. Use <code>/session-save &lt;name&gt;</code> to save one.';
          }
          scrollBottom();
        })
        .catch(err => {
          delBtn.disabled = false;
          delBtn.style.opacity = '';
          addMessage('bot', `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`);
          scrollBottom();
        });
      });

      row.appendChild(btn);
      row.appendChild(delBtn);
      selList.appendChild(row);
    });

    list.appendChild(selList);
    bubble.innerHTML = '';
    bubble.appendChild(list);
    scrollBottom();
  })
  .catch(() => {
    bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load sessions.</span>';
    scrollBottom();
  });
}

function showSessionSummary() {
  const rows = [];

  const srvName  = currentServer ? currentServer.name    : DEFAULT_SERVER;
  const srvAddr  = currentServer ? currentServer.address : DEFAULT_SERVER;
  const srvOs    = currentServer ? currentServer.os      : DEFAULT_SERVER_OS;
  const srvLabel = currentServer
    ? `<span style="color:#a78bfa">${escapeHtml(srvName)}</span> <span style="color:#475569">(${escapeHtml(srvAddr)}, ${escapeHtml(srvOs)})</span>`
    : `<span style="color:#a78bfa">${escapeHtml(srvName)}</span> <span style="color:#475569">(default)</span>`;
  rows.push({ label: 'Server', value: srvLabel });

  const wfActive = currentWorkflow || DEFAULT_WORKFLOW;
  const wfLabel  = currentWorkflow
    ? `<span style="color:#a78bfa">${escapeHtml(wfActive)}</span>`
    : `<span style="color:#a78bfa">${escapeHtml(wfActive)}</span> <span style="color:#475569">(default)</span>`;
  rows.push({ label: 'Workflow', value: wfLabel });

  const faceWfActive = currentFaceWorkflow || DEFAULT_FACE_WORKFLOW;
  const faceWfLabel  = faceWfActive
    ? (currentFaceWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(faceWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(faceWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Face-detail workflow', value: faceWfLabel });

  const upWfActive = currentUpscaleWorkflow || DEFAULT_UPSCALE_WORKFLOW;
  const upWfLabel  = upWfActive
    ? (currentUpscaleWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(upWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(upWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Upscale workflow', value: upWfLabel });

  const i2iWfActive = currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW;
  const i2iWfLabel  = i2iWfActive
    ? (currentImage2ImageWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(i2iWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(i2iWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Image2image workflow', value: i2iWfLabel });

  const i2vWfActive = currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW;
  const i2vWfLabel  = i2vWfActive
    ? (currentImage2VideoWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(i2vWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(i2vWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Image2video workflow', value: i2vWfLabel });

  const inpaintWfActive = currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW;
  const inpaintWfLabel  = inpaintWfActive
    ? (currentInpaintingWorkflow
        ? `<span style="color:#a78bfa">${escapeHtml(inpaintWfActive)}</span>`
        : `<span style="color:#a78bfa">${escapeHtml(inpaintWfActive)}</span> <span style="color:#475569">(default)</span>`)
    : `<span style="color:#475569">not set</span>`;
  rows.push({ label: 'Inpainting workflow', value: inpaintWfLabel });

  const resLabel = currentResolution
    ? `<span style="color:#a78bfa">${currentResolution.width}×${currentResolution.height}</span>`
    : `<span style="color:#475569">workflow default</span>`;
  rows.push({ label: 'Resolution', value: resLabel });

  rows.push({ label: 'Iterations', value: `<span style="color:#a78bfa">${iterations}</span>${iterations > 1 ? ' per prompt' : ''}` });

  if (currentGenerationSteps !== null) {
    rows.push({ label: 'Generation steps', value: `<span style="color:#a78bfa">${currentGenerationSteps}</span>` });
  }

  const DENOISE_LABELS = { face: 'Face-detailer', image2image: 'Image2image', inpaint: 'Inpainting', upscale: 'Upscale' };
  const denoiseOverrides = Object.entries(currentDenoise)
    .filter(([k, v]) => v !== DEFAULT_DENOISE[k])
    .map(([k, v]) => `${DENOISE_LABELS[k]}: <span style="color:#a78bfa">${v.toFixed(2)}</span>`)
    .join(' · ');
  if (denoiseOverrides) {
    rows.push({ label: 'Denoise overrides', value: denoiseOverrides });
  }

  const vs = currentVideoSettings;
  rows.push({
    label: 'Video settings',
    value: `<span style="color:#a78bfa">${fmtDuration(vs.duration)}s</span> · ` +
           `<span style="color:#a78bfa">${vs.frames}</span> frames · ` +
           `<span style="color:#a78bfa">${vs.fps}</span> fps · ` +
           `audio <span style="color:#a78bfa">${vs.audio !== false ? 'on' : 'off'}</span> ` +
           `<span style="color:#475569">(🔒 ${videoLock})</span>`,
  });

  if (lastFaceDetailPrompt) {
    rows.push({ label: 'Face-detail prompt', value: `<code>${escapeHtml(lastFaceDetailPrompt)}</code>` });
  }

  if (lastInpaintingPrompt) {
    rows.push({ label: 'Inpainting prompt', value: `<code>${escapeHtml(lastInpaintingPrompt)}</code>` });
  }

  if (sequenceReplacements.length) {
    const list = sequenceReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Sequence replacements (${sequenceReplacements.length})`, value: list });
  }

  if (image2imageReplacements.length) {
    const list = image2imageReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Image2image replacements (${image2imageReplacements.length})`, value: list });
  }

  if (image2imageOverridePrompt) {
    rows.push({ label: 'Image2image override prompt', value: `<code>${escapeHtml(image2imageOverridePrompt)}</code>` });
  }

  if (image2videoReplacements.length) {
    const list = image2videoReplacements
      .map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`)
      .join(', ');
    rows.push({ label: `Image2video replacements (${image2videoReplacements.length})`, value: list });
  }

  if (image2videoOverridePrompt) {
    rows.push({ label: 'Image2video override prompt', value: `<code>${escapeHtml(image2videoOverridePrompt)}</code>` });
  }

  const aliasKeys = Object.keys(ALIASES).sort();
  if (aliasKeys.length) {
    const preview = aliasKeys.slice(0, 3).map(k => `<code>${escapeHtml(k)}</code>`).join(', ');
    const more = aliasKeys.length > 3 ? ` <span style="color:#475569">+${aliasKeys.length - 3} more</span>` : '';
    rows.push({ label: `Aliases (${aliasKeys.length})`, value: `${preview}${more} — <code>/alias-list</code> to see all` });
  }

  rows.push({ label: 'Session images', value: `<span style="color:#a78bfa">${sessionImages.length}</span>` });

  const rowsHtml = rows
    .map(r => `<div style="font-size:0.85rem;color:#94a3b8"><strong style="color:#cbd5e1">${r.label}:</strong> ${r.value}</div>`)
    .join('');
  addMessage('bot', `<strong>Session summary</strong><div class="sel-list" style="margin-top:10px;gap:4px">${rowsHtml}</div>`);
  scrollBottom();
}

function handleSlashCommand(raw) {
  const parts = raw.trim().split(/\s+/);
  const cmd   = parts[0].toLowerCase();

  if (cmd === '/multi') {
    const lines = raw.slice('/multi'.length).split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) {
      addMessage('user', escapeHtml(raw), null);
      addMessage('bot', '<span style="color:#f87171">⚠ Paste line-separated prompts after <code>/multi</code> (use Shift+Enter between lines)</span>');
      return;
    }
    iterationsFromSequence = false; // /multi is a prompt, not a sequence — drop any borrowed count
    sendBtn.disabled = true;
    (async () => {
      for (const prompt of lines) {
        const expanded = expandAliases(prompt, ALIASES);
        addMessage('user', escapeHtml(expanded), expanded);
        const ok = await runGeneration(expanded, '');
        if (!ok) break;
      }
      sendBtn.disabled = false;
    })();
    return;
  }

  if (cmd === '/sequence') {
    const master = expandAliases(raw.slice('/sequence'.length).trim(), ALIASES);
    addMessage('user', escapeHtml(raw), raw);
    if (!master) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide a master prompt, e.g. <code>/sequence a woman practising yoga at sunrise</code></span>');
      return;
    }
    // Number of prompts comes from /iterations, but 1 is never right for a
    // sequence, so fall back to 15 in that case.
    const count = iterations === 1 ? 15 : iterations;
    // The sequence has borrowed `iterations` as its prompt count. Flag it so the
    // next non-sequence prompt resets back to 1 instead of silently generating
    // `count` images.
    iterationsFromSequence = true;
    sendBtn.disabled = true;
    const statusBubble = addMessage('bot', `
      <div class="status-text">Asking Grok for ${count} prompt(s)…</div>
      <div class="dots"><span></span><span></span><span></span></div>
    `);
    (async () => {
      // The Grok call runs as a cancellable job; null means cancelled/errored
      // (the bubble already shows why).
      const result = await runSequenceJob('/api/sequence', master, count, statusBubble);
      if (!result) { sendBtn.disabled = false; return; }
      const prompts = result.prompts || [];
      // Remember this run for /sequence-review (plain sequence — no action/audio).
      lastSequence = { video: false, items: prompts.map(p => ({ prompt: p, action: '', audio: '' })) };
      statusBubble.innerHTML = `<div class="status-text">Grok returned <strong style="color:#a78bfa">${prompts.length}</strong> prompt(s) — generating one after another…</div>`;
      scrollBottom();
      // Generate each prompt sequentially, exactly like /multi.
      for (const prompt of prompts) {
        addMessage('user', escapeHtml(prompt), prompt);
        const ok = await runGeneration(prompt, '');
        if (!ok) break;
      }
      sendBtn.disabled = false;
    })();
    return;
  }

  if (cmd === '/video-sequence') {
    const master = expandAliases(raw.slice('/video-sequence'.length).trim(), ALIASES);
    addMessage('user', escapeHtml(raw), raw);
    if (!master) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide a master prompt, e.g. <code>/video-sequence a woman dancing in the rain</code></span>');
      return;
    }
    // Same count rules as /sequence — borrow /iterations, default 15.
    const count = iterations === 1 ? 15 : iterations;
    iterationsFromSequence = true;
    sendBtn.disabled = true;
    const statusBubble = addMessage('bot', `
      <div class="status-text">Asking Grok for ${count} video shot(s)…</div>
      <div class="dots"><span></span><span></span><span></span></div>
    `);
    (async () => {
      const result = await runSequenceJob('/api/video-sequence', master, count, statusBubble);
      if (!result) { sendBtn.disabled = false; return; }
      const shots = result.prompts || [];
      // Remember this run for /sequence-review (carries action/audio per shot).
      lastSequence = {
        video: true,
        items: shots.map(s => ({ prompt: s.prompt || '', action: s.action || '', audio: s.audio || '' })),
      };
      statusBubble.innerHTML = `<div class="status-text">Grok returned <strong style="color:#a78bfa">${shots.length}</strong> shot(s) — generating one after another…</div>`;
      scrollBottom();
      // Generate each still from its prompt only; remember action/audio so the
      // video button can fold them in later.
      for (const shot of shots) {
        const prompt = shot.prompt || '';
        if (!prompt) continue;
        addMessage('user', escapeHtml(prompt), prompt);
        const ok = await runGeneration(prompt, '', null, {
          videoMeta: { action: shot.action || '', audio: shot.audio || '' },
        });
        if (!ok) break;
      }
      sendBtn.disabled = false;
    })();
    return;
  }

  if (cmd === '/sequence-review') {
    addMessage('user', escapeHtml(raw), raw);
    if (!lastSequence || !lastSequence.items.length) {
      addMessage('bot', '<span style="color:#f87171">⚠ No sequence has been run yet — use <code>/sequence</code> or <code>/video-sequence</code> first.</span>');
      return;
    }
    const bubble = addMessage('bot', '');
    renderSequenceReview(bubble, lastSequence);
    return;
  }

  if (cmd === '/sequence-replacement') {
    addMessage('user', escapeHtml(raw), raw);
    if (!parts[1]) {
      if (!sequenceReplacements.length) {
        addMessage('bot', `No sequence replacements set.<br>Usage: <code>/sequence-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Matching is case-insensitive and preserves the matched case (<code>bird</code>→<code>dog</code>, <code>Bird</code>→<code>Dog</code>). Applied to every prompt <code>/sequence</code> gets back from Grok.<br><code>/sequence-replacement clear</code> removes them all.`);
      } else {
        const list = sequenceReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
        addMessage('bot', `<strong>Sequence replacements:</strong><br>${list}<br><br><code>/sequence-replacement clear</code> removes them all.`);
      }
      return;
    }
    if (parts[1].toLowerCase() === 'clear') {
      sequenceReplacements = [];
      addMessage('bot', 'Sequence replacements cleared.');
      return;
    }
    const from = parts[1];
    const to   = parts.slice(2).join(' ');
    if (!to) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/sequence-replacement woman elegant woman in a red dress</code></span>');
      return;
    }
    sequenceReplacements.push([from, to]);
    addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to every prompt from <code>/sequence</code>.`);
    return;
  }

  if (cmd === '/image2image-replacement') {
    addMessage('user', escapeHtml(raw), raw);
    if (!parts[1]) {
      if (!image2imageReplacements.length) {
        addMessage('bot', `No image2image replacements set.<br>Usage: <code>/image2image-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Applied to the original generation prompt when <code>/image2image</code> is run with no prompt.<br><code>/image2image-replacement-reset</code> removes them all.`);
      } else {
        const list = image2imageReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
        addMessage('bot', `<strong>Image2image replacements:</strong><br>${list}<br><br><code>/image2image-replacement-reset</code> removes them all.`);
      }
      return;
    }
    const from = parts[1];
    const to   = parts.slice(2).join(' ');
    if (!to) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/image2image-replacement Dog Cat</code></span>');
      return;
    }
    image2imageReplacements.push([from, to]);
    addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to the original generation prompt when <code>/image2image</code> runs with no prompt.`);
    return;
  }

  if (cmd === '/image2image-replacement-reset') {
    addMessage('user', escapeHtml(raw), raw);
    image2imageReplacements = [];
    addMessage('bot', 'Image2image replacements cleared.');
    return;
  }

  if (cmd === '/image2image-set-prompt') {
    addMessage('user', escapeHtml(raw), raw);
    const override = raw.slice('/image2image-set-prompt'.length).trim();
    if (!override) {
      if (image2imageOverridePrompt) {
        addMessage('bot', `Current image2image override prompt: <code>${escapeHtml(image2imageOverridePrompt)}</code><br>Usage: <code>/image2image-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2image</code> (or the 🎨 button) runs without its own prompt. <code>/image2image-set-prompt-reset</code> clears it.`);
      } else {
        addMessage('bot', 'No image2image override prompt set.<br>Usage: <code>/image2image-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2image</code> (or the 🎨 button) runs without its own prompt. Useful after a <code>/review</code> when the original prompts aren\'t available.');
      }
      return;
    }
    image2imageOverridePrompt = override;
    addMessage('bot', `Image2image override prompt set: <code>${escapeHtml(override)}</code>. It will be used by <code>/image2image</code> and the 🎨 button until cleared with <code>/image2image-set-prompt-reset</code>.`);
    return;
  }

  if (cmd === '/image2image-set-prompt-reset') {
    addMessage('user', escapeHtml(raw), raw);
    image2imageOverridePrompt = null;
    addMessage('bot', 'Image2image override prompt cleared.');
    return;
  }

  if (cmd === '/image2image-workflow') {
    renderWorkflowPicker({
      url: '/api/image2image-workflows',
      title: 'Select an image2image workflow:',
      loadingText: 'Loading image2image workflows…',
      failLabel: 'image2image workflows',
      emptyMsg: 'No image2image workflows available — add one to the <code>image2image/</code> folder.',
      current: currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW,
      setMsg: 'Image2image workflow set to',
      onSelect: wf => { currentImage2ImageWorkflow = wf; },
    });
    return;
  }

  if (cmd === '/image2video-replacement') {
    addMessage('user', escapeHtml(raw), raw);
    if (!parts[1]) {
      if (!image2videoReplacements.length) {
        addMessage('bot', `No image2video replacements set.<br>Usage: <code>/image2video-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Applied to the original generation prompt when <code>/image2video</code> is run with no prompt.<br><code>/image2video-replacement-reset</code> removes them all.`);
      } else {
        const list = image2videoReplacements.map(([f, t]) => `<code>${escapeHtml(f)}</code> → <code>${escapeHtml(t)}</code>`).join('<br>');
        addMessage('bot', `<strong>Image2video replacements:</strong><br>${list}<br><br><code>/image2video-replacement-reset</code> removes them all.`);
      }
      return;
    }
    const from = parts[1];
    const to   = parts.slice(2).join(' ');
    if (!to) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide both a from and a to value, e.g. <code>/image2video-replacement Dog Cat</code></span>');
      return;
    }
    image2videoReplacements.push([from, to]);
    addMessage('bot', `Replacement added: <code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code>. Applied to the original generation prompt when <code>/image2video</code> runs with no prompt.`);
    return;
  }

  if (cmd === '/image2video-replacement-reset') {
    addMessage('user', escapeHtml(raw), raw);
    image2videoReplacements = [];
    addMessage('bot', 'Image2video replacements cleared.');
    return;
  }

  if (cmd === '/image2video-set-prompt') {
    addMessage('user', escapeHtml(raw), raw);
    const override = raw.slice('/image2video-set-prompt'.length).trim();
    if (!override) {
      if (image2videoOverridePrompt) {
        addMessage('bot', `Current image2video override prompt: <code>${escapeHtml(image2videoOverridePrompt)}</code><br>Usage: <code>/image2video-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2video</code> (or the 🎬 button) runs without its own prompt. <code>/image2video-set-prompt-reset</code> clears it.`);
      } else {
        addMessage('bot', 'No image2video override prompt set.<br>Usage: <code>/image2video-set-prompt &lt;prompt&gt;</code> — overrides the per-image original prompt when <code>/image2video</code> (or the 🎬 button) runs without its own prompt. Useful after a <code>/review</code> when the original prompts aren\'t available.');
      }
      return;
    }
    image2videoOverridePrompt = override;
    addMessage('bot', `Image2video override prompt set: <code>${escapeHtml(override)}</code>. It will be used by <code>/image2video</code> and the 🎬 button until cleared with <code>/image2video-set-prompt-reset</code>.`);
    return;
  }

  if (cmd === '/image2video-set-prompt-reset') {
    addMessage('user', escapeHtml(raw), raw);
    image2videoOverridePrompt = null;
    addMessage('bot', 'Image2video override prompt cleared.');
    return;
  }

  if (cmd === '/image2video-workflow') {
    renderWorkflowPicker({
      url: '/api/image2video-workflows',
      title: 'Select an image2video workflow:',
      loadingText: 'Loading image2video workflows…',
      failLabel: 'image2video workflows',
      emptyMsg: 'No image2video workflows available — add one to the <code>image2video/</code> folder.',
      current: currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW,
      setMsg: 'Image2video workflow set to',
      onSelect: wf => { currentImage2VideoWorkflow = wf; },
    });
    return;
  }

  if (cmd === '/image2video') {
    addMessage('user', escapeHtml(raw), raw);
    if (!sessionImages.length) {
      addMessage('bot', 'No image from this session for image2video — generate one first.');
      return;
    }
    const i2vArg = raw.slice('/image2video'.length).trim();
    if (i2vArg !== '' && !/^\d+$/.test(i2vArg)) {
      addMessage('bot', '<span style="color:#f87171">⚠ <code>/image2video</code> takes only a number (how many recent images to process). To use a custom prompt, set one with <code>/image2video-set-prompt &lt;prompt&gt;</code> first.</span>');
      return;
    }
    const i2vN = i2vArg !== '' ? parseInt(i2vArg, 10) : 1;
    if (i2vN < 1) {
      addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/image2video</code> or <code>/image2video &lt;N&gt;</code></span>');
      return;
    }
    const i2vTargets = sessionImages.slice(-i2vN);
    let i2vChain = Promise.resolve();
    let i2vAborted = false;
    i2vTargets.forEach(img => {
      i2vChain = i2vChain.then(() => {
        if (i2vAborted) return;
        let prompt;
        if (image2videoOverridePrompt) {
          prompt = image2videoOverridePrompt;
        } else {
          const orig = imagePrompts[img];
          if (!orig) {
            i2vAborted = true;
            addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2video-set-prompt &lt;prompt&gt;</code></span>');
            return;
          }
          prompt = buildVideoPrompt(applyReplacements(orig, image2videoReplacements), imageVideoMeta[img], currentVideoSettings.audio);
        }
        addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
        return runImage2Video(prompt, img);
      });
    });
    return;
  }

  if (cmd === '/inpaint-workflow') {
    renderWorkflowPicker({
      url: '/api/inpainting-workflows',
      title: 'Select an inpainting workflow:',
      loadingText: 'Loading inpainting workflows…',
      failLabel: 'inpainting workflows',
      emptyMsg: 'No inpainting workflows available — add one to the <code>inpainting/</code> folder.',
      current: currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW,
      setMsg: 'Inpainting workflow set to',
      onSelect: wf => { currentInpaintingWorkflow = wf; },
    });
    return;
  }

  if (cmd === '/inpainting-prompt') {
    const prompt = raw.slice('/inpainting-prompt'.length).trim();
    addMessage('user', escapeHtml(raw), raw);
    if (!prompt) {
      lastInpaintingPrompt = null;
      addMessage('bot', 'Inpainting prompt cleared — the 🩹 button will show an error until a new one is set.');
      return;
    }
    lastInpaintingPrompt = prompt;
    addMessage('bot', `Inpainting prompt set — the 🩹 button will use <code>${escapeHtml(prompt)}</code>.`);
    return;
  }

  if (cmd === '/image2image') {
    addMessage('user', escapeHtml(raw), raw);
    if (!sessionImages.length) {
      addMessage('bot', 'No image from this session for image2image — generate one first.');
      return;
    }
    const i2iArg = raw.slice('/image2image'.length).trim();
    // The only argument is an integer N: run over the last N images, each from
    // its own original generation prompt (with replacements), or the override
    // prompt if one is set via /image2image-set-prompt. Empty means N=1. To run
    // with a one-off prompt, set it first with /image2image-set-prompt.
    if (i2iArg !== '' && !/^\d+$/.test(i2iArg)) {
      addMessage('bot', '<span style="color:#f87171">⚠ <code>/image2image</code> takes only a number (how many recent images to process). To use a custom prompt, set one with <code>/image2image-set-prompt &lt;prompt&gt;</code> first.</span>');
      return;
    }
    const i2iN = i2iArg !== '' ? parseInt(i2iArg, 10) : 1;
    if (i2iN < 1) {
      addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/image2image</code> or <code>/image2image &lt;N&gt;</code></span>');
      return;
    }
    const i2iTargets = sessionImages.slice(-i2iN);
    let i2iChain = Promise.resolve();
    let i2iAborted = false;
    i2iTargets.forEach(img => {
      i2iChain = i2iChain.then(() => {
        if (i2iAborted) return;
        let prompt;
        if (image2imageOverridePrompt) {
          prompt = image2imageOverridePrompt;
        } else {
          const orig = imagePrompts[img];
          if (!orig) {
            i2iAborted = true;
            addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
            return;
          }
          prompt = applyReplacements(orig, image2imageReplacements);
        }
        addMessage('user', 'Image2image: ' + escapeHtml(prompt), prompt);
        return runImage2Image(prompt, img);
      });
    });
    return;
  }

  if (cmd === '/workflow-iterate') {
    const master = raw.slice('/workflow-iterate'.length).trim();
    addMessage('user', escapeHtml(raw), raw);
    if (!master) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide a prompt, e.g. <code>/workflow-iterate a cat astronaut</code> — then tick the workflows to run it against</span>');
      return;
    }
    const bubble = addMessage('bot', '<div class="status-text">Loading workflows…</div>').parentElement.querySelector('.bubble');
    fetch('/api/workflows').then(r => r.json()).then(workflows => {
      if (!workflows.length) {
        bubble.innerHTML = 'No workflows available — upload one with <code>/upload</code>.';
        return;
      }
      let html = `<strong>Run against which workflows?</strong>
        <div style="font-size:0.8rem;color:#94a3b8;margin:4px 0 8px">Prompt: <code>${escapeHtml(master)}</code></div>
        <div class="sel-list">`;
      workflows.forEach((wf, i) => {
        html += `<label class="wfi-row" style="display:flex;align-items:center;gap:8px;font-size:0.9rem;color:#cbd5e1;cursor:pointer">
                   <input type="checkbox" class="wfi-check" value="${escapeHtml(wf)}" id="wfi-${i}">
                   ${escapeHtml(wf)}
                 </label>`;
      });
      html += `</div>
        <button class="sel-btn wfi-go" style="margin-top:10px">Generate</button>`;
      bubble.innerHTML = html;

      const goBtn = bubble.querySelector('.wfi-go');
      goBtn.addEventListener('click', () => {
        const selected = [...bubble.querySelectorAll('.wfi-check:checked')].map(c => c.value);
        if (!selected.length) {
          // Nothing ticked — nudge, but leave the checkboxes in place.
          if (!bubble.querySelector('.wfi-warn')) {
            const warn = document.createElement('div');
            warn.className = 'wfi-warn';
            warn.style.cssText = 'color:#f87171;font-size:0.82rem;margin-top:6px';
            warn.textContent = '⚠ Tick at least one workflow.';
            bubble.appendChild(warn);
          }
          return;
        }
        // Lock the selection UI so it reads as a record of what was run.
        bubble.querySelectorAll('.wfi-check, .wfi-go').forEach(el => { el.disabled = true; });
        const warn = bubble.querySelector('.wfi-warn');
        if (warn) warn.remove();
        bubble.insertAdjacentHTML('beforeend',
          `<div class="status-text" style="margin-top:8px">Generating <strong style="color:#a78bfa">${selected.length}</strong> workflow(s)…</div>`);

        iterationsFromSequence = false; // this is a prompt run, not a sequence — drop any borrowed count
        sendBtn.disabled = true;
        (async () => {
          for (let i = 0; i < selected.length; i++) {
            const wf = selected[i];
            const label = ` — ${wf} (${i + 1}/${selected.length})`;
            const ok = await runGeneration(master, label, wf);
            if (!ok) break;
          }
          sendBtn.disabled = false;
        })();
      });
      scrollBottom();
    }).catch(() => { bubble.innerHTML = '<span style="color:#f87171">Failed to load workflows.</span>'; });
    return;
  }

  if (cmd === '/face-detail-prompt') {
    const prompt = raw.slice('/face-detail-prompt'.length).trim();
    addMessage('user', escapeHtml(raw), raw);
    if (!prompt) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide a prompt, e.g. <code>/face-detail-prompt a clear, detailed face &lt;lora:name:strength&gt;</code></span>');
      return;
    }
    if (!/<lora:[^>]+>/i.test(prompt)) {
      addMessage('bot', '<span style="color:#f87171">⚠ <code>/face-detail-prompt</code> needs a LoRA tag in the prompt, e.g. <code>/face-detail-prompt a clear, detailed face &lt;lora:name:strength&gt;</code> — type <code>/lora</code> to find one.</span>');
      return;
    }
    lastFaceDetailPrompt = prompt; // overrides per-image derivation; the face icons reuse it
    addMessage('bot', `Face-detail prompt set — the face icons will use <code>${escapeHtml(prompt)}</code>.`);
    return;
  }

  if (cmd === '/face-detail-prompt-reset') {
    addMessage('user', escapeHtml(raw), raw);
    lastFaceDetailPrompt = null; // back to deriving a prompt from each image's own generation prompt
    addMessage('bot', 'Face-detail prompt cleared — the face icons will derive a prompt from each image again.');
    return;
  }

  if (cmd === '/upscale') {
    addMessage('user', escapeHtml(raw), raw);
    if (!sessionImages.length) {
      addMessage('bot', 'No image from this session to upscale — generate one first.');
      return;
    }
    const upscaleArg = raw.slice('/upscale'.length).trim();
    const upscaleN = upscaleArg ? parseInt(upscaleArg, 10) : 1;
    if (isNaN(upscaleN) || upscaleN < 1) {
      addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/upscale</code> or <code>/upscale &lt;N&gt;</code> — upscale the last N images</span>');
      return;
    }
    const upscaleTargets = sessionImages.slice(-upscaleN);
    let upscaleChain = Promise.resolve();
    upscaleTargets.forEach(img => { upscaleChain = upscaleChain.then(() => runUpscale(img)); });
    return;
  }

  if (cmd === '/face-detail-session') {
    addMessage('user', escapeHtml(raw), raw);
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session to face-detail — generate some first.');
      return;
    }
    const fdSessionTargets = sessionImages.slice();
    let fdSessionChain = Promise.resolve();
    fdSessionTargets.forEach(img => {
      fdSessionChain = fdSessionChain.then(() => {
        const prompt = lastFaceDetailPrompt || deriveFaceDetailPrompt(imagePrompts[img]);
        if (!prompt) {
          addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        addMessage('user', 'Face detail: ' + escapeHtml(prompt));
        return runFaceDetail(prompt, img);
      });
    });
    return;
  }

  if (cmd === '/face-detail') {
    addMessage('user', escapeHtml(raw), raw);
    if (!sessionImages.length) {
      addMessage('bot', 'No image from this session to face-detail — generate one first.');
      return;
    }
    const fdArg = raw.slice('/face-detail'.length).trim();
    const fdN = fdArg ? parseInt(fdArg, 10) : 1;
    if (isNaN(fdN) || fdN < 1) {
      addMessage('bot', '<span style="color:#f87171">⚠ Usage: <code>/face-detail</code> or <code>/face-detail &lt;N&gt;</code> — face-detail the last N images</span>');
      return;
    }
    const fdTargets = sessionImages.slice(-fdN);
    let fdChain = Promise.resolve();
    fdTargets.forEach(img => {
      fdChain = fdChain.then(() => {
        const prompt = lastFaceDetailPrompt || deriveFaceDetailPrompt(imagePrompts[img]);
        if (!prompt) {
          addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        addMessage('user', 'Face detail: ' + escapeHtml(prompt));
        return runFaceDetail(prompt, img);
      });
    });
    return;
  }

  addMessage('user', escapeHtml(raw), raw);

  if (cmd === '/help') {
    addMessage('bot', `
      <strong>Available commands</strong>
      <div class="sel-list" style="margin-top:10px;gap:4px">
        <div style="font-size:0.85rem;color:#94a3b8"><code>/help</code> — show this message</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/alias-create &lt;word&gt; &lt;expansion&gt;</code> — create or update a text alias; typing the word in a prompt and pressing space expands it immediately
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code> &nbsp;·&nbsp; quotes are optional</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/alias-list</code> — list all defined aliases</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/server</code> — choose a ComfyUI server</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/addserver &lt;name&gt; &lt;host:port:os&gt;</code> — add a server
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">
            OS types: <code>unix</code> (Linux/macOS) &nbsp;·&nbsp; <code>windows</code> (Windows path separators)<br>
            e.g. <code>/addserver mordor mordor:8000:windows</code><br>
            e.g. <code>/addserver mybox 192.168.1.50:8188:unix</code>
          </div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/iterations &lt;n&gt;</code> — generate n images per prompt (default 1)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/generation-steps &lt;n&gt;</code> — override the steps field in generation workflows (e.g. <code>/generation-steps 20</code>); does not affect face-detail, upscale or image2image workflows; <code>/generation-steps reset</code> restores the workflow default</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/resolution &lt;WxH&gt;</code> — set output resolution, e.g. <code>/resolution 640x480</code> or <code>/resolution phone</code>
          <div style="margin-top:2px;color:#475569;font-size:0.78rem"><code>phone</code> (or <code>iphone</code>) measures this device's viewport &nbsp;·&nbsp; presets: ipad, hd, fhd, square &nbsp;·&nbsp; <code>/resolution flip</code> swaps W/H &nbsp;·&nbsp; <code>/resolution reset</code> restores workflow default</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/lora</code> — fuzzy-find a LoRA to insert (works anywhere in a prompt)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/multi</code> — generate images for multiple prompts; paste one prompt per line (Shift+Enter between lines)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/sequence &lt;master prompt&gt;</code> — ask Grok to expand a master prompt into a sequence of prompts, then generate them one after another
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">count comes from <code>/iterations</code> (or 15 if iterations is 1) &nbsp;·&nbsp; needs <code>XAI_API_KEY</code> set on the server</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/video-sequence &lt;master prompt&gt;</code> — like <code>/sequence</code>, but Grok also returns an action &amp; audio per shot; folded into the prompt (<code>&lt;prompt&gt;. &lt;action&gt;. Audio: &lt;audio&gt;</code>) when the image is turned into a video</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/sequence-review</code> — show the last sequence's prompts (with action/audio for a video sequence) in a grid; press ▶ on a row to generate that prompt</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/sequence-replacement &lt;from&gt; &lt;to&gt;</code> — find→replace applied to each Grok prompt (no args lists them; <code>clear</code> removes them)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/workflow</code> — choose a workflow template</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/workflow-iterate &lt;prompt&gt;</code> — tick several workflows, then run the prompt against each one</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail [N]</code> — run face-detail over the last N images (default 1); uses <code>/face-detail-prompt</code> override or derives from each image's prompt</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-prompt &lt;prompt&gt;</code> — set the prompt the per-image face (&#128100;) icons use; otherwise each icon derives one from that image's own prompt (needs a <code>&lt;lora:…&gt;</code> tag)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-prompt-reset</code> — clear that override so the face icons derive a prompt from each image again</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-session</code> — face-detail every image from this session, one after another</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-workflow</code> — choose which face-detailer workflow the face icons use</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/upscale [N]</code> — run an upscaler workflow over the last N generated images (default 1, no prompt needed)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image [N]</code> — re-run an image2image workflow over the last N images (default 1), each from its own original prompt, or the override prompt if set</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image-replacement &lt;from&gt; &lt;to&gt;</code> — find→replace applied to the original prompt when <code>/image2image</code> runs with no override (no args lists them)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image-replacement-reset</code> — clear all image2image replacements</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image-set-prompt &lt;prompt&gt;</code> — override prompt used by <code>/image2image</code> and the 🎨 button instead of each image's original prompt (handy after a <code>/review</code>); no args shows it</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image-set-prompt-reset</code> — clear the override prompt</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2image-workflow</code> — choose which image2image workflow <code>/image2image</code> uses</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video [N]</code> — run an image2video workflow over the last N images (default 1), each from its own original prompt or the override prompt if set</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video-replacement &lt;from&gt; &lt;to&gt;</code> — find→replace applied to the original prompt when <code>/image2video</code> runs with no override (no args lists them)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video-replacement-reset</code> — clear all image2video replacements</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video-set-prompt &lt;prompt&gt;</code> — override prompt used by <code>/image2video</code> and the 🎬 button instead of each image's original prompt; no args shows it</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video-set-prompt-reset</code> — clear the override prompt</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/image2video-workflow</code> — choose which image2video workflow <code>/image2video</code> uses</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/video-settings</code> — set video duration, frames, fps &amp; audio for image2video
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">lock one value (🔒); editing either of the other two keeps <code>frames = duration × fps</code> &nbsp;·&nbsp; only one lock at a time &nbsp;·&nbsp; untick Audio to drop <code>Audio:</code> cues for workflows without sound</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/upload</code> — upload a new workflow JSON file</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/purge</code> — free GPU memory on the active ComfyUI server</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete</code> — delete the last image</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-session</code> — delete all images from this session (chat + output folder)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-today</code> — delete every image generated today (asks y/n first)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-all</code> — delete every image in the output folder (asks y/n first)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-session [name]</code> — copy this session's images and videos into the encrypted volume, then remove the originals (optional folder name, e.g. <code>/archive-session man walking on beach</code>)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-today [name]</code> — archive images and videos generated today into the encrypted volume (optional folder name)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-all [name]</code> — archive every image and video in the output folder into the encrypted volume (asks y/n first; optional folder name)
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">needs the <code>archive-agent</code> running on the host and <code>ARCHIVE_*</code> set on the server</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/clear</code> — clear the visible chat while keeping settings, prompt history (up-arrow recall) and session images (<code>/review-session</code>)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/session-new</code> — start a completely new session, resetting all settings to defaults</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/session-save &lt;name&gt;</code> — save the current session (chat history, images, settings, up/down prompt history) to disk; omit the name to pick an existing session to overwrite or delete</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/session-load</code> — pick and restore a previously saved session</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/session-summary</code> — show a summary of all active settings (server, workflow, resolution, replacements, etc.)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review &lt;n&gt;</code> — grid of the last N images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-all</code> — grid of every image, oldest first (tap to view, trash to delete)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-today</code> — grid of today's images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-session</code> — grid of this session's images</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/composite-videos-session</code> — drag this session's videos into order, then press ✓ to join them into one clip</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/slideshow &lt;n&gt;</code> — browse the last N images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/slideshow-all</code> — browse every image, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/slideshow-reverse</code> — browse every image, newest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/slideshow-today</code> — browse today's images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/slideshow-session</code> — browse this session's images
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">← → keys on desktop &nbsp;·&nbsp; Del deletes the current image &nbsp;·&nbsp; swipe left/right on mobile &nbsp;·&nbsp; auto-advances every 3s</div>
        </div>
      </div>
      <div style="margin-top:10px;font-size:0.8rem;color:#475569">
        Include LoRAs in any prompt with <code>&lt;lora:name:strength&gt;</code>,
        or type <code>/lora</code> while writing a prompt to search for one
      </div>
    `);
    return;
  }

  if (cmd === '/server') {
    const bubble = addMessage('bot', '<div class="status-text">Loading servers…</div>').parentElement.querySelector('.bubble');
    fetch('/api/servers').then(r => r.json()).then(servers => {
      const curAddr = currentServer ? currentServer.address : DEFAULT_SERVER;
      let html = '<strong>Select a server:</strong><div class="sel-list">';
      servers.forEach(s => {
        const addr = `${s.host}:${s.port}`;
        const isCur = addr === curAddr;
        html += `<button class="sel-btn${isCur ? ' current' : ''}"
                   data-addr="${addr}" data-os="${s.os || 'unix'}" data-name="${s.name}">
                   ${escapeHtml(s.name)} <span style="color:#475569;font-size:0.8em">${addr}</span>
                   ${isCur ? ' <span style="color:#7c3aed">✓</span>' : ''}
                 </button>`;
      });
      html += '</div>';
      bubble.innerHTML = html;
      bubble.querySelectorAll('.sel-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          currentServer = { address: btn.dataset.addr, os: btn.dataset.os, name: btn.dataset.name };
          bubble.innerHTML = `Server set to <strong style="color:#a78bfa">${escapeHtml(currentServer.name)}</strong> <span style="color:#475569">(${currentServer.address})</span>`;
          updateHeaderStatus();
        });
      });
      scrollBottom();
    }).catch(() => { bubble.innerHTML = '<span style="color:#f87171">Failed to load servers.</span>'; });
    return;
  }

  if (cmd === '/workflow') {
    renderWorkflowPicker({
      url: '/api/workflows',
      title: 'Select a workflow:',
      loadingText: 'Loading workflows…',
      failLabel: 'workflows',
      current: currentWorkflow || DEFAULT_WORKFLOW,
      setMsg: 'Workflow set to',
      onSelect: wf => { currentWorkflow = wf; updateHeaderStatus(); },
    });
    return;
  }

  if (cmd === '/face-detail-workflow') {
    renderWorkflowPicker({
      url: '/api/facedetailer-workflows',
      title: 'Select a face-detailer workflow:',
      loadingText: 'Loading face-detailer workflows…',
      failLabel: 'face-detailer workflows',
      emptyMsg: 'No face-detailer workflows available — add one to the <code>facedetailer/</code> folder.',
      current: currentFaceWorkflow || DEFAULT_FACE_WORKFLOW,
      setMsg: 'Face-detailer workflow set to',
      onSelect: wf => { currentFaceWorkflow = wf; },
    });
    return;
  }

  if (cmd === '/upload') {
    const bubble = addMessage('bot', `
      <div><strong>Upload a workflow</strong></div>
      <label class="upload-zone" id="upload-zone">
        <input type="file" accept=".json" id="upload-input">
        <div>Drag &amp; drop a <code>.json</code> file here</div>
        <span class="upload-btn">Browse…</span>
      </label>
    `).parentElement.querySelector('.bubble');

    const zone  = bubble.querySelector('#upload-zone');
    const input = bubble.querySelector('#upload-input');

    // Drag-and-drop styling
    zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file) doUpload(file, bubble);
    });

    input.addEventListener('change', () => {
      if (input.files[0]) doUpload(input.files[0], bubble);
    });

    scrollBottom();
    return;
  }

  if (cmd === '/addserver') {
    // Expected: /addserver <name> <host:port:os>
    const name = parts[1];
    const connStr = parts[2];
    if (!name || !connStr) {
      addMessage('bot', `Usage: <code>/addserver &lt;name&gt; &lt;host:port:os&gt;</code><br>
        e.g. <code>/addserver mordor mordor:8000:windows</code>`);
      return;
    }
    const connParts = connStr.split(':');
    if (connParts.length !== 3) {
      addMessage('bot', `<span style="color:#f87171">⚠ Expected <code>host:port:os</code> — e.g. <code>mordor:8000:windows</code></span>`);
      return;
    }
    const [host, portStr, os] = connParts;
    const port = parseInt(portStr, 10);
    if (!host || isNaN(port) || !os) {
      addMessage('bot', `<span style="color:#f87171">⚠ Invalid format. Expected <code>host:port:os</code></span>`);
      return;
    }
    fetch('/api/add-server', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, host, port, os }),
    })
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      addMessage('bot', `Server <strong style="color:#a78bfa">${escapeHtml(data.name)}</strong> added (<code>${escapeHtml(data.host)}:${data.port}</code>, OS: <code>${escapeHtml(data.os)}</code>).<br>Use <code>/server</code> to select it.`);
    })
    .catch(err => addMessage('bot', `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`));
    return;
  }

  if (cmd === '/slideshow-session') {
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session yet — generate some first!');
      return;
    }
    const bubble = addMessage('bot', '');
    // sessionImages is oldest-first.
    activeSlideshowCtrl = createSlideshow(bubble, sessionImages.slice());
    return;
  }

  if (cmd === '/slideshow') {
    const n = parseInt(parts[1], 10);
    if (!Number.isInteger(n) || n < 1) {
      addMessage('bot', 'Usage: <code>/slideshow &lt;n&gt;</code> — browse the last N images, oldest first.');
      return;
    }
    const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
    fetch('/api/images')
      .then(r => r.json())
      .then(images => {
        if (!images.length) {
          bubble.innerHTML = 'No images yet — generate some first!';
          return;
        }
        // The API returns newest-first; take the last N, then show oldest-first.
        activeSlideshowCtrl = createSlideshow(bubble, images.slice(0, n).reverse());
      })
      .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
    return;
  }

  if (cmd === '/slideshow-all' || cmd === '/slideshow-today' || cmd === '/slideshow-reverse') {
    const todayOnly = cmd === '/slideshow-today';
    const reverse   = cmd === '/slideshow-reverse';
    const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
    fetch(todayOnly ? '/api/images?filter=today' : '/api/images')
      .then(r => r.json())
      .then(images => {
        if (!images.length) {
          bubble.innerHTML = todayOnly
            ? 'No images generated today — try <code>/slideshow-all</code> for all images.'
            : 'No images yet — generate some first!';
          return;
        }
        // The API returns newest-first; show oldest-first unless reversed.
        if (!reverse) images = images.slice().reverse();
        activeSlideshowCtrl = createSlideshow(bubble, images);
      })
      .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
    return;
  }

  if (cmd === '/review-session') {
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session yet — generate some first!');
      return;
    }
    const bubble = addMessage('bot', '');
    renderReviewGrid(bubble, sessionImages.slice());
    return;
  }

  if (cmd === '/composite-videos-session') {
    const videos = sessionImages.filter(isVideoUrl);
    if (videos.length < 2) {
      addMessage('bot', videos.length
        ? 'Only one video in this session — generate at least two to composite.'
        : 'No videos from this session yet — generate some with image2video first!');
      return;
    }
    const bubble = addMessage('bot', '');
    renderCompositeGrid(bubble, videos);
    return;
  }

  if (cmd === '/review') {
    const n = parseInt(parts[1], 10);
    if (!Number.isInteger(n) || n < 1) {
      addMessage('bot', 'Usage: <code>/review &lt;n&gt;</code> — grid of the last N images, oldest first.');
      return;
    }
    const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
    fetch('/api/images')
      .then(r => r.json())
      .then(images => {
        if (!images.length) {
          bubble.innerHTML = 'No images yet — generate some first!';
          return;
        }
        // The API returns newest-first; take the last N, then show oldest-first.
        renderReviewGrid(bubble, images.slice(0, n).reverse());
      })
      .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
    return;
  }

  if (cmd === '/review-all' || cmd === '/review-today') {
    const todayOnly = cmd === '/review-today';
    const bubble = addMessage('bot', '<div class="status-text">Loading images…</div>');
    fetch(todayOnly ? '/api/images?filter=today' : '/api/images')
      .then(r => r.json())
      .then(images => {
        if (!images.length) {
          bubble.innerHTML = todayOnly
            ? 'No images generated today — try <code>/review-all</code> for every image.'
            : 'No images yet — generate some first!';
          return;
        }
        // The API returns newest-first; show oldest-first to match the slideshow.
        renderReviewGrid(bubble, images.slice().reverse());
      })
      .catch(() => { bubble.innerHTML = '<span style="color:#f87171">⚠ Failed to load images.</span>'; });
    return;
  }

  if (cmd === '/delete') {
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session left to delete.');
      return;
    }
    const url = sessionImages[sessionImages.length - 1];
    const bubble = addMessage('bot', '<div class="status-text">Deleting…</div>');
    deleteImageFile(url).then(() => {
      removeImageFromChat(url);
      bubble.innerHTML = 'Deleted 1 image.';
      scrollBottom();
    }).catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
      scrollBottom();
    });
    return;
  }

  if (cmd === '/delete-session') {
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session left to delete.');
      return;
    }
    const targets = [...sessionImages];
    const bubble = addMessage('bot', '<div class="status-text">Deleting…</div>');
    Promise.allSettled(targets.map(url =>
      deleteImageFile(url).then(() => removeImageFromChat(url))
    )).then(results => {
      const failed = results.filter(r => r.status === 'rejected');
      const ok = results.length - failed.length;
      if (!failed.length) {
        history.length = 0;
        historyIdx = -1;
        savedDraft = '';
        fauxFullscreenEls.clear();
        document.body.style.overflow = '';
        messagesEl.innerHTML = '';
        addMessage('bot', 'Chat cleared. Describe the image you\'d like to generate.');
      } else {
        bubble.innerHTML = `Deleted ${ok} image(s). <span style="color:#f87171">⚠ ${failed.length} failed: ${escapeHtml(failed[0].reason.message)}</span>`;
        scrollBottom();
      }
    });
    return;
  }

  if (cmd === '/delete-today') {
    addMessage('bot', 'This deletes <strong>every</strong> image generated today, not just this session\'s. Type <code>y</code> to confirm or <code>n</code> to cancel.');
    pendingConfirm = (answer) => {
      if (!/^y(es)?$/i.test(answer)) {
        addMessage('bot', 'Cancelled — no images deleted.');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Deleting today\'s images…</div>').parentElement.querySelector('.bubble');
      fetch('/api/images?filter=today')
        .then(r => r.json())
        .then(images => {
          if (!images.length) {
            bubble.innerHTML = 'No images generated today.';
            return;
          }
          return Promise.allSettled(images.map(url =>
            deleteImageFile(url).then(() => removeImageFromChat(url))
          )).then(results => {
            const failed = results.filter(r => r.status === 'rejected');
            const ok = results.length - failed.length;
            if (failed.length) {
              bubble.innerHTML = `Deleted ${ok} image(s) generated today. <span style="color:#f87171">⚠ ${failed.length} failed: ${escapeHtml(failed[0].reason.message)}</span>`;
            } else {
              bubble.innerHTML = `Deleted ${ok} image(s) generated today.`;
            }
            scrollBottom();
          });
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
    };
    return;
  }

  if (cmd === '/delete-all') {
    addMessage('bot', 'This deletes <strong>every</strong> image in the output folder, not just this session\'s. Type <code>y</code> to confirm or <code>n</code> to cancel.');
    pendingConfirm = (answer) => {
      if (!/^y(es)?$/i.test(answer)) {
        addMessage('bot', 'Cancelled — no images deleted.');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Deleting all images…</div>').parentElement.querySelector('.bubble');
      fetch('/api/images', { method: 'DELETE' })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          if (!ok || data.error) throw new Error(data.error || 'Delete failed');
          // Clear chat too, since any on-screen images now point at deleted files.
          history.length = 0;
          historyIdx = -1;
          savedDraft = '';
          sessionImages.length = 0;
          fauxFullscreenEls.clear();
          document.body.style.overflow = '';
          messagesEl.innerHTML = '';
          addMessage('bot', `Deleted ${data.deleted} image(s) from the output folder.`);
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
    };
    return;
  }

  if (cmd === '/archive-session') {
    if (!sessionImages.length) {
      addMessage('bot', 'No images from this session to archive.');
      return;
    }
    const name = raw.trim().slice(parts[0].length).trim();
    const targets = [...sessionImages];
    const filenames = targets.map(url => decodeURIComponent(url.split('/').pop()));
    const bubble = addMessage('bot', '<div class="status-text">Archiving…</div>').parentElement.querySelector('.bubble');
    fetch('/api/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'session', filenames, name }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || data.error) throw new Error(data.error || 'Archive failed');
        // Originals are deleted after archiving, so drop them from the chat.
        targets.forEach(removeImageFromChat);
        bubble.innerHTML = `Archived ${data.archived} image(s) to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`;
        scrollBottom();
      })
      .catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
    return;
  }

  if (cmd === '/archive-today') {
    const name = raw.trim().slice(parts[0].length).trim();
    const bubble = addMessage('bot', '<div class="status-text">Archiving today\'s images…</div>').parentElement.querySelector('.bubble');
    fetch('/api/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'today', name }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || data.error) throw new Error(data.error || 'Archive failed');
        if (!data.archived) {
          bubble.innerHTML = 'No images generated today to archive.';
          scrollBottom();
          return;
        }
        // Originals are gone now; clear the chat since on-screen images may point
        // at archived files.
        history.length = 0;
        historyIdx = -1;
        savedDraft = '';
        sessionImages.length = 0;
        fauxFullscreenEls.clear();
        document.body.style.overflow = '';
        messagesEl.innerHTML = '';
        addMessage('bot', `Archived ${data.archived} image(s) generated today to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`);
      })
      .catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
    return;
  }

  if (cmd === '/archive-all') {
    const name = raw.trim().slice(parts[0].length).trim();
    addMessage('bot', 'This archives <strong>every</strong> image in the output folder to the encrypted volume and then <strong>removes the originals</strong>. Type <code>y</code> to confirm or <code>n</code> to cancel.');
    pendingConfirm = (answer) => {
      if (!/^y(es)?$/i.test(answer)) {
        addMessage('bot', 'Cancelled — nothing archived.');
        return;
      }
      const bubble = addMessage('bot', '<div class="status-text">Archiving all images…</div>').parentElement.querySelector('.bubble');
      fetch('/api/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'all', name }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          if (!ok || data.error) throw new Error(data.error || 'Archive failed');
          // Clear chat too, since any on-screen images now point at archived files.
          history.length = 0;
          historyIdx = -1;
          savedDraft = '';
          sessionImages.length = 0;
          fauxFullscreenEls.clear();
          document.body.style.overflow = '';
          messagesEl.innerHTML = '';
          addMessage('bot', `Archived ${data.archived} image(s) from the output folder to <code>${escapeHtml(data.folder)}</code> on the encrypted volume.`);
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Archive failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
    };
    return;
  }

  if (cmd === '/clear') {
    // Only wipe the visible chat. Prompt history (up-arrow recall), session
    // images (/review-session) and per-image metadata are all preserved.
    fauxFullscreenEls.clear();
    document.body.style.overflow = '';
    messagesEl.innerHTML = '';
    addMessage('bot', 'Chat cleared. Settings, prompt history and session images preserved — describe the image you\'d like to generate.');
    return;
  }

  if (cmd === '/session-new') {
    history.length = 0;
    historyIdx = -1;
    savedDraft = '';
    sessionImages.length = 0;
    for (const k of Object.keys(imagePrompts)) delete imagePrompts[k];
    for (const k of Object.keys(imageVideoMeta)) delete imageVideoMeta[k];
    lastSequence = null;
    fauxFullscreenEls.clear();
    document.body.style.overflow = '';
    currentServer = null;
    currentWorkflow = null;
    currentFaceWorkflow = null;
    currentUpscaleWorkflow = null;
    currentImage2ImageWorkflow = null;
    currentImage2VideoWorkflow = null;
    currentInpaintingWorkflow = null;
    lastFaceDetailPrompt = null;
    lastInpaintingPrompt = null;
    currentResolution = { width: 1365, height: 768 };
    currentGenerationSteps = null;
    currentDenoise = { ...DEFAULT_DENOISE };
    currentVideoSettings = { ...DEFAULT_VIDEO_SETTINGS };
    videoLock = 'fps';
    iterations = 1;
    iterationsFromSequence = false;
    sequenceReplacements = [];
    image2imageReplacements = [];
    image2imageOverridePrompt = null;
    image2videoReplacements = [];
    image2videoOverridePrompt = null;
    messagesEl.innerHTML = '';
    updateHeaderStatus();
    addMessage('bot', 'New session started. Describe the image you\'d like to generate.');
    return;
  }

  if (cmd === '/session-save') {
    const rawName = raw.slice('/session-save'.length).trim();
    addMessage('user', escapeHtml(raw), raw);

    const buildPayload = (name) => ({
      name,
      settings: {
        server: currentServer,
        workflow: currentWorkflow,
        faceWorkflow: currentFaceWorkflow,
        upscaleWorkflow: currentUpscaleWorkflow,
        image2imageWorkflow: currentImage2ImageWorkflow,
        image2videoWorkflow: currentImage2VideoWorkflow,
        inpaintingWorkflow: currentInpaintingWorkflow,
        resolution: currentResolution,
        generationSteps: currentGenerationSteps,
        iterations,
        sequenceReplacements: sequenceReplacements.slice(),
        image2imageReplacements: image2imageReplacements.slice(),
        image2imageOverridePrompt,
        image2videoReplacements: image2videoReplacements.slice(),
        image2videoOverridePrompt,
        lastFaceDetailPrompt,
        lastInpaintingPrompt,
        currentDenoise: { ...currentDenoise },
        videoSettings: { ...currentVideoSettings },
        videoLock,
      },
      sessionImages: sessionImages.slice(),
      imagePrompts: Object.assign({}, imagePrompts),
      imageVideoMeta: Object.assign({}, imageVideoMeta),
      lastSequence,
      promptHistory: history.slice(),
      messages: captureSessionMessages(),
    });

    const doSave = (name, bubble) => {
      bubble.innerHTML = '<div class="status-text">Saving session…</div>';
      fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload(name)),
      })
      .then(parseJsonResponse)
      .then(data => {
        if (data.error) throw new Error(data.error);
        bubble.innerHTML = `Session saved as <strong style="color:#a78bfa">${escapeHtml(data.name)}</strong>. Use <code>/session-load</code> to restore it.`;
        scrollBottom();
      })
      .catch(err => {
        bubble.innerHTML = `<span style="color:#f87171">⚠ Save failed: ${escapeHtml(err.message)}</span>`;
        scrollBottom();
      });
    };

    if (rawName) {
      const bubble = addMessage('bot', '').parentElement.querySelector('.bubble');
      doSave(rawName, bubble);
      return;
    }

    // No name given: let the user pick an existing session to overwrite (or
    // delete one via the trash icon).
    renderSessionPicker({
      headerHtml: '<strong>Select a session to overwrite:</strong>',
      onSelect: (name, bubble) => doSave(name, bubble),
    });
    return;
  }

  if (cmd === '/session-load') {
    renderSessionPicker({
      headerHtml: '<strong>Select a session to restore:</strong>',
      onSelect: (name, bubble) => {
        bubble.innerHTML = `<div class="status-text">Loading <strong>${escapeHtml(name)}</strong>…</div>`;
        fetch('/api/sessions/' + encodeURIComponent(name))
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          restoreSession(data);
          bubble.innerHTML = `Session <strong style="color:#a78bfa">${escapeHtml(name)}</strong> restored.`;
          scrollBottom();
          showSessionSummary();
        })
        .catch(err => {
          bubble.innerHTML = `<span style="color:#f87171">⚠ Load failed: ${escapeHtml(err.message)}</span>`;
          scrollBottom();
        });
      },
    });
    return;
  }

  if (cmd === '/session-summary') {
    showSessionSummary();
    return;
  }

  if (cmd === '/purge') {
    const bubble = addMessage('bot', '<div class="status-text">Freeing GPU memory…</div><div class="dots"><span></span><span></span><span></span></div>').parentElement.querySelector('.bubble');
    const server = currentServer ? currentServer.address : null;
    fetch('/api/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(server ? { server } : {}),
    })
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      bubble.innerHTML = 'GPU memory freed.';
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Purge failed: ${escapeHtml(err.message)}</span>`;
      scrollBottom();
    });
    return;
  }

  if (cmd === '/lora') {
    addMessage('bot', 'Type <code>/lora</code> followed by a search term while writing a prompt, then pick a LoRA from the list — it inserts a <code>&lt;lora:name:strength&gt;</code> tag at the cursor.');
    return;
  }

  if (cmd === '/resolution') {
    const arg = (parts[1] || '').toLowerCase();
    if (!arg) {
      const cur = currentResolution
        ? `<strong style="color:#a78bfa">${currentResolution.width}×${currentResolution.height}</strong>`
        : 'workflow default';
      addMessage('bot', `Current resolution: ${cur}<br>
        Usage: <code>/resolution &lt;WxH&gt;</code> — e.g. <code>/resolution 640x480</code><br>
        <code>16:9</code> — aspect ratio (shorter side 768px) &nbsp;·&nbsp; <code>phone</code> — measures this device's viewport (alias: <code>iphone</code>) &nbsp;·&nbsp; presets: ${Object.keys(RESOLUTION_PRESETS).map(k => `<code>${k}</code>`).join(', ')}<br>
        <code>/resolution flip</code> — swap width and height &nbsp;·&nbsp; <code>/resolution reset</code> — restore workflow default`);
      return;
    }
    if (arg === 'reset') {
      currentResolution = null;
      addMessage('bot', 'Resolution reset to workflow default.');
      return;
    }
    if (arg === 'flip') {
      if (!currentResolution) {
        addMessage('bot', '<span style="color:#f87171">⚠ No resolution set — use <code>/resolution &lt;WxH&gt;</code> or a preset first.</span>');
        return;
      }
      const { width: w, height: h } = currentResolution;
      currentResolution = { width: h, height: w };
      addMessage('bot', `Resolution flipped to <strong style="color:#a78bfa">${h}×${w}</strong>.`);
      return;
    }
    if (arg === 'phone' || arg === 'iphone') {
      const dpr  = window.devicePixelRatio || 1;
      // Use screen dimensions — unlike visualViewport/innerHeight these are
      // unaffected by the on-screen keyboard being open when the command is typed.
      const snap = v => Math.round(v / 8) * 8;
      const physW = snap(window.screen.width  * dpr);
      const physH = snap(window.screen.height * dpr);
      // Always portrait (width < height)
      const w = Math.min(physW, physH), h = Math.max(physW, physH);
      currentResolution = { width: w, height: h };
      addMessage('bot', `Resolution set to <strong style="color:#a78bfa">${w}×${h}</strong> (this device's screen in portrait, ${dpr}× DPR).`);
      return;
    }
    const preset = RESOLUTION_PRESETS[arg];
    if (preset) {
      currentResolution = { width: preset.width, height: preset.height };
      addMessage('bot', `Resolution set to ${preset.label}.`);
      return;
    }
    // Aspect-ratio alias, e.g. `16:9` → 1365×768 (shorter side fixed at 768).
    const ar = arg.match(/^(\d+):(\d+)$/);
    if (ar) {
      const a = parseInt(ar[1], 10), b = parseInt(ar[2], 10);
      if (a < 1 || b < 1) {
        addMessage('bot', '<span style="color:#f87171">⚠ Aspect ratio parts must be positive (e.g. <code>16:9</code>).</span>');
        return;
      }
      const base = 768;
      const w = a >= b ? Math.round(base * a / b) : base;
      const h = a >= b ? base : Math.round(base * b / a);
      currentResolution = { width: w, height: h };
      addMessage('bot', `Resolution set to <strong style="color:#a78bfa">${w}×${h}</strong> (${a}:${b} aspect ratio).`);
      return;
    }
    const m = arg.match(/^(\d+)[x×*](\d+)$/);
    if (!m) {
      addMessage('bot', `<span style="color:#f87171">⚠ Unrecognised resolution <code>${escapeHtml(arg)}</code>.<br>
        Use <code>WxH</code> (e.g. <code>640x480</code>) or a preset: ${Object.keys(RESOLUTION_PRESETS).map(k => `<code>${k}</code>`).join(', ')}.</span>`);
      return;
    }
    const w = parseInt(m[1], 10), h = parseInt(m[2], 10);
    if (w < 64 || h < 64 || w > 8192 || h > 8192) {
      addMessage('bot', '<span style="color:#f87171">⚠ Resolution must be between 64 and 8192 in each dimension.</span>');
      return;
    }
    currentResolution = { width: w, height: h };
    addMessage('bot', `Resolution set to <strong style="color:#a78bfa">${w}×${h}</strong>.`);
    return;
  }

  if (cmd === '/iterations') {
    if (!parts[1]) {
      addMessage('bot', `Each prompt currently generates <strong style="color:#a78bfa">${iterations}</strong> image(s).<br>Usage: <code>/iterations &lt;n&gt;</code> — e.g. <code>/iterations 8</code>`);
      return;
    }
    const n = parseInt(parts[1], 10);
    if (isNaN(n) || n < 1 || n > 64) {
      addMessage('bot', '<span style="color:#f87171">⚠ Iterations must be a number between 1 and 64.</span>');
      return;
    }
    iterations = n;
    iterationsFromSequence = false; // explicit user choice — don't auto-reset it later
    addMessage('bot', `Each prompt will now generate <strong style="color:#a78bfa">${iterations}</strong> image(s)${n > 1 ? ', one after another' : ''}.`);
    return;
  }

  if (cmd === '/generation-steps') {
    if (!parts[1] || parts[1].toLowerCase() === 'reset') {
      if (parts[1] && parts[1].toLowerCase() === 'reset') {
        currentGenerationSteps = null;
        addMessage('bot', 'Generation steps reset to workflow default.');
      } else {
        const cur = currentGenerationSteps !== null
          ? `<strong style="color:#a78bfa">${currentGenerationSteps}</strong>`
          : 'workflow default';
        addMessage('bot', `Current generation steps: ${cur}<br>Usage: <code>/generation-steps &lt;n&gt;</code> — e.g. <code>/generation-steps 20</code><br><code>/generation-steps reset</code> — restore workflow default`);
      }
      return;
    }
    const n = parseInt(parts[1], 10);
    if (isNaN(n) || n < 1 || n > 200) {
      addMessage('bot', '<span style="color:#f87171">⚠ Steps must be a number between 1 and 200.</span>');
      return;
    }
    currentGenerationSteps = n;
    addMessage('bot', `Generation steps set to <strong style="color:#a78bfa">${n}</strong>. Applies to generation workflows only, not face-detail or upscaling.`);
    return;
  }

  if (cmd === '/denoise') {
    addMessage('user', escapeHtml(raw), raw);
    const DENOISE_ROWS = [
      { key: 'face',       label: 'Face-detailer' },
      { key: 'image2image', label: 'Image2image'  },
      { key: 'inpaint',   label: 'Inpainting'    },
      { key: 'upscale',   label: 'Upscale'       },
    ];
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';
    const sliders = {};
    const inputs  = {};
    DENOISE_ROWS.forEach(({ key, label }) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1';
      const lbl = document.createElement('span');
      lbl.textContent = label + ':';
      lbl.style.cssText = 'min-width:110px;color:#94a3b8';
      const sl = document.createElement('input');
      sl.type = 'range'; sl.min = '0.01'; sl.max = '1'; sl.step = '0.01';
      sl.value = currentDenoise[key].toFixed(2);
      sl.style.cssText = 'width:130px;accent-color:#f472b6;cursor:pointer';
      const inp = document.createElement('input');
      inp.type = 'text'; inp.value = currentDenoise[key].toFixed(2);
      inp.style.cssText = 'width:44px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
      sl.addEventListener('input', () => { inp.value = parseFloat(sl.value).toFixed(2); });
      inp.addEventListener('change', () => {
        let v = parseFloat(inp.value);
        if (isNaN(v)) v = currentDenoise[key];
        v = Math.min(1, Math.max(0.01, Math.round(v * 100) / 100));
        inp.value = v.toFixed(2);
        sl.value  = v.toFixed(2);
      });
      sliders[key] = sl; inputs[key] = inp;
      row.appendChild(lbl); row.appendChild(sl); row.appendChild(inp);
      wrap.appendChild(row);
    });
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
    const applyBtn = document.createElement('button');
    applyBtn.textContent = 'Apply';
    applyBtn.className = 'sel-btn';
    applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
    const resetBtn = document.createElement('button');
    resetBtn.textContent = 'Reset to defaults';
    resetBtn.className = 'sel-btn';
    resetBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';
    applyBtn.addEventListener('click', () => {
      DENOISE_ROWS.forEach(({ key }) => {
        currentDenoise[key] = parseFloat(sliders[key].value);
      });
      const summary = DENOISE_ROWS.map(({ key, label }) =>
        `${label}: <strong style="color:#a78bfa">${currentDenoise[key].toFixed(2)}</strong>`).join(' · ');
      addMessage('bot', `Denoise defaults set — ${summary}`);
      scrollBottom();
    });
    resetBtn.addEventListener('click', () => {
      currentDenoise = { ...DEFAULT_DENOISE };
      DENOISE_ROWS.forEach(({ key }) => {
        sliders[key].value = DEFAULT_DENOISE[key].toFixed(2);
        inputs[key].value  = DEFAULT_DENOISE[key].toFixed(2);
      });
      addMessage('bot', 'Denoise defaults reset to: Face-detailer <strong style="color:#a78bfa">0.35</strong> · Image2image <strong style="color:#a78bfa">0.30</strong> · Inpainting <strong style="color:#a78bfa">0.45</strong> · Upscale <strong style="color:#a78bfa">0.15</strong>');
      scrollBottom();
    });
    btnRow.appendChild(applyBtn);
    btnRow.appendChild(resetBtn);
    wrap.appendChild(btnRow);
    const bubble = addMessage('bot', '<strong>Denoise defaults</strong>').parentElement.querySelector('.bubble');
    bubble.appendChild(wrap);
    scrollBottom();
    return;
  }

  if (cmd === '/video-settings') {
    addMessage('user', escapeHtml(raw), raw);
    const VIDEO_ROWS = [
      { key: 'duration', label: 'Duration (s)' },
      { key: 'frames',   label: 'Frames'       },
      { key: 'fps',      label: 'FPS'          },
    ];
    // Work on a copy so nothing is committed until Apply.
    const work    = { ...currentVideoSettings };
    let   lockSel = videoLock;
    const els = {};

    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:6px';

    function refresh() {
      VIDEO_ROWS.forEach(({ key }) => {
        const locked = lockSel === key;
        const { lockBtn, slider, input } = els[key];
        lockBtn.textContent = locked ? '🔒' : '🔓';
        lockBtn.title = locked ? `${key} is locked — drag the others` : `lock ${key}`;
        slider.disabled = locked;
        input.disabled  = locked;
        slider.style.opacity = locked ? '0.35' : '';
        slider.value = String(work[key]);
        input.value  = key === 'duration' ? fmtDuration(work[key]) : String(work[key]);
      });
    }

    function onEdit(key, rawVal) {
      const v = parseFloat(rawVal);
      if (isNaN(v)) { refresh(); return; }
      work[key] = clampVideo(key, v);
      recomputeVideo(work, lockSel, key);
      refresh();
    }

    VIDEO_ROWS.forEach(({ key, label }) => {
      const lim = VIDEO_LIMITS[key];
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1';

      const lockBtn = document.createElement('button');
      lockBtn.className = 'sel-btn';
      lockBtn.style.cssText = 'flex:none;width:30px;padding:2px 0;font-size:0.95rem;line-height:1';
      lockBtn.addEventListener('click', () => {
        if (lockSel === key) return;  // only one lock at a time; can't unlock the only lock
        lockSel = key;
        refresh();
      });

      const lbl = document.createElement('span');
      lbl.textContent = label + ':';
      lbl.style.cssText = 'min-width:92px;color:#94a3b8';

      const slider = document.createElement('input');
      slider.type = 'range';
      slider.min = String(lim.min); slider.max = String(lim.max);
      slider.step = key === 'duration' ? '0.1' : '1';
      slider.style.cssText = 'width:130px;accent-color:#f472b6;cursor:pointer';
      slider.addEventListener('input', () => onEdit(key, slider.value));

      const input = document.createElement('input');
      input.type = 'text';
      input.style.cssText = 'width:52px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;padding:2px 4px;font-size:0.85rem;text-align:center';
      input.addEventListener('change', () => onEdit(key, input.value));

      els[key] = { lockBtn, slider, input };
      row.appendChild(lockBtn); row.appendChild(lbl); row.appendChild(slider); row.appendChild(input);
      wrap.appendChild(row);
    });

    // Audio toggle — when off, the "Audio: …" cue that /video-sequence folds into
    // a video prompt (buildVideoPrompt) is dropped. Useful for image2video
    // workflows that don't generate audio (e.g. the Wan template), so the model
    // isn't fed audio instructions it ignores.
    const audioRow = document.createElement('label');
    audioRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:0.85rem;color:#cbd5e1;cursor:pointer';
    const audioBox = document.createElement('input');
    audioBox.type = 'checkbox';
    audioBox.checked = work.audio !== false;
    audioBox.style.cssText = 'width:15px;height:15px;accent-color:#f472b6;cursor:pointer';
    audioBox.addEventListener('change', () => { work.audio = audioBox.checked; });
    const audioLbl = document.createElement('span');
    audioLbl.innerHTML = 'Audio <span style="color:#475569">— include <code>Audio:</code> cues in video prompts</span>';
    audioRow.appendChild(audioBox); audioRow.appendChild(audioLbl);
    wrap.appendChild(audioRow);

    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:0.78rem;color:#475569;margin-top:2px';
    hint.innerHTML = 'Lock one value (🔒), then drag or type the others — the third follows so that <code>frames = duration × fps</code>.';
    wrap.appendChild(hint);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:4px';
    const applyBtn = document.createElement('button');
    applyBtn.textContent = 'Apply';
    applyBtn.className = 'sel-btn';
    applyBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem';
    const resetBtn = document.createElement('button');
    resetBtn.textContent = 'Reset to defaults';
    resetBtn.className = 'sel-btn';
    resetBtn.style.cssText = 'flex:none;padding:4px 14px;font-size:0.85rem;color:#94a3b8';
    applyBtn.addEventListener('click', () => {
      currentVideoSettings = { ...work };
      videoLock = lockSel;
      addMessage('bot', `Video settings set — Duration <strong style="color:#a78bfa">${fmtDuration(work.duration)}s</strong> · Frames <strong style="color:#a78bfa">${work.frames}</strong> · FPS <strong style="color:#a78bfa">${work.fps}</strong> · Audio <strong style="color:#a78bfa">${work.audio !== false ? 'on' : 'off'}</strong> <span style="color:#475569">(🔒 ${lockSel})</span>`);
      scrollBottom();
    });
    resetBtn.addEventListener('click', () => {
      Object.assign(work, DEFAULT_VIDEO_SETTINGS);
      lockSel = 'fps';
      audioBox.checked = work.audio !== false;
      refresh();
    });
    btnRow.appendChild(applyBtn);
    btnRow.appendChild(resetBtn);
    wrap.appendChild(btnRow);

    refresh();
    const bubble = addMessage('bot', '<strong>Video settings</strong> <span style="color:#475569">(image2video)</span>').parentElement.querySelector('.bubble');
    bubble.appendChild(wrap);
    scrollBottom();
    return;
  }

  if (cmd === '/alias-create') {
    const argStr = raw.slice('/alias-create'.length).trim();
    addMessage('user', escapeHtml(raw), raw);
    if (!argStr) {
      addMessage('bot', 'Usage: <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code><br>' +
        'e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code><br>' +
        'Quotes around the expansion are optional but useful when it contains special characters.');
      return;
    }
    const spaceIdx = argStr.indexOf(' ');
    if (spaceIdx === -1) {
      addMessage('bot', '<span style="color:#f87171">⚠ Provide both a word and an expansion — e.g. <code>/alias-create prophoto "Professional Photo, Medium format look"</code></span>');
      return;
    }
    const aliasFrom = argStr.slice(0, spaceIdx);
    let aliasTo = argStr.slice(spaceIdx + 1).trim();
    // Strip surrounding quotes
    if ((aliasTo.startsWith('"') && aliasTo.endsWith('"')) ||
        (aliasTo.startsWith("'") && aliasTo.endsWith("'"))) {
      aliasTo = aliasTo.slice(1, -1).trim();
    } else if (aliasTo.startsWith('"') || aliasTo.startsWith("'")) {
      aliasTo = aliasTo.slice(1).trim();
    }
    if (!aliasTo) {
      addMessage('bot', '<span style="color:#f87171">⚠ Expansion cannot be empty.</span>');
      return;
    }
    const bubble = addMessage('bot', '<div class="status-text">Saving alias…</div>').parentElement.querySelector('.bubble');
    fetch('/api/aliases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: aliasFrom, to: aliasTo }),
    })
    .then(parseJsonResponse)
    .then(data => {
      if (data.error) throw new Error(data.error);
      ALIASES[aliasFrom] = aliasTo;
      const verb = data.updated ? 'Updated' : 'Created';
      bubble.innerHTML = `${verb} alias: <code>${escapeHtml(aliasFrom)}</code> → <code>${escapeHtml(aliasTo)}</code>`;
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`;
      scrollBottom();
    });
    return;
  }

  if (cmd === '/alias-list') {
    addMessage('user', escapeHtml(raw), raw);
    const entries = Object.entries(ALIASES).sort(([a], [b]) => a.localeCompare(b));
    if (!entries.length) {
      addMessage('bot', 'No aliases defined. Use <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code> to create one.');
      return;
    }
    const bubble = addMessage('bot', '').parentElement.querySelector('.bubble');
    const header = document.createElement('strong');
    header.textContent = `Aliases (${entries.length}):`;
    const selList = document.createElement('div');
    selList.className = 'sel-list';
    selList.style.cssText = 'margin-top:8px;gap:4px';

    entries.forEach(([k, v]) => {
      const row = document.createElement('div');
      row.className = 'sel-row';

      const label = document.createElement('div');
      label.style.cssText = 'font-size:0.85rem;color:#94a3b8;flex:1;min-width:0;overflow-wrap:break-word';
      label.innerHTML = `<code>${escapeHtml(k)}</code> → <code>${escapeHtml(v)}</code>`;

      const delBtn = document.createElement('button');
      delBtn.className = 'sel-del-btn';
      delBtn.title = 'Delete alias';
      delBtn.innerHTML = '&#128465;&#xFE0E;';
      delBtn.addEventListener('click', e => {
        e.stopPropagation();
        delBtn.disabled = true;
        delBtn.style.opacity = '0.4';
        fetch('/api/aliases/' + encodeURIComponent(k), { method: 'DELETE' })
        .then(parseJsonResponse)
        .then(data => {
          if (data.error) throw new Error(data.error);
          delete ALIASES[k];
          row.remove();
          if (!selList.querySelector('.sel-row')) {
            bubble.innerHTML = 'No aliases defined. Use <code>/alias-create &lt;word&gt; &lt;expansion&gt;</code> to create one.';
          } else {
            header.textContent = `Aliases (${selList.querySelectorAll('.sel-row').length}):`;
          }
          scrollBottom();
        })
        .catch(err => {
          delBtn.disabled = false;
          delBtn.style.opacity = '';
          addMessage('bot', `<span style="color:#f87171">⚠ Delete failed: ${escapeHtml(err.message)}</span>`);
          scrollBottom();
        });
      });

      row.appendChild(label);
      row.appendChild(delBtn);
      selList.appendChild(row);
    });

    bubble.appendChild(header);
    bubble.appendChild(selList);
    scrollBottom();
    return;
  }

  addMessage('bot', `Unknown command <code>${escapeHtml(cmd)}</code> — type <code>/help</code> for available commands.`);
}

function createSlideshow(bubble, images) {
  let idx = 0;
  let timer = null;
  let paused = false;

  bubble.innerHTML = `
    <div class="slideshow">
      <div class="ss-img-wrap"><img class="ss-image" src="" alt="Generated image"></div>
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
  const counter  = bubble.querySelector('.ss-counter');
  const bar      = bubble.querySelector('.ss-bar');
  const wrap     = bubble.querySelector('.ss-img-wrap');
  const pauseBtn = bubble.querySelector('.ss-pause');

  function restartBar() {
    bar.style.animation = 'none';
    bar.offsetWidth;  // force reflow to restart CSS animation
    bar.style.animation = paused ? 'none' : '';
  }

  function show(i) {
    idx = ((i % images.length) + images.length) % images.length;
    img.src = images[idx];
    counter.textContent = `${idx + 1} / ${images.length}`;
    restartBar();
    clearTimeout(timer);
    if (!paused) timer = setTimeout(() => show(idx + 1), 3000);
    scrollBottom();
  }

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
          if (isFsActive()) fsBtn.click();
          bubble.innerHTML = 'All images deleted.';
          if (activeSlideshowCtrl === ctrl) activeSlideshowCtrl = null;
          return;
        }
        show(idx);  // same index now points at the next image
      }))
      .catch(err => {
        counter.textContent = '⚠ ' + err.message;
        clearTimeout(timer);
        if (!paused) timer = setTimeout(() => show(idx + 1), 3000);
      })
      .finally(() => { deleting = false; });
  }

  function togglePause() {
    paused = !paused;
    pauseBtn.innerHTML = paused ? '&#9654;' : '&#9646;&#9646;';
    pauseBtn.title     = paused ? 'Play' : 'Pause';
    if (paused) {
      clearTimeout(timer);
      bar.style.animationPlayState = 'paused';
    } else {
      bar.style.animationPlayState = 'running';
      timer = setTimeout(() => show(idx + 1), 3000);
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
  // Sync the ss-fs styling class + button icon with the actual fullscreen
  // state (native or faux). CSS targets .ss-fs rather than :fullscreen so
  // one unsupported prefixed selector can't invalidate the whole rule.
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
      // Faux fullscreen for iOS Safari
      const entering = !ssEl.classList.contains('ss-faux-fullscreen');
      ssEl.classList.toggle('ss-faux-fullscreen', entering);
      entering ? enterFauxFs(ssEl) : exitFauxFs(ssEl);
      syncFsState();
    }
  });
  // fullscreenchange is delivered to document (it bubbles there when fired
  // on the element), so listen there — listening on ssEl misses Escape exits.
  document.addEventListener('fullscreenchange', syncFsState);
  document.addEventListener('webkitfullscreenchange', syncFsState);

  // Touch swipe + tap-to-zoom (image has pointer-events:none so touches land on wrap)
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
    suppressClick = true; // always suppress the synthetic click that follows touchend
    if (Math.abs(dx) > 40) { navigate(dx < 0 ? 1 : -1); }
    else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) { openLightbox(img.src); }
  });
  // Desktop click (image has pointer-events:none so clicks land on wrap)
  wrap.addEventListener('click', () => {
    if (suppressClick) { suppressClick = false; return; }
    openLightbox(img.src);
  });

  show(0);
  const ctrl = { navigate, deleteCurrent };
  return ctrl;
}

function doUpload(file, bubble) {
  if (!file.name.toLowerCase().endsWith('.json')) {
    bubble.innerHTML = '<span style="color:#f87171">⚠ Please choose a <code>.json</code> file.</span>';
    return;
  }
  bubble.innerHTML = '<div class="status-text">Uploading…</div><div class="dots"><span></span><span></span><span></span></div>';
  scrollBottom();

  const fd = new FormData();
  fd.append('file', file);

  fetch('/api/upload-workflow', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      bubble.innerHTML = `Workflow <strong style="color:#a78bfa">${escapeHtml(data.name)}</strong> uploaded successfully. Use <code>/workflow</code> to select it.`;
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Upload failed: ${escapeHtml(err.message)}</span>`;
      scrollBottom();
    });
}

// URLs of images generated this session, oldest first. Used by /delete.
const sessionImages = [];

// url -> the generation prompt (raw, incl. <lora:…> tags) that produced it.
// Lets a face icon derive a default face-detail prompt from the image's own
// generation prompt when no explicit /face-detail-prompt has been set.
const imagePrompts = {};

// url -> { maskB64, prompt, denoise } for images that are an inpaint result (or
// the rejected original of one). Drives the per-image "re-run inpaint" button,
// which re-uploads the stored mask for a fresh single-use token and re-runs the
// same inpaint. The mask is captured client-side so it survives the server's
// single-use token consumption.
const imageMasks = {};

// url -> { action, audio } for images generated via /video-sequence, where Grok
// also produced an "action" (what happens in the video) and "audio" (what's said
// / heard) alongside the still-image prompt. Used to enrich the video prompt when
// the image is later turned into a video. Absent for plain /sequence images, in
// which case the video flow falls back to the bare original prompt.
const imageVideoMeta = {};

// Deletes an image file from the server's output folder. Resolves on
// success; a 404 also resolves since the file is already gone (e.g.
// deleted from a slideshow).
function deleteImageFile(url) {
  const filename = url.split('/').pop();
  return fetch('/api/images/' + encodeURIComponent(filename), { method: 'DELETE' })
    .then(r => r.json().then(data => {
      if (!r.ok && r.status !== 404) throw new Error(data.error || 'Delete failed');
    }));
}

// Removes every chat copy of an image and forgets it from session tracking.
function removeImageFromChat(url) {
  messagesEl.querySelectorAll('.img-wrap img, .img-wrap video').forEach(media => {
    if (media.getAttribute('src') === url) media.closest('.img-wrap').remove();
  });
  const i = sessionImages.indexOf(url);
  if (i !== -1) sessionImages.splice(i, 1);
  delete imagePrompts[url];
  delete imageMasks[url];
  delete imageVideoMeta[url];
}

// Build a default face-detail prompt from a generation prompt by keeping its
// <lora:…> tag(s) and a subject phrase, plus any facial expressions / emotions
// found in the prompt so the re-detailed face keeps the original mood. Returns
// null if no LoRA. Expressions are appended as comma-separated tags (how
// diffusion prompts read), so noun/adjective grammar mixing doesn't matter.

// Runs a face-detailer workflow over `image` using `prompt`. Driven by the
// per-image face icons. When `imgWrap` (the source image's .img-wrap) is given,
// the result is offered in place as a before/after slider with accept/reject
// instead of being appended to the timeline. Returns the generation promise so
// callers can re-enable their own controls when it settles.
function runFaceDetail(prompt, image, imgWrap) {
  iterationsFromSequence = false; // a face-detail run is a single image, not a sequence
  sendBtn.disabled = true;
  return runGeneration(prompt, '', null, {
    face: { image, workflow: currentFaceWorkflow || DEFAULT_FACE_WORKFLOW },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

// Runs an upscaler workflow over `image`. Shared by the /upscale command and the
// per-image "up" button. Takes no prompt. When `imgWrap` is given (the per-image
// button case) the result is offered in place as a before/after slider with
// accept/reject. Returns the generation promise so callers can re-enable their
// own controls when it settles.
function runUpscale(image, imgWrap) {
  iterationsFromSequence = false; // an upscale run is a single image, not a sequence
  sendBtn.disabled = true;
  return runGeneration('', '', null, {
    upscale: { image, workflow: currentUpscaleWorkflow || DEFAULT_UPSCALE_WORKFLOW },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}


// Runs an image2image workflow over `image` using `prompt`. Mirrors
// runFaceDetail. When `imgWrap` (the source image's .img-wrap) is given, the
// result is offered in place as a before/after slider. Returns the generation
// promise so callers can re-enable their own controls when it settles.
function runImage2Image(prompt, image, imgWrap) {
  iterationsFromSequence = false; // an image2image run is a single image, not a sequence
  sendBtn.disabled = true;
  return runGeneration(prompt, '', null, {
    image2image: { image, workflow: currentImage2ImageWorkflow || DEFAULT_IMAGE2IMAGE_WORKFLOW },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

// Runs an image2video workflow over `image` using `prompt`. Mirrors
// runImage2Image but uses the image2video endpoint (no denoise, no LoRA).
// Unlike image2image, the result is a video — a different artifact from the
// source image, not a refinement of it — so it is appended as a new item below
// rather than offered as an in-place before/after slider (which compares two
// images). The source image is left untouched.
function runImage2Video(prompt, image) {
  iterationsFromSequence = false;
  sendBtn.disabled = true;
  // If an end frame has been designated (🎞️) and it isn't the source image itself,
  // pass it as the <INPUT_LAST_FRAME> so the workflow interpolates start → end.
  const lastFrame = (lastFrameUrl && lastFrameUrl !== image) ? lastFrameUrl : null;
  return runGeneration(prompt, '', null, {
    image2video: { image, lastFrame, workflow: currentImage2VideoWorkflow || DEFAULT_IMAGE2VIDEO_WORKFLOW },
  })
    .finally(() => { sendBtn.disabled = false; });
}

// The image currently designated as the image2video end frame (<INPUT_LAST_FRAME>).
// There is a single last-frame slot, so this is one global selection; clicking the
// 🎞️ button on an image toggles it. null = no end frame (plain single-image i2v).
let lastFrameUrl = null;

function lastFrameButtonTitle(url) {
  return url === lastFrameUrl
    ? 'This image is the image2video end frame — click to unset'
    : 'Use this image as the image2video end frame (last frame)';
}

// Sync every 🎞️ button on the page to the current lastFrameUrl selection, so the
// designated end frame stays highlighted no matter which image it sits on.
function refreshLastFrameButtons() {
  document.querySelectorAll('.img-lastframe').forEach(b => {
    b.classList.toggle('active', lastFrameUrl !== null && b.dataset.url === lastFrameUrl);
    b.title = lastFrameButtonTitle(b.dataset.url);
  });
}

// Build the 🎞️ toggle that designates an image as the image2video end frame.
function makeLastFrameButton(url, extraClass) {
  const btn = document.createElement('button');
  btn.className = 'img-lastframe' + (extraClass ? ' ' + extraClass : '');
  btn.dataset.url = url;
  btn.innerHTML = '&#127902;&#xFE0E;';   // 🎞 film frames
  btn.title = lastFrameButtonTitle(url);
  if (url === lastFrameUrl) btn.classList.add('active');
  btn.addEventListener('click', e => {
    e.stopPropagation();
    lastFrameUrl = (lastFrameUrl === url) ? null : url;
    refreshLastFrameButtons();
  });
  return btn;
}

// Opens a full-screen mask editor over `imageUrl`. The user paints a translucent
// yellow mask; on "Apply Inpaint" the mask is exported as a binary PNG, uploaded
// to the server, and runInpaint() is called. `imgWrap` is the source .img-wrap
// for the in-place comparison slider (null in the review-grid case).
function openMaskEditor(imageUrl, imgWrap) {
  // Prevent opening a second editor while one is already active.
  if (document.getElementById('mask-editor-overlay')) return;

  // Capture the prompt at editor-open time so a concurrent /inpainting-prompt
  // command can't silently change what gets submitted.
  const capturedPrompt = lastInpaintingPrompt;

  // Set once closeEditor() is called so an in-flight upload doesn't launch a
  // job after the user has cancelled.
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
  // yellow mask always renders on top. It carries visible image content that becomes
  // the inpaint source when the user draws a hint.
  const drawCanvas = document.createElement('canvas');
  drawCanvas.id = 'mask-editor-draw-canvas';

  const canvas = document.createElement('canvas');
  canvas.id = 'mask-editor-canvas';

  wrap.appendChild(img);
  wrap.appendChild(drawCanvas);
  wrap.appendChild(canvas);
  overlay.appendChild(wrap);

  // Editable inpaint prompt, prefilled with the captured /inpainting-prompt so it can
  // be tweaked per-edit without leaving the editor. Changes here persist as the new
  // global inpainting prompt on Apply.
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

  // Tool group: switch between painting the inpaint mask (🩹) and drawing a colored
  // hint (✏️) onto the image. The color picker drives the pen colour.
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

  toolGroup.appendChild(maskToolBtn);
  toolGroup.appendChild(penToolBtn);
  toolGroup.appendChild(colorPicker);

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
  denoiseSlider.value = currentDenoise.inpaint.toFixed(2);
  denoiseSlider.className = 'mask-editor-denoise-slider';
  const denoiseValue = document.createElement('span');
  denoiseValue.className = 'mask-editor-denoise-value';
  denoiseValue.textContent = currentDenoise.inpaint.toFixed(2);
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
  applyBtn.disabled = true; // enabled once the image has loaded and been sized

  actions.appendChild(toolGroup);
  actions.appendChild(denoiseControl);
  actions.appendChild(clearBtn);
  actions.appendChild(cancelBtn);
  actions.appendChild(applyBtn);
  overlay.appendChild(actions);
  document.body.appendChild(overlay);

  const ctx = canvas.getContext('2d');
  const drawCtx = drawCanvas.getContext('2d');

  // CSS-pixel dimensions used for clearRect (after the DPR scale transform is active).
  let cssW = 0, cssH = 0;

  function syncCanvasSize() {
    if (!img.naturalWidth) return; // guard: broken/undecodable image
    const rect = img.getBoundingClientRect();
    cssW = rect.width;
    cssH = rect.height;
    for (const c of [canvas, drawCanvas]) {
      c.width  = Math.round(rect.width  * dpr);
      c.height = Math.round(rect.height * dpr);
      c.style.width  = rect.width  + 'px';
      c.style.height = rect.height + 'px';
      c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0); // absolute — safe to re-call on resize
    }
    canvas.style.opacity = denoiseSlider.value;
    applyBtn.disabled = false;
  }

  if (img.complete && img.naturalWidth) {
    syncCanvasSize();
  } else {
    img.addEventListener('load', syncCanvasSize, { once: true });
  }

  // Cache the bounding rect at pointerdown to avoid forced layout on every move.
  let painting = false;
  let cachedRect = null;
  // Last painted point, so consecutive pointermove events can be joined by a
  // continuous stroke rather than leaving gaps between isolated dabs (fast
  // movement otherwise produces a string of disconnected blobs along the path).
  let lastX = null, lastY = null;
  let brushRadius = 30;

  // Active tool: 'mask' paints the yellow inpaint mask, 'pen' draws a colour hint.
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

  function setTool(name) {
    tool = name;
    maskToolBtn.classList.toggle('is-active', name === 'mask');
    penToolBtn.classList.toggle('is-active', name === 'pen');
    // Tint the cursor border to the pen colour while drawing, white while masking.
    cursorEl.style.borderColor = name === 'pen' ? penColor : 'rgba(255,255,255,0.85)';
  }
  setTool('mask');

  penToolBtn.addEventListener('click', () => setTool('pen'));
  maskToolBtn.addEventListener('click', () => setTool('mask'));
  colorPicker.addEventListener('input', () => {
    penColor = colorPicker.value;
    setTool('pen'); // picking a colour implies drawing
  });

  const onResize = () => { cachedRect = null; };
  window.addEventListener('resize', onResize);

  // Sweep a thick round-capped line from the previous point so the stroke is a
  // solid, gap-free region. Also dab a circle so a single click still paints.
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
    lastX = lastY = null; // start a fresh stroke (no line from the previous one)
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

  // Clear only the active layer so resetting scribbles doesn't wipe the mask, and
  // vice-versa.
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
    if (!promptInput.value.trim()) {
      promptInput.focus();
      promptInput.classList.add('mask-editor-prompt-invalid');
      return;
    }
    applyBtn.disabled = true;
    applyBtn.textContent = 'Uploading…';

    // Derive a binary mask from the visual canvas: any painted pixel (alpha > 0)
    // becomes white; unpainted pixels become black. Then scale to the image's
    // natural pixel dimensions for ComfyUI.
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
    // Nearest-neighbour scaling keeps the mask strictly black/white. Bilinear
    // smoothing (the default) would interpolate edges into gray, partial-mask
    // values that ComfyUI renders as swirly artifacts along the painted region.
    offCtx.imageSmoothingEnabled = false;
    offCtx.drawImage(binaryCanvas, 0, 0, offscreen.width, offscreen.height);

    const b64 = offscreen.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');

    // If the user drew a hint, composite it onto the source image and upload the
    // result as a temporary inpaint source (consumed once). Detect any drawn pixel
    // by scanning the draw layer's alpha channel.
    const drawn = drawCtx.getImageData(0, 0, drawCanvas.width, drawCanvas.height).data;
    let hasDrawing = false;
    for (let i = 3; i < drawn.length; i += 4) {
      if (drawn[i] > 0) { hasDrawing = true; break; }
    }

    // Returns a Promise resolving to a draw token (or null when nothing was drawn).
    function uploadDrawing() {
      if (!hasDrawing) return Promise.resolve(null);
      const comp = document.createElement('canvas');
      comp.width  = img.naturalWidth;
      comp.height = img.naturalHeight;
      const compCtx = comp.getContext('2d');
      // Colour content — smoothing on is fine (unlike the strict B&W mask above).
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
      if (aborted) return; // user cancelled while upload was in-flight
      const capturedDenoise = parseFloat(denoiseSlider.value);
      // Use the (possibly edited) prompt and persist it as the new global default.
      const finalPrompt = promptInput.value.trim();
      lastInpaintingPrompt = finalPrompt || null;
      closeEditor();
      addMessage('user', `Inpaint: ${escapeHtml(finalPrompt)}`);
      runInpaint(imageUrl, maskToken, imgWrap, finalPrompt, capturedDenoise, b64, drawToken);
    })
    .catch(err => {
      if (aborted) return;
      applyBtn.disabled = false;
      applyBtn.textContent = 'Apply Inpaint';
      addMessage('bot', `<span style="color:#f87171">⚠ Mask upload failed: ${escapeHtml(err.message)}</span>`);
    });
  });

  function onKey(e) {
    // Don't hijack typing in the prompt field (e.g. the [ / ] brush-resize keys).
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

// Opens a mask editor over `newUrl` (image 2 / face-detail result) that lets
// the user paint which faces to keep from image 2. On "Apply", the browser
// composites image 1 (oldUrl) as the base with image 2 pixels only where the
// mask was painted, uploads the result via /api/save-image, and calls
// onComposite(compositeUrl). Never sends anything to ComfyUI.
function openCompositeEditor(oldUrl, newUrl, onComposite) {
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

    // Convert the painted canvas to a binary mask at display resolution
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

    // Scale mask to the natural image resolution (nearest-neighbour keeps it binary)
    const maskCanvas = document.createElement('canvas');
    maskCanvas.width  = natW;
    maskCanvas.height = natH;
    const maskCtx = maskCanvas.getContext('2d');
    maskCtx.imageSmoothingEnabled = false;
    maskCtx.drawImage(binaryCanvas, 0, 0, natW, natH);
    const maskData = maskCtx.getImageData(0, 0, natW, natH);

    // Draw image 2 (already loaded in `img`) to an offscreen canvas
    const img2Canvas = document.createElement('canvas');
    img2Canvas.width  = natW;
    img2Canvas.height = natH;
    const img2Ctx = img2Canvas.getContext('2d');
    img2Ctx.drawImage(img, 0, 0, natW, natH);
    const img2Data = img2Ctx.getImageData(0, 0, natW, natH);

    // Load image 1 (original) and composite
    const img1El = new Image();
    img1El.onload = () => {
      const img1Canvas = document.createElement('canvas');
      img1Canvas.width  = natW;
      img1Canvas.height = natH;
      const img1Ctx = img1Canvas.getContext('2d');
      img1Ctx.drawImage(img1El, 0, 0, natW, natH);
      const img1Data = img1Ctx.getImageData(0, 0, natW, natH);

      // Where mask is white → take image 2 pixel; otherwise keep image 1 pixel
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

// Runs an inpainting workflow over `image` using `mask` (a server token) and
// `prompt`. When `imgWrap` (the source .img-wrap) is given the result is offered
// in place as a before/after slider. Returns the generation promise.
// `maskB64` (the raw mask PNG) is retained so the result's "re-run inpaint"
// button can re-upload the same mask for a fresh single-use token.
function runInpaint(image, mask, imgWrap, prompt, denoise, maskB64, drawToken) {
  iterationsFromSequence = false;
  sendBtn.disabled = true;
  return runGeneration(prompt || '', '', null, {
    inpaint: { image, mask, workflow: currentInpaintingWorkflow || DEFAULT_INPAINTING_WORKFLOW, denoise, maskB64, prompt, drawToken },
    sliderReplace: imgWrap || null,
  })
    .finally(() => { sendBtn.disabled = false; });
}

function runDoOver(url, imgWrap) {
  const prompt = imagePrompts[url] || '';
  return runGeneration(prompt, '', null, { replaceWrap: imgWrap, preserveMtimeFrom: url });
}

// Builds a before/after comparison slider for a face-detail or upscale result.
// `oldUrl` (the source image) shows on the left, `newUrl` (the result) on the
// right; dragging the handle wipes between them. A ✓/✗ row underneath calls
// `onAccept`/`onReject`. The optional `onComposite(compositeUrl, sliderEl)`
// enables the 🩹 button, which opens a mask editor so the user can paint which
// parts of image 2 to apply to image 1 (useful for keeping only one face from a
// face-detail result). Returns a single container node.
function buildComparisonSlider(oldUrl, newUrl, onAccept, onReject, onComposite) {
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

  // Corner badges so it's obvious which side is which: 1 = original (left),
  // 2 = edited result (right). The numbered buttons below match these.
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

  // 1 keeps the original (discards the edit); 2 keeps the edited result.
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

    // Size slider to fill viewport while preserving aspect ratio.
    // `before` is already loaded in the in-page slider so naturalWidth is available.
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
        // Delegate to the inline mask button logic via the inline container
        // by re-opening the composite editor in the non-modal context.
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

// Appends a generated image to a bubble with a trash-icon overlay (top-right,
// deletes from chat + output folder) and a face-icon overlay (bottom-right,
// runs face detail over this image using the /face-detail-prompt override or a
// prompt derived from this image's own generation prompt).
function appendChatImage(container, url) {
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';

  const media = createMediaElement(url, { autoplay: true });

  // Video results can't be re-fed into the image-input ops (face-detail,
  // upscale, do-over, image2image, inpaint, image2video), so those overlays are
  // image-only; a video keeps the delete button plus a scissors button that
  // cuts its last frame into the chat as an image (for last-frame continuity).
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

  const img = media;  // an <img> for the image path; named `img` for the overlays below

  const face = document.createElement('button');
  face.className = 'img-face';
  face.title = 'Run face detail';
  face.innerHTML = '&#128100;&#xFE0E;';
  face.addEventListener('click', e => {
    e.stopPropagation();
    if (face.disabled || sendBtn.disabled) return;
    const prompt = lastFaceDetailPrompt || deriveFaceDetailPrompt(imagePrompts[url]);
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
    let prompt;
    if (image2imageOverridePrompt) {
      prompt = image2imageOverridePrompt;
    } else {
      const orig = imagePrompts[url];
      if (!orig) {
        addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
        return;
      }
      prompt = applyReplacements(orig, image2imageReplacements);
    }
    i2i.disabled = true;
    addMessage('user', 'Image2image: ' + escapeHtml(prompt), prompt);
    runImage2Image(prompt, url, wrap).finally(() => { i2i.disabled = false; });
  });

  const inpaintBtn = document.createElement('button');
  inpaintBtn.className = 'img-inpaint';
  inpaintBtn.title = 'Inpaint';
  inpaintBtn.textContent = '🩹';
  inpaintBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (inpaintBtn.disabled || sendBtn.disabled) return;
    openMaskEditor(url, wrap);
  });

  const i2v = document.createElement('button');
  i2v.className = 'img-i2v';
  i2v.title = i2vTooltip(imageVideoMeta[url]);
  i2v.innerHTML = '&#127916;&#xFE0E;';
  i2v.addEventListener('click', e => {
    e.stopPropagation();
    if (i2v.disabled || sendBtn.disabled) return;
    let prompt;
    if (image2videoOverridePrompt) {
      prompt = image2videoOverridePrompt;
    } else {
      const orig = imagePrompts[url];
      if (!orig) {
        addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2video-set-prompt &lt;prompt&gt;</code></span>');
        return;
      }
      prompt = buildVideoPrompt(applyReplacements(orig, image2videoReplacements), imageVideoMeta[url], currentVideoSettings.audio);
    }
    i2v.disabled = true;
    addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
    runImage2Video(prompt, url).finally(() => { i2v.disabled = false; });
  });

  const editMeta = document.createElement('button');
  editMeta.className = 'img-edit-meta';
  editMeta.title = 'Edit metadata (prompt / action / audio)';
  editMeta.innerHTML = '&#9998;&#xFE0E;';   // ✎ pencil
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

  // Re-run inpaint: only present once this image has an associated mask (i.e.
  // it is itself an inpaint result, or the rejected original of one). Sits just
  // below the inpaint button. Re-uploads the stored mask for a fresh single-use
  // token, then re-runs the same inpaint, yielding another 1/2 comparison.
  const maskCtx = imageMasks[url];
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

// Renders the last /sequence (or /video-sequence) run into `bubble` as a grid of
// prompt rows, each with a ▶ button that generates an image from that prompt.
// For a /video-sequence run, the action and audio lines are shown too, and the
// generated image carries them as videoMeta so its 🎬 video button folds them in.
function renderSequenceReview(bubble, seq) {
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
      // Carry action/audio only for a video sequence, so the resulting image's
      // 🎬 button can fold them into the video prompt later.
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

    // Show action/audio for a video sequence (only the lines that are present).
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
// thumb opens the lightbox; the trash button deletes from the output folder
// (and removes every chat copy via removeImageFromChat).
function renderReviewGrid(bubble, urls) {
  bubble.innerHTML = '';
  const grid = document.createElement('div');
  grid.className = 'review-grid';

  urls.forEach(url => {
    const cell = document.createElement('div');
    cell.className = 'review-thumb';

    const isVideo = isVideoUrl(url);
    const media = createMediaElement(url);
    if (!isVideo) media.addEventListener('click', () => openLightbox(url));
    const img = media;  // named `img` for the shared appends/overlays below

    const del = document.createElement('button');
    del.className = 'img-del review-del';   // reuse existing img-del styling
    del.title = 'Delete image';
    del.innerHTML = '&#128465;&#xFE0E;';
    del.addEventListener('click', e => {
      e.stopPropagation();
      if (del.disabled) return;
      del.disabled = true;
      deleteImageFile(url)
        .then(() => {
          removeImageFromChat(url);  // clears chat copies + sessionImages
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

    // A video thumbnail keeps only the delete button — the rest are image-input ops.
    if (isVideo) {
      cell.appendChild(media);
      cell.appendChild(del);
      grid.appendChild(cell);
      return;
    }

    const face = document.createElement('button');
    face.className = 'img-face review-face';   // reuse existing img-face styling
    face.title = 'Run face detail';
    face.innerHTML = '&#128100;&#xFE0E;';
    face.addEventListener('click', e => {
      e.stopPropagation();
      if (face.disabled || sendBtn.disabled) return;
      const prompt = lastFaceDetailPrompt || deriveFaceDetailPrompt(imagePrompts[url]);
      if (!prompt) {
        addMessage('bot', '<span style="color:#f87171">No LoRA in this image’s prompt — set one with <code>/face-detail-prompt &lt;prompt&gt;</code></span>');
        return;
      }
      face.disabled = true;
      addMessage('user', 'Face detail: ' + escapeHtml(prompt));
      runFaceDetail(prompt, url).finally(() => { face.disabled = false; });
    });

    const up = document.createElement('button');
    up.className = 'img-up review-up';   // reuse existing img-up styling
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
      if (image2imageOverridePrompt) {
        prompt = image2imageOverridePrompt;
      } else {
        const orig = imagePrompts[url];
        if (!orig) {
          addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2image-set-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        prompt = applyReplacements(orig, image2imageReplacements);
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
      openMaskEditor(url, null);
    });

    const ri2v = document.createElement('button');
    ri2v.className = 'img-i2v review-i2v';
    ri2v.title = i2vTooltip(imageVideoMeta[url]);
    ri2v.innerHTML = '&#127916;&#xFE0E;';
    ri2v.addEventListener('click', e => {
      e.stopPropagation();
      if (ri2v.disabled || sendBtn.disabled) return;
      let prompt;
      if (image2videoOverridePrompt) {
        prompt = image2videoOverridePrompt;
      } else {
        const orig = imagePrompts[url];
        if (!orig) {
          addMessage('bot', '<span style="color:#f87171">No original prompt for this image — set one with <code>/image2video-set-prompt &lt;prompt&gt;</code></span>');
          return;
        }
        prompt = buildVideoPrompt(applyReplacements(orig, image2videoReplacements), imageVideoMeta[url], currentVideoSettings.audio);
      }
      ri2v.disabled = true;
      addMessage('user', 'Image2video: ' + escapeHtml(prompt), prompt);
      runImage2Video(prompt, url).finally(() => { ri2v.disabled = false; });
    });

    cell.appendChild(img);
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

// Renders a draggable grid of this session's videos for /composite-videos-session.
// Each thumbnail is a playable <video>; the ⠿ handle reorders cells (pointer-based,
// so it works with both mouse and touch); the ✓ button joins them, in the shown
// order, into a single clip that lands at the bottom of the chat.
function renderCompositeGrid(bubble, urls) {
  bubble.innerHTML = '';

  const hint = document.createElement('div');
  hint.className = 'composite-hint';
  hint.textContent = 'Drag the ⠿ handle to reorder, play each to preview, then press ✓ to join them into one clip.';
  bubble.appendChild(hint);

  let order = urls.slice();
  const cells = new Map();   // url -> cell element

  const grid = document.createElement('div');
  grid.className = 'composite-grid';

  function renderBadges() {
    order.forEach((url, i) => {
      const cell = cells.get(url);
      if (cell) cell.querySelector('.composite-order').textContent = String(i + 1);
    });
  }

  // Re-append cells in `order` (moving a DOM node re-orders it) and renumber.
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

  let dragging = null;      // the cell currently being dragged
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

    const video = createMediaElement(url);   // <video controls loop playsInline>
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

// POSTs the ordered video URLs to the server, which ffmpeg-concatenates them into
// a single clip; the result is dropped at the bottom of the chat like any other
// generated video (and tracked in sessionImages so it appears in reviews).
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
      sessionImages.push(data.url);
      appendChatImage(bubble, data.url);
      scrollBottom();
    })
    .catch(err => {
      bubble.innerHTML = `<span style="color:#f87171">⚠ Could not composite videos: ${escapeHtml(err.message)}</span>`;
    });
}

// ---------------------------------------------------------------------------
// Session save / restore helpers
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
  history.length = 0;
  historyIdx = -1;
  savedDraft = '';
  sessionImages.length = 0;
  for (const k of Object.keys(imagePrompts)) delete imagePrompts[k];
  for (const k of Object.keys(imageVideoMeta)) delete imageVideoMeta[k];
  lastSequence = null;
  fauxFullscreenEls.clear();
  document.body.style.overflow = '';
  messagesEl.innerHTML = '';

  const s = data.settings || {};
  if (s.server              !== undefined) currentServer             = s.server;
  if (s.workflow            !== undefined) currentWorkflow           = s.workflow;
  if (s.faceWorkflow        !== undefined) currentFaceWorkflow       = s.faceWorkflow;
  if (s.upscaleWorkflow     !== undefined) currentUpscaleWorkflow    = s.upscaleWorkflow;
  if (s.image2imageWorkflow !== undefined) currentImage2ImageWorkflow = s.image2imageWorkflow;
  if (s.image2videoWorkflow !== undefined) currentImage2VideoWorkflow = s.image2videoWorkflow;
  if (s.inpaintingWorkflow  !== undefined) currentInpaintingWorkflow  = s.inpaintingWorkflow;
  if (s.resolution          !== undefined) currentResolution         = s.resolution;
  if (s.generationSteps     !== undefined) currentGenerationSteps    = s.generationSteps;
  if (s.iterations          !== undefined) iterations                = s.iterations;
  if (s.sequenceReplacements    !== undefined) sequenceReplacements    = s.sequenceReplacements;
  if (s.image2imageReplacements !== undefined) image2imageReplacements = s.image2imageReplacements;
  if (s.image2imageOverridePrompt !== undefined) image2imageOverridePrompt = s.image2imageOverridePrompt;
  if (s.image2videoReplacements !== undefined) image2videoReplacements = s.image2videoReplacements;
  if (s.image2videoOverridePrompt !== undefined) image2videoOverridePrompt = s.image2videoOverridePrompt;
  if (s.lastFaceDetailPrompt    !== undefined) lastFaceDetailPrompt    = s.lastFaceDetailPrompt;
  if (s.lastInpaintingPrompt    !== undefined) lastInpaintingPrompt    = s.lastInpaintingPrompt;
  if (s.currentDenoise          !== undefined) currentDenoise          = { ...DEFAULT_DENOISE, ...s.currentDenoise };
  if (s.videoSettings           !== undefined) currentVideoSettings    = { ...DEFAULT_VIDEO_SETTINGS, ...s.videoSettings };
  if (s.videoLock               !== undefined) videoLock               = s.videoLock;
  iterationsFromSequence = false;
  updateHeaderStatus();

  for (const url of (data.sessionImages || [])) sessionImages.push(url);
  Object.assign(imagePrompts, data.imagePrompts || {});
  Object.assign(imageVideoMeta, data.imageVideoMeta || {});
  lastSequence = data.lastSequence || null;
  // Restore up/down recall history (prompts, sequences and slash commands).
  for (const entry of (data.promptHistory || [])) history.push(entry);

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
// Send message
// ---------------------------------------------------------------------------

function sendMessage() {
  let raw = inputEl.value.trim();

  // A command is awaiting y/n — consume this message as the answer.
  if (pendingConfirm) {
    if (!raw) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    historyIdx = -1;
    savedDraft = '';
    addMessage('user', escapeHtml(raw), null);
    const cb = pendingConfirm;
    pendingConfirm = null;
    cb(raw);
    return;
  }

  if (!raw && history.length) raw = history[0]; // empty prompt redoes the last one
  if (!raw || sendBtn.disabled) return;

  if (raw.startsWith('/')) {
    inputEl.value = '';
    inputEl.style.height = 'auto';
    // Slash commands (including /sequence) join the up/down recall history, just
    // like plain prompts. Skip duplicates of the most recent entry.
    if (history[0] !== raw) history.unshift(raw);
    historyIdx = -1;
    savedDraft = '';
    handleSlashCommand(raw);
    return;
  }

  // Push to front of history, skip duplicates of the most recent entry
  if (history[0] !== raw) history.unshift(raw);
  historyIdx = -1;
  savedDraft = '';

  // A /sequence borrowed `iterations` as its prompt count. A plain prompt isn't
  // a sequence, so reset to 1 before generating — otherwise it would silently
  // produce a whole sequence's worth of images.
  if (iterationsFromSequence) {
    iterations = 1;
    iterationsFromSequence = false;
  }

  // Expand any aliases that weren't caught by the real-time trigger (e.g. no
  // trailing space before Enter was pressed).
  raw = expandAliases(raw, ALIASES);

  addMessage('user', escapeHtml(raw), raw);
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;

  (async () => {
    for (let i = 0; i < iterations; i++) {
      const label = iterations > 1 ? ` (${i + 1}/${iterations})` : '';
      const ok = await runGeneration(raw, label);
      if (!ok) break;   // a failed run would likely just fail again
    }
    sendBtn.disabled = false;
  })();
}

// Runs a Grok prompt-sequence job (the slow call that expands a master prompt
// into many prompts). Mirrors runGeneration's lifecycle: the job is tracked
// server-side, watched over SSE, and cancellable with the same ✕ button on the
// status bubble. Resolves with the `done` message ({prompts, video}) on success,
// or null if the job was cancelled or errored — the reason is rendered in the
// bubble. Never rejects.
function runSequenceJob(endpoint, master, count, statusBubble) {
  return new Promise(resolve => {
    const statusText = statusBubble.querySelector('.status-text');

    // ✕ cancel button — enabled once we have a job_id, removed when the job ends.
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
      body: JSON.stringify({ prompt: master, count, replacements: sequenceReplacements }),
    })
    .then(parseJsonResponse)
    .then(data => {
      if (data.error) throw new Error(data.error);

      // Now we have a job_id, the ✕ can actually cancel it.
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

// Runs one generation job in its own bot bubble. Resolves true on success,
// false on any failure — it never rejects; errors are rendered in the bubble.
function runGeneration(raw, label, workflowOverride, opts = {}) {
  return new Promise(resolve => {
  const face = opts.face || null;
  const upscale = opts.upscale || null;
  const image2image = opts.image2image || null;
  const image2video = opts.image2video || null;
  const inpaint = opts.inpaint || null;
  const videoMeta = opts.videoMeta || null;
  const replaceWrap = opts.replaceWrap || null;
  const sliderReplace = opts.sliderReplace || null;
  const preserveMtimeFrom = opts.preserveMtimeFrom || null;
  // Either in-place flow (do-over or before/after slider) edits an existing
  // image rather than appending a new one, so the progress bubble belongs
  // beside that image, not at the bottom of the chat.
  const inPlaceWrap = sliderReplace || replaceWrap;
  const job = face || upscale || image2image || image2video || inpaint; // an image-input job vs a plain generation
  const endpoint = face ? '/api/face-detail'
                 : upscale ? '/api/upscale'
                 : image2image ? '/api/image2image'
                 : image2video ? '/api/image2video'
                 : inpaint ? '/api/inpaint'
                 : '/api/generate';
  const botBubble = addMessage('bot', `
    <div class="status-text" id="status-line">Connecting…${label}</div>
    <div class="dots"><span></span><span></span><span></span></div>
    <div class="progress-bar-wrap"><div class="progress-bar"></div></div>
  `);

  const statusLine = botBubble.querySelector('#status-line');
  const dotsEl     = botBubble.querySelector('.dots');
  const barWrap    = botBubble.querySelector('.progress-bar-wrap');

  // Wall-clock start, used to report generation time when the job completes.
  const startTime = Date.now();

  // For an in-place edit (do-over, or face-detail / upscale slider), move the
  // progress bubble from the bottom of the chat to directly beneath the image
  // being edited, so the user can watch progress without leaving the comparison.
  if (inPlaceWrap && inPlaceWrap.parentNode) {
    const srcMessage = inPlaceWrap.closest('.message');
    if (srcMessage) srcMessage.after(botBubble.parentElement);
    // addMessage() scrolled to the bottom when it appended the progress bubble;
    // undo that by bringing the image being edited back into view so it (and the
    // progress bubble now beneath it) stay visible throughout the job.
    inPlaceWrap.scrollIntoView({ block: 'center' });
  }

  // ✕ cancel button — enabled once we have a job_id, removed when the job
  // ends (done/error/cancelled). Lives on the bubble (not the status line,
  // whose textContent is rewritten on every progress update).
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'cancel-btn';
  cancelBtn.title = 'Cancel this job';
  cancelBtn.textContent = '✕';
  cancelBtn.disabled = true;
  botBubble.appendChild(cancelBtn);

  // Face-detail and upscale run their own workflow over a source image; a plain
  // generation uses the selected/override workflow and the current resolution.
  const wf = job ? job.workflow : (workflowOverride || currentWorkflow);
  fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt: raw,
      ...(currentServer     ? { server: currentServer.address, server_os: currentServer.os } : {}),
      ...(wf ? { workflow: wf } : {}),
      ...(!job && currentResolution ? { width: currentResolution.width, height: currentResolution.height } : {}),
      ...(!job && currentGenerationSteps !== null ? { steps: currentGenerationSteps } : {}),
      ...(job ? { image: job.image } : {}),
      ...(inpaint ? { mask: inpaint.mask } : {}),
      ...(inpaint && inpaint.drawToken ? { draw_token: inpaint.drawToken } : {}),
      ...(face        ? { denoise: currentDenoise.face } : {}),
      ...(upscale     ? { denoise: currentDenoise.upscale } : {}),
      ...(image2image ? { denoise: currentDenoise.image2image } : {}),
      ...(image2video ? { duration: currentVideoSettings.duration, frames: currentVideoSettings.frames, fps: currentVideoSettings.fps } : {}),
      ...(image2video && image2video.lastFrame ? { last_frame: image2video.lastFrame } : {}),
      ...(inpaint     ? { denoise: inpaint.denoise != null ? inpaint.denoise : currentDenoise.inpaint } : {}),
      ...(preserveMtimeFrom ? { preserve_mtime_from: preserveMtimeFrom } : {}),
    }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) throw new Error(data.error);

    // Now we have a job_id, the ✕ can actually cancel it.
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
        // The prompt to remember for this image. For an image-input job
        // (face-detail, upscale or image2image) we inherit the *source image's*
        // original generation prompt — not `raw`, which for face-detail is the
        // derived face-detail prompt. This keeps the result's do-over
        // regenerating from the original prompt (not the face/i2i prompt) and
        // lets a face icon on the result re-derive cleanly. A plain generation
        // just uses its own prompt.
        const originPrompt = job ? (imagePrompts[job.image] || '') : (raw || '');
        // Video metadata (action/audio from /video-sequence) follows the same
        // inheritance: a directly-passed videoMeta for a fresh generation, else
        // the source image's metadata for an image-input job (do-over,
        // face-detail, upscale, i2i) so the result keeps a working video button.
        const originVideoMeta = videoMeta || (job ? imageVideoMeta[job.image] : null);

        // In-place before/after slider for a single-image face-detail / upscale
        // result: replace the source .img-wrap with a comparison slider and let
        // the user accept (keep new, delete original) or reject (keep original,
        // delete new). The result stays provisional — it is not tracked in
        // sessionImages/imagePrompts until accepted.
        if (sliderReplace && sliderReplace.parentNode && msg.images.length === 1) {
          const oldUrl = sliderReplace.querySelector('img').getAttribute('src');
          const newUrl = msg.images[0];
          const onAccept = sliderEl => {
            deleteImageFile(oldUrl).catch(() => {});
            const idx = sessionImages.indexOf(oldUrl);
            if (idx !== -1) sessionImages.splice(idx, 1, newUrl);
            else sessionImages.push(newUrl);
            delete imagePrompts[oldUrl];
            delete imageVideoMeta[oldUrl];
            if (originPrompt) imagePrompts[newUrl] = originPrompt;
            if (originVideoMeta) imageVideoMeta[newUrl] = originVideoMeta;
            // Carry the mask onto the kept inpaint result so it can be re-run;
            // the old image is gone, so drop its (stale) mask. Non-inpaint ops
            // (face/upscale/i2i) clear it too — their result has new pixels the
            // old mask no longer matches.
            delete imageMasks[oldUrl];
            if (inpaint && inpaint.maskB64) {
              imageMasks[newUrl] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
            }
            const tmp = document.createElement('div');
            appendChatImage(tmp, newUrl);
            sliderEl.replaceWith(tmp.firstChild);
          };
          const onReject = sliderEl => {
            deleteImageFile(newUrl).catch(() => {});
            // Keep the rejected inpaint's mask on the restored original so it
            // can be re-run for another attempt. (Non-inpaint ops leave any
            // existing mask on oldUrl untouched.)
            if (inpaint && inpaint.maskB64) {
              imageMasks[oldUrl] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
            }
            const tmp = document.createElement('div');
            appendChatImage(tmp, oldUrl);
            sliderEl.replaceWith(tmp.firstChild);
          };
          // For face-detail results, offer selective compositing: the user paints
          // which faces from image 2 to apply to image 1, so only chosen faces get
          // the detailer treatment. Not shown for non-face ops (upscale, i2i) where
          // the concept doesn't apply.
          const onComposite = face ? (compositeUrl, sliderEl) => {
            deleteImageFile(oldUrl).catch(() => {});
            deleteImageFile(newUrl).catch(() => {});
            const idx = sessionImages.indexOf(oldUrl);
            if (idx !== -1) sessionImages.splice(idx, 1, compositeUrl);
            else sessionImages.push(compositeUrl);
            delete imagePrompts[oldUrl];
            delete imageMasks[oldUrl];
            delete imageVideoMeta[oldUrl];
            if (originPrompt) imagePrompts[compositeUrl] = originPrompt;
            if (originVideoMeta) imageVideoMeta[compositeUrl] = originVideoMeta;
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
          if (originPrompt) imagePrompts[url] = originPrompt;
          // Remember Grok's action/audio against this image so a later video
          // generation can fold them into the video prompt.
          if (originVideoMeta) imageVideoMeta[url] = originVideoMeta;
          // Inpaint result added directly (no in-place slider, e.g. launched
          // from the lightbox/review grid): record its mask so it too gets a
          // "re-run inpaint" button.
          if (inpaint && inpaint.maskB64) {
            imageMasks[url] = { maskB64: inpaint.maskB64, prompt: inpaint.prompt, denoise: inpaint.denoise };
          }
          if (i === 0 && replaceWrap && replaceWrap.parentNode) {
            const oldImg = replaceWrap.querySelector('img');
            const oldSrc = oldImg ? oldImg.getAttribute('src') : null;
            if (oldSrc) {
              deleteImageFile(oldSrc).catch(() => {});  // discard the old file from disk so it doesn't linger in /review-session
              delete imagePrompts[oldSrc];
              delete imageVideoMeta[oldSrc];
            }
            // Replace the old image in-place in sessionImages so /review-session
            // keeps its position rather than moving the regenerated image to the end.
            const oldIdx = oldSrc ? sessionImages.indexOf(oldSrc) : -1;
            if (oldIdx !== -1) sessionImages.splice(oldIdx, 1, url);
            else sessionImages.push(url);
            const tmp = document.createElement('div');
            appendChatImage(tmp, url);
            replaceWrap.replaceWith(tmp.firstChild);
          } else {
            sessionImages.push(url);
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
      // 'tick' and 'ping' need no UI update
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

