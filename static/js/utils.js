// Pure utility functions shared between the browser app and Jest unit tests.
// No DOM dependencies — every function here is a plain transformation.

// Custom drag-and-drop MIME type carrying a chat media URL when a generated image or
// video is dragged into the /references table. Distinct from the 'Files' type the
// global desktop-file drop overlay listens for, so in-app drags never trigger it.
export const COMFY_URL_DND_TYPE = 'application/x-comfy-url';

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// True for output URLs that are videos (rendered via <video> rather than <img>).
// Mirrors VIDEO_EXTS in config.py. A query string / fragment is ignored.
export function isVideoUrl(url) {
  return /\.(mp4|webm)(?:[?#]|$)/i.test(String(url));
}

// Clamp a popup's top-left corner so the whole box stays inside the viewport.
// Used by the media right-click menu: the pointer can be anywhere, including the
// bottom-right corner, where a menu drawn at (x, y) would overflow off-screen.
// Prefers the requested position, flips back by the box size when it would spill,
// and never returns a negative coordinate (a menu taller than the viewport is
// pinned to the top-left rather than pushed off the other edge).
export function clampMenuPosition(x, y, w, h, vw, vh, margin = 8) {
  return {
    left: Math.max(margin, Math.min(x, vw - w - margin)),
    top:  Math.max(margin, Math.min(y, vh - h - margin)),
  };
}

// Summarise one volume's /api/fscheck result into a small {icon, label, tone}
// for display. Pure — covers every result shape the server sends:
//   {configured:false}     -> not configured on this server
//   {available:false}      -> configured but no startup check result yet
//   {skipped:...}          -> volume not provisioned yet (nothing to check)
//   {ok:false, error}      -> the check itself failed
//   {ok:true, clean}       -> filesystem already clean
//   {ok:true, uncorrected} -> errors remain (could not be fully repaired)
//   {ok:true, corrected}   -> errors found and repaired
// uncorrected is checked before corrected because a run can report both.
export function formatFscheckResult(result) {
  const r = result || {};
  if (r.configured === false) return { icon: '—', label: 'not configured', tone: 'muted' };
  if (r.available === false)  return { icon: '—', label: 'not checked yet', tone: 'muted' };
  if (r.skipped)              return { icon: '—', label: 'volume not yet created', tone: 'muted' };
  if (r.ok === false)         return { icon: '⚠', label: `check failed: ${r.error || 'unknown error'}`, tone: 'error' };
  if (r.clean)                return { icon: '✓', label: 'clean', tone: 'ok' };
  if (r.uncorrected)          return { icon: '⚠', label: 'problems remain — could not fully repair', tone: 'error' };
  if (r.corrected)            return { icon: '🔧', label: 'errors found and repaired', tone: 'warn' };
  return { icon: '?', label: 'unknown result', tone: 'muted' };
}

// Subsequence fuzzy match: every query char must appear in order.
// Returns a score (higher = better) or -1 for no match.
export function fuzzyScore(query, text) {
  query = query.toLowerCase();
  text  = text.toLowerCase();
  if (!query) return 0;
  let score = 0, from = 0, last = -2;
  for (const ch of query) {
    const idx = text.indexOf(ch, from);
    if (idx === -1) return -1;
    score += (idx === last + 1) ? 3 : 1;  // reward consecutive runs
    if (idx === 0) score += 2;             // reward matching the start
    last = idx;
    from = idx + 1;
  }
  return score;
}

// Parse a fetch Response as JSON, degrading gracefully when the body isn't
// JSON (e.g. a gunicorn/proxy timeout page or an empty body).
export async function parseJsonResponse(r) {
  // login_required answers with a 302 to /login (for /api/* too), and fetch follows
  // redirects transparently — so an expired session arrives here as the login page's
  // HTML with status 200. Detect it and send the browser to the login page rather
  // than surfacing a baffling "non-JSON response: <!DOCTYPE html..." error.
  if (r.redirected && new URL(r.url).pathname === '/login') {
    if (typeof window !== 'undefined') window.location.href = '/login';
    throw new Error('Session expired — please log in again');
  }
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

// Expand word-for-word aliases in `text` using the given aliases map.
// Splits on whitespace runs so separators are preserved.
export function expandAliases(text, aliases) {
  if (!Object.keys(aliases).length) return text;
  return text.split(/(\s+)/).map(tok => (/\S/.test(tok) && aliases[tok] !== undefined) ? aliases[tok] : tok).join('');
}

// Apply find→replace pairs to a prompt string (plain substring replacement).
// A falsy prompt (null/'') is passed through unchanged so callers can wrap a
// possibly-null derived prompt without a separate guard.
export function applyReplacements(prompt, replacements) {
  if (!prompt) return prompt;
  for (const [from, to] of replacements) prompt = prompt.split(from).join(to);
  return prompt;
}

// Add a find→replace pair to `list`, overwriting any existing entry with the
// same "from" instead of appending a duplicate — otherwise the earlier pair
// always wins (it is applied first, so the later one no longer matches) and
// re-defining a replacement looks like it does nothing. `caseInsensitive`
// mirrors how the pairs are applied: sequence replacements match
// case-insensitively (case_preserving_replace, server side), the other
// families are plain substring replacements. Mutates `list` in place and
// returns the "to" it displaced, or null if the pair was new.
export function upsertReplacement(list, from, to, caseInsensitive = false) {
  const norm = s => (caseInsensitive ? s.toLowerCase() : s);
  const i = list.findIndex(([f]) => norm(f) === norm(from));
  if (i === -1) {
    list.push([from, to]);
    return null;
  }
  const prev = list[i][1];
  list[i] = [from, to];
  return prev;
}

// Move the item at index `from` to index `to`, returning a new array (the
// input is left untouched). Out-of-range indices and from===to yield a shallow
// copy unchanged. Powers drag-to-reorder in the /video-splice grid.
export function reorderList(arr, from, to) {
  const out = arr.slice();
  if (from === to) return out;
  if (from < 0 || from >= out.length || to < 0 || to >= out.length) return out;
  const [moved] = out.splice(from, 1);
  out.splice(to, 0, moved);
  return out;
}

// Used only by deriveFaceDetailPrompt — kept here so they travel together.
const SUBJECT_RE = /\b(woman|man|girl|boy|lady)\b/i;
// Multi-word / hyphenated forms first so e.g. "open mouth" wins over a bare word.
const EXPRESSION_RE = /\b(open[- ]mouthed|open mouth|wide[- ]eyed|teary[- ]eyed|gritted teeth|clenched teeth|furrowed brow|raised eyebrows?|tongue out|biting lip|lip bite|pursed lips|puppy eyes|side[- ]eye|rolling eyes|eyes closed|closed eyes|head tilt|smiling|smile|grinning|grin|laughing|laugh|chuckling|giggling|beaming|smirking|smirk|winking|wink|frowning|frown|scowling|scowl|pouting|pout|crying|sobbing|weeping|tearful|sniffling|screaming|scream|shouting|yelling|yawning|sneering|snarling|grimacing|gasping|blushing|flushed|surprised|shocked|astonished|amazed|stunned|angry|furious|enraged|rage|annoyed|irritated|sad|sorrowful|melancholy|depressed|gloomy|happy|joyful|joy|cheerful|delighted|gleeful|ecstatic|ecstasy|euphoric|blissful|content|terrified|scared|fearful|afraid|frightened|horrified|panicked|worried|anxious|nervous|confused|puzzled|perplexed|disgusted|disgust|contempt|bored|tired|sleepy|exhausted|serious|stern|solemn|calm|serene|peaceful|relaxed|seductive|flirtatious|sultry|coy|smug|mischievous|playful|determined|focused|concentrating|pained|anguished|agony|suffering|embarrassed|ashamed|shy|bashful|hopeful|longing|yearning|dreamy|thoughtful|pensive|suspicious|skeptical|disappointed|frustrated|desperate|hysterical|manic|deadpan|expressionless|neutral|intense|fierce|menacing)\b/gi;

// Build a default face-detail prompt from a generation prompt by keeping its
// <lora:…> tags plus a subject phrase and any facial expressions found in the
// prompt. Returns null if there is no LoRA tag (required by face-detailers).
export function deriveFaceDetailPrompt(genPrompt) {
  if (!genPrompt) return null;
  const loraTags = genPrompt.match(/<lora:[^>]+>/gi);
  if (!loraTags || !loraTags.length) return null;
  const m = genPrompt.match(SUBJECT_RE);
  const subject = m ? `a ${m[1].toLowerCase()}'s face` : 'a face';
  const expressions = [...new Set((genPrompt.match(EXPRESSION_RE) || []).map(s => s.toLowerCase()))];
  const desc = [subject, ...expressions].join(', ');
  return `${desc} ${loraTags.join(' ')}`;
}

// Folds an image's video metadata into its base (image) prompt to form the prompt
// sent to an image2video workflow: "<base>. <action>. Audio: <audio>". Empty
// parts are skipped; with no/empty meta it returns `base` unchanged, preserving
// backward compatibility with /sequence and plain generations (which carry no
// action/audio). `meta` is { action, audio } or null/undefined.
//
// `includeAudio` (default true) gates the "Audio: <audio>" segment. The Audio
// checkbox in /video-settings sets it false for workflows that don't generate
// audio (e.g. the Wan image2video template), so audio cues aren't fed to a model
// that ignores them. action is always kept.
export function buildVideoPrompt(base, meta, includeAudio = true) {
  if (!meta) return base;
  const action = (meta.action || '').trim();
  const audio = (meta.audio || '').trim();
  const parts = [base];
  if (action) parts.push(action);
  if (includeAudio && audio) parts.push('Audio: ' + audio);
  return parts.filter(p => p && p.trim()).join('. ');
}

// Builds the tooltip for an image's image2video button. Defaults to
// "Image to video"; when the image carries video metadata (action/audio from
// /video-sequence) it appends them: "Image to video: <action>, <audio>" (only
// the parts present). `meta` is { action, audio } or null/undefined.
export function i2vTooltip(meta) {
  const base = 'Image to video';
  if (!meta) return base;
  const parts = [(meta.action || '').trim(), (meta.audio || '').trim()].filter(p => p);
  return parts.length ? `${base}: ${parts.join(', ')}` : base;
}

// ---------------------------------------------------------------------------
// Video settings (image2video <DURATION>/<FRAMES>/<FPS>)
//
// The three values are interdependent: frames = duration × fps. frames and fps
// are integers (PrimitiveInt nodes in the workflow); duration is shown to one
// decimal. Output is driven by frames and fps, so duration is effectively a
// derived value and may round by a frame at the extremes.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Workflow model variants
// ---------------------------------------------------------------------------
// A template can name several interchangeable models on one loader (a comma-separated
// unet_name), so one file covers what used to be one file per model. The pick rides the
// workflow name itself as "<workflow>@<model>", which is why it needs no field of its
// own anywhere it travels: payloads, the saved chat session, the /settings-save stack,
// macros and server-side sequence runs all carry the workflow name already.
export const WORKFLOW_VARIANT_SEP = '@';

// Split "workflow@model" into its parts; variant is null when there is no suffix.
// Splits on the LAST separator, mirroring workflow.split_workflow_variant server-side.
export function splitWorkflowVariant(name) {
  if (!name || !name.includes(WORKFLOW_VARIANT_SEP)) return { name: name || '', variant: null };
  const i = name.lastIndexOf(WORKFLOW_VARIANT_SEP);
  const base = name.slice(0, i), variant = name.slice(i + 1);
  if (!base || !variant) return { name, variant: null };
  return { name: base, variant };
}

// Join a workflow and a model back into a wire name. A null/empty variant — and the
// template's own default, which the server picks when unsuffixed — yields the BARE
// name, so leaving the default alone keeps the wire format byte-identical to before.
export function joinWorkflowVariant(name, variant) {
  return variant ? `${name}${WORKFLOW_VARIANT_SEP}${variant}` : name;
}

// Render a possibly-suffixed workflow name: the workflow, then its model dimmed.
export function workflowLabelHtml(name) {
  const { name: base, variant } = splitWorkflowVariant(name);
  const model = variant
    ? ` <span style="color:#64748b;font-size:0.85em">${WORKFLOW_VARIANT_SEP} ${escapeHtml(variant)}</span>`
    : '';
  return `${escapeHtml(base)}${model}`;
}

// Bypassable optimisations in the video workflows, each matching an "[opt:<key>] ..."
// marked node in the template (see workflow.bypass_optimisation_nodes). `key` is the
// wire/marker name — mirrored server-side as VIDEO_OPTIMIZATIONS in config.py — and
// `stateKey` is its flag on currentVideoSettings. Most default ON: that is the fast
// preview mode, traded against quality. Declared here rather than in state.js because
// state.js imports from this module.
//
// A descriptor carrying `steps` is an *accelerator* — a distillation LoRA that only
// makes sense at that sampler step count. Chaining two of different step counts gives
// mush, so they are mutually exclusive by step count (see activeAccelerator). The two
// 8-step variants share a count and so do not exclude each other: they are never both
// present in one template (fl2va in i2v/t2v, ref2va in r2v), so "both on" just means
// "whichever 8-step LoRA this workflow has".
export const VIDEO_OPTIMIZATIONS = [
  { key: 'turbo',     stateKey: 'optTurbo',     label: 'Turbo 4-step LoRA',                   steps: 4, hint: 'sets Steps to 4' },
  { key: 'accel8fl',  stateKey: 'optAccel8Fl',  label: '8-step accel LoRA (fl2va — i2v/t2v)', steps: 8, hint: 'sets Steps to 8' },
  { key: 'accel8ref', stateKey: 'optAccel8Ref', label: '8-step accel LoRA (ref2va — r2v)',    steps: 8, hint: 'sets Steps to 8' },
  { key: 'cache',    stateKey: 'optCache',    label: 'H3 FirstBlockCache' },
  { key: 'sage',     stateKey: 'optSage',     label: 'Sage attention' },
  { key: 'sol',      stateKey: 'optSol',      label: 'Sol attention' },
  { key: 'spectrum', stateKey: 'optSpectrum', label: 'H3 Spectrum' },
];
// Sampler steps the Turbo LoRA needs, and the step count the templates bake in for
// running with no accelerator at all. The /video-settings panel moves the steps
// override between an accelerator's own count and this base as they are ticked.
export const TURBO_STEPS = 4;
export const BASE_VIDEO_STEPS = 20;

export const DEFAULT_VIDEO_SETTINGS = {
  duration: 5, frames: 125, fps: 25, audio: true, width: 1280, height: 720,
  optTurbo: false, optAccel8Fl: true, optAccel8Ref: true,
  optCache: true, optSage: true, optSol: true, optSpectrum: true,
};

// The accelerator actually in force, or null if none is on.
//
// Accelerators of two different step counts can read as on at once only in a session
// saved before the 8-step LoRAs existed: it carries an explicit optTurbo:true and no
// 8-step flags at all, and absent reads as on. The lowest step count wins, so such a
// session resolves to the turbo LoRA it actually chose and renders exactly as before.
// From the panel the exclusion is enforced as you tick, so this never fires there.
export function activeAccelerator(vs) {
  const on = VIDEO_OPTIMIZATIONS.filter(o => o.steps && (vs || {})[o.stateKey] !== false);
  if (!on.length) return null;
  return on.reduce((a, b) => (b.steps < a.steps ? b : a));
}

// Build the video_opts wire object. Every flag is sent explicitly: the server reads an
// absent video_opts as "bypass nothing", so omitting an off flag would silently mean on.
export function videoOptsPayload(vs) {
  const out = {};
  const accel = activeAccelerator(vs);
  // Absent reads as on, so an old saved session keeps today's behaviour.
  VIDEO_OPTIMIZATIONS.forEach(({ key, stateKey, steps }) => {
    const on = (vs || {})[stateKey] !== false;
    // Only the winning step-count group survives; a loser is forced off rather than
    // stacked, whatever the stored flags say.
    out[key] = steps ? (on && !!accel && steps === accel.steps) : on;
  });
  return out;
}
export const VIDEO_LIMITS = {
  duration: { min: 0.1, max: 60 },
  frames:   { min: 1,   max: 1000 },
  fps:      { min: 1,   max: 60 },
  // Video resolution is kept distinct from /image-settings (which targets stills):
  // video models have very different size constraints. Dimensions are snapped to
  // a multiple of 16 (see clampVideo) since most video models require it.
  width:    { min: 64,  max: 2048 },
  height:   { min: 64,  max: 2048 },
};

export function fmtDuration(d) {
  const r = Math.round(d * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

export function clampVideo(key, val) {
  const lim = VIDEO_LIMITS[key];
  let v = Math.min(lim.max, Math.max(lim.min, val));
  if (key === 'duration') return Math.round(v * 10) / 10;
  if (key === 'width' || key === 'height') {
    // Snap to a multiple of 16 (most video models require it), then re-clamp.
    const snapped = Math.round(v / 16) * 16;
    return Math.min(lim.max, Math.max(lim.min, snapped));
  }
  return Math.round(v);
}

// Re-derive `s` in place so frames = duration × fps holds. `lock` is the value
// held constant; `edited` is the value the user just changed; the remaining one
// follows, then `edited` is snapped back so the pair stays consistent after any
// clamping. Editing the locked value is a no-op the caller should prevent.
export function recomputeVideo(s, lock, edited) {
  if (edited === lock) return;
  const derive = key => {
    if (key === 'frames')   s.frames   = clampVideo('frames',   s.duration * s.fps);
    else if (key === 'fps') s.fps      = clampVideo('fps',      s.frames / s.duration);
    else                    s.duration = clampVideo('duration', s.frames / s.fps);
  };
  const third = ['duration', 'frames', 'fps'].find(k => k !== lock && k !== edited);
  derive(third);
  derive(edited);
}

// Find the bounding box of pixels that differ between two same-size RGBA images
// — used by the face-detail-super tile picker to locate the face a detailer
// changed, by diffing a result against the original. `a` and `b` are RGBA byte
// arrays (Uint8ClampedArray, length w*h*4). Returns a padded, clamped
// {x, y, w, h} in pixels, or null when nothing meaningful differs (or the inputs
// don't match) — the caller then falls back to showing the whole image.
//   threshold: summed |ΔR|+|ΔG|+|ΔB| above which a pixel counts as changed.
//   pad:       fraction of the box size added on every side.
//   minFrac:   ignore boxes smaller than this fraction of the image area (noise).
export function computeDiffBox(a, b, w, h, { threshold = 40, pad = 0.15, minFrac = 0.0004 } = {}) {
  if (!a || !b || a.length !== b.length || a.length !== w * h * 4) return null;
  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const d = Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]);
      if (d > threshold) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return null;                       // nothing changed
  const bw = maxX - minX + 1, bh = maxY - minY + 1;
  if (bw * bh < minFrac * w * h) return null;      // too small — treat as noise
  const px = Math.round(bw * pad), py = Math.round(bh * pad);
  const x0 = Math.max(0, minX - px);
  const y0 = Math.max(0, minY - py);
  const x1 = Math.min(w, maxX + 1 + px);
  const y1 = Math.min(h, maxY + 1 + py);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// --- Generation progress (the {type:"tick"} SSE payload) --------------------
// ComfyUI's WebSocket feed is folded server-side (comfy_progress.py) into a
// snapshot that rides the 2s tick already being sent. Both helpers take that
// raw tick object and are total: a tick from a run whose listener never
// connected carries no fields at all, and both answer null so the caller leaves
// the indeterminate marquee alone.

// Overall completion as a 0..100 number, or null when the tick carries none.
export function progressPercent(tick) {
  if (!tick || typeof tick.percent !== 'number' || !isFinite(tick.percent)) return null;
  return Math.max(0, Math.min(100, tick.percent));
}

// The line under the bar: which node is running and how far through it is.
// Falls back to the queue depth, which is all that's known before execution
// starts, and to null when even that is absent.
export function progressCaption(tick) {
  if (!tick) return null;
  const parts = [];
  if (tick.phase) {
    parts.push(tick.steps > 1 ? `${tick.phase} — step ${tick.step}/${tick.steps}` : tick.phase);
  }
  if (tick.node_total) parts.push(`node ${Math.min(tick.node_index, tick.node_total)}/${tick.node_total}`);
  if (parts.length) return parts.join(' · ');
  if (tick.queue > 0) return `Waiting — ${tick.queue} ahead in queue`;
  return null;
}
