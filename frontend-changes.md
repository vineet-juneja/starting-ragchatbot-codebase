# Frontend Changes

Two related features on the `ui_feature` branch:

1. **Theme Toggle Button** — an icon-based light/dark switch.
2. **Light Theme CSS Variables** — an accessibility-vetted light palette.

---

# 1. Theme Toggle Button

Adds an icon-based light/dark theme toggle to the Course Materials Assistant UI.

## Summary

- New circular toggle button fixed to the **top-right** of the viewport.
- **Sun / moon icons** (inline SVG, `currentColor`) that cross-fade and rotate when switched.
- **Smooth transitions** on the icons and on the themed surfaces (background/text/border) when the theme changes.
- The app was previously dark-only; a full **light theme** palette was added via CSS variables.
- Preference is **persisted to `localStorage`** and re-applied before first paint (no flash of wrong theme).
- Button is a native `<button>` — fully **keyboard-navigable** (Tab to focus, Enter/Space to activate), with a visible `:focus-visible` ring and `aria-pressed` / dynamic `aria-label`.
- Respects `prefers-reduced-motion` (transitions disabled).

## Files changed

### `frontend/index.html`

- Added an inline `<head>` script that reads `themePreference` from `localStorage` and sets `data-theme="light"` on `<html>` before the stylesheet renders, preventing a theme flash.
- Added the toggle button markup as the first child of `.container`:
  - `<button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light theme" aria-pressed="false" title="Toggle light / dark theme">`
  - Contains two SVGs: `.theme-toggle__icon--sun` (feather-style sun) and `.theme-toggle__icon--moon` (crescent), both `aria-hidden="true"`.
- Bumped cache-busting query strings: `style.css?v=12 → v=13`, `script.js?v=12 → v=13`.

### `frontend/style.css`

- Relabeled `:root` as the dark theme and added `--code-bg` token.
- Added `:root[data-theme="light"]` block overriding all palette variables (background, surface, text, borders, shadow, welcome, link, code background) for an accessible light theme.
- Added a shared `transition` rule for themed elements so palette swaps animate smoothly (0.3s), plus a `prefers-reduced-motion` guard that disables them.
- Added `.theme-toggle` styles:
  - `position: fixed; top: 1.25rem; right: 1.25rem; z-index: 100`, 44×44 circular, uses `var(--surface)` / `var(--border-color)` / `var(--shadow)` to match the existing aesthetic.
  - `:hover` (lift + primary border), `:active` (settle), `:focus-visible` (3px `--focus-ring`, matching other controls).
- Added `.theme-toggle__icon` styles: absolutely stacked, `opacity` + `transform: rotate()/scale()` transition (0.3s / 0.4s cubic-bezier). Moon shown by default; `:root[data-theme="light"]` swaps to the sun.
- Updated `.message-content code` / `pre` to use `var(--code-bg)` so code blocks read correctly in light mode.
- Mobile (`max-width: 768px`): toggle shrinks to 40×40 and moves to `top/right: 0.75rem`.

### `frontend/script.js`

- Added `themeToggle` to the tracked DOM elements.
- Added theme module:
  - `THEME_STORAGE_KEY = 'themePreference'`.
  - `applyTheme(theme)` — toggles `data-theme` on `<html>` and syncs `aria-pressed` + `aria-label`.
  - `getStoredTheme()` / `storeTheme()` — `localStorage` access wrapped in try/catch.
  - `initTheme()` — syncs ARIA state on load with whatever the inline script already applied.
  - `toggleTheme()` — flips dark ⇄ light, applies, and persists.
- `initTheme()` is called on `DOMContentLoaded`; the button gets a `click` listener (native button keyboard handling covers Enter/Space).

---

# 2. Light Theme CSS Variables

Expands the `:root[data-theme="light"]` block into a full, accessibility-checked
light palette, and tokenizes the last hard-coded colors so every surface responds
to the theme.

## Palette (all values in `frontend/style.css`)

| Token | Light value | Notes / contrast |
|---|---|---|
| `--background` | `#ffffff` | page background |
| `--surface` | `#f1f5f9` | sidebar, assistant bubble, input |
| `--surface-hover` | `#e2e8f0` | hover state |
| `--text-primary` | `#0f172a` | 17.9:1 on `#ffffff`, 16.1:1 on `#f1f5f9` (AAA) |
| `--text-secondary` | `#475569` | 7.4:1 on `#ffffff`, 6.7:1 on `#f1f5f9` (AAA) |
| `--primary-color` | `#1d4ed8` | darkened from `#2563eb`: 5.9:1 as text on white, 5.2:1 on `#e2e8f0`, and behind white button icons — all clear AA |
| `--primary-hover` | `#1e40af` | 7.4:1 on white |
| `--border-color` | `#cbd5e1` | subtle dividers; interactive controls get a 3px `--focus-ring` on focus for the 3:1 state indicator |
| `--user-message` | `#2563eb` | white text on it = 4.6:1 (AA) |
| `--assistant-message` | `#e2e8f0` | — |
| `--shadow` | `0 4px 6px -1px rgba(15,23,42,0.1)` | softer for a light UI |
| `--focus-ring` | `rgba(29,78,216,0.35)` | visible focus halo |
| `--welcome-bg` | `#eff6ff` | `--text-primary` on it = 16.6:1 |
| `--welcome-border` | `#2563eb` | — |
| `--link-color` | `#0369a1` | 5.6:1 on white, 5.0:1 on `#f1f5f9` (AA) |
| `--code-bg` | `rgba(15,23,42,0.06)` | inline / block code tint |
| `--error-text` | `#b91c1c` on `--error-bg` `#fef2f2` | 6.4:1 (AA) |
| `--success-text` | `#15803d` on `--success-bg` `#f0fdf4` | 4.8:1 (AA) |

Contrast ratios are annotated inline as comments in the `:root[data-theme="light"]`
rule.

## New tokens added to **both** themes (`:root` and the light override)

- `--error-bg`, `--error-text`, `--error-border`
- `--success-bg`, `--success-text`, `--success-border`

Dark theme keeps its previous literal values (`#f87171` / `#4ade80` tints); the
light theme gets the darker text shades above so status messages stay readable on
a light card.

## Rules updated to consume the tokens

- `.error-message` — `background` / `color` / `border` now use `--error-*`
  (were hard-coded `rgba(239,68,68,…)` / `#f87171`).
- `.success-message` — likewise now uses `--success-*` (were `rgba(34,197,94,…)`
  / `#4ade80`).
- `.message-content blockquote` — `border-left` fixed from the undefined
  `var(--primary)` to `var(--primary-color)` so the accent renders in both themes.

## Accessibility summary

- All body-text foreground/background pairs meet or exceed WCAG 2.1 **AA (4.5:1)**;
  primary/secondary text pairs reach **AAA**.
- `--primary-color` was deliberately darkened for the light theme because at
  `#2563eb` it fell just under 4.5:1 as text on `#e2e8f0` (used by
  `.suggested-item:hover`).
- Focus is never conveyed by color alone — every interactive control keeps its
  3px `--focus-ring` outline, now tuned for the light background.
- No markup or JS changes were needed; the toggle from feature 1 already drives
  `data-theme` and persists the choice.
