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

  // Active slideshow controller (keyboard navigation target)
  activeSlideshowCtrl:         null,

  // Prompt history (up/down arrow recall)
  history:                     [],
  historyIdx:                  -1,
  savedDraft:                  '',

  // y/n confirmation callback for destructive commands
  pendingConfirm:              null,

  // Tracks which elements are in faux-fullscreen so body overflow is only
  // restored when the last one exits.
  fauxFullscreenEls:           new Set(),
};
