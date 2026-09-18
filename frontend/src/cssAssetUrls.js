import { projectFileContentUrl } from './api.js'

const CSS_URL_PATTERN = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi
const ABSOLUTE_URL_PATTERN = /^(https?:)?\/\/|^data:/i

export function resolveCssAssetUrls(cssText, projectId, sessionId) {
  return cssText.replace(CSS_URL_PATTERN, (whole, quote, target) => {
    const trimmed = target.trim()
    if (!trimmed || ABSOLUTE_URL_PATTERN.test(trimmed)) return whole
    const basename = trimmed.split('/').pop()
    return `url(${quote}${projectFileContentUrl(projectId, `media/${basename}`, sessionId)}${quote})`
  })
}
