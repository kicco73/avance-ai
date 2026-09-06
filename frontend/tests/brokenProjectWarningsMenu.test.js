// BrokenProjectWarningsMenu.vue as a consumer of the shared channel: a
// project breaking or being fixed reaches every admin as a pushed
// system_warning frame, and the counter moves without waiting for this
// view to be refreshed. Clicking a row hands the caller the file/line the
// build failed on, so EditProjectView can open the editor right there.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('../src/api.js', () => ({
  getProjectBrokenWarnings: vi.fn(),
  deleteProjectBrokenWarning: vi.fn().mockResolvedValue({ status: 'ok' })
}))
const { subscribe, unsubscribe } = vi.hoisted(() => {
  const unsubscribe = vi.fn()
  return { subscribe: vi.fn(() => unsubscribe), unsubscribe }
})
vi.mock('../src/chatChannel.js', () => ({ chatChannel: { subscribe } }))

function warning(id, projectId, overrides = {}) {
  return {
    id, project_id: projectId, kind: 'project_broken', message: `Project '${projectId}' no longer builds — nope`,
    file: 'index.yml', line: 4, timestamp: '2026-09-06T08:00:00Z', ...overrides
  }
}

describe('BrokenProjectWarningsMenu.vue', () => {
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  async function mountMenu(rows) {
    api.getProjectBrokenWarnings.mockResolvedValue({ warnings: rows })
    const Menu = (await import('../src/components/settings/BrokenProjectWarningsMenu.vue')).default
    const opened = []
    const app = createApp(Menu, { metadataById: { p: { ui_label: 'Payments' } }, onOpen: (w) => opened.push(w) })
    app.mount(container)
    await vi.waitFor(() => expect(container.querySelector('.warnings-count')).not.toBeNull())
    return { opened, app, pushedFrame: subscribe.mock.calls[0][1] }
  }

  // The list itself lives behind the toolbar button — nothing is rendered
  // until the panel is open.
  async function openPanel() {
    container.querySelector('.warnings-btn').click()
    await vi.waitFor(() => expect(container.querySelector('.warnings-item')).not.toBeNull())
  }

  it('subscribes to system_warning frames and reloads when a project breaks', async () => {
    const { pushedFrame } = await mountMenu([warning(1, 'p')])

    expect(subscribe.mock.calls[0][0]).toBe('system_warning')
    expect(container.querySelector('.warnings-count').textContent).toBe('1')
    api.getProjectBrokenWarnings.mockResolvedValue({ warnings: [warning(1, 'p'), warning(2, 'q')] })

    pushedFrame({ type: 'system_warning', kind: 'project_broken', project_id: 'q', message: 'nope' })

    await vi.waitFor(() => expect(container.querySelector('.warnings-count').textContent).toBe('2'))
  })

  it('drops a project’s rows on a project_fixed frame, without refetching', async () => {
    const { pushedFrame } = await mountMenu([warning(1, 'p'), warning(2, 'q')])
    api.getProjectBrokenWarnings.mockClear()

    pushedFrame({ type: 'system_warning', kind: 'project_fixed', project_id: 'p' })

    await vi.waitFor(() => expect(container.querySelector('.warnings-count').textContent).toBe('1'))
    expect(api.getProjectBrokenWarnings).not.toHaveBeenCalled()
  })

  it('shows the short summary rather than the whole build message, keeping it as the tooltip', async () => {
    await mountMenu([warning(1, 'p')])
    await openPanel()

    const message = container.querySelector('.warnings-item-message')
    expect(message.textContent).toBe('index.yml no longer builds')
    expect(message.getAttribute('title')).toContain('no longer builds — nope')
  })

  it('hands the clicked row’s project, file and line to the caller', async () => {
    const { opened } = await mountMenu([warning(1, 'p')])
    await openPanel()

    container.querySelector('.warnings-item-open').click()

    expect(opened).toHaveLength(1)
    expect(opened[0]).toMatchObject({ project_id: 'p', file: 'index.yml', line: 4 })
  })

  it('dismisses one row on its own, leaving the rest', async () => {
    await mountMenu([warning(1, 'p'), warning(2, 'q')])
    await openPanel()

    container.querySelector('.warnings-item-dismiss').click()

    await vi.waitFor(() => expect(api.deleteProjectBrokenWarning).toHaveBeenCalledWith(1))
    await nextTick()
    expect(container.querySelectorAll('.warnings-item')).toHaveLength(1)
  })

  it('unsubscribes from the channel when it goes away', async () => {
    const { app } = await mountMenu([warning(1, 'p')])

    app.unmount()

    expect(unsubscribe).toHaveBeenCalled()
  })
})
