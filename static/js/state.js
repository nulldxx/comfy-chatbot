import { DEFAULT_VIDEO_SETTINGS } from './utils.js';

export const DEFAULT_DENOISE = { face: 0.35, image2image: 0.30, inpaint: 0.45, upscale: 0.15 };

export const RESOLUTION_PRESETS = {
  ipad:    { width: 2048, height: 2732, label: 'iPad Pro portrait (2048×2732)'   },
  hd:      { width: 1280, height:  720, label: 'HD 720p (1280×720)'              },
  fhd:     { width: 1920, height: 1080, label: 'Full HD 1080p (1920×1080)'       },
  square:  { width: 1024, height: 1024, label: 'Square (1024×1024)'              },
};

// Landscape presets for the /video-settings dialog. Kept separate from the
// still-image presets above because video models have different size limits
// (see VIDEO_LIMITS / clampVideo). '360p' is a low-cost "quick" preview size.
export const VIDEO_RESOLUTION_PRESETS = {
  '360p':  { width:  640, height:  360, label: '360p (640×360)'    },
  '540p':  { width:  960, height:  540, label: '540p (960×540)'    },
  '720p':  { width: 1280, height:  720, label: '720p (1280×720)'   },
  '1080p': { width: 1920, height: 1080, label: '1080p (1920×1080)' },
  square:  { width: 1024, height: 1024, label: 'Square (1024×1024)' },
};

// Slot counts for the /references table (MiniMax H3 R2V): 9 images, 3 videos,
// 3 standalone audios — capped at 12 files in total. This map is the source of truth
// for the "array of URL-or-null" slot groups, which is why videoTracks (flags, not
// files) is deliberately not a member of it.
export const REFERENCE_SLOT_COUNTS = { images: 9, videos: 3, audios: 3 };
export const REFERENCE_MAX_FILES = 12;

// A reference video is one clip with two usable tracks (ComfyUI's VHS Load Video node
// emits IMAGE and AUDIO). A freshly dropped clip feeds both, so it costs 2 of the 12.
export const REFERENCE_TRACK_DEFAULT = { video: true, audio: true };

// Pad/trim an array to exactly n entries, filling with null.
function padSlots(arr, n) {
  const a = Array.isArray(arr) ? arr.slice(0, n) : [];
  while (a.length < n) a.push(null);
  return a;
}

// Pad/trim the per-video track flags to exactly n entries. An empty slot gets the
// default (both tracks), which is what a clip dropped into it should start with.
// A slot holding a clip from a session saved before the checkboxes existed has no
// flags: it used the parallel videoAudios URL array instead, so a filled entry there
// becomes the audio track and an absent one leaves audio off — preserving exactly what
// that session used to send.
function padTracks(arr, n, legacyAudios, videos) {
  const src = Array.isArray(arr) ? arr : [];
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = src[i];
    if (t && typeof t === 'object') {
      out.push({ video: !!t.video, audio: !!t.audio });
    } else if (videos && videos[i]) {
      out.push({ video: true, audio: !!(legacyAudios && legacyAudios[i]) });
    } else {
      out.push({ ...REFERENCE_TRACK_DEFAULT });
    }
  }
  return out;
}

// Pad/trim one group's enable flags to exactly n entries. Anything absent — an empty
// slot, a whole group, a session saved before the toggles existed — is on, so a
// restored table behaves exactly as it did before this flag existed.
function padEnabled(arr, n) {
  const src = Array.isArray(arr) ? arr : [];
  const out = [];
  for (let i = 0; i < n; i++) out.push(src[i] === undefined ? true : !!src[i]);
  return out;
}

// The enable flags for every slot group, keyed like REFERENCE_SLOT_COUNTS. A disabled
// slot keeps its URL (and, for a video, its track ticks) but is charged nothing against
// the cap and sent as empty — the whole point being to park a reference without losing
// it. Kept parallel to the URL arrays rather than boxing each slot into an object so
// the existing padSlots/countReferenceFiles shape survives untouched.
function padEnabledGroups(src) {
  const r = src || {};
  const out = {};
  for (const [key, n] of Object.entries(REFERENCE_SLOT_COUNTS)) out[key] = padEnabled(r[key], n);
  return out;
}

// Fresh, empty reference-slot structure for the /references table. A factory (not a
// shared const) so newChat / restore each get their own object rather than aliasing.
export function newReferences() {
  return {
    images:      padSlots([], REFERENCE_SLOT_COUNTS.images),
    videos:      padSlots([], REFERENCE_SLOT_COUNTS.videos),
    videoTracks: padTracks([], REFERENCE_SLOT_COUNTS.videos),
    audios:      padSlots([], REFERENCE_SLOT_COUNTS.audios),
    enabled:     padEnabledGroups(null),
  };
}

// Deep copy of a references object, tolerant of a partial/legacy shape (missing keys
// default to empty). Migrates the pre-expansion single-value slots (video/videoAudio/
// audio scalars, 3-image array) into the indexed arrays, and the separate
// videoAudios upload slots into per-video track flags. Used by session save/restore
// and the /settings-save stack — which is why none of those call sites needs to know
// about the shape change.
//
// One accepted loss: the old table could pair a video with a *different* audio file.
// That can't survive the one-clip model, so such an audio is dropped on restore.
export function cloneReferences(refs) {
  const r = refs || {};
  // Legacy scalar → single-element array so the old first value lands in slot 0.
  const legacy = (newArr, scalar) => Array.isArray(newArr) ? newArr : (scalar ? [scalar] : []);
  const videos = padSlots(legacy(r.videos, r.video), REFERENCE_SLOT_COUNTS.videos);
  return {
    images:      padSlots(r.images, REFERENCE_SLOT_COUNTS.images),
    videos,
    videoTracks: padTracks(r.videoTracks, REFERENCE_SLOT_COUNTS.videos,
                           legacy(r.videoAudios, r.videoAudio), videos),
    audios:      padSlots(legacy(r.audios, r.audio), REFERENCE_SLOT_COUNTS.audios),
    enabled:     padEnabledGroups(r.enabled),
  };
}

// Whether slot i of group `key` is switched on. Absent flags read as on, so a payload
// from a client that predates the toggles is counted exactly as it used to be.
export function referenceSlotEnabled(refs, key, i) {
  const g = (refs || {}).enabled;
  const arr = g && Array.isArray(g[key]) ? g[key] : null;
  return !arr || arr[i] === undefined ? true : !!arr[i];
}

// Files charged against the 12-file cap by one slot, given its URL and flags. Images
// and standalone audios cost 1; a video costs one per track ticked (both = 2, one = 1,
// neither = 0 — the clip is attached but inactive). An empty or switched-off slot costs
// nothing, which is what makes disabling a row a way to free budget rather than a
// cosmetic dimming. Exported because the /references table needs to price a prospective
// edit (fill, tick, enable) before applying it.
export function referenceSlotCost(key, url, enabled, tracks) {
  if (!url || !enabled) return 0;
  if (key !== 'videos') return 1;
  const t = tracks || {};
  return (t.video ? 1 : 0) + (t.audio ? 1 : 0);
}

// Total files charged against the 12-file cap.
export function countReferenceFiles(refs) {
  const r = refs || {};
  const tracks = Array.isArray(r.videoTracks) ? r.videoTracks : [];
  return Object.keys(REFERENCE_SLOT_COUNTS).reduce((n, k) => {
    const arr = Array.isArray(r[k]) ? r[k] : [];
    return n + arr.reduce((m, url, i) => m + referenceSlotCost(
      k, url, referenceSlotEnabled(r, k, i), tracks[i]), 0);
  }, 0);
}

export const state = {
  // Server & workflow selections (null = use backend default)
  currentServer:               null,
  currentWorkflow:             null,
  currentFaceWorkflow:         null,
  currentUpscaleWorkflow:      null,
  currentImage2ImageWorkflow:  null,
  currentImage2VideoWorkflow:  null,
  currentText2VideoWorkflow:   null,
  currentInpaintingWorkflow:   null,
  currentRemovalWorkflow:      null,

  // Face-detail "super" mode: when >1, the face icon runs N detailer
  // variations and shows a tile picker instead of a single before/after slider.
  faceSuperN:                  1,

  // Text-to-video mode (/t2v): while on, a plain chat prompt is generated as a
  // video by the text2video workflow instead of an image by the t2i one.
  t2vMode:                     false,

  // Prompt overrides
  lastFaceDetailPrompt:        null,

  // Metadata of the most recent image2video launch ({prompt, action, audio}), used by
  // the metadata editor's Clone button. null = no video generated yet in this chat.
  lastVideoMeta:               null,
  lastInpaintingPrompt:        null,
  extraPrompt:                 null,

  // Generation settings
  currentResolution:           { width: 1365, height: 768 },
  currentGenerationSteps:      null,
  currentDenoise:              { ...DEFAULT_DENOISE },
  currentVideoSettings:        { ...DEFAULT_VIDEO_SETTINGS },
  videoLock:                   'fps',
  // Sampler-steps override for video (image2video / text2video). null = leave the
  // video workflow's own steps untouched. Kept separate from currentGenerationSteps
  // (still images) since video graphs have their own step budgets.
  currentVideoSteps:           null,
  // One-shot seed reuse (/getseed): when set, the next primary generation
  // (t2i / i2v / t2v) is submitted with this seed instead of a random one, then
  // this is cleared. null = randomize as usual.
  reuseSeed:                   null,
  iterations:                  1,
  iterationsFromSequence:      false,

  // Replacement & override state
  sequenceReplacements:        [],
  lastSequence:                null,
  image2imageReplacements:     [],
  image2imageOverridePrompt:   null,
  image2videoReplacements:     [],
  image2videoOverridePrompt:   null,
  faceDetailReplacements:      [],
  autoFaceDetail:              false,

  // Session image tracking
  sessionImages:               [],
  imagePrompts:                {},
  imageMasks:                  {},
  imageVideoMeta:              {},

  // Catalogues (populated via fetch on startup)
  ALIASES:                     {},
  MACROS:                      {},
  LORAS:                       [],

  // Image2video end-frame selection
  lastFrameUrl:                null,

  // Reference assets (the /references table). image slot 1 is the LTX face-ID
  // identity reference (falls back to the triggered image when null); slots 2/3 and
  // the video/audio slots drive MiniMax H3 (R2V) workflows. Images hold gallery
  // /images/ URLs; video may be a gallery or /references-file/ URL; audio is always
  // a /references-file/ URL. Each slot also carries an enable flag (references.enabled)
  // so a reference can be switched off — costing nothing, sent as empty — without
  // being thrown away. See newReferences() for the shape.
  references:                  newReferences(),

  // Active slideshow controller (keyboard navigation target)
  activeSlideshowCtrl:         null,

  // Prompt history (up/down arrow recall)
  history:                     [],
  historyIdx:                  -1,
  savedDraft:                  '',

  // Default macro name for the 🤖 image button (null = not set)
  defaultMacro:                null,

  // y/n confirmation callback for destructive commands
  pendingConfirm:              null,

  // In-memory stack for /settings-save / /settings-restore
  settingsStack:               [],

  // Active recording chat name. Recording is always on: a temporary name is
  // assigned at startup (see newTempSessionName in chat.js) and every image is
  // auto-saved to it. The sidebar renames and restores saved chats.
  recordingName:               null,

  // Name of the chat a server-side sequence run (/api/sequence-run) is writing
  // to while this browser is attached to it. While set, client-side auto-save is
  // suppressed so the server is the sole writer of that chat file (prevents the
  // full-overwrite save from clobbering the server's incremental appends).
  liveRunSession:              null,

  // Tracks which elements are in faux-fullscreen so body overflow is only
  // restored when the last one exits.
  fauxFullscreenEls:           new Set(),
};
