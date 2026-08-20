// runOnEnterScript executes an action's own on-enter field (a free-form
// script, e.g. "celebrate()" or "notify('Title', 'Body')") against exactly
// onEnterLocals — never the real module scope of confetti.js/toastStore.js
// beyond what's re-exported there, and never throws out of a bad script.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/confetti.js', () => ({ celebrate: vi.fn() }))
vi.mock('../src/toastStore.js', () => ({ notify: vi.fn() }))

describe('runOnEnterScript', () => {
  let onEnterActions
  let confetti
  let toastStore
  let consoleErrorSpy

  beforeEach(async () => {
    vi.resetModules()
    onEnterActions = await import('../src/onEnterActions.js')
    confetti = await import('../src/confetti.js')
    toastStore = await import('../src/toastStore.js')
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.clearAllMocks()
    consoleErrorSpy.mockRestore()
  })

  it('does nothing for a null/empty script', () => {
    onEnterActions.runOnEnterScript(null)
    onEnterActions.runOnEnterScript('')
    expect(confetti.celebrate).not.toHaveBeenCalled()
    expect(toastStore.notify).not.toHaveBeenCalled()
  })

  it('calls celebrate() when the script is exactly that', () => {
    onEnterActions.runOnEnterScript('celebrate()')
    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
  })

  it('calls notify(title, body) with the script\'s own arguments', () => {
    onEnterActions.runOnEnterScript("notify('Nice!', 'You reached **state B**.')")
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'You reached **state B**.')
  })

  it('runs multiple statements in one script', () => {
    onEnterActions.runOnEnterScript("celebrate(); notify('Nice!', 'Done')")
    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'Done')
  })

  it('catches a script referencing an unknown identifier instead of throwing', () => {
    expect(() => onEnterActions.runOnEnterScript('doesNotExist()')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })

  it('catches a syntactically invalid script instead of throwing', () => {
    expect(() => onEnterActions.runOnEnterScript('celebrate(')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })
})
