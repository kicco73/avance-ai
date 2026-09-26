import { describe, expect, it } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import SelectProfileDialog from '../src/components/chat/SelectProfileDialog.vue'

async function mount(profiles) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const selection = { heading: 'Pick', profiles, busy: false, open: true, select: () => {} }
  const app = createApp({ setup: () => () => h(SelectProfileDialog, { selection }) })
  app.provide('closeDialog', () => {})
  app.mount(host)
  await nextTick()
  return host
}

function controls(host) {
  return [...host.querySelectorAll('.profile-controls button')].map((button) => button.className)
}

describe('the profile picker', () => {
  it('shows only the select button, alone in its row, when there is one profile', async () => {
    const host = await mount([{ name: 'choice:p:0', title: 'Ana', description: 'd', key: 'Ana' }])

    expect(controls(host)).toEqual(['profile-select'])
  })

  it('keeps both arrows around the select button when there are several', async () => {
    const host = await mount([
      { name: 'choice:p:0', title: 'Ana', description: 'd', key: 'Ana' },
      { name: 'choice:p:1', title: 'Bea', description: 'd', key: 'Bea' },
    ])

    expect(controls(host)).toEqual(['profile-arrow', 'profile-select', 'profile-arrow'])
  })
})
