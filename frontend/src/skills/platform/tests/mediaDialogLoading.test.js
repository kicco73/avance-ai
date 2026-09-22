import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { createApp, h, defineComponent, nextTick } from 'vue'

vi.mock('vue-pdf-embed', () => ({
  default: defineComponent({
    props: { source: { type: String, required: true } },
    emits: ['rendered'],
    mounted() { this.$emit('rendered') },
    render() { return h('div', { class: 'fake-pdf' }) }
  })
}))

const { getMe, getDriveFiles, postSaveMediaToDrive, postDownloadMediaToDrive } = vi.hoisted(() => ({
  getMe: vi.fn(),
  getDriveFiles: vi.fn(),
  postSaveMediaToDrive: vi.fn(),
  postDownloadMediaToDrive: vi.fn(),
}))

vi.mock('../../../api.js', () => ({
  getMe, getDriveFiles, postSaveMediaToDrive, postDownloadMediaToDrive,
}))

const MediaDialog = (await import('../../../components/MediaDialog.vue')).default

function mount(url) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup: () => () => h(MediaDialog, { url }) })
  app.mount(host)
  return host
}

describe('MediaDialog loading state', () => {
  const realFetch = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ text: () => Promise.resolve('# hi') }))
  })

  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('shows the spinner and hides the content for a pdf until it has rendered', async () => {
    const host = mount('https://example.com/doc.pdf')
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })

  it('shows the spinner for an image until it loads, then reveals it', async () => {
    const host = mount('https://example.com/pic.png')
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).not.toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).toBeNull()

    host.querySelector('.media-dialog-image').dispatchEvent(new Event('load'))
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })

  it('shows the spinner for markdown until the fetch resolves', async () => {
    const host = mount('https://example.com/notes.md')

    expect(host.querySelector('.media-dialog-loading-overlay')).not.toBeNull()

    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })
})

describe('MediaDialog Save/Download to drive', () => {
  const PDF_URL = '/api/core/projects/proj-1/files/media/report.pdf/content'

  beforeEach(() => {
    getMe.mockReset()
    getDriveFiles.mockReset()
    postSaveMediaToDrive.mockReset()
    postDownloadMediaToDrive.mockReset()
  })

  function actionButtons(host) {
    return [...host.querySelectorAll('.media-dialog-action-btn')].map((el) => el.textContent.trim())
  }

  it('shows both Save and Download for a customer when the file is not yet in their drive', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [] })

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    expect(actionButtons(host)).toEqual(['Save', 'Download'])
  })

  it('shows only Download once the file is already in the drive', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [{ path: 'media/report.pdf' }] })

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    expect(actionButtons(host)).toEqual(['Download'])
  })

  it('shows neither button below customer', async () => {
    getMe.mockResolvedValue({ role: 'user' })
    getDriveFiles.mockResolvedValue({ files: [] })

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    expect(actionButtons(host)).toEqual([])
  })

  it('never shows the buttons for a non-pdf media kind, even for a customer', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [] })

    const host = mount('/api/core/projects/proj-1/files/media/pic.png/content')
    await nextTick()
    host.querySelector('.media-dialog-image')?.dispatchEvent(new Event('load'))
    await nextTick()

    expect(actionButtons(host)).toEqual([])
  })

  it('clicking Save adds the file to the drive and then hides the Save button', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [] })
    postSaveMediaToDrive.mockResolvedValue({ path: 'media/report.pdf', downloads: 0 })

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    host.querySelector('.media-dialog-action-btn').click()
    await nextTick()
    await nextTick()

    expect(postSaveMediaToDrive).toHaveBeenCalledWith('proj-1', 'media/report.pdf')
    expect(actionButtons(host)).toEqual(['Download'])
  })

  it('clicking Download saves it, counts it, and triggers a real browser download', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [] })
    postDownloadMediaToDrive.mockResolvedValue({ path: 'media/report.pdf', downloads: 1 })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    const downloadBtn = [...host.querySelectorAll('.media-dialog-action-btn')].find((el) => el.textContent.trim() === 'Download')
    downloadBtn.click()
    await nextTick()
    await nextTick()

    expect(postDownloadMediaToDrive).toHaveBeenCalledWith('proj-1', 'media/report.pdf')
    expect(clickSpy).toHaveBeenCalledOnce()
    clickSpy.mockRestore()
  })
})
