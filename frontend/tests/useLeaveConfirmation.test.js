import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useLeaveConfirmation } from '../src/composables/useLeaveConfirmation.js'

describe('useLeaveConfirmation', () => {
  it('returns true without prompting when shouldConfirm is false', () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(false), 'Leave anyway?')

    expect(confirmLeaveIfNeeded()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('prompts with the given message and returns the user choice when shouldConfirm is true', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(true), 'Discard unsaved changes to this file?')

    expect(confirmLeaveIfNeeded()).toBe(true)
    expect(confirmSpy).toHaveBeenCalledWith('Discard unsaved changes to this file?')
    confirmSpy.mockRestore()
  })

  it('returns false when the user declines the prompt', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(ref(true), 'Leave anyway?')

    expect(confirmLeaveIfNeeded()).toBe(false)
    confirmSpy.mockRestore()
  })

  it('re-reads shouldConfirm on every call, not just at construction', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const flag = ref(false)
    const { confirmLeaveIfNeeded } = useLeaveConfirmation(computed(() => flag.value), 'Leave anyway?')

    expect(confirmLeaveIfNeeded()).toBe(true)
    expect(confirmSpy).not.toHaveBeenCalled()

    flag.value = true
    expect(confirmLeaveIfNeeded()).toBe(true)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    confirmSpy.mockRestore()
  })
})
