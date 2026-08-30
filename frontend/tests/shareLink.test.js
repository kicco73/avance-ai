import { beforeEach, describe, expect, it, vi } from 'vitest'

// shareLink.js reads window.location.search once, at module import time
// (see its own comment) — vi.resetModules() + a dynamic import() per test
// is what gets a fresh top-level evaluation against whatever URL that
// test set up first, same technique any module-scope-state module needs.
describe('shareLink', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('captures a ?project=<id> query param, strips it from the address bar, and hands it out exactly once', async () => {
    window.history.pushState(null, '', '/?project=abc123&foo=bar')
    const { consumeSharedProjectId } = await import('../src/shareLink.js')

    expect(window.location.search).toBe('?foo=bar')
    expect(consumeSharedProjectId()).toBe('abc123')
    expect(consumeSharedProjectId()).toBeNull()
  })

  it('drops the query string entirely once project was its only param', async () => {
    window.history.pushState(null, '', '/?project=abc123')
    await import('../src/shareLink.js')

    expect(window.location.search).toBe('')
  })

  it('resolves to null when there is no project param at all', async () => {
    window.history.pushState(null, '', '/?foo=bar')
    const { consumeSharedProjectId } = await import('../src/shareLink.js')

    expect(consumeSharedProjectId()).toBeNull()
    expect(window.location.search).toBe('?foo=bar') // left untouched
  })

  it('buildShareUrl encodes the id into a link at the current origin/pathname', async () => {
    window.history.pushState(null, '', '/')
    const { buildShareUrl } = await import('../src/shareLink.js')

    expect(buildShareUrl('my id/token')).toBe(`${window.location.origin}/?project=my%20id%2Ftoken`)
  })
})
