import { confirmDialog } from '../../dialogStore.js'

export function useLeaveConfirmation(shouldConfirm, message) {
  async function confirmLeaveIfNeeded() {
    if (!shouldConfirm.value) return true
    return confirmDialog({ title: 'Unsaved changes', body: message, okLabel: 'Discard' })
  }

  return { confirmLeaveIfNeeded }
}
