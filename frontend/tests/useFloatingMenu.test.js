import { afterEach, describe, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'
import { useFloatingMenu } from '../src/composables/useFloatingMenu.js'

// onBeforeUnmount needs an active component instance, so the composable is
// exercised inside a bare setup() rather than called directly.
function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

function click(target) {
  target.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

describe('useFloatingMenu', () => {
  let unmount

  afterEach(() => {
    unmount?.()
  })

  it('starts closed', () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    expect(mounted.result.open.value).toBe(false)
  })

  it('toggle() opens it and positions the panel against triggerRef', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle, style } = mounted.result
    triggerRef.value = document.createElement('button')
    document.body.appendChild(triggerRef.value)

    await toggle()

    expect(open.value).toBe(true)
    expect(style.value.left).toMatch(/px$/)
    expect(style.value.top).toMatch(/px$/)
  })

  it('toggle() again closes it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')

    await toggle()
    await toggle()

    expect(open.value).toBe(false)
  })

  it('a click outside both trigger and panel closes it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, panelRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')
    panelRef.value = document.createElement('div')
    document.body.append(triggerRef.value, panelRef.value)

    await toggle()
    expect(open.value).toBe(true)

    click(document.body)
    expect(open.value).toBe(false)
  })

  it('a click on the trigger itself does not close it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')
    document.body.appendChild(triggerRef.value)

    await toggle()
    click(triggerRef.value)

    expect(open.value).toBe(true)
  })

  it('a click inside the panel does not close it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, panelRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')
    panelRef.value = document.createElement('div')
    const item = document.createElement('button')
    panelRef.value.appendChild(item)
    document.body.append(triggerRef.value, panelRef.value)

    await toggle()
    click(item)

    expect(open.value).toBe(true)
  })

  it('window resize closes it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')

    await toggle()
    window.dispatchEvent(new Event('resize'))

    expect(open.value).toBe(false)
  })

  it('window scroll closes it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')

    await toggle()
    window.dispatchEvent(new Event('scroll'))

    expect(open.value).toBe(false)
  })

  it('close() closes it directly', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const { open, triggerRef, toggle, close } = mounted.result
    triggerRef.value = document.createElement('button')

    await toggle()
    close()

    expect(open.value).toBe(false)
  })

  it('unmounting stops listening — a later outside click no longer closes it', async () => {
    const mounted = mountComposable(() => useFloatingMenu())
    const { open, triggerRef, toggle } = mounted.result
    triggerRef.value = document.createElement('button')
    document.body.appendChild(triggerRef.value)

    await toggle()
    mounted.unmount()
    click(document.body)

    // open itself is meaningless post-unmount; this only proves the
    // document-level listener was actually removed, not left leaking.
    expect(open.value).toBe(true)
  })
})
