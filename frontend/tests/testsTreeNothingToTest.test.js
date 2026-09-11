// Launching a test suite with nothing to analyse must not be possible:
// every aggregation (states, signals, users, the whole suite) runs over
// the annotated sessions, so with none of those there is no run to make.
import { afterEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'

import TestsTree from '../src/components/project/edit/test/TestsTree.vue'

let app = null
let container = null

function mount(sessions) {
  container = document.createElement('div')
  app = createApp(TestsTree, {
    sessions,
    states: ['greeting'],
    signals: [{ name: 'mood', ui_label: 'Mood' }],
    statuses: {},
    progresses: {},
    selectedNodeId: null
  })
  app.mount(container)
  return container
}

afterEach(() => {
  app?.unmount()
  app = null
})

describe('TestsTree with no annotated session', () => {
  it('disables every run button, and enables them again as soon as there is one', () => {
    const empty = mount([])
    const buttons = [...empty.querySelectorAll('button.test-node-btn')]
    expect(buttons.length).toBeGreaterThan(0)
    expect(buttons.every((button) => button.disabled)).toBe(true)

    app.unmount()
    const withSession = mount([{ id: 1, username: 'u', title: 'One', datetime_start: null }])
    const enabled = [...withSession.querySelectorAll('button.test-node-btn')]
    expect(enabled.every((button) => button.disabled)).toBe(false)
  })
})
