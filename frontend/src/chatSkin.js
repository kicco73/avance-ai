import { ref, watch } from 'vue'
import { projectFileContentUrl } from './api.js'
import { resolveCssAssetUrls } from './cssAssetUrls.js'

// A project's index.css "skin" — one single <style> element for the whole
// app, not one per chat store. It has exactly one owner at a time: the
// chat the app is showing (the live chat, or EditProjectView's embedded
// "Run" test chat covering it, whichever activeChatMode names), unless a
// panel showing some other app's own skin has taken it (see holdSkin).
// So there is never an ordering fight between two <style> tags, nor a
// stale skin left over from whichever was visible before.
export const applyAspect = ref(true)

// Run mode's own remembered "Apply aspect" choice — ChatView.vue forces
// the shared applyAspect flag above off on every manual-mode mount and
// back to true on unmount (see that file's own docstring), since
// applyAspect is a single app-wide flag also read by the live chat
// elsewhere. Without this separate memory, that symmetric reset would
// also wipe out whatever the user had chosen in Run mode's own toggle the
// moment they switched away and back via EditProjectView's Design/Run/Test
// segmented control (RunChat.vue, and therefore ChatView.vue, unmounts on
// every such switch).
export const manualApplyAspectPreference = ref(false)
export const skinVersion = ref(0)

// "Whatever owns the skin right now, read it again" — the bump every
// caller that changed what a source would answer goes through.
export function invalidateSkin() {
  skinVersion.value++
}

// 'live' | 'test' — set by EditProjectView.vue's own mode switch.
export const activeChatMode = ref('live')

const sources = {}

// A chat's own skin: the project's saved index.css, read at the revision
// the session showing it is pinned to.
class SessionSkinSource {
  constructor(projectIdRef, sessionIdRef) {
    this._projectIdRef = projectIdRef
    this._sessionIdRef = sessionIdRef
  }

  key() {
    return `${this._projectIdRef.value} ${this._sessionIdRef.value}`
  }

  async css() {
    const projectId = this._projectIdRef.value ?? null
    const sessionId = this._sessionIdRef.value ?? null
    if (!projectId || sessionId == null) return null
    // credentials: 'include' — this bypasses api.js's apiFetch (which
    // already sets it), so without this explicit option the request
    // drops the session cookie behind AuthMiddleware whenever frontend
    // and backend aren't same-origin, 401s, and this silently reads as
    // "no index.css". cache: 'no-store' — this fires on every
    // index.yml/css save via skinVersion, and the URL doesn't otherwise
    // change; relying on the browser to always revalidate a
    // Cache-Control: no-cache response left the skin looking stale in
    // practice, so this skips the HTTP cache entirely instead of trusting
    // revalidation.
    const response = await fetch(
      projectFileContentUrl(projectId, 'index.css', sessionId),
      { credentials: 'include', cache: 'no-store' }
    )
    if (!response.ok) return null
    // The fetched text's own url(...) references are still bare basenames
    // (see get_project_file_content's own docstring on why the server never
    // rewrites these itself) — resolved here into fetchable URLs, so a
    // background-image etc. actually loads instead of silently 404ing
    // against whatever origin this page happens to be running on.
    return resolveCssAssetUrls(await response.text(), projectId, sessionId)
  }
}

// Called once by each chat store instance right after creation — never
// by a component directly. Each source watches only its own refs, so
// registration order between stores never matters and neither has to
// exist yet for the other to work correctly.
export function registerSkinSource(kind, projectIdRef, sessionIdRef) {
  sources[kind] = new SessionSkinSource(projectIdRef, sessionIdRef)
  watch([projectIdRef, sessionIdRef], () => {
    if (currentSource() === sources[kind]) scheduleSkinLoad()
  })
}

// The panels that show an app's skin without being that app's chat — the
// store's "Try me!", Manage projects' "Test", the design editor's live
// draft. Innermost last: whoever took it last is showing it, and a panel
// leaving hands it back only if it still has it, so a panel on its way
// out never repaints over the one that replaced it.
const holds = []

export function holdSkin(source) {
  holds.push(source)
  scheduleSkinLoad()
  return () => {
    const index = holds.lastIndexOf(source)
    if (index === -1) return
    holds.splice(index, 1)
    scheduleSkinLoad()
  }
}

function currentSource() {
  return holds[holds.length - 1] ?? sources[activeChatMode.value] ?? null
}

let skinStyleEl = null

function clearSkin() {
  skinStyleEl?.remove()
  skinStyleEl = null
}

function writeSkin(css) {
  if (!skinStyleEl) {
    skinStyleEl = document.createElement('style')
    document.head.appendChild(skinStyleEl)
  }
  skinStyleEl.textContent = css
}

// Registrable hook for "the live skin's own CSS was just (re)written" —
// LiveChatWindow.vue's own canvas-color sync (see its comment) needs to
// re-read .chat-footer's computed background right after that happens,
// but this module can't import that component directly (it's the other
// way around: chat stores register themselves here, not the reverse) —
// a plain callback list keeps the dependency one-directional. Returns an
// unregister function, since LiveChatWindow.vue's own instance can mount
// and unmount many times across a single admin session (push/pop 'chat').
const liveSkinAppliedCallbacks = []
export function onLiveSkinApplied(callback) {
  liveSkinAppliedCallbacks.push(callback)
  return () => {
    const index = liveSkinAppliedCallbacks.indexOf(callback)
    if (index !== -1) liveSkinAppliedCallbacks.splice(index, 1)
  }
}

// One read per tick, whoever asked for it: selecting another app in the
// store releases the panel that was showing the previous one and takes
// the skin for the new one in the same breath, and the element only ever
// has to end up showing the second of those.
let pendingLoad = null

function scheduleSkinLoad() {
  pendingLoad = pendingLoad ?? Promise.resolve().then(() => {
    pendingLoad = null
    return loadSkin()
  })
  return pendingLoad
}

async function loadSkin() {
  const source = currentSource()
  if (!applyAspect.value || source === null) {
    clearSkin()
    return
  }
  const key = source.key()
  let css
  try {
    css = await source.css()
  } catch {
    return
  }
  // Stale-response guard: applyAspect, who owns the skin, and what that
  // owner would answer can all move on while this is in flight. A later
  // loadSkin() call (triggered by whichever of those changed) already
  // reflects the current state, or will; without this check the earlier,
  // now-stale answer would win the race and re-apply a skin something
  // already turned off or took over.
  if (!applyAspect.value || currentSource() !== source || source.key() !== key) return
  if (css === null) {
    clearSkin()
    return
  }
  writeSkin(css)
  if (source === sources.live) {
    for (const callback of liveSkinAppliedCallbacks) callback()
  }
}

// Module-level, not inside any component, so it never needs an
// onBeforeUnmount to stop it. Covers everything not specific to one
// source (see registerSkinSource above for the per-source half) — a mode
// switch, an explicit invalidateSkin() bump, or the applyAspect toggle.
watch([activeChatMode, skinVersion, applyAspect], scheduleSkinLoad, { immediate: true })
