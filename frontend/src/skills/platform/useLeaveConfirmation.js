import { confirmDialog } from '../dialogStore.js'

// Simple proceed-or-stay confirmation for unsaved changes.
// Not a save/discard flow — it never acts on the data itself.
export function useLeaveConfirmation(shouldConfirm, message) {
  async function confirmLeaveIfNeeded() {
    if (!shouldConfirm.value) return true
    return confirmDialog({ title: 'Unsaved changes', body: message, okLabel: 'Discard' })
  }

  return { confirmLeaveIfNeeded }
}
