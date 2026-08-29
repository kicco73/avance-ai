import { afterEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'
import { useResizablePanel } from '../src/composables/useResizablePanel.js'

// onBeforeUnmount needs an active component instance, so the composable is
// exercised inside a bare setup() rather than called directly.
function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

function move(movementX) {
  const event = new Event('mousemove')
  Object.defineProperty(event, 'movementX', { value: movementX })
  window.dispatchEvent(event)
}

describe('useResizablePanel', () => {
  let unmount

  afterEach(() => {
    unmount?.()
  })

  it('starts at the given initial width', () => {
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480 }))
    unmount = mounted.unmount
    expect(mounted.result.width.value).toBe(280)
  })

  it('startDrag then a window mousemove resizes within [min, max]', () => {
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480 }))
    unmount = mounted.unmount
    const { width, startDrag } = mounted.result

    startDrag({ preventDefault: () => {} })
    move(50)
    expect(width.value).toBe(330)

    move(1000) // clamps at max
    expect(width.value).toBe(480)
  })

  it('a mousemove before startDrag does nothing', () => {
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480 }))
    unmount = mounted.unmount
    move(50)
    expect(mounted.result.width.value).toBe(280)
  })

  it('mouseup stops the drag — a later mousemove no longer resizes', () => {
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480 }))
    unmount = mounted.unmount
    const { width, startDrag } = mounted.result

    startDrag({ preventDefault: () => {} })
    move(20)
    window.dispatchEvent(new Event('mouseup'))
    move(20)
    expect(width.value).toBe(300) // only the one move before mouseup counted
  })

  it('invert: true negates the drag direction — dragging left grows the panel', () => {
    const mounted = mountComposable(() => useResizablePanel(360, { min: 240, max: 560, invert: true }))
    unmount = mounted.unmount
    const { width, startDrag } = mounted.result

    startDrag({ preventDefault: () => {} })
    move(-40) // dragging left...
    expect(width.value).toBe(400) // ...grows the panel

    move(-1000) // clamps at max
    expect(width.value).toBe(560)
  })

  it('onResize runs after every width change', () => {
    let calls = 0
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480, onResize: () => { calls++ } }))
    unmount = mounted.unmount
    const { startDrag } = mounted.result

    startDrag({ preventDefault: () => {} })
    move(10)
    move(10)
    expect(calls).toBe(2)
  })

  it('unmounting stops listening — a later mousemove no longer resizes', () => {
    const mounted = mountComposable(() => useResizablePanel(280, { min: 200, max: 480 }))
    const { width, startDrag } = mounted.result

    startDrag({ preventDefault: () => {} })
    mounted.unmount()
    move(50)
    expect(width.value).toBe(280)
  })
})
