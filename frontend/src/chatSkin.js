import { ref, watch } from 'vue'
import { projectFileContentUrl } from './api.js'
import { resolveCssAssetUrls } from './cssAssetUrls.js'
import { scopeSkinToChat } from './chatSkinScope.js'

export const applyAspect = ref(true)

export const manualApplyAspectPreference = ref(false)
export const skinVersion = ref(0)

export function invalidateSkin() {
  skinVersion.value++
}

export const activeChatMode = ref('live')

const sources = {}

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
    const response = await fetch(
      projectFileContentUrl(projectId, 'index.css', sessionId),
      { credentials: 'include', cache: 'no-store' }
    )
    if (!response.ok) return null
    return resolveCssAssetUrls(await response.text(), projectId, sessionId)
  }
}

export function registerSkinSource(kind, projectIdRef, sessionIdRef) {
  sources[kind] = new SessionSkinSource(projectIdRef, sessionIdRef)
  watch([projectIdRef, sessionIdRef], () => {
    if (skinIsOwnedByChat(kind)) scheduleSkinLoad()
  })
}

class ActiveChatSkinSource {
  _delegate() {
    return sources[activeChatMode.value] ?? null
  }

  key() {
    return this._delegate()?.key() ?? ''
  }

  async css() {
    return (await this._delegate()?.css()) ?? null
  }
}

export const activeChatSkin = new ActiveChatSkinSource()

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
  return holds[holds.length - 1] ?? activeChatSkin
}

function skinIsOwnedByChat(kind) {
  return currentSource() === activeChatSkin && activeChatMode.value === kind
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
  skinStyleEl.textContent = scopeSkinToChat(css)
}

const liveSkinAppliedCallbacks = []
export function onLiveSkinApplied(callback) {
  liveSkinAppliedCallbacks.push(callback)
  return () => {
    const index = liveSkinAppliedCallbacks.indexOf(callback)
    if (index !== -1) liveSkinAppliedCallbacks.splice(index, 1)
  }
}

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
  if (!applyAspect.value) {
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
  if (!applyAspect.value || currentSource() !== source || source.key() !== key) return
  if (css === null) {
    clearSkin()
    return
  }
  writeSkin(css)
  if (skinIsOwnedByChat('live')) {
    for (const callback of liveSkinAppliedCallbacks) callback()
  }
}

watch([activeChatMode, skinVersion, applyAspect], scheduleSkinLoad, { immediate: true })
