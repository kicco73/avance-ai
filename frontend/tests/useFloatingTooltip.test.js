import { describe, expect, it } from 'vitest'
import { useFloatingTooltip } from '../src/useFloatingTooltip.js'

describe('useFloatingTooltip', () => {
  it('is hidden until show() is called', () => {
    const { visible } = useFloatingTooltip()
    expect(visible.value).toBe(false)
  })

  it('hide() hides it again', () => {
    const { visible, show, hide, triggerRef } = useFloatingTooltip()
    triggerRef.value = document.createElement('span')

    show()
    expect(visible.value).toBe(true)
    hide()
    expect(visible.value).toBe(false)
  })

  it('show(element) positions against the given element, not triggerRef', () => {
    const { visible, style, show } = useFloatingTooltip()
    const el = document.createElement('span')
    document.body.appendChild(el)

    show(el)

    expect(visible.value).toBe(true)
    expect(style.value.bottom).toMatch(/px$/)
    expect(style.value.right).toMatch(/px$/)
  })

  // Regression test: `@mouseenter="show"` (a bare method reference, the
  // single-trigger usage pattern this composable's own docstring
  // documents) makes Vue pass the native DOM Event as show()'s sole
  // argument — not an element. Before this session's fix, `target ??
  // triggerRef.value` treated that truthy Event as the element itself and
  // called `event.getBoundingClientRect()`, which doesn't exist and threw
  // silently inside the handler — so the tooltip never actually appeared,
  // for every caller using the documented bare-reference pattern (see
  // InspectorGraphTab.vue's (?) icon, MessageBubble.vue's (!) badge).
  it('falls back to triggerRef when given a non-Element (e.g. a native Event, from a bare @mouseenter="show" binding)', () => {
    const { visible, style, show, triggerRef } = useFloatingTooltip()
    const el = document.createElement('span')
    document.body.appendChild(el)
    triggerRef.value = el

    const fakeEvent = new Event('mouseenter')
    expect(() => show(fakeEvent)).not.toThrow()

    expect(visible.value).toBe(true)
    expect(style.value.bottom).toMatch(/px$/)
  })

  it('show() with no target and no triggerRef set does nothing', () => {
    const { visible, show } = useFloatingTooltip()

    show()

    expect(visible.value).toBe(false)
  })
})
