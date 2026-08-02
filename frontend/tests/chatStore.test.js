// Verifies the "on-enter" wire key (see backend's chat_service.py
// apply_manual_action/_process_turn_locked, sent as "on-enter" — kebab,
// matching the YAML field's own spelling, unlike every other snake_case
// response key) actually reaches confetti.js's celebrate() through
// chatStore.js's handleAction (a manual test action) and submitMessage
// (an auto-tracking-triggered transition), and only when the state
// genuinely changed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/confetti.js', () => ({ celebrate: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn()
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn() }))

describe('handleAction (manual test action) triggers celebrate via on-enter', () => {
  let chatStore
  let confetti
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    confetti = await import('../src/confetti.js')
    api = await import('../src/api.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('celebrates when the fired test action carries on-enter: celebrate and the state actually changed', async () => {
    api.postAction.mockResolvedValue({
      reply: [],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': 'celebrate',
      session_id: 1
    })

    await chatStore.handleAction('go-loud')

    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
    expect(chatStore.state.value.key).toBe('b')
  })

  it('does not celebrate when the fired action has no on-enter', async () => {
    api.postAction.mockResolvedValue({
      reply: [],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleAction('go-quiet')

    expect(confetti.celebrate).not.toHaveBeenCalled()
  })

  it('does not celebrate on a self-loop even if on-enter is set, since the state key did not change', async () => {
    // First call establishes the current state as 'b'.
    api.postAction.mockResolvedValue({
      reply: [],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': null,
      session_id: 1
    })
    await chatStore.handleAction('go-quiet')
    expect(confetti.celebrate).not.toHaveBeenCalled()

    // Second call: a self-loop back onto 'b' that *does* carry on-enter —
    // must still not celebrate, since state.value.key === newState.key.
    api.postAction.mockResolvedValue({
      reply: [],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': 'celebrate',
      session_id: 1
    })
    await chatStore.handleAction('self-loop-with-on-enter')

    expect(confetti.celebrate).not.toHaveBeenCalled()
  })
})

describe('submitMessage (auto-tracking-triggered transition) also threads on-enter through', () => {
  let chatStore
  let confetti
  let chatClient

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    confetti = await import('../src/confetti.js')
    chatClient = await import('../src/chatClient.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('celebrates when a chat turn auto-fires an action with on-enter: celebrate', async () => {
    chatClient.sendMessage.mockResolvedValue({
      reply: [{ id: 1, content: 'hi', audio_text: null }],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': 'celebrate',
      session_id: 1
    })

    await chatStore.handleSend('trigger the transition')

    expect(confetti.celebrate).toHaveBeenCalledTimes(1)
  })
})
