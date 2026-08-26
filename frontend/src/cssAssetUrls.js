import { projectFileContentUrl } from './api.js'

const CSS_URL_PATTERN = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi
const ABSOLUTE_URL_PATTERN = /^(https?:)?\/\/|^data:/i

// Rewrites every relative url(...) target in `cssText` to the project's
// file-content endpoint — index.css is always injected as literal text
// into a <style> element (ChatPreview.vue's live editor preview,
// chatStore.js's real served skin), so a bare url(basename) would
// otherwise resolve against the *page's* own origin instead of the API's.
// Deliberately client-side only, not something the server could do on
// its own behalf: it has no way to know what origin the page injecting
// its response actually runs on relative to itself — a same-origin
// relative path only happens to work in production, where nginx proxies
// both onto one origin, not in dev, where the two are on different ports
// with no proxy between them. `sessionId`, when given, is carried onto
// each rewritten URL so an asset resolves at the same pinned revision as
// the stylesheet referencing it (see chatStore.js's own loadSkin) —
// ChatPreview.vue omits it: a design-time preview of the live draft has
// no session to pin to.
export function resolveCssAssetUrls(cssText, projectName, sessionId) {
  return cssText.replace(CSS_URL_PATTERN, (whole, quote, target) => {
    const trimmed = target.trim()
    if (!trimmed || ABSOLUTE_URL_PATTERN.test(trimmed)) return whole
    const basename = trimmed.split('/').pop()
    return `url(${quote}${projectFileContentUrl(projectName, `aspect/${basename}`, sessionId)}${quote})`
  })
}
