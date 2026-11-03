# Bare (UI-free) fullscreen slideshow

## Context
Every slideshow — `/review-all`, sequence review, `/archive-explore`'s ▶ Slideshow —
is built by `createSlideshow()` (`static/js/slideshow.js`). In fullscreen the controls
row and the progress bar stay on screen with 16px of padding. That is a good default,
but there was no way to watch just the pictures.

## Decision
A **⤢ Hide controls** button sits in the controls row, beside the fullscreen button.
It is only displayed while the slideshow is fullscreen (`.ss-fs`). Clicking it adds
`.ss-bare` to the `.slideshow` element. The CSS then hides the controls and progress bar,
removes the padding and border radii, and hides the cursor over the media.

- **Getting the UI back**: a tap or click on the media clears `.ss-bare`. While bare it
  does not open the lightbox or toggle video playback. Swipes still navigate.
- **Leaving fullscreen clears it**: `syncFsState()` drops `.ss-bare` whenever the
  slideshow is not fullscreen. This covers Esc, native exit and the faux-fullscreen
  path, so re-entering fullscreen always starts with the bar visible.
- **Playback is unaffected**: auto-advance, pause state and the bar's CSS animation keep
  running while hidden, so the bar reappears in sync.
- Works the same under native fullscreen and the iOS faux-fullscreen fallback, because
  both are keyed off the `.ss-fs` class.

## Not covered
- There are no keyboard shortcuts.
- The controls do not reappear on mouse movement. Reveal is tap/click only, which is
  deliberate, so a nudged mouse does not bring the UI back.
