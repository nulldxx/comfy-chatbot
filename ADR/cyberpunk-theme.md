# Cyberpunk theme

**Status:** implemented (2026-11-22)

## Context
The UI was a stock slate + violet dark theme with every colour hard-coded: ~260 literals
in `chat.css`, an inline `<style>` in `login.html`, and ~400 more in JS inline styles.
There was no way to restyle it short of editing every one.

## Decision
- **A token layer first.** `static/css/theme.css` defines CSS custom properties, loaded
  before `chat.css` on both pages. A scripted pass mapped every literal to a token by
  *role* — slate greys to a neutral charcoal scale (`--g-*`, same steps, so contrast
  relationships held), violet to `--neon` (cyan), pink to `--neon-2` (magenta), sky to
  `--info`, red/amber/green to status tokens, and the matching `rgba()` overlays.
- **Exclusions** where `var()` cannot apply or the colour is data: canvas drawing,
  `<input type=color>`, the multi-face layer colours. One JS site appended hex alpha to a
  colour (`/jobs` status badge) and now uses `color-mix`.
- **Moderate effects, hand-applied**: neon glow on focus/hover/current, a cyan→magenta
  rule under the header and on progress bars, chamfered Generate/Sign-in buttons via
  `clip-path` (glow on a wrapper, since `clip-path` clips `filter`), magenta-edged user
  bubbles and cyan-edged bot bubbles, static scanlines.
- **Display font stored locally** (Rajdhani, OFL, Latin subset ~14KB/weight) — the
  appliance can be offline, so no Google Fonts.
- **Generated art** with the krea2-image skill in one shared style phrase: background
  grid texture, header skyline (masked to fade in from the left), login street scene,
  welcome illustration, favicon.

## Consequences
- New UI must use tokens (documented in CLAUDE.md); a re-theme is now an edit to
  `theme.css`.
- Cyan is light: text on a solid cyan fill is dark, not white.
- No animations were added; existing ones (progress marquee, dots) are untouched.
