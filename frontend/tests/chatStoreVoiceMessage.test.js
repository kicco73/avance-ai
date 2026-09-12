// A voice message occupies a row while something else turns it into
// words. When the words arrive the row has to *change* — not merely hold
// the right value: the text was written onto the raw object the closure
// captured, so the value was there and nothing on screen ever redrew.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, watch } from 'vue'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe('a voice message shows its words once they arrive', () => {
  let chatStore

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  it('redraws the row, rather than only holding the right value', async () => {
    const redrawn = []
    watch(
      () => chatStore.messages.value.map((m) => m.content),
      (contents) => redrawn.push(contents),
    )

    const pending = chatStore.beginVoiceMessage()
    await nextTick()
    pending.transcribed('ciao come stai')
    await nextTick()

    expect(redrawn.at(-1)).toContain('ciao come stai')
    expect(chatStore.messages.value[0]).toMatchObject({
      role: 'user', content: 'ciao come stai', transcribing: false,
    })
  })

  it('sends what was heard', async () => {
    const bus = await import('./fakeBus.js')
    chatStore.beginVoiceMessage().transcribed('ciao')
    await nextTick()

    expect(bus.busChannel.send).toHaveBeenCalledWith({
      type: 'input.text', session_id: 1, text: 'ciao',
    })
  })

  it('takes the row away when nothing was heard', async () => {
    chatStore.beginVoiceMessage().abandoned()
    await nextTick()

    expect(chatStore.messages.value).toHaveLength(0)
  })
})
