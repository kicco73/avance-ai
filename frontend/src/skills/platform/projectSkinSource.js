import { projectFileContentUrl } from '../../api.js'
import { resolveCssAssetUrls } from '../../cssAssetUrls.js'

export class ProjectSkinSource {
  constructor(projectIdRef) {
    this._projectIdRef = projectIdRef
  }

  key() {
    return String(this._projectIdRef.value)
  }

  async css() {
    const projectId = this._projectIdRef.value
    if (!projectId) return null
    try {
      const response = await fetch(
        projectFileContentUrl(projectId, 'index.css'),
        { credentials: 'include', cache: 'no-store' }
      )
      if (!response.ok) return null
      return resolveCssAssetUrls(await response.text(), projectId)
    } catch {
      return null
    }
  }
}
