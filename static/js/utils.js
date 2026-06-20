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
