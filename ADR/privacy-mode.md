# Privacy mode (`/privacy`)

## Decision
`/privacy` toggles a screen shield: every inline image and video is covered by an
opaque panel (🙈) that lifts while the mouse is over that item. The header shows
`🙈 privacy` while it is on.

## How it works
- **One body class, pure CSS.** `static/js/privacy.js` toggles `privacy-mode` on
  `<body>`; a `::after` rule in `chat.css` masks `.img-wrap` (chat bubbles),
  `.review-thumb` (`/review*` and `/archive-explore` grids) and `.composite-cell`, and
  `:hover` fades it out. Nothing is wired per render, so media rendered later — new
  generations, grids, archive browsing — is covered automatically.
- **`pointer-events: none`** on the mask, so click-to-lightbox, drag-to-`/references`
  and the right-click media menu all still reach the media. The hover buttons show
  only on hover, when the mask is already gone.
- **Per browser, not per chat.** Stored in `localStorage['privacy-mode']` (`'1'`/`'0'`),
  like `sidebar-collapsed`. It concerns who can see this screen, not the chat, so it
  deliberately stays out of `saveSession`/`restoreSession`, the `/settings-save` stack
  and `newChat`. A throwing or missing storage reads as off (`readPrivacyMode`).
- **No unmasked flash on load.** The inline `<script>` in `templates/index.html` adds
  the class before the deferred `chat.js` module restores any session media;
  `initPrivacyMode()` re-applies it from JS so the two can't disagree.
- Also listed in `/help`, autocomplete, the `/settings` launcher and the `/settings`
  summary.

## Limitations
- Touch screens have no hover: a tap usually sets a sticky `:hover` and reveals the item
  until you tap elsewhere.
- The lightbox, slideshow, mask/crop editors and `/references` thumbnails are not
  masked. Opening a viewer is a deliberate act.
- A video keeps playing under its mask. This is a screen shield, not access control.
