import { ref, watch } from 'vue'
import { projectFileContentUrl } from './api.js'
import { resolveCssAssetUrls } from './cssAssetUrls.js'

// A project's index.css "skin" — one single <style> element for the whole
// app, not one per chat store. Whichever chat is currently the one on
// screen (the live chat, or EditProjectView's embedded "Run" test chat
// covering it) registers its own project/session refs here; only that
// one's skin is ever applied, so there's never an ordering fight between
// two <style> tags nor a stale skin left over from whichever was visible
// before a mode switch.
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
export function invalidateSkin() {
  skinVersion.value++
}

// 'live' | 'test' — set by EditProjectView.vue's own mode switch.
export const activeChatMode = ref('live')

const sources = {}

// Called once by each chat store instance right after creation — never
// by a component directly. Each source watches only its own refs, so
// registration order between stores never matters and neither has to
// exist yet for the other to work correctly.
export function registerSkinSource(kind, projectIdRef, sessionIdRef) {
  sources[kind] = { projectIdRef, sessionIdRef }
  watch([projectIdRef, sessionIdRef], () => {
    if (activeChatMode.value === kind) loadSkin()
  })
}

let skinStyleEl = null

function clearSkin() {
  skinStyleEl?.remove()
  skinStyleEl = null
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

// Writes `css` into the one shared skin element, creating it on first use.
// Shared by loadSkin's own fetched-and-saved skin below and by
// ChatPreview.vue's live draft — both go through this single function so
// there is still ever only one tag, never a second one racing it.
export function setSkinCss(css, projectId, sessionId) {
  if (!skinStyleEl) {
    skinStyleEl = document.createElement('style')
    document.head.appendChild(skinStyleEl)
  }
  skinStyleEl.textContent = resolveCssAssetUrls(css, projectId, sessionId)
}

async function loadSkin() {
  const source = sources[activeChatMode.value]
  const projectId = source?.projectIdRef.value ?? null
  const sessionId = source?.sessionIdRef.value ?? null
  if (!applyAspect.value || !projectId || sessionId == null) {
    clearSkin()
    return
  }
  let css
  try {
    // credentials: 'include' — this bypasses api.js's apiFetch (which
    // already sets it), so without this explicit option the request
    // drops the session cookie behind AuthMiddleware whenever frontend
    // and backend aren't same-origin, 401s, and loadSkin silently treats
    // that the same as "no index.css". cache: 'no-store' — this fires on
    // every index.yml/css save via skinVersion, and the URL doesn't
    // otherwise change; relying on the browser to always revalidate a
    // Cache-Control: no-cache response left the skin looking stale in
    // practice, so this skips the HTTP cache entirely instead of trusting
    // revalidation.
    const response = await fetch(
      projectFileContentUrl(projectId, 'index.css', sessionId),
      { credentials: 'include', cache: 'no-store' }
    )
    if (!response.ok) {
      clearSkin()
      return
    }
    css = await response.text()
  } catch {
    return
  }
  // Stale-response guard: applyAspect/mode/project/session can all move on
  // while this fetch is in flight. A later loadSkin() call (triggered by
  // whichever of those changed) already reflects the current state, or
  // will; without this check the earlier, now-stale response would win
  // the race and re-apply a skin something already turned off/switched
  // away from.
  const stillCurrent = sources[activeChatMode.value] === source
    && source.projectIdRef.value === projectId && source.sessionIdRef.value === sessionId
  if (!applyAspect.value || !stillCurrent) return
  // The fetched text's own url(...) references are still bare basenames
  // (see get_project_file_content's own docstring on why the server never
  // rewrites these itself) — resolved here into fetchable URLs the exact
  // same way ChatPreview.vue's live-editor preview already does, so a
  // background-image etc. actually loads instead of silently 404ing
  // against whatever origin this page happens to be running on.
  setSkinCss(css, projectId, sessionId)
  if (activeChatMode.value === 'live') {
    for (const callback of liveSkinAppliedCallbacks) callback()
  }
}

// Module-level, not inside any component, so it never needs an
// onBeforeUnmount to stop it. Covers everything not specific to one
// source (see registerSkinSource above for the per-source half) — a mode
// switch, an explicit invalidateSkin() bump, or the applyAspect toggle.
watch([activeChatMode, skinVersion, applyAspect], loadSkin, { immediate: true })
