import { afterEach, describe, expect, it } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import ActionButtons from '../src/components/chat/ActionButtons.vue'

describe('the action row after a button was pressed', () => {
  let app
  let container

  afterEach(() => {
    app?.unmount()
    container?.remove()
  })

  function mount(actions, disabled) {
    container = document.createElement('div')
    document.body.appendChild(container)
    app = createApp({
      setup: () => () => h(ActionButtons, { actions: actions.value, disabled: disabled.value })
    })
    app.mount(container)
  }

  it('renders fresh buttons when the same row is re-enabled', async () => {
    const actions = ref([{ name: 'yes', ui_button: 'Yes' }, { name: 'no', ui_button: 'No' }])
    const disabled = ref(false)
    mount(actions, disabled)

    const before = [...container.querySelectorAll('.action-btn')]

    disabled.value = true
    await nextTick()
    disabled.value = false
    await nextTick()

    const after = [...container.querySelectorAll('.action-btn')]
    expect(after.map((b) => b.textContent.trim())).toEqual(['Yes', 'No'])
    expect(after.some((b) => before.includes(b))).toBe(false)
  })

  it('renders fresh buttons when a new row carries the same actions', async () => {
    const actions = ref([{ name: 'yes', ui_button: 'Yes' }])
    const disabled = ref(false)
    mount(actions, disabled)

    const before = [...container.querySelectorAll('.action-btn')]

    actions.value = [{ name: 'yes', ui_button: 'Yes' }]
    await nextTick()

    const after = [...container.querySelectorAll('.action-btn')]
    expect(after).toHaveLength(1)
    expect(after.some((b) => before.includes(b))).toBe(false)
  })
})
