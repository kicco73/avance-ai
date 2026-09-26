import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const { handlers } = vi.hoisted(() => ({ handlers: new Map() }))

vi.mock('./busChannel.js', () => ({
  busChannel: { subscribe: (type, handler) => { handlers.set(type, handler) } }
}))

const { EnvClassBinding } = await import('./envClassBinding.js')

function frame(type, body) {
  handlers.get(type)({ type, ...body })
}

describe('the chat window follows its ui-binding keys', () => {
  it('starts from the values told on entering, then follows env.changed of those keys only', () => {
    const binding = new EnvClassBinding(ref(7))

    frame('env.bindings', { session_id: 7, values: { mood: 'sad' } })
    frame('env.changed', { session_id: 7, key: 'mood', value: 'very happy' })
    frame('env.changed', { session_id: 7, key: 'level', value: 3 })
    frame('env.changed', { session_id: 8, key: 'mood', value: 'angry' })

    expect(binding.classes.value).toEqual(['env-mood-very_happy'])
  })
})
