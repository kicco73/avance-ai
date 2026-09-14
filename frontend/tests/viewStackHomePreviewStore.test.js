import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../src/errorStore.js', () => ({ enterScreen: vi.fn() }))

import { useViewStack } from '../src/composables/useViewStack.js'

describe('the view stack under an admin previewing the customer home', () => {
  it('pops the store back onto the home preview it was opened from', () => {
    const stack = useViewStack(ref('admin'))
    stack.openHomePreview('customer')

    stack.pushView('appStore')
    expect(stack.pushedView.value).toBe('appStore')
    expect(stack.homePreviewRole.value).toBe('customer')

    stack.popPushedView()

    expect(stack.pushedView.value).toBeNull()
    expect(stack.homePreviewRole.value).toBe('customer')
  })

  it('pops a chat opened from the home preview back onto it too', () => {
    const stack = useViewStack(ref('admin'))
    stack.openHomePreview('customer')
    stack.pushView('chat')

    stack.popPushedView()

    expect(stack.chatOpen.value).toBe(false)
    expect(stack.homePreviewRole.value).toBe('customer')
  })
})
