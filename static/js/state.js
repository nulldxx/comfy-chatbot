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

// Fresh, empty reference-slot structure for the /references table. A factory (not a
// shared const) so newChat / restore each get their own object rather than aliasing.
export function newReferences() {
  return { images: [null, null, null], video: null, videoAudio: null, audio: null };
}

// Deep copy of a references object, tolerant of a partial/legacy shape (missing keys
// default to empty). Used by session save/restore and the /settings-save stack.
export function cloneReferences(refs) {
  const r = refs || {};
  const images = Array.isArray(r.images) ? r.images.slice(0, 3) : [];
  while (images.length < 3) images.push(null);
  return {
    images,
    video: r.video || null,
    videoAudio: r.videoAudio || null,
    audio: r.audio || null,
  };
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
  // a /references-file/ URL. See newReferences() for the shape.
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
