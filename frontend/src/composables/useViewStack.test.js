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

  it('keeps the view underneath on the stack, so opening the editor never uncovers home', () => {
    const stack = useViewStack(ref('admin'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })

    expect(stack.views.value.map((entry) => entry.view)).toEqual(['manageProjects', 'edit'])
  })

  it('takes an admin to the root home, not to a preview of it', () => {
    const stack = useViewStack(ref('admin'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })

    stack.goHome()

    expect(stack.views.value).toEqual([])
    expect(stack.homePreviewRole.value).toBe(null)
  })

  it('still previews the customer home for a supervisor, whose root is the labelling view', () => {
    const stack = useViewStack(ref('supervisor'))

    stack.goHome()

    expect(stack.homePreviewRole.value).toBe('customer')
  })

  it('drops the whole stack when the boot sequence resolves a landing view', () => {
    const stack = useViewStack(ref('admin'))
    stack.pushView('manageProjects')
    stack.pushView('edit', { projectId: 'burn_out' })
    stack.openProfile()

    stack.resetToRoot()

    expect(stack.views.value).toEqual([])
    expect(stack.showProfile.value).toBe(false)
    expect(stack.chatOpen.value).toBe(false)

    stack.popPushedView()
    expect(stack.pushedView.value).toBe(null)
  })
})
