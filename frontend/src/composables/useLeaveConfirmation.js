// Shared "you're about to leave with something unconfirmed — proceed or
// stay?" prompt — first extracted from EditProjectView.vue's own
// handleClose (isDirty.value && !window.confirm(...)), now also used by
// BenchmarkProjectView.vue's own annotation-changed check. Not a
// save-or-discard flow (see EditProjectView.vue's own three-way
// selectFile modal for that, a genuinely different problem: a real save
// to offer) — just a reminder with two outcomes, proceed or stay, never
// an action on the data itself.
export function useLeaveConfirmation(shouldConfirm, message) {
  function confirmLeaveIfNeeded() {
    if (!shouldConfirm.value) return true
    return window.confirm(message)
  }

  return { confirmLeaveIfNeeded }
}
