// Keeps a bubble's tool status line ("Searching Flights…") on screen for
// at least TOOL_STATUS_MIN_MS once shown, however fast the tool's own
// result arrives — the backend never delays anything; the minimum is a
// display concern and lives here only. `bubble` is whichever object owns
// the status line and exposes setStatusText(text).
export const TOOL_STATUS_MIN_MS = 1500

export class ToolStatusHold {
  constructor(bubble) {
    this._bubble = bubble
    this._shownAt = null
    this._timer = null
  }

  show(text) {
    this._cancelTimer()
    this._shownAt = Date.now()
    this._bubble.setStatusText(text)
  }

  hide() {
    if (this._shownAt === null) return
    const remaining = TOOL_STATUS_MIN_MS - (Date.now() - this._shownAt)
    if (remaining <= 0) {
      this._clear()
      return
    }
    if (this._timer === null) this._timer = setTimeout(() => this._clear(), remaining)
  }

  cancel() {
    this._cancelTimer()
    this._shownAt = null
  }

  _clear() {
    this._cancelTimer()
    this._shownAt = null
    this._bubble.setStatusText('')
  }

  _cancelTimer() {
    if (this._timer !== null) {
      clearTimeout(this._timer)
      this._timer = null
    }
  }
}
