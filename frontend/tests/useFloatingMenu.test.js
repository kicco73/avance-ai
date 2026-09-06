import { afterEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'
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

  function menu({ withPanel = false } = {}) {
    const mounted = mountComposable(() => useFloatingMenu())
    unmount = mounted.unmount
    const result = mounted.result
    result.triggerRef.value = document.createElement('button')
    document.body.appendChild(result.triggerRef.value)
    if (withPanel) {
      result.panelRef.value = document.createElement('div')
      document.body.appendChild(result.panelRef.value)
    }
    return { ...result, unmount: mounted.unmount }
  }

  it('starts closed, and toggle() opens it positioned against triggerRef then closes it again', async () => {
    const { open, toggle, style } = menu()
    expect(open.value).toBe(false)

    await toggle()
    expect(open.value).toBe(true)
    expect(style.value.left).toMatch(/px$/)
    expect(style.value.top).toMatch(/px$/)

    await toggle()
    expect(open.value).toBe(false)
  })

  it('stays open for a click on the trigger or anywhere inside the panel', async () => {
    const { open, triggerRef, panelRef, toggle } = menu({ withPanel: true })
    const item = document.createElement('button')
    panelRef.value.appendChild(item)

    await toggle()
    click(triggerRef.value)
    expect(open.value).toBe(true)

    click(item)
    expect(open.value).toBe(true)
  })

  it('closes on an outside click, a window resize or scroll, and on close()', async () => {
    const outside = menu({ withPanel: true })
    await outside.toggle()
    click(document.body)
    expect(outside.open.value).toBe(false)
    outside.unmount()

    const resized = menu()
    await resized.toggle()
    window.dispatchEvent(new Event('resize'))
    expect(resized.open.value).toBe(false)
    resized.unmount()

    const scrolled = menu()
    await scrolled.toggle()
    window.dispatchEvent(new Event('scroll'))
    expect(scrolled.open.value).toBe(false)
    scrolled.unmount()

    const closed = menu()
    await closed.toggle()
    closed.close()
    expect(closed.open.value).toBe(false)
  })

  it('unmounting stops listening — a later outside click no longer closes it', async () => {
    const { open, toggle, unmount: unmountNow } = menu()

    await toggle()
    unmountNow()
    click(document.body)

    // open itself is meaningless post-unmount; this only proves the
    // document-level listener was actually removed, not left leaking.
    expect(open.value).toBe(true)
  })
})
