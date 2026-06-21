// Pure utility functions shared between the browser app and Jest unit tests.
// No DOM dependencies — every function here is a plain transformation.

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
export function applyReplacements(prompt, replacements) {
  for (const [from, to] of replacements) prompt = prompt.split(from).join(to);
  return prompt;
}

// Move the item at index `from` to index `to`, returning a new array (the
// input is left untouched). Out-of-range indices and from===to yield a shallow
// copy unchanged. Powers drag-to-reorder in the /splice-session grid.
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

export const DEFAULT_VIDEO_SETTINGS = { duration: 5, frames: 125, fps: 25, audio: true, width: 1280, height: 720 };
export const VIDEO_LIMITS = {
  duration: { min: 0.1, max: 60 },
  frames:   { min: 1,   max: 1000 },
  fps:      { min: 1,   max: 60 },
  // Video resolution is kept distinct from /resolution (which targets stills):
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
