# Cyberpunk theme revamp

## Context
The UI is a stock "slate + violet" dark theme (`#1a1a2e` bg, `#7c3aed`/`#a78bfa` accent)
and reads as generic. Goal: a cyberpunk look — dark charcoal base, neon cyan/magenta
highlights, moderate effects, a stored display font, and generated graphical elements
made with the `krea2-image` skill.

Decisions (from the user): **cyan + magenta** palette · **moderate** effects ·
**stored OFL display font** for headings and labels, with the system font kept for body text.

Current state: there is no theming layer. `static/css/chat.css` (1662 lines) has about 260 hard-coded
colours, `templates/login.html` has its own inline `<style>`, and the JS sets about 400 colour
literals inline (mostly `commands.js`, `chat.js`, `editors.js`). Static files are served by
Flask's default unauthenticated `/static` route and copied in by `COPY static/ static/`
in the Dockerfile, so new assets need no backend or Dockerfile changes.

## Plan (commit `plans/cyberpunk-theme.md` first, per repo convention)

### 1. Theme tokens — `static/css/theme.css` (new, loaded before chat.css)
`:root` custom properties:
- surfaces: `--bg #16171b`, `--bg-elev #1c1e23`, `--surface #1f2126`, `--surface-2 #272a31`,
  `--border #33363f`, `--border-strong #4a4e5a`
- text: `--text #e6e8ee`, `--text-dim #a3a8b5`, `--text-mute #6b7080`
- neon: `--neon #00f0ff` (primary), `--neon-2 #ff2bd6` (accent), `--warn #f5ff3b`,
  `--danger #ff3860`, `--ok #39ff88`, plus `-soft` rgba variants (≈15% alpha) for fills
- `--glow: 0 0 6px var(--neon), 0 0 18px rgba(0,240,255,.35)` and `--glow-2` (magenta)
- `--font-display: "Rajdhani", ui-monospace, monospace`, and `--chamfer` (clip-path polygon
  for angled corners)
- `@font-face` for Rajdhani 500/700 woff2 (OFL, from the google/fonts GitHub repo) in
  `static/fonts/`, with its `OFL.txt` alongside.

### 2. Convert `chat.css` to the tokens
Map the existing palette one-to-one, so the meaning of each colour is kept:
- `#1a1a2e/#16213e/#0f172a/#0b1224` → bg / bg-elev; `#1e293b` → surface; `#334155/#0f3460` → border
- `#7c3aed/#a78bfa/#c4b5fd/#1e1040` (violet accent) → `--neon` (and its soft fill)
- `#f472b6/#ec4899` (pink) → `--neon-2`; `#38bdf8/#67e8f9/#7dd3fc` → `--neon`
- `#f87171/#ef4444/#fca5a5` → `--danger`; `#facc15/#fb923c` → `--warn`; `#34d399/#4ade80` → `--ok`
- slate text greys → `--text*`; `rgba(15,23,42,.75/.85)` overlays → `rgba(22,23,27,…)`

Leave alone the colours that carry data rather than theme: the `--face-colour` face layers
(red/green/blue/yellow) in `facemulti`, and brush/mask colours.

### 3. Moderate effects (in chat.css)
- **Header**: charcoal with a 2px cyan→magenta gradient bottom border, `h1` in the
  display font, uppercase, letter-spaced, with a cyan text-shadow glow; generated header art
  as a faint `background-image` on the right.
- **Body**: the generated background texture (charcoal, faint neon grid / city haze)
  at low opacity and `background-attachment: fixed`, plus a `body::after` scanline overlay
  (`repeating-linear-gradient`, ~3% opacity, `pointer-events:none`).
- **Bubbles**: bot bubbles get a surface fill with a 1px border and a cyan left edge; user bubbles get a
  dark-magenta fill with a magenta border, instead of solid violet. Chat text keeps full contrast.
- **Buttons / inputs**: `--chamfer` angled corners on primary buttons (Generate, sel-btn);
  focus/hover = neon border + `--glow`. `.sel-btn.current` = cyan border + soft fill.
- **Sidebar**: same tokens, with a cyan glow on the active chat.
- **Progress bar**: cyan→magenta gradient fill with a glow (the existing `.determinate` logic is untouched).
- Respect `prefers-reduced-motion` (no flicker or animated glows); the scanlines are static.
- Keep the `privacy-mode` `::after` masks working. They use `::after` on media, not body,
  so there is no clash, but check the z-index of the scanline overlay against the lightbox and slideshow.

### 4. JS inline colours
Replace hex literals in inline style strings with `var(--token)` using the same mapping
(`commands.js`, `chat.js`, `archive.js`, `grids.js`, `sidebar.js`, `slideshow.js`,
`story.js`, `utils.js`). Exclusions: `&#128465;`-style HTML entities (not colours),
canvas `fillStyle`/`strokeStyle` (where `var()` does not work) in `editors.js`/`facemulti.js`,
and the face-layer colours. Do it with a careful scripted pass plus review of the diff, then
`node --check` on every edited file (curly-quote pitfall).

### 5. Login page — `templates/login.html`
Link `theme.css`, convert its inline `<style>` to the tokens, use the generated key art as a
full-bleed background behind a chamfered, glowing login card.

### 6. Generated art (krea2-image skill → `static/img/`)
Use one consistent style phrase across all of them: *"dark charcoal grey, neon cyan and magenta
accents, cyberpunk, clean, low-contrast, subtle film grain"*.
- `bg-texture.webp` (1920×1080): seamless low-contrast charcoal texture with faint neon
  perspective grid and haze, no focal point — the body background.
- `header-art.webp` (2048×256 via `--size`): wide neon skyline / circuit-trace strip,
  fading to charcoal on the left so the title stays readable.
- `login-hero.webp` (1920×1080): rainy neon city street at night, empty centre for the card.
- `empty-state.webp` (~800×450): small illustration for the welcome bubble (glowing
  terminal / camera lens), shown under the greeting in `index.html`.
- `favicon.png` (512→64/32): neon lightning-bolt glyph, linked from both templates.
Check each one with Read before using it, and re-roll seeds where needed. Keep the WebP files small (<300KB).

### 7. Docs
CLAUDE.md: a short "Theming (`theme.css`)" section — new colours must use the tokens, and
JS inline styles should use `var(--…)`. ADR `ADR/cyberpunk-theme.md` once it is done.

## Critical files
`static/css/theme.css` (new), `static/css/chat.css`, `templates/index.html`,
`templates/login.html`, `static/js/{commands,chat,archive,grids,sidebar,slideshow,story,utils}.js`,
`static/img/*`, `static/fonts/*`, CLAUDE.md, `plans/` + `ADR/`.

## Verification
- `node --check static/js/*.js` and `npm run test:js` (some utils tests may assert colour
  strings; update them to the token form).
- `./scripts/test-all` (Python plus Docker build — confirms the new static assets ship).
- `docker-compose up --build -d`, then screenshot the main flows with claude-in-chrome:
  login, empty chat, a generation with progress bar, `/help`, `/settings`, `/references`,
  `/server-status`, the sidebar expanded, the lightbox, `/privacy` on, and the mobile width. Check
  contrast and that nothing is still violet.
- `grep -nE '#(7c3aed|a78bfa|1a1a2e|16213e)' static/ templates/` should return nothing.
- Then offer `push-to-portainer`, confirming before the Portainer redeploy.
