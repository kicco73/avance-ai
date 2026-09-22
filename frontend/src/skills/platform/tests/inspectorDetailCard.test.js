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

async function mount(data, { editable = false, open = false } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const setField = vi.fn()
  const app = createApp({
    setup: () => () => h(InspectorDetailCard, {
      selectedElement: { kind: 'state', data }, editable, open, closable: false, stateTokens: 120,
      onSetField: setField,
    })
  })
  app.mount(host)
  await nextTick()
  return { host, setField }
}

function badges(host) {
  return [...host.querySelectorAll('.inspector-detail-badges .inspector-detail-badge')].map((el) => el.textContent.trim())
}

function aiBadge(host) {
  return [...host.querySelectorAll('.inspector-detail-badges .inspector-detail-badge')].find((el) => el.textContent.trim() === 'AI') ?? null
}

describe('the AI badge on a state card', () => {
  it('is lit on an ai state, read-only', async () => {
    const { host } = await mount(stateData())

    expect(aiBadge(host).classList.contains('inspector-detail-badge-ai-on')).toBe(true)
  })

  it('does not appear at all on a system state, read-only', async () => {
    const { host } = await mount(stateData({ inputProcessor: 'system', chatEnabled: false }))

    expect(badges(host)).toEqual([])
    expect(host.querySelector('.inspector-detail-tokens')).toBeNull()
  })

  it('is shown off and clickable on a system state in edit, and asks for ai', async () => {
    const { host, setField } = await mount(stateData({ inputProcessor: 'system', chatEnabled: false }), { editable: true, open: true })

    const badge = aiBadge(host)
    expect(badge.classList.contains('inspector-detail-badge-toggle-off')).toBe(true)
    badge.click()

    expect(setField).toHaveBeenCalledWith('input-processor', 'ai')
  })

  it('asks for system when clicked on an ai state', async () => {
    const { host, setField } = await mount(stateData(), { editable: true, open: true })

    aiBadge(host).click()

    expect(setField).toHaveBeenCalledWith('input-processor', 'system')
  })

  it('hides every field only the model reads on a system state, in edit', async () => {
    const { host } = await mount(stateData({ inputProcessor: 'system', chatEnabled: false }), { editable: true, open: true })

    expect(badges(host)).toEqual(['AI'])
    expect(host.querySelector('.inspector-detail-textarea + .inspector-detail-textarea')).toBeNull()
    expect([...host.querySelectorAll('.inspector-detail-form-label')].map((el) => el.textContent.trim())).toEqual(['Description'])
    expect(host.querySelector('.inspector-detail-tokens')).toBeNull()
  })

  it('shows them all on an ai state, in edit', async () => {
    const { host } = await mount(stateData(), { editable: true, open: true })

    expect(badges(host)).toEqual(['AI', 'No chat', 'History cutoff', 'Reactions', 'Sources'])
    expect([...host.querySelectorAll('.inspector-detail-form-label')].map((el) => el.textContent.trim())).toEqual(['Description', 'Contextual prompt'])
    expect(host.querySelector('.inspector-detail-tokens')).not.toBeNull()
  })
})
