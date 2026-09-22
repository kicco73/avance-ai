import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const { getDriveFiles } = vi.hoisted(() => ({ getDriveFiles: vi.fn() }))
const { openMediaDialog } = vi.hoisted(() => ({ openMediaDialog: vi.fn() }))

vi.mock('../api.js', () => ({ getDriveFiles, driveFileContentUrl: (projectId, path) => `/api/core/projects/${projectId}/drive/${path}` }))
vi.mock('../../../openMediaDialog.js', () => ({ openMediaDialog }))

const InspectorDriveTab = (await import('../components/inspector/InspectorDriveTab.vue')).default

async function mount(projectId) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  let instance = null
  const app = createApp({
    setup: () => () => h(InspectorDriveTab, { projectId, ref: (el) => { instance = el } })
  })
  app.mount(host)
  await nextTick()
  await instance.loadFiles()
  await nextTick()
  return host
}

describe('InspectorDriveTab opens files the same way show_media does', () => {
  afterEach(() => {
    getDriveFiles.mockReset()
    openMediaDialog.mockReset()
  })

  it('clicking a file opens the shared MediaDialog with the drive content url, not a raw-text dialog', async () => {
    getDriveFiles.mockResolvedValue({ files: [{ path: 'reports/last.pdf', size: 100, updated_at: null }] })

    const host = await mount('proj-1')

    host.querySelector('.inspector-drive-item').click()

    expect(openMediaDialog).toHaveBeenCalledWith('/api/core/projects/proj-1/drive/reports/last.pdf')
  })
})
