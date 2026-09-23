import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApp, defineComponent, h, inject, nextTick } from 'vue'

import DialogHost from '../src/components/DialogHost.vue'
import { activeDialog, blockingDialog, customDialog, resolveActiveDialog } from '../src/dialogStore.js'

const Picker = defineComponent({
  setup() {
    const closeDialog = inject('closeDialog')
    return () => h('button', { class: 'picker-pick', onClick: () => closeDialog('ada') }, 'Ada')
  }
})

describe('a blocking dialog', () => {
  let container
  let app

  beforeEach(async () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    app = createApp(DialogHost)
    app.mount(container)
    await nextTick()
  })

  afterEach(() => {
    while (activeDialog.value) resolveActiveDialog(null)
    app.unmount()
    container.remove()
  })

  const dialogEl = () => container.querySelector('.app-dialog')

  async function settle() {
    await new Promise((resolve) => setTimeout(resolve, 250))
    await nextTick()
  }

  async function shown() {
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(resolve))
    await nextTick()
  }

  it('offers no way out of its own: no close button, no backdrop, no Escape', async () => {
    let resolved = 'still open'
    blockingDialog({ component: Picker }).then((value) => { resolved = value })
    await shown()

    expect(container.querySelector('.dialog-close-btn')).toBe(null)

    dialogEl().dispatchEvent(new Event('cancel', { cancelable: true }))
    dialogEl().click()
    await settle()

    expect(resolved).toBe('still open')
    expect(dialogEl()).not.toBe(null)
  })

  it('closes on what its own component decides, and hands back what it picked', async () => {
    const pending = blockingDialog({ component: Picker })
    await shown()

    container.querySelector('.picker-pick').click()
    await settle()

    await expect(pending).resolves.toBe('ada')
  })

  it('leaves an ordinary custom dialog dismissible', async () => {
    const pending = customDialog({ component: Picker })
    await shown()

    expect(container.querySelector('.dialog-close-btn')).not.toBe(null)

    container.querySelector('.dialog-close-btn').click()
    await settle()

    await expect(pending).resolves.toBe(null)
  })
})
