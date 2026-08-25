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
export function registerSkinSource(kind, projectNameRef, sessionIdRef) {
  sources[kind] = { projectNameRef, sessionIdRef }
  watch([projectNameRef, sessionIdRef], () => {
    if (activeChatMode.value === kind) loadSkin()
  })
}

let skinStyleEl = null

function clearSkin() {
  skinStyleEl?.remove()
  skinStyleEl = null
}

// Writes `css` into the one shared skin element, creating it on first use.
// Shared by loadSkin's own fetched-and-saved skin below and by
// ChatPreview.vue's live draft — both go through this single function so
// there is still ever only one tag, never a second one racing it.
export function setSkinCss(css, projectName, sessionId) {
  if (!skinStyleEl) {
    skinStyleEl = document.createElement('style')
    document.head.appendChild(skinStyleEl)
  }
  skinStyleEl.textContent = resolveCssAssetUrls(css, projectName, sessionId)
}

async function loadSkin() {
  const source = sources[activeChatMode.value]
  const projectName = source?.projectNameRef.value ?? null
  const sessionId = source?.sessionIdRef.value ?? null
  if (!applyAspect.value || !projectName || sessionId == null) {
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
      projectFileContentUrl(projectName, 'index.css', sessionId),
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
    && source.projectNameRef.value === projectName && source.sessionIdRef.value === sessionId
  if (!applyAspect.value || !stillCurrent) return
  // The fetched text's own url(...) references are still bare basenames
  // (see get_project_file_content's own docstring on why the server never
  // rewrites these itself) — resolved here into fetchable URLs the exact
  // same way ChatPreview.vue's live-editor preview already does, so a
  // background-image etc. actually loads instead of silently 404ing
  // against whatever origin this page happens to be running on.
  setSkinCss(css, projectName, sessionId)
}

// Module-level, not inside any component, so it never needs an
// onBeforeUnmount to stop it. Covers everything not specific to one
// source (see registerSkinSource above for the per-source half) — a mode
// switch, an explicit invalidateSkin() bump, or the applyAspect toggle.
watch([activeChatMode, skinVersion, applyAspect], loadSkin, { immediate: true })
