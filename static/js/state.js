import { DEFAULT_VIDEO_SETTINGS } from './utils.js';

export const DEFAULT_DENOISE = { face: 0.35, image2image: 0.30, inpaint: 0.45, upscale: 0.15 };

export const RESOLUTION_PRESETS = {
  ipad:    { width: 2048, height: 2732, label: 'iPad Pro portrait (2048×2732)'   },
  hd:      { width: 1280, height:  720, label: 'HD 720p (1280×720)'              },
  fhd:     { width: 1920, height: 1080, label: 'Full HD 1080p (1920×1080)'       },
  square:  { width: 1024, height: 1024, label: 'Square (1024×1024)'              },
};

export const state = {
  // Server & workflow selections (null = use backend default)
  currentServer:               null,
  currentWorkflow:             null,
  currentFaceWorkflow:         null,
  currentUpscaleWorkflow:      null,
  currentImage2ImageWorkflow:  null,
  currentImage2VideoWorkflow:  null,
  currentInpaintingWorkflow:   null,
  currentRemovalWorkflow:      null,

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

  // Image2video identity reference face (LTX face-ID workflows), pinned via
  // /image2video-set-ref-image; when null the triggered image is used instead.
  refImageUrl:                 null,

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
