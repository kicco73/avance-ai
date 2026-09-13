import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

import ChatInput from '../src/components/chat/ChatInput.vue'
import { chatInputControls } from '../src/skills/registry.js'

describe('the input row', () => {
  let container

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(async () => {
    container.remove()
    const { publishServices } = await import('../src/skillServices.js')
    publishServices(Object.fromEntries(chatInputControls.value.map((control) => [control.id.split('-')[0], true])))
  })

  function show(props) {
    const app = createApp(ChatInput, { store: { messages: { value: [] } }, ...props })
    app.mount(container)
    return app
  }

  it('has a control from every skill that contributes one', () => {
    expect(chatInputControls.value.length).toBeGreaterThan(0)
  })

  it('switches every one of them off with the chat', () => {
    const app = show({ disabled: true, sample: true })

    const controls = [...container.querySelectorAll('.chat-input-control')]
    expect(controls.length).toBe(chatInputControls.value.length)
    expect(controls.filter((el) => !el.disabled).map((el) => el.className)).toEqual([])

    app.unmount()
  })

  it('draws every one of them in a sample row, whatever any conversation can reach', async () => {
    const { publishServices } = await import('../src/skillServices.js')
    publishServices(Object.fromEntries(chatInputControls.value.map((c) => [c.id.split('-')[0], false])))

    const app = show({ sample: true })

    expect(container.querySelectorAll('.chat-input-control').length).toBe(chatInputControls.value.length)

    app.unmount()
  })

  it('draws only what this conversation can reach in a real row', async () => {
    const { publishServices } = await import('../src/skillServices.js')
    publishServices(Object.fromEntries(chatInputControls.value.map((c) => [c.id.split('-')[0], false])))

    const app = show({})

    expect(container.querySelectorAll('.chat-input-control')).toHaveLength(0)

    app.unmount()
  })

  it('draws a sample row\'s controls unselected, whatever this person has switched on', async () => {
    const preferences = await import('../src/chatPreferences.js')
    preferences.audioEnabled.value = true
    preferences.spokenTextEnabled.value = true

    const app = show({ sample: true })

    expect(container.querySelector('.audio-btn').classList).not.toContain('audio-btn-on')
    expect(container.querySelector('.spoken-text-btn').classList).not.toContain('spoken-text-btn-on')

    app.unmount()
  })

  it('draws a real row\'s controls as this person left them', async () => {
    const preferences = await import('../src/chatPreferences.js')
    preferences.audioEnabled.value = true

    const app = show({})

    expect(container.querySelector('.audio-btn').classList).toContain('audio-btn-on')

    app.unmount()
    preferences.audioEnabled.value = false
  })

  it('leaves them on while the chat is open', () => {
    const app = show({ disabled: false, sample: true })

    const controls = [...container.querySelectorAll('.chat-input-control')]
    expect(controls.filter((el) => el.disabled)).toEqual([])

    app.unmount()
  })
})

describe('a fresh page', () => {
  it('has the audio and the spoken text switched off', async () => {
    vi.resetModules()
    const preferences = await import('../src/chatPreferences.js')

    expect(preferences.audioEnabled.value).toBe(false)
    expect(preferences.spokenTextEnabled.value).toBe(false)
  })
})
