# Privacy mode (`/privacy`)

## Context
Generated media shows in the chat in full, so anyone glancing at the screen sees it.
`/privacy` toggles a mode in which every inline image/video is covered by an opaque
panel that lifts only while the mouse is over that item. Decided with the user:
- **Scope:** all inline media — chat bubble media (`.img-wrap`) plus grid thumbnails
  (`.review-thumb` in `/review-all` and `/archive-explore`, `.composite-cell`). The
  lightbox and slideshow stay unmasked (opening them is deliberate).
- **Persistence:** per browser, in `localStorage` (like `sidebar-collapsed` in
  `sidebar.js`), so it survives reloads, `newChat` and chat switching. Not part of
  session save/restore or the `/settings-save` stack.

## Approach — one body class plus CSS, no per-render wiring
Put a `privacy-mode` class on `<body>` and let CSS mask every matching container. Media
rendered later (new generations, grids, archive browsing) is covered automatically, the
same way `mediamenu.js` uses one delegated listener rather than wiring each render.

### 1. `static/js/privacy.js` (new, small)
- `PRIVACY_STORAGE_KEY = 'privacy-mode'`
- `readPrivacyMode(storage)`: pure. Returns true iff the stored value is `'1'`, and
  catches any error thrown by the accessor (private window, blocked storage).
- `isPrivacyMode()`: reads `document.body.classList`.
- `setPrivacyMode(on)`: toggles the body class and writes `'1'`/`'0'` inside try/catch.
- `initPrivacyMode()`: `setPrivacyMode(readPrivacyMode(localStorage))` without the write.

### 2. Apply before any media paints
- In the inline `<script>` at the end of `templates/index.html`, add a one-line
  try/catch that adds `privacy-mode` to `document.body` when
  `localStorage['privacy-mode'] === '1'`. It runs before the deferred `chat.js` module
  renders any restored session images, so nothing flashes unmasked on load.
- `chat.js` still calls `initPrivacyMode()` at startup, so the JS state and the class
  can't disagree.

### 3. CSS (`static/css/chat.css`, next to the `.img-wrap` rules ~l.280)
```css
body.privacy-mode :is(.img-wrap, .review-thumb, .composite-cell) { position: relative; }
body.privacy-mode :is(.img-wrap, .review-thumb, .composite-cell)::after {
  content: '🙈'; position: absolute; inset: 0; z-index: 5;
  display: flex; align-items: center; justify-content: center;
  background: #0f172a; border-radius: inherit; font-size: 28px; color: #475569;
  pointer-events: none;            /* clicks, drags and right-click still reach the media */
  transition: opacity .15s;
}
body.privacy-mode :is(.img-wrap, .review-thumb, .composite-cell):hover::after { opacity: 0; }
```
- Check the `z-index` against the hover buttons (`.img-del`, `.img-face`, …) and the
  i2i dialog that floats inside `.img-wrap`. Everything interactive is shown only on
  hover, when the mask is already gone, so the mask can sit on top. Confirm that
  `.review-thumb` and `.composite-cell` are already positioned; if one isn't, the
  `position: relative` rule above covers it.
- A `<video>` keeps playing under the mask. The mask hides pixels only, and videos
  already autoplay muted.

### 4. Command wiring (`static/js/commands.js`, `autocomplete.js`, `chat.js`)
- `/privacy` handler next to `/t2v` (~l.2204): echo the user line, flip the mode with
  `setPrivacyMode(!isPrivacyMode())`, call `deps.updateHeaderStatus()` and reply with
  **ON** ("images are hidden until you hover over them; this browser only") or **OFF**.
- `updateHeaderStatus()` (`chat.js:115`): append `  ·  🙈 privacy` when it is on,
  following the `🎬 t2v` badge, so the mode is always visible.
- `autocomplete.js` entry, a help entry beside `/t2v` (~l.2663), a `SETTINGS_MENU` item
  "Privacy mode (toggle)" with `mode: 'run'` (~l.1667), and a "Privacy mode" row in the
  `/settings` summary (~l.577).

### 5. Tests and docs
- `tests/js/privacy.test.js`: `readPrivacyMode` with a fake storage returning
  `'1'`/`'0'`/`null`, and one whose `getItem` throws. The Jest env is `node`, so only
  the pure helper is tested.
- Save this plan as `plans/privacy-mode.md` and commit it before implementing.
  Afterwards, write `ADR/privacy-mode.md` and add a short "Privacy mode" section to
  `CLAUDE.md`.

## Known limitations (document in ADR)
- Touch devices have no hover. A tap usually sets a sticky `:hover` and reveals the
  item until you tap elsewhere.
- Lightbox, slideshow, mask/crop editors and `/references` thumbnails are not masked.
- This is a screen shield, not access control. Save, Copy and dragging still work.

## Verification
- `node --check` on every edited JS file (curly-quote pitfall), then `npm run test:js`
  and `./scripts/test-all`.
- Manual check with `docker-compose up --build -d`:
  - `/privacy` masks the existing chat images and newly generated ones, and hover
    reveals each one.
  - `/review-all` and `/archive-explore` thumbnails are masked; the lightbox is not.
  - Reloading keeps the mode with no unmasked flash; `newChat` keeps it too.
  - The header shows `🙈 privacy`; `/privacy` again turns everything off.
  - Right-click menu, drag to `/references` and the hover buttons still work.
