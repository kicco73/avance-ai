import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../errorStore.js', () => ({ enterScreen: vi.fn() }))

import { useViewStack } from './useViewStack.js'

describe('the view stack', () => {
  it('returns from the project editor to manage projects, then from manage projects to home', () => {
    const stack = useViewStack(ref('admin'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })
    expect(stack.pushedView.value).toBe('edit')

    stack.popPushedView()
    expect(stack.pushedView.value).toBe('manageProjects')
    expect(stack.pushedViewContext.value).toEqual({})

    stack.popPushedView()
    expect(stack.pushedView.value).toBe(null)
  })

  it('returns from a chat opened over the editor to the editor, with its project', () => {
    const stack = useViewStack(ref('admin'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })
    stack.pushView('chat')
    expect(stack.chatOpen.value).toBe(true)

    stack.popPushedView()
    expect(stack.chatOpen.value).toBe(false)
    expect(stack.pushedView.value).toBe('edit')
    expect(stack.pushedViewContext.value).toEqual({ projectId: 'burn_out' })
  })

  it('forgets the history when a customer goes home', () => {
    const stack = useViewStack(ref('customer'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })
    stack.goHome()
    expect(stack.pushedView.value).toBe(null)

    stack.pushView('label', { projectId: 'burn_out' })
    stack.popPushedView()
    expect(stack.pushedView.value).toBe(null)
  })
})
