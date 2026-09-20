import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/confetti.js', () => ({ celebrate: vi.fn() }))
vi.mock('../src/toastStore.js', () => ({ notify: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ infoDialog: vi.fn(), customDialog: vi.fn() }))

describe('runTaskScript', () => {
  let taskActions
  let confetti
  let toastStore
  let playBackgroundAudio
  let dialogStore
  let consoleErrorSpy

  beforeEach(async () => {
    vi.resetModules()
    taskActions = await import('../src/taskActions.js')
    confetti = await import('../src/confetti.js')
    toastStore = await import('../src/toastStore.js')
    dialogStore = await import('../src/dialogStore.js')
    playBackgroundAudio = vi.fn()
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
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'You reached **state B**.', null)
  })

  it('runs multiple statements in one script', () => {
    taskActions.runTaskScript("celebrate(); notify('Nice!', 'Done')")
    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
    expect(toastStore.notify).toHaveBeenCalledWith('Nice!', 'Done', null)
  })

  it('catches a script referencing an unknown identifier instead of throwing', () => {
    expect(() => taskActions.runTaskScript('doesNotExist()')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })

  it('catches a syntactically invalid script instead of throwing', () => {
    expect(() => taskActions.runTaskScript('celebrate(')).not.toThrow()
    expect(consoleErrorSpy).toHaveBeenCalled()
  })

  it('resolves a backend-relative media url onto the configured API origin before playing audio', () => {
    taskActions.runTaskScript("show_media('/api/core/projects/text_adventure/files/media/title.mp3/content')", { playBackgroundAudio })
    expect(playBackgroundAudio).toHaveBeenCalledWith(
      'http://localhost:8000/api/core/projects/text_adventure/files/media/title.mp3/content'
    )
  })

  it('resolves a backend-relative media url before opening the media dialog for non-audio media', () => {
    taskActions.runTaskScript("show_media('/api/core/projects/text_adventure/files/media/start.jpeg/content')", { playBackgroundAudio })
    expect(dialogStore.customDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        props: { url: 'http://localhost:8000/api/core/projects/text_adventure/files/media/start.jpeg/content' }
      })
    )
  })
})
