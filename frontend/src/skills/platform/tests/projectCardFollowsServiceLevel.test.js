import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const { getProjectMetadata } = vi.hoisted(() => ({ getProjectMetadata: vi.fn() }))

vi.mock('../api.js', async (importOriginal) => ({ ...(await importOriginal()), getProjectMetadata }))

const Inspector = (await import('../components/inspector/Inspector.vue')).default
const EditorStateTab = (await import('../components/project/edit/EditorStateTab.vue')).default

function project(services) {
  return { project: { id: 'p', ui_label: 'P', ui_description: '', general_prompt: '', services } }
}

async function flush() {
  for (let i = 0; i < 5; i++) await nextTick()
}

async function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  let inspector = null
  const app = createApp({
    setup: () => () => h(
      Inspector,
      { tabs: [{ id: 'state', label: 'State' }], ref: (el) => { inspector = el } },
      { 'tab-state': ({ registerTab }) => h(EditorStateTab, { projectId: 'p', ref: registerTab('state') }) }
    )
  })
  app.mount(host)
  await flush()
  host.querySelector('.inspector-project-card').click()
  await flush()
  return { host, inspector }
}

function skillsBadge(host) {
  return host.querySelector('.inspector-skills-badge-btn')
}

describe('the project card follows a service level set from the Skills dialog', () => {
  it('turns the Skills badge on once the inspector resyncs after the edit', async () => {
    getProjectMetadata.mockResolvedValue(project({}))
    const { host, inspector } = await mount()
    expect(skillsBadge(host).classList.contains('inspector-detail-badge-toggle-off')).toBe(true)

    getProjectMetadata.mockResolvedValue(project({ talk: 'required' }))
    await inspector.resync()
    await flush()

    expect(skillsBadge(host).classList.contains('inspector-detail-badge-toggle-on')).toBe(true)
  })
})
