import { resolveCssAssetUrls } from '../../../../../../cssAssetUrls.js'

// The index.css being typed in the editor, as the design preview shows
// it: no fetch, the draft itself. No sessionId — a design-time preview of
// the live draft has no session to pin an asset url(...) to (see
// resolveCssAssetUrls's own docstring).
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
