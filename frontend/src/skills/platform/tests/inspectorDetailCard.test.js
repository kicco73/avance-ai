import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const InspectorDetailCard = (await import('../../../components/skillkit/InspectorDetailCard.vue')).default

function stateData(overrides = {}) {
  return {
    id: 's', uiLabel: 'S', uiDescription: '', final: false, isStart: false, inputProcessor: 'ai', chatEnabled: true,
    historyCutoff: false, reactionsEnabled: false, hasReactions: false, contextualPrompt: 'hi',
    aiMayReadSources: [], aiMustReadSources: [], input: [], output: [], ...overrides,
  }
}

async function mount(data, { editable = false, open = false, kind = 'state' } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const setField = vi.fn()
  const duplicate = vi.fn()
  const app = createApp({
    setup: () => () => h(InspectorDetailCard, {
      selectedElement: { kind, data }, editable, open, closable: false, stateTokens: 120,
      onSetField: setField, onDuplicate: duplicate,
    })
  })
  app.mount(host)
  await nextTick()
  return { host, setField, duplicate }
}

function badges(host) {
  return [...host.querySelectorAll('.inspector-detail-badges .inspector-detail-badge')].map((el) => el.textContent.trim())
}

describe('the fields only the model reads on a state card', () => {
  it('shows no badges and no token bar on a system state, read-only', async () => {
    const { host } = await mount(stateData({ inputProcessor: 'system', chatEnabled: false }))

    expect(badges(host)).toEqual([])
    expect(host.querySelector('.inspector-detail-tokens')).toBeNull()
  })

  it('shows no badges, no contextual prompt and no token bar on a system state, in edit', async () => {
    const { host } = await mount(stateData({ inputProcessor: 'system', chatEnabled: false }), { editable: true, open: true })

    expect(badges(host)).toEqual([])
    expect([...host.querySelectorAll('.inspector-detail-form-label')].map((el) => el.textContent.trim())).toEqual(['Description'])
    expect(host.querySelector('.inspector-detail-tokens')).toBeNull()
  })

  it('shows them all on an ai state, in edit', async () => {
    const { host } = await mount(stateData(), { editable: true, open: true })

    expect(badges(host)).toEqual(['No chat', 'History cutoff', 'Reactions', 'Sources'])
    expect([...host.querySelectorAll('.inspector-detail-form-label')].map((el) => el.textContent.trim())).toEqual(['Description', 'Contextual prompt'])
    expect(host.querySelector('.inspector-detail-tokens')).not.toBeNull()
  })
})

describe('the card menu', () => {
  async function openMenu(host) {
    host.querySelector('.card-menu-btn').click()
    await nextTick()
    return [...host.querySelectorAll('.card-menu-dropdown button')]
  }

  it('duplicates a state', async () => {
    const { host, duplicate } = await mount(stateData(), { editable: true })
    const items = await openMenu(host)

    expect(items.map((el) => el.textContent.trim())).toEqual(['Actions order', 'Duplicate', 'Delete'])
    items[1].click()
    await nextTick()
    expect(duplicate).toHaveBeenCalledTimes(1)
    expect(host.querySelector('.card-menu-dropdown')).toBeNull()
  })

  it('offers no duplicate on an action', async () => {
    const { host } = await mount({ matchStateKey: 's', actionName: 'go', uiLabel: 'Go', hasTrigger: false }, { editable: true, kind: 'action' })

    expect((await openMenu(host)).map((el) => el.textContent.trim())).toEqual(['Delete'])
  })
})
