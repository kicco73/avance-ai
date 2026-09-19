import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'

const signals = [
  { signal: { name: 'mood', ui_label: 'Mood', definition: 'd' }, relevant: true, attachments: [] },
  { signal: { name: 'pace', ui_label: 'Pace', definition: 'd' }, relevant: false, attachments: [] },
]

vi.mock('../api.js', () => ({
  getProjectSignals: async () => ({ signals }),
}))

const DesignSignalsTab = (await import('../components/project/edit/DesignSignalsTab.vue')).default
const InspectorSignalsTab = (await import('../components/inspector/InspectorSignalsTab.vue')).default

async function mount(component, props, listeners = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const current = ref(props)
  const app = createApp({
    setup: () => () => h(component, { projectId: 'p', ...current.value, ...listeners })
  })
  app.mount(host)
  for (let i = 0; i < 4; i += 1) await nextTick()
  return { host, current }
}

function segments(host) {
  return [...host.querySelectorAll('.segmented-control-option')].map((button) => ({
    label: button.textContent,
    active: button.classList.contains('segmented-control-option-active'),
  }))
}

describe('the design Signals tab', () => {
  it('lists every signal with no relevance filter and marks the ones this state does not track', async () => {
    const { host } = await mount(DesignSignalsTab, {
      stateKey: 's1', stateData: { signalTrackingStrategy: 'relevant' }, editableFiles: [],
    })

    expect(host.querySelectorAll('input[type=checkbox]').length).toBe(0)
    const blocks = [...host.querySelectorAll('.inspector-signal-block')]
    expect(blocks.map((b) => b.querySelector('.inspector-signal-name').textContent)).toEqual(['Mood', 'Pace'])
    expect(blocks.map((b) => b.classList.contains('inspector-signal-block-untracked'))).toEqual([false, true])
  })

  it('shows the tracking control before the signals, on the selected state, and writes the state field', async () => {
    const setStateField = vi.fn()
    const { host, current } = await mount(
      DesignSignalsTab,
      { stateKey: 's1', stateData: { signalTrackingStrategy: 'relevant' }, editableFiles: [] },
      { onSetStateField: setStateField },
    )

    expect(host.firstElementChild.firstElementChild.className).toBe('design-signals-strategy')
    expect(segments(host)).toEqual([{ label: 'Relevant', active: true }, { label: 'All signals', active: false }])

    host.querySelectorAll('.segmented-control-option')[1].click()
    expect(setStateField).toHaveBeenCalledWith('signal-tracking-strategy', 'all')

    current.value = { stateKey: 's1', stateData: { signalTrackingStrategy: 'all' }, editableFiles: [] }
    await nextTick()
    expect(segments(host).map((s) => s.active)).toEqual([false, true])

    current.value = { stateKey: null, stateData: null, editableFiles: [] }
    await nextTick()
    expect(segments(host)).toEqual([])
  })
})

describe('the runtime Signals tab', () => {
  it('keeps the relevance filter, on by default', async () => {
    const { host } = await mount(InspectorSignalsTab, { stateKey: 's1' })

    const toggle = host.querySelector('.inspector-signals-relevant-toggle input')
    expect(toggle.checked).toBe(true)
    expect([...host.querySelectorAll('.inspector-signal-name')].map((el) => el.textContent)).toEqual(['Mood'])
    expect(host.querySelectorAll('.segmented-control').length).toBe(0)

    toggle.click()
    await nextTick()
    expect([...host.querySelectorAll('.inspector-signal-name')].map((el) => el.textContent)).toEqual(['Mood', 'Pace'])
  })
})
