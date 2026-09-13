import { resolveCssAssetUrls } from '../../../../../../cssAssetUrls.js'

export class DraftSkinSource {
  constructor(cssRef, projectIdRef) {
    this._cssRef = cssRef
    this._projectIdRef = projectIdRef
  }

  key() {
    return `${this._projectIdRef.value} ${this._cssRef.value}`
  }

  async css() {
    return resolveCssAssetUrls(this._cssRef.value ?? '', this._projectIdRef.value)
  }
}
