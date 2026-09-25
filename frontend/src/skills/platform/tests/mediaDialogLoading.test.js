import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { createApp, h, defineComponent, nextTick } from 'vue'

const { receivedPdfSources } = vi.hoisted(() => ({ receivedPdfSources: [] }))

vi.mock('vue-pdf-embed', () => ({
  default: defineComponent({
    props: { source: { required: true } },
    emits: ['rendered'],
    mounted() { receivedPdfSources.push(this.source); this.$emit('rendered') },
    render() { return h('div', { class: 'fake-pdf' }) }
  })
}))

const { getMe, getDriveFiles, postSaveMediaToDrive, postDownloadMediaToDrive, postRecordDriveDownload } = vi.hoisted(() => ({
  getMe: vi.fn(),
  getDriveFiles: vi.fn(),
  postSaveMediaToDrive: vi.fn(),
  postDownloadMediaToDrive: vi.fn(),
  postRecordDriveDownload: vi.fn(),
}))

vi.mock('../../../api.js', () => ({
  getMe, getDriveFiles, postSaveMediaToDrive, postDownloadMediaToDrive, postRecordDriveDownload,
}))

const MediaDialog = (await import('../../../components/MediaDialog.vue')).default

function fakeResponse({ ok = true, contentType = '', text = '', arrayBuffer = new ArrayBuffer(0), blob = new Blob() } = {}) {
  return {
    ok,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? contentType : null) },
    text: () => Promise.resolve(text),
    arrayBuffer: () => Promise.resolve(arrayBuffer),
    blob: () => Promise.resolve(blob),
  }
}

function mount(url) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ setup: () => () => h(MediaDialog, { url }) })
  app.mount(host)
  return host
}

describe('MediaDialog classifies by the response Content-Type, not the url', () => {
  const realFetch = globalThis.fetch

  beforeEach(() => {
    receivedPdfSources.length = 0
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:fake'), revokeObjectURL: vi.fn() })
  })

  afterEach(() => {
    globalThis.fetch = realFetch
    vi.unstubAllGlobals()
  })

  it('renders the pdf viewer for a Content-Type of application/pdf even when the path carries no .pdf extension', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'application/pdf' })))

    const host = mount('/api/core/projects/proj-1/drive/invoice')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.fake-pdf')).not.toBeNull()
    expect(host.querySelector('.media-dialog-image')).toBeNull()
    expect(host.querySelector('.media-dialog-status')).toBeNull()
    expect(receivedPdfSources).toHaveLength(1)
    expect(receivedPdfSources[0]).toBeInstanceOf(Uint8Array)
  })

  it('shows the spinner and hides the content for a pdf until it has rendered', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'application/pdf' })))

    const host = mount('/api/core/projects/proj-1/drive/report.pdf')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })

  it('shows the spinner for an image until it loads, then reveals it', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'image/png' })))

    const host = mount('/api/core/projects/proj-1/drive/pic')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).not.toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).toBeNull()

    host.querySelector('.media-dialog-image').dispatchEvent(new Event('load'))
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })

  it('shows the spinner for markdown until the fetch resolves', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'text/markdown', text: '# hi' })))

    const host = mount('/api/core/projects/proj-1/drive/notes')

    expect(host.querySelector('.media-dialog-loading-overlay')).not.toBeNull()

    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-loading-overlay')).toBeNull()
    expect(host.querySelector('.media-dialog-content-ready')).not.toBeNull()
  })

  it('renders text/plain as markdown too, not a broken image — drive.write() with no recognized extension stores plain text this way', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'text/plain', text: 'a plain note' })))

    const host = mount('/api/core/projects/proj-1/drive/note')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-markdown')?.textContent).toContain('a plain note')
    expect(host.querySelector('.media-dialog-status')).toBeNull()
  })

  it('renders an audio player for an audio Content-Type', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'audio/mpeg' })))

    const host = mount('/api/core/projects/proj-1/drive/song')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-audio')).not.toBeNull()
  })

  it('shows a status message instead of a broken image when the fetch itself fails', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ ok: false })))

    const host = mount('/api/core/projects/proj-1/drive/missing')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(host.querySelector('.media-dialog-status')?.textContent).toContain("Couldn't load")
    expect(host.querySelector('.media-dialog-image')).toBeNull()
  })
})

describe('MediaDialog Save/Download to drive', () => {
  const PDF_URL = '/api/core/projects/proj-1/files/media/report.pdf/content'

  beforeEach(() => {
    getMe.mockReset()
    getDriveFiles.mockReset()
    postSaveMediaToDrive.mockReset()
    postDownloadMediaToDrive.mockReset()
    postRecordDriveDownload.mockReset()
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'application/pdf' })))
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:fake'), revokeObjectURL: vi.fn() })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
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

  it('shows no action below customer', async () => {
    getMe.mockResolvedValue({ role: 'user' })
    getDriveFiles.mockResolvedValue({ files: [] })

    const host = mount(PDF_URL)
    await nextTick()
    await nextTick()
    await nextTick()

    expect(actionButtons(host)).toEqual([])
  })

  it('never shows Save/Download for a non-pdf media kind, even for a customer', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    getDriveFiles.mockResolvedValue({ files: [] })
    globalThis.fetch = vi.fn(() => Promise.resolve(fakeResponse({ contentType: 'image/png' })))

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

  it('shows only Download, never Save, for a file already viewed straight from the drive', async () => {
    getMe.mockResolvedValue({ role: 'customer' })

    const host = mount('/api/core/projects/proj-1/drive/media%2Freport.pdf')
    await nextTick()
    await nextTick()
    await nextTick()

    expect(getDriveFiles).not.toHaveBeenCalled()
    expect(actionButtons(host)).toEqual(['Download'])
  })

  it('clicking Download on a drive-viewed file only counts it, no re-save', async () => {
    getMe.mockResolvedValue({ role: 'customer' })
    postRecordDriveDownload.mockResolvedValue({ path: 'media/report.pdf', downloads: 3 })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const host = mount('/api/core/projects/proj-1/drive/media%2Freport.pdf')
    await nextTick()
    await nextTick()
    await nextTick()

    host.querySelector('.media-dialog-action-btn').click()
    await nextTick()
    await nextTick()

    expect(postRecordDriveDownload).toHaveBeenCalledWith('proj-1', 'media/report.pdf')
    expect(postDownloadMediaToDrive).not.toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalledOnce()
    clickSpy.mockRestore()
  })
})
