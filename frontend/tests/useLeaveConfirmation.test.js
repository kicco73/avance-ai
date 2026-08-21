import { computed, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useLeaveConfirmation } from '../src/composables/useLeaveConfirmation.js'
import { confirmDialog } from '../src/dialogStore.js'

vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn() }))

describe('useLeaveConfirmation', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('returns true without prompting when shouldConfirm is false', async () => {
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(false), 'Leave anyway?')

    await expect(confirmLeaveIfNeeded()).resolves.toBe(true)
    expect(confirmDialog).not.toHaveBeenCalled()
  })

  it('prompts with the given message and returns the user choice when shouldConfirm is true', async () => {
    confirmDialog.mockResolvedValue(true)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(true), 'Discard unsaved changes to this file?')

    await expect(confirmLeaveIfNeeded()).resolves.toBe(true)
    expect(confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ body: 'Discard unsaved changes to this file?' })
    )
  })

  it('returns false when the user declines the prompt', async () => {
    confirmDialog.mockResolvedValue(false)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(true), 'Leave anyway?')

    await expect(confirmLeaveIfNeeded()).resolves.toBe(false)
  })

  it('re-reads shouldConfirm on every call, not just at construction', async () => {
    confirmDialog.mockResolvedValue(true)
    const flag = ref(false)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(computed(() => flag.value), 'Leave anyway?')

    await expect(confirmLeaveIfNeeded()).resolves.toBe(true)
    expect(confirmDialog).not.toHaveBeenCalled()

    flag.value = true
    await expect(confirmLeaveIfNeeded()).resolves.toBe(true)
    expect(confirmDialog).toHaveBeenCalledTimes(1)
  })
})
