import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const InspectorStateTab = (await import('../components/inspector/InspectorStateTab.vue')).default

function stateElement(overrides = {}) {
  return {
    kind: 'state',
    data: {
      id: 's', uiLabel: 'S', uiDescription: '', final: false, isStart: false, inputProcessor: 'ai',
      chatEnabled: true, historyCutoff: false, reactionsEnabled: false, hasReactions: false, contextualPrompt: '',
      aiMayReadSources: [], aiMustReadSources: [], input: [], output: [], ...overrides,
    }
  }
}

async function mount(selectedElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const setField = vi.fn()
  const app = createApp({
    setup: () => () => h(InspectorStateTab, {
      projectId: 'p', selectedElement, onSetField: setField,
    })
  })
  app.mount(host)
  await nextTick()
  return { host, setField }
}

function processorOptionButtons(host) {
  return [...host.querySelectorAll('.inspector-state-tab-processor .segmented-control-option')]
}

describe('the input processor segmented control on a selected state', () => {
  it('shows ai active for an ai state and switches to system on click', async () => {
    const { host, setField } = await mount(stateElement())

    const buttons = processorOptionButtons(host)
    expect(buttons.map((b) => b.textContent.trim())).toEqual(['AI', 'System'])
    expect(buttons[0].classList.contains('segmented-control-option-active')).toBe(true)

    buttons[1].click()
    expect(setField).toHaveBeenCalledWith('input-processor', 'system')
  })

  it('shows system active for a system state and switches to ai on click', async () => {
    const { host, setField } = await mount(stateElement({ inputProcessor: 'system', chatEnabled: false }))

    const buttons = processorOptionButtons(host)
    expect(buttons[1].classList.contains('segmented-control-option-active')).toBe(true)

    buttons[0].click()
    expect(setField).toHaveBeenCalledWith('input-processor', 'ai')
  })

  it('does not appear for an action', async () => {
    const { host } = await mount({ kind: 'action', data: { matchStateKey: 's', actionName: 'go', isInitEdge: false, hasTrigger: true } })

    expect(host.querySelector('.inspector-state-tab-processor')).toBeNull()
  })

  it('does not appear when nothing is selected', async () => {
    const { host } = await mount(null)

    expect(host.querySelector('.inspector-state-tab-processor')).toBeNull()
  })
})

describe('the state card on a selected state', () => {
  it('opens closed by default', async () => {
    const { host } = await mount(stateElement())

    expect(host.querySelector('.inspector-detail-card-open')).toBeNull()
  })
})
