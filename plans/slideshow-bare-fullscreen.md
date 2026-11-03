# Slideshow: "UI-free" button in fullscreen

## Context
Every slideshow (`/review-all`, sequence review, `/archive-explore` ▶ Slideshow, …) goes
through `createSlideshow()` in `static/js/slideshow.js`. In fullscreen (native or the
iOS faux-fullscreen fallback, both kept in sync via the `.ss-fs` class) the controls row
and the progress bar stay visible at the bottom, plus 16px padding. The user likes that
default but wants a button to drop into a genuinely chrome-free view: just the media,
edge to edge, black background.

## Approach
One new state class, `.ss-bare`, on the `.slideshow` element, layered on top of `.ss-fs`.

### `static/js/slideshow.js`
- Add a button to the controls row next to the fullscreen button:
  `<button class="ss-bare-btn" title="Hide controls">` (glyph e.g. `&#x1F441;&#xFE0E;` / ⤢ —
  pick one that renders in the existing font stack, keep `&#xFE0E;` text-presentation
  like the 🗑 button).
- `setBare(on)`: toggles `.ss-bare` on `ssEl`. Click on the button → `setBare(true)`.
- Getting back out (no visible UI, so it must be discoverable without one):
  - **Tap/click on the media** while bare → `setBare(false)` and return early, instead of
    the existing behaviour (open lightbox / toggle video play). Hook into the existing
    `wrap` `click` and `touchend` tap branch; **swipe navigation stays active** while bare.
  - **Leaving fullscreen** clears it: in `syncFsState()`, if `!isFs` → `setBare(false)`.
    So Esc (native exit) and the faux-fs exit both reset it; re-entering fullscreen always
    starts with the progress bar visible, as today.
- Slideshow auto-advance, pause state, video playback and the bar animation are
  untouched — the bar is just hidden, so it keeps running and reappears in sync.

### `static/css/chat.css` (slideshow block, ~line 1135)
- `.ss-bare-btn` styled like `.ss-fullscreen-btn` (share the rule), and
  `display: none` unless `.slideshow.ss-fs` — the button only makes sense in fullscreen.
- `.slideshow.ss-fs.ss-bare`: `padding: 0`.
- `.slideshow.ss-fs.ss-bare .ss-controls, .slideshow.ss-fs.ss-bare .ss-bar-wrap`:
  `display: none`.
- `.slideshow.ss-fs.ss-bare .ss-img-wrap`: `border-radius: 0; cursor: none;` and the
  media `border-radius: 0`, so nothing frames the picture.

### Docs
Per CLAUDE.md: commit a short plan to `plans/slideshow-bare-fullscreen.md` before
implementing; afterwards rewrite it as `ADR/slideshow-bare-fullscreen.md`.

## Files
- `static/js/slideshow.js`
- `static/css/chat.css`
- `plans/` → `ADR/slideshow-bare-fullscreen.md`

## Verification
- `node --check static/js/slideshow.js` (curly-quote pitfall), `npm run test:js`,
  `./scripts/test-all` to make sure nothing regresses.
- Manual, in the running app (`docker-compose up --build -d`): open `/review-all`
  slideshow → fullscreen → new button hides controls + bar, media fills the screen,
  slides keep auto-advancing; swipe still navigates; tap restores the UI (and doesn't open
  the lightbox); Esc exits fullscreen and re-entering shows the bar again; button is not
  visible outside fullscreen; check a video slide and the iOS/faux-fullscreen path
  (narrow viewport with fullscreen API unavailable).
