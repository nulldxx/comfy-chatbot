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
  { cmd: '/archive-all',     desc: 'archive every image to the encrypted volume (optional folder name)',     args: ' ' },
  { cmd: '/archive-session', desc: 'archive all images from this session (optional folder name)',            args: ' ' },
  { cmd: '/archive-today',   desc: 'archive images generated today (optional folder name)',                  args: ' ' },
  { cmd: '/clear',      desc: 'clear the chat history',             args: ''  },
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
  { cmd: '/iterations', desc: 'set images generated per prompt',    args: ' ' },
  { cmd: '/lora',       desc: 'fuzzy-find a LoRA to insert',        args: ' ' },
  { cmd: '/multi',      desc: 'generate images for multiple prompts (one per line)', args: '\n' },
  { cmd: '/purge',      desc: 'free GPU memory on active server',   args: ''  },
  { cmd: '/resolution', desc: 'set output resolution (e.g. 640x480 or phone)', args: ' ' },
  { cmd: '/review',         desc: 'grid of the last N images, oldest first', args: ' ' },
  { cmd: '/review-all',     desc: 'grid of every image (tap to view, trash to delete)', args: '' },
  { cmd: '/review-session', desc: 'grid of this session\'s images (tap to view, trash to delete)', args: '' },
  { cmd: '/review-today',   desc: 'grid of today\'s images (tap to view, trash to delete)', args: '' },
  { cmd: '/sequence',   desc: 'generate a prompt sequence from a master prompt (Grok)', args: ' ' },
  { cmd: '/sequence-replacement', desc: 'add a find→replace applied to Grok prompts', args: ' ' },
  { cmd: '/server',     desc: 'choose a ComfyUI server',            args: ''  },
  { cmd: '/slideshow',         desc: 'browse the last N images, oldest first',  args: ' ' },
  { cmd: '/slideshow-all',     desc: 'browse every image, oldest first',       args: '' },
  { cmd: '/slideshow-reverse', desc: 'browse every image, newest first',       args: '' },
  { cmd: '/slideshow-session', desc: 'browse this session\'s images',          args: '' },
  { cmd: '/slideshow-today',   desc: 'browse today\'s images, oldest first',   args: '' },
  { cmd: '/upload',     desc: 'upload a new workflow JSON file',    args: ''  },
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

// Parse a fetch Response as JSON, but degrade gracefully when the body isn't
// JSON at all (e.g. a gunicorn/proxy timeout page or an empty body). Without
// this, r.json() throws the cryptic "unexpected character at line 1 column 1".
async function parseJsonResponse(r) {
  const text = await r.text();
  try {
    return JSON.parse(text);
  } catch (e) {
    const snippet = text.trim().slice(0, 120);
    throw new Error(
      r.ok
        ? `Server returned a non-JSON response${snippet ? ': ' + snippet : ''}`
        : `Request failed (HTTP ${r.status})${snippet ? ': ' + snippet : ''}`
    );
  }
}

// Subsequence fuzzy match: every query char must appear in order.
// Returns a score (higher = better) or -1 for no match.
function fuzzyScore(query, text) {
  query = query.toLowerCase();
  text  = text.toLowerCase();
  if (!query) return 0;
  let score = 0, from = 0, last = -2;
  for (const ch of query) {
    const idx = text.indexOf(ch, from);
    if (idx === -1) return -1;
    score += (idx === last + 1) ? 3 : 1;  // reward consecutive runs
    if (idx === 0) score += 2;            // reward matching the start
    last = idx;
    from = idx + 1;
  }
  return score;
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
let lastFaceDetailPrompt = null; // global override set by /face-detail-prompt; takes priority over per-image derivation
let currentResolution = { width: 1365, height: 768 };  // {width, height} or null (null = workflow default); defaults to 16:9
let iterations        = 1;     // images generated per prompt (set via /iterations)
let iterationsFromSequence = false; // true while `iterations` is borrowed as a /sequence count; reset to 1 on the next non-sequence prompt
let sequenceReplacements = []; // [from, to] pairs applied to /sequence prompts

// Auto-purge of idle GPU memory is handled server-side (see app.py),
// so it fires even after the browser is closed.

function updateHeaderStatus() {
  const srv = currentServer  ? currentServer.name  : DEFAULT_SERVER;
  const wf  = currentWorkflow ? currentWorkflow     : DEFAULT_WORKFLOW;
  document.getElementById('header-status').textContent = `${srv}  ·  ${wf}`;
}
updateHeaderStatus();

// Auto-resize textarea + slash autocomplete
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
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
        addMessage('user', escapeHtml(prompt), prompt);
        const ok = await runGeneration(prompt, '');
        if (!ok) break;
      }
      sendBtn.disabled = false;
    })();
    return;
  }

  if (cmd === '/sequence') {
    const master = raw.slice('/sequence'.length).trim();
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
    fetch('/api/sequence', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: master, count, replacements: sequenceReplacements }),
    })
    .then(parseJsonResponse)
    .then(async data => {
      if (data.error) throw new Error(data.error);
      const prompts = data.prompts || [];
      statusBubble.innerHTML = `<div class="status-text">Grok returned <strong style="color:#a78bfa">${prompts.length}</strong> prompt(s) — generating one after another…</div>`;
      scrollBottom();
      // Generate each prompt sequentially, exactly like /multi.
      for (const prompt of prompts) {
        addMessage('user', escapeHtml(prompt), prompt);
        const ok = await runGeneration(prompt, '');
        if (!ok) break;
      }
      sendBtn.disabled = false;
    })
    .catch(err => {
      statusBubble.innerHTML = `<span style="color:#f87171">⚠ ${escapeHtml(err.message)}</span>`;
      sendBtn.disabled = false;
    });
    return;
  }

  if (cmd === '/sequence-replacement') {
    addMessage('user', escapeHtml(raw), raw);
    if (!parts[1]) {
      if (!sequenceReplacements.length) {
        addMessage('bot', `No sequence replacements set.<br>Usage: <code>/sequence-replacement &lt;from&gt; &lt;to&gt;</code> — the first word is the text to find, the rest is what to replace it with. Applied to every prompt <code>/sequence</code> gets back from Grok.<br><code>/sequence-replacement clear</code> removes them all.`);
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
        <div style="font-size:0.85rem;color:#94a3b8"><code>/server</code> — choose a ComfyUI server</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/addserver &lt;name&gt; &lt;host:port:os&gt;</code> — add a server
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">
            OS types: <code>unix</code> (Linux/macOS) &nbsp;·&nbsp; <code>windows</code> (Windows path separators)<br>
            e.g. <code>/addserver mordor mordor:8000:windows</code><br>
            e.g. <code>/addserver mybox 192.168.1.50:8188:unix</code>
          </div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/iterations &lt;n&gt;</code> — generate n images per prompt (default 1)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/resolution &lt;WxH&gt;</code> — set output resolution, e.g. <code>/resolution 640x480</code> or <code>/resolution phone</code>
          <div style="margin-top:2px;color:#475569;font-size:0.78rem"><code>phone</code> (or <code>iphone</code>) measures this device's viewport &nbsp;·&nbsp; presets: ipad, hd, fhd, square &nbsp;·&nbsp; <code>/resolution flip</code> swaps W/H &nbsp;·&nbsp; <code>/resolution reset</code> restores workflow default</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/lora</code> — fuzzy-find a LoRA to insert (works anywhere in a prompt)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/multi</code> — generate images for multiple prompts; paste one prompt per line (Shift+Enter between lines)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/sequence &lt;master prompt&gt;</code> — ask Grok to expand a master prompt into a sequence of prompts, then generate them one after another
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">count comes from <code>/iterations</code> (or 15 if iterations is 1) &nbsp;·&nbsp; needs <code>XAI_API_KEY</code> set on the server</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/sequence-replacement &lt;from&gt; &lt;to&gt;</code> — find→replace applied to each Grok prompt (no args lists them; <code>clear</code> removes them)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/workflow</code> — choose a workflow template</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/workflow-iterate &lt;prompt&gt;</code> — tick several workflows, then run the prompt against each one</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail [N]</code> — run face-detail over the last N images (default 1); uses <code>/face-detail-prompt</code> override or derives from each image's prompt</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-prompt &lt;prompt&gt;</code> — set the prompt the per-image face (&#128100;) icons use; otherwise each icon derives one from that image's own prompt (needs a <code>&lt;lora:…&gt;</code> tag)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-prompt-reset</code> — clear that override so the face icons derive a prompt from each image again</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-session</code> — face-detail every image from this session, one after another</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/face-detail-workflow</code> — choose which face-detailer workflow the face icons use</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/upscale [N]</code> — run an upscaler workflow over the last N generated images (default 1, no prompt needed)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/upload</code> — upload a new workflow JSON file</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/purge</code> — free GPU memory on the active ComfyUI server</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete</code> — delete the last image</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-session</code> — delete all images from this session (chat + output folder)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-today</code> — delete every image generated today (asks y/n first)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/delete-all</code> — delete every image in the output folder (asks y/n first)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-session [name]</code> — copy this session's images into the encrypted volume, then remove the originals (optional folder name, e.g. <code>/archive-session man walking on beach</code>)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-today [name]</code> — archive images generated today into the encrypted volume (optional folder name)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/archive-all [name]</code> — archive every image in the output folder into the encrypted volume (asks y/n first; optional folder name)
          <div style="margin-top:2px;color:#475569;font-size:0.78rem">needs the <code>archive-agent</code> running on the host and <code>ARCHIVE_*</code> set on the server</div>
        </div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/clear</code> — clear the chat history</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review &lt;n&gt;</code> — grid of the last N images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-all</code> — grid of every image, oldest first (tap to view, trash to delete)</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-today</code> — grid of today's images, oldest first</div>
        <div style="font-size:0.85rem;color:#94a3b8"><code>/review-session</code> — grid of this session's images</div>
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
    history.length = 0;
    historyIdx = -1;
    savedDraft = '';
    fauxFullscreenEls.clear();
    document.body.style.overflow = '';
    messagesEl.innerHTML = '';
    addMessage('bot', 'Chat cleared. Describe the image you\'d like to generate.');
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
  messagesEl.querySelectorAll('.img-wrap img').forEach(img => {
    if (img.getAttribute('src') === url) img.closest('.img-wrap').remove();
  });
  const i = sessionImages.indexOf(url);
  if (i !== -1) sessionImages.splice(i, 1);
  delete imagePrompts[url];
}

// Build a default face-detail prompt from a generation prompt by keeping its
// <lora:…> tag(s) and a subject phrase, plus any facial expressions / emotions
// found in the prompt so the re-detailed face keeps the original mood. Returns
// null if no LoRA. Expressions are appended as comma-separated tags (how
// diffusion prompts read), so noun/adjective grammar mixing doesn't matter.
const SUBJECT_RE = /\b(woman|man|girl|boy|lady)\b/i;   // \b stops 'man' matching inside 'woman'
// Multi-word / hyphenated forms first so e.g. "open mouth" wins over a bare word.
const EXPRESSION_RE = /\b(open[- ]mouthed|open mouth|wide[- ]eyed|teary[- ]eyed|gritted teeth|clenched teeth|furrowed brow|raised eyebrows?|tongue out|biting lip|lip bite|pursed lips|puppy eyes|side[- ]eye|rolling eyes|eyes closed|closed eyes|head tilt|smiling|smile|grinning|grin|laughing|laugh|chuckling|giggling|beaming|smirking|smirk|winking|wink|frowning|frown|scowling|scowl|pouting|pout|crying|sobbing|weeping|tearful|sniffling|screaming|scream|shouting|yelling|yawning|sneering|snarling|grimacing|gasping|blushing|flushed|surprised|shocked|astonished|amazed|stunned|angry|furious|enraged|rage|annoyed|irritated|sad|sorrowful|melancholy|depressed|gloomy|happy|joyful|joy|cheerful|delighted|gleeful|ecstatic|ecstasy|euphoric|blissful|content|terrified|scared|fearful|afraid|frightened|horrified|panicked|worried|anxious|nervous|confused|puzzled|perplexed|disgusted|disgust|contempt|bored|tired|sleepy|exhausted|serious|stern|solemn|calm|serene|peaceful|relaxed|seductive|flirtatious|sultry|coy|smug|mischievous|playful|determined|focused|concentrating|pained|anguished|agony|suffering|embarrassed|ashamed|shy|bashful|hopeful|longing|yearning|dreamy|thoughtful|pensive|suspicious|skeptical|disappointed|frustrated|desperate|hysterical|manic|deadpan|expressionless|neutral|intense|fierce|menacing)\b/gi;
function deriveFaceDetailPrompt(genPrompt) {
  if (!genPrompt) return null;
  const loraTags = genPrompt.match(/<lora:[^>]+>/gi);   // preserves name + strength verbatim
  if (!loraTags || !loraTags.length) return null;
  const m = genPrompt.match(SUBJECT_RE);
  const subject = m ? `a ${m[1].toLowerCase()}'s face` : 'a face';
  const expressions = [...new Set((genPrompt.match(EXPRESSION_RE) || []).map(s => s.toLowerCase()))];
  const desc = [subject, ...expressions].join(', ');
  return `${desc} ${loraTags.join(' ')}`;
}

// Runs a face-detailer workflow over `image` using `prompt`. Driven by the
// per-image face icons. Returns the generation promise so callers can
// re-enable their own controls when it settles.
function runFaceDetail(prompt, image) {
  iterationsFromSequence = false; // a face-detail run is a single image, not a sequence
  sendBtn.disabled = true;
  return runGeneration(prompt, '', null, { face: { image, workflow: currentFaceWorkflow || DEFAULT_FACE_WORKFLOW } })
    .finally(() => { sendBtn.disabled = false; });
}

// Runs an upscaler workflow over `image`. Shared by the /upscale command and the
// per-image "up" button. Takes no prompt. Returns the generation promise so
// callers can re-enable their own controls when it settles.
function runUpscale(image) {
  iterationsFromSequence = false; // an upscale run is a single image, not a sequence
  sendBtn.disabled = true;
  return runGeneration('', '', null, { upscale: { image, workflow: currentUpscaleWorkflow || DEFAULT_UPSCALE_WORKFLOW } })
    .finally(() => { sendBtn.disabled = false; });
}

function runDoOver(url, imgWrap) {
  const prompt = imagePrompts[url] || '';
  return runGeneration(prompt, '', null, { replaceWrap: imgWrap });
}

// Appends a generated image to a bubble with a trash-icon overlay (top-right,
// deletes from chat + output folder) and a face-icon overlay (bottom-right,
// runs face detail over this image using the /face-detail-prompt override or a
// prompt derived from this image's own generation prompt).
function appendChatImage(container, url) {
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';

  const img = document.createElement('img');
  img.src = url;
  img.alt = 'Generated image';

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
    addMessage('user', 'Face detail: ' + escapeHtml(prompt));
    runFaceDetail(prompt, url).finally(() => { face.disabled = false; });
  });

  const up = document.createElement('button');
  up.className = 'img-up';
  up.title = 'Upscale image';
  up.textContent = 'up';
  up.addEventListener('click', e => {
    e.stopPropagation();
    if (up.disabled || sendBtn.disabled) return;
    up.disabled = true;
    addMessage('user', 'Upscale image');
    runUpscale(url).finally(() => { up.disabled = false; });
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

  wrap.appendChild(img);
  wrap.appendChild(del);
  wrap.appendChild(face);
  wrap.appendChild(up);
  wrap.appendChild(redo);
  container.appendChild(wrap);
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

    const img = document.createElement('img');
    img.src = url;
    img.alt = 'Generated image';
    img.addEventListener('click', () => openLightbox(url));

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
    up.textContent = 'up';
    up.addEventListener('click', e => {
      e.stopPropagation();
      if (up.disabled || sendBtn.disabled) return;
      up.disabled = true;
      addMessage('user', 'Upscale image');
      runUpscale(url).finally(() => { up.disabled = false; });
    });

    cell.appendChild(img);
    cell.appendChild(del);
    cell.appendChild(face);
    cell.appendChild(up);
    grid.appendChild(cell);
  });

  bubble.appendChild(grid);
  scrollBottom();
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

// Runs one generation job in its own bot bubble. Resolves true on success,
// false on any failure — it never rejects; errors are rendered in the bubble.
function runGeneration(raw, label, workflowOverride, opts = {}) {
  return new Promise(resolve => {
  const face = opts.face || null;
  const upscale = opts.upscale || null;
  const replaceWrap = opts.replaceWrap || null;
  const job = face || upscale; // an image-input job (face-detail or upscale) vs a plain generation
  const endpoint = face ? '/api/face-detail' : upscale ? '/api/upscale' : '/api/generate';
  const botBubble = addMessage('bot', `
    <div class="status-text" id="status-line">Connecting…${label}</div>
    <div class="dots"><span></span><span></span><span></span></div>
    <div class="progress-bar-wrap"><div class="progress-bar"></div></div>
  `);

  const statusLine = botBubble.querySelector('#status-line');
  const dotsEl     = botBubble.querySelector('.dots');
  const barWrap    = botBubble.querySelector('.progress-bar-wrap');

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
      ...(job ? { image: job.image } : {}),
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
        scrollBottom();

      } else if (msg.type === 'done') {
        es.close();
        dotsEl.remove();
        barWrap.remove();
        cancelBtn.remove();
        statusLine.textContent = `Done — ${msg.images.length} image(s)${label}`;
        // The prompt to remember for this image: the raw generation prompt, or
        // for prompt-less jobs (upscale) the source image's own prompt, so a
        // face icon on the result can still derive a face-detail prompt.
        const originPrompt = raw || (job && imagePrompts[job.image]) || '';
        msg.images.forEach((url, i) => {
          sessionImages.push(url);
          if (originPrompt) imagePrompts[url] = originPrompt;
          if (i === 0 && replaceWrap && replaceWrap.parentNode) {
            const oldImg = replaceWrap.querySelector('img');
            if (oldImg) {
              const oldIdx = sessionImages.indexOf(oldImg.src);
              if (oldIdx !== -1) sessionImages.splice(oldIdx, 1);
              delete imagePrompts[oldImg.src];
            }
            const tmp = document.createElement('div');
            appendChatImage(tmp, url);
            replaceWrap.replaceWith(tmp.firstChild);
          } else {
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
        scrollBottom();
        resolve(false);

      } else if (msg.type === 'error') {
        es.close();
        dotsEl.remove();
        barWrap.remove();
        cancelBtn.remove();
        statusLine.textContent = '';
        botBubble.innerHTML += `<span style="color:#f87171">⚠ ${escapeHtml(msg.message)}</span>`;
        scrollBottom();
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

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
