// The dots are what a bubble shows while it is waiting for its own
// words. One rule for both sides: a reply being written, and what a
// person said on its way back as text — the user's row used to sit
// there empty for the whole transcription instead.
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'

import MessageBubble from '../src/components/chat/MessageBubble.vue'

describe('a bubble waiting for its own words', () => {
  let container

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    container.remove()
  })

  function show(message) {
    const app = createApp(MessageBubble, { message, showTimestamp: false })
    app.mount(container)
    return app
  }

  function dots() {
    return container.querySelectorAll('.typing-dots .typing-dot')
  }

  it('shows them on a voice message while it is being turned into text', () => {
    const app = show({ id: 1, role: 'user', content: '', transcribing: true })

    expect(dots()).toHaveLength(3)
    expect(container.querySelector('.bubble').classList).toContain('bubble-arriving')

    app.unmount()
  })

  it('shows them on a reply that has started being written', () => {
    const app = show({ id: 2, role: 'assistant', content: '', awaitingReply: true })

    expect(dots()).toHaveLength(3)

    app.unmount()
  })

  it('shows the words instead, once they are there', () => {
    const app = show({ id: 3, role: 'user', content: 'ciao come stai', transcribing: false })

    expect(dots()).toHaveLength(0)
    expect(container.textContent).toContain('ciao come stai')

    app.unmount()
  })
})
