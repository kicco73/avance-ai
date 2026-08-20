import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('toastStore', () => {
  let toastStore

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    toastStore = await import('../src/toastStore.js')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('notify() pushes a toast with an incrementing id', () => {
    toastStore.notify('Title 1', 'Body 1')
    toastStore.notify('Title 2', 'Body 2')

    expect(toastStore.toasts.value).toHaveLength(2)
    expect(toastStore.toasts.value[0]).toMatchObject({ title: 'Title 1', body: 'Body 1' })
    expect(toastStore.toasts.value[1]).toMatchObject({ title: 'Title 2', body: 'Body 2' })
    expect(toastStore.toasts.value[0].id).not.toBe(toastStore.toasts.value[1].id)
  })

  it('dismissToast() removes exactly the toast with that id', () => {
    toastStore.notify('Title 1', 'Body 1')
    toastStore.notify('Title 2', 'Body 2')
    const [first, second] = toastStore.toasts.value

    toastStore.dismissToast(first.id)

    expect(toastStore.toasts.value).toHaveLength(1)
    expect(toastStore.toasts.value[0].id).toBe(second.id)
  })

  it('auto-dismisses a toast after its timeout', () => {
    toastStore.notify('Title', 'Body')
    expect(toastStore.toasts.value).toHaveLength(1)

    vi.runAllTimers()

    expect(toastStore.toasts.value).toHaveLength(0)
  })
})
