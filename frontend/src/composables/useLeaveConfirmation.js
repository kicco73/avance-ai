// Simple proceed-or-stay confirmation for unsaved changes.
// Not a save/discard flow — it never acts on the data itself.
export function useLeaveConfirmation(shouldConfirm, message) {
  function confirmLeaveIfNeeded() {
    if (!shouldConfirm.value) return true
    return window.confirm(message)
  }

  return { confirmLeaveIfNeeded }
}
