# Chat skin format (`index.css`)

Authoritative reference for a project's optional `index.css` "skin" — the
mechanism that restyles the chat widget's look (colors, background images,
per-state visuals) without touching its markup or behavior. This document
is exhaustive and self-contained: following it precisely is enough to
build a valid, working skin with no other context, whether by hand or
programmatically (e.g. an LLM generating one).

The two things that actually enforce these rules are
`backend/src/project/archive/css_validator.py` (`CssValidator` — syntax
and asset-reference checks, run on every save) and
`backend/src/project/editor.py` (`ProjectEditor.put_project_file` /
`delete_project_file` — where those checks are wired in, plus the
cascade-delete rule in §6).

## 1. What a skin is, on disk/in the API

A skin is:

- **`index.css`** — one plain CSS file at the project root, alongside
  `index.yml`. Optional: a project with no `index.css` just shows the
  chat widget's own default (unstyled) look.
- **`aspect/<basename>`** — zero or more image files the stylesheet's own
  `url(...)` rules reference. Extensions: `.png`, `.jpg`/`.jpeg`, `.gif`,
  `.webp`, `.svg`. 5 MB max per file.

Both are managed through the same project-files API as every other
project file:

- `GET /api/projects/{project_name}/files` — lists every file, including
  `index.css` and each `aspect/...` asset if present.
- `PUT /api/projects/{project_name}/files/index.css` — create or edit the
  stylesheet. Body is the raw CSS text, `Content-Type: text/plain`.
- `PUT /api/projects/{project_name}/files/<any-image-name>` — create or
  edit an asset. Body is the raw image bytes, `Content-Type` must exactly
  match the extension (`image/png`, `image/jpeg`, `image/gif`,
  `image/webp`, or `image/svg+xml`). **Any name you upload under is
  canonicalized to `aspect/<basename>`** — an image extension always
  lands there regardless of what path you PUT it to (see
  `ArchiveLayout.canonicalize_name`).
- `GET /api/projects/{project_name}/files/index.css/content` and the
  equivalent for each asset — raw bytes, for fetching/previewing.
- `DELETE /api/projects/{project_name}/files/index.css` — see §6 for its
  cascade behavior.

In the editor UI (Design tab → file explorer), this is the **"Aspect"**
branch: "+ → New aspect" seeds a fresh `index.css`, uploading a
`.png`/`.jpg`/`.gif`/`.webp`/`.svg`/`.css` file there adds an asset.

## 2. Validation — what makes a save succeed or fail

Every `PUT` of `index.css` is validated **before** anything is persisted
— an invalid save changes nothing and returns the specific error below.

1. **Syntax.** The file must be syntactically valid CSS (checked with
   `tinycss2`, a syntax-only CSS3 parser — it does not understand SCSS/LESS
   nesting, and it will not catch a nonsense *value* like `color: bees;`,
   only real malformation: an unterminated string, an unclosed block, a
   declaration missing its colon, etc.). A failure looks like:

   ```text
   index.css has invalid syntax: line 4: unclosed block at end of stylesheet.
   ```

2. **Asset references.** Every `url(...)` in the file is scanned. A
   target is skipped (not checked) if it's empty or **absolute** —
   `http://...`, `https://...`, `//...`, or `data:...`. Every other
   target's **basename** (the last path segment — any directory prefix
   you write is discarded) must match the basename of an existing
   `aspect/` asset, or the save is rejected:

   ```text
   index.css references missing file(s): logo.png.
   ```

   Practical consequence: `url("logo.png")`, `url("img/logo.png")`, and
   `url("./anything/logo.png")` are all equivalent — only the file's own
   basename matters, and it must already exist under `aspect/` (upload
   the image **first**, then reference it — or accept the "missing
   file(s)" error and fix it up afterward).

3. **Reference scanning is text-based, not AST-based.** `referenced_basenames`
   regexes the raw CSS text for `url(...)` — it does **not** skip CSS
   comments. A `url(...)` left inside `/* ... */` still counts as a
   reference: it will keep a "missing file" error alive even though the
   rule is commented out, and (see §6) it will keep you from deleting
   that asset. Delete or rewrite commented-out `url(...)` calls too, not
   just live ones.

Nothing else about the CSS is validated — colors, unknown properties,
selectors that don't match anything at runtime, `@media`/`@supports`
queries, `@keyframes`, custom properties (`--foo: ...`), gradients,
transitions/animations are all plain, unrestricted CSS.

## 3. How the skin is actually applied

`index.css`'s text is injected **verbatim** into one `<style>` element
shared by the whole app (`chatSkin.js`) — there is no scoping, no CSS
Modules, no shadow DOM. Whatever selector you write matches the real,
live chat DOM exactly as an inline stylesheet would.

Two things happen to that text before it's applied, both purely
client-side:

- **`url(...)` rewriting.** Every relative `url(basename)` is rewritten
  to the asset's actual file-content endpoint
  (`GET /api/projects/{name}/files/aspect/{basename}/content[?session_id=...]`).
  This is why §2's basename-only rule holds: whatever directory you wrote
  is stripped and replaced regardless. Absolute URLs (`https://...`,
  `data:...`) are left untouched, so an external image/font/data-URI
  works too — it just isn't checked for existing at all.
- **Scope.** The skin is only ever "live" for **one** chat at a time —
  whichever chat is currently on screen (the real live chat, or the
  Design/Run tabs' own preview/test chat below). It applies while an
  actual conversation state is showing (header + message list + footer);
  it does **not** reach the splash screens ("Connecting…", "No active
  session", "Project under maintenance"), the legal-terms acceptance
  screen, or the sessions-panel drawer — those render *instead of* the
  skinned chat window, not inside it.

## 4. The style surface — what you can reliably target

The chat widget is a Vue app with its own component-scoped default
styles. A handful of elements are **deliberately left bare** (no
background/color/border of their own) specifically so a skin can paint
them with no fight; everything else still renders, but is internal UI
with its own baked-in look that a same-specificity `index.css` rule
cannot reliably beat (see the note at the end of this section). Build
your skin out of the guaranteed surface first.

### 4.1 Guaranteed hooks

| Selector | What it is | Notes |
| --- | --- | --- |
| `.chat-header` | The top bar | Fixed size (70px + the device's own safe-area inset) — background/border/`::before` decoration are yours, resizing it isn't reliable (see below). |
| `.chat-header-icon` | An empty `<div>` inside `.chat-header` | Completely unstyled by default — zero size until your CSS gives it `width`/`height`/`background-image`/etc. Typically an app icon or logo. |
| `.chat-body` (alias: `.messages`) | The scrollable message list | Background of the conversation area. Message bubbles inside it are not part of this guaranteed surface — see §4.2. |
| `.chat-footer` | The bottom strip (action buttons + text input) | Background/border of the footer. |
| `.action-buttons` / `.action-btn` | The row of manual-action buttons (when the current state has any) | Colors only — `background`, `border`, `color`, `:hover`, `:disabled`. Layout (padding, wrapping, sizing) is fixed and not meant to be overridden. |
| `.chat-window-shell` | The element wrapping header + body + footer | Rarely styled directly; it's what carries the state markers below. |
| `.state-<key>` | A class added to `.chat-window-shell` while the automaton is in state `<key>` | `<key>` is the literal state key from `index.yml` (case-sensitive). Scope any rule to one state with a descendant selector, e.g. `.state-Crisis .chat-header { ... }`. |
| `[data-state]` | Attribute on `.chat-window-shell`, same value as the current `.state-<key>` | Attribute form of the same information — handy for `[data-state="X"]` or for pairing with `[data-prev-state]` below. Absent (no attribute) before any state is reached. |
| `[data-prev-state]` | Attribute on `.chat-window-shell`: the state just left | Set on the first transition and never cleared again, so it stays available afterward. Combine both attributes to target one specific transition: `[data-prev-state="Relapse"][data-state="Crisis"] { ... }`. |

None of `.chat-header`/`.chat-body`/`.chat-footer` carry a default
`background`, `color`, or `border` — that's what "empty by design" means.
Any such property you set on them simply applies; there's no competing
rule to lose to.

### 4.2 Everything else is best-effort, not a stable API

Message bubbles (`.bubble`, `.bubble-user`, `.bubble-assistant`, the
rendered-Markdown children inside them), the text input row, the
mic/audio/spoken-text buttons, the sessions panel, and the overlay app
header are internal Vue component UI, each with its own default colors
**already declared** on the same class name, scoped to that component.
Vue's scoping compiles a selector like `.bubble-user` into
`.bubble-user[data-v-xxxxxxxx]` — a strictly higher-specificity selector
than a plain `.bubble-user` rule in `index.css`, so **per CSS cascade
rules the component's own rule wins regardless of load order**, on any
property both rules set. A skin's `.bubble-user { background: ... }`
is silently ignored.

Two practical consequences:

- You *can* still style one of these classes for a property the
  component itself never sets on it — e.g. `.chat-window { background:
  ... }` paints the full column behind the header/body/footer edges,
  because `ChatView.vue`'s own `.chat-window` rule sets layout but never
  `background`. This works today, but `.chat-window` is an internal
  class name, not a documented hook — a future refactor could add a
  background rule there (or rename the class) and silently break it.
- Design *around* this surface rather than fighting it: pick
  `.chat-body`/`.chat-footer` background colors that read well behind
  the bubbles' own fixed colors, instead of trying to recolor the
  bubbles themselves.

## 5. Previewing and testing a skin

The editor's Design tab → "Aspect" panel (opened by clicking `index.css`
in the file explorer) is a live split view:

- **Left: preview.** A real chat instance fed a static, 3-message mock
  conversation and two disabled mock action buttons, re-rendered on
  every keystroke — nothing needs to be saved to see the effect. A
  "— Preview state —" dropdown lets you pick any automaton state; picking
  one adds `.state-<key>`/`[data-state]` to the preview root (so
  state-scoped rules become visible) and jumps the code editor's cursor
  to the first `.state-<key>` occurrence in your CSS.
- **Right: code editor.** Undo/Redo/Save, same as every other file
  editor in this app. Save runs the validation in §2 — a rejected save
  changes nothing, with the specific error shown.

The **Run** tab (a real embedded test conversation, not the mock above)
has its own "Apply aspect" checkbox — off by default, remembered across
visits — so you can compare the skinned and bare (default) look side by
side using a real conversation instead of the mock one.

The live production chat always applies the saved `index.css` as soon as
it exists — there is no separate publish step beyond Save.

## 6. Deleting things

- **Deleting an `aspect/` asset still referenced by `index.css`
  (including inside a comment — see §2.3) is rejected**:

  ```text
  'aspect/logo.png' is still referenced by index.css — remove the
  reference there first (or delete index.css itself, which takes its
  assets with it).
  ```

- **Deleting `index.css` cascades**: every `aspect/` asset is deleted
  along with it, since none of them mean anything without a stylesheet
  to reference them.

## 7. Worked example

A minimal skin only needs the three guaranteed containers — this is
exactly what "New aspect" seeds for you:

```css
.chat-header {
}

.chat-body {
}

.chat-footer {
}
```

A fuller example using every mechanism above — a header icon that swaps
per state, a background image, and a one-off transition rule for one
specific state change (upload `icon-calm.svg`, `icon-alert.svg`, and
`bg.jpg` to `aspect/` first):

```css
.chat-header {
  background-color: #101425;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-header-icon {
  width: 40px;
  height: 40px;
  background-repeat: no-repeat;
  background-size: contain;
  background-image: url("icon-calm.svg");
  transition: filter 0.3s ease;
}

.chat-body {
  background: #0c0f1a url("bg.jpg") center / cover no-repeat;
}

.chat-footer {
  background: #101425;
  border-top: 1px solid #262b40;
}

/* Per-state override — <key> must match an actual state key in index.yml */
.state-alert .chat-header-icon {
  background-image: url("icon-alert.svg");
  filter: brightness(1.3);
}

/* One specific transition, not just "entering a state" */
[data-prev-state="calm"][data-state="alert"] .chat-header {
  animation: header-flash 0.5s ease;
}

@keyframes header-flash {
  from { background-color: #7f1d1d; }
  to { background-color: #101425; }
}

/* Guaranteed, unscoped surface — safe to recolor */
.action-btn {
  border-color: #4a6fa5;
  color: #4a6fa5;
}

.action-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}
```

## 8. Checklist (for hand-authoring or generating a skin programmatically)

- Write plain CSS3 — no SCSS/LESS syntax, no nesting other than what
  `@media`/`@supports` blocks themselves give you.
- Build primarily from the guaranteed hooks in §4.1: `.chat-header`,
  `.chat-header-icon`, `.chat-body`/`.messages`, `.chat-footer`,
  `.action-buttons`/`.action-btn`, `.state-<key>`, `[data-state]`,
  `[data-prev-state]`. Treat anything else (message bubbles, input row,
  mic/audio buttons, sessions panel) as unstyleable in practice.
- Reference every image as `url("basename.ext")` — path prefixes are
  ignored, so just use the basename. Upload the asset under `aspect/`
  (any upload path canonicalizes there) before or immediately after
  referencing it.
- Keep every `url(...)` basename — including ones inside `/* comments */`
  — pointing at a file that actually exists under `aspect/`, or the save
  is rejected.
- Images: `.png`/`.jpg`/`.jpeg`/`.gif`/`.webp`/`.svg` only, 5 MB max each.
- `<key>` in `.state-<key>`/`[data-state="<key>"]`/`[data-prev-state="<key>"]`
  must exactly match a state key declared in this project's `index.yml`
  (see `PROJECT_SPECS.md` §3 for state key rules) — case-sensitive,
  used literally.
- An absolute URL (`https://...`, `//...`, `data:...`) in `url(...)`
  skips the missing-asset check entirely — usable for an external
  image/font without uploading anything, at the cost of an external
  dependency.
