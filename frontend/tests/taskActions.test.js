// runTaskScript executes an action's own task field (a free-form
// script, e.g. "celebrate()" or "notify('Title', 'Body')") against exactly
// taskLocals — never the real module scope of confetti.js/toastStore.js
// beyond what's re-exported there, and never throws out of a bad script.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/confetti.js', () => ({ celebrate: vi.fn() }))
vi.mock('../src/toastStore.js', () => ({ notify: vi.fn() }))

describe('runTaskScript', () => {
  let taskActions
  let confetti
  let toastStore
  let consoleErrorSpy

  beforeEach(async () => {
    vi.resetModules()
    taskActions = await import('../src/taskActions.js')
    confetti = await import('../src/confetti.js')
    toastStore = await import('../src/toastStore.js')
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.clearAllMocks()
    consoleErrorSpy.mockRestore()
  })

  it('does nothing for a null/empty script', () => {
    taskActions.runTaskScript(null)
    taskActions.runTaskScript('')
    expect(confetti.celebrate).not.toHaveBeenCalled()
    expect(toastStore.notify).not.toHaveBeenCalled()
  })

  it('calls celebrate() when the script is exactly that', () => {
    taskActions.runTaskScript('celebrate()')
    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
  })

  it('calls notify(title, body) with the script\'s own arguments', () => {
    taskActions.runTaskScript("notify('Nice!', 'You reached **state B**.')")
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'You reached **state B**.')
  })

  it('runs multiple statements in one script', () => {
    taskActions.runTaskScript("celebrate(); notify('Nice!', 'Done')")
    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'Done')
  })

  it('catches a script referencing an unknown identifier instead of throwing', () => {
    expect(() => taskActions.runTaskScript('doesNotExist()')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })

  it('catches a syntactically invalid script instead of throwing', () => {
    expect(() => taskActions.runTaskScript('celebrate(')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })
})
