import { appStoreFileContentUrl } from './api.js'
import { resolveCssAssetUrls } from '../../cssAssetUrls.js'

// An app's skin as the store shows it: index.css read at the published
// revision, through the app-store's own endpoint — what somebody
// browsing apps is entitled to see, whether or not any session of that
// app is open yet.
export class AppSkinSource {
  constructor(appIdRef) {
    this._appIdRef = appIdRef
  }

  key() {
    return String(this._appIdRef.value)
  }

  async css() {
    const appId = this._appIdRef.value
    if (!appId) return null
    try {
      const response = await fetch(
        appStoreFileContentUrl(appId, 'index.css'),
        { credentials: 'include', cache: 'no-store' }
      )
      if (!response.ok) return null
      return resolveCssAssetUrls(await response.text(), appId)
    } catch {
      return null
    }
  }
}
