// Every control in the input row is switched off when the chat is:
// ChatInput hands each contributed button the same `disabled` it gets,
// and a button that ignored it stayed clickable on a closed session
// (Quit in a preview chat left audio and spoken-text lit up).
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
    // Whatever a test said this conversation could reach, the next one
    // starts from a build that has everything.
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
    // Told the way the app tells them — no control is named here, which
    // is the point: this file is about the row, not about who fills it.
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

// A fresh page has the audio off — on a phone as anywhere else, where a
// reply that started talking by itself would be both a surprise and,
// outside a gesture, blocked by the browser (see audio.js's own
// unlockAudioPlayback). Nothing restores it from anywhere: it is off
// until this person switches it on, and switching it on is what tells
// the session (see chatStoreFactory.js's own syncAudioPreference).
describe('a fresh page', () => {
  it('has the audio and the spoken text switched off', async () => {
    vi.resetModules()
    const preferences = await import('../src/chatPreferences.js')

    expect(preferences.audioEnabled.value).toBe(false)
    expect(preferences.spokenTextEnabled.value).toBe(false)
  })
})
