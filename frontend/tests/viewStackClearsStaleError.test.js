import { describe, it, expect, beforeEach } from 'vitest'
import { ref } from 'vue'
import { useViewStack } from '../src/composables/useViewStack.js'
import { currentScreen, errorMessage, enterScreen, setApiError } from '../src/errorStore.js'

describe('an error and the screen it belongs to', () => {
  let stack

  beforeEach(() => {
    stack = useViewStack(ref('admin'))
    enterScreen('edit:habitat')
    setApiError("Project 'habitat' no longer builds", 'nope')
  })

  it('shows it while that screen is the one on display', () => {
    expect(errorMessage.value).toBe("Project 'habitat' no longer builds")
  })

  it('drops it when another project is opened', () => {
    stack.pushView('edit', { projectId: 'andre_the_game' })

    expect(currentScreen.value).toBe('edit:andre_the_game')
    expect(errorMessage.value).toBe('')
  })

  it('never shows a failure that lands after its screen is gone', () => {
    const askedFrom = currentScreen.value
    stack.pushView('edit', { projectId: 'andre_the_game' })

    setApiError("Project 'habitat' no longer builds", 'nope', askedFrom)

    expect(errorMessage.value).toBe('')
  })

  it("still shows the new screen's own failures", () => {
    stack.pushView('edit', { projectId: 'andre_the_game' })

    setApiError("Project 'andre_the_game' is paused", '')

    expect(errorMessage.value).toBe("Project 'andre_the_game' is paused")
  })

  it('drops it on the way back, and on profile or home preview', () => {
    stack.popPushedView()
    expect(errorMessage.value).toBe('')

    enterScreen('edit:habitat')
    setApiError('again', '')
    stack.openProfile()
    expect(errorMessage.value).toBe('')
  })
})
