import { projectFileContentUrl } from './api.js'

const CSS_URL_PATTERN = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi
const ABSOLUTE_URL_PATTERN = /^(https?:)?\/\/|^data:/i

// Rewrites every relative url(...) target in `cssText` to the project's
// file-content endpoint. index.css is injected as literal text into a
// <style> element for the live editor preview (see ChatPreview.vue) — a
// bare url(basename) would otherwise resolve against the page's own
// origin instead of the file it names. Mirrors ProjectService's own
// resolve_css_asset_urls, which does the same for the real served skin.
export function resolveCssAssetUrls(cssText, projectName) {
  return cssText.replace(CSS_URL_PATTERN, (whole, quote, target) => {
    const trimmed = target.trim()
    if (!trimmed || ABSOLUTE_URL_PATTERN.test(trimmed)) return whole
    const basename = trimmed.split('/').pop()
    return `url(${quote}${projectFileContentUrl(projectName, basename)}${quote})`
  })
}
