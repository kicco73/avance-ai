import { beforeEach, describe, expect, it, vi } from 'vitest'

// shareLink.js reads window.location.search once, at module import time
// (see its own comment) — vi.resetModules() + a dynamic import() per test
// is what gets a fresh top-level evaluation against whatever URL that
// test set up first, same technique any module-scope-state module needs.
describe('shareLink', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('captures a ?invite=<code> query param, strips it from the address bar, and hands it out exactly once', async () => {
    window.history.pushState(null, '', '/?invite=Ab3dE9&foo=bar')
    const { consumeInviteCode } = await import('../src/shareLink.js')

    expect(window.location.search).toBe('?foo=bar')
    expect(consumeInviteCode()).toBe('Ab3dE9')
    expect(consumeInviteCode()).toBeNull()
  })

  it('drops the query string entirely once invite was its only param', async () => {
    window.history.pushState(null, '', '/?invite=Ab3dE9')
    await import('../src/shareLink.js')

    expect(window.location.search).toBe('')
  })

  it('resolves to null when there is no invite param at all', async () => {
    window.history.pushState(null, '', '/?foo=bar')
    const { consumeInviteCode } = await import('../src/shareLink.js')

    expect(consumeInviteCode()).toBeNull()
    expect(window.location.search).toBe('?foo=bar') // left untouched
  })

  it('buildInviteUrl encodes the code into a link at the current origin/pathname', async () => {
    window.history.pushState(null, '', '/')
    const { buildInviteUrl } = await import('../src/shareLink.js')

    expect(buildInviteUrl('Ab 3d/E9')).toBe(`${window.location.origin}/?invite=Ab%203d%2FE9`)
  })

  describe('peekInviteCode', () => {
    it('reads the captured code without spending it — repeatable, unlike consumeInviteCode', async () => {
      window.history.pushState(null, '', '/?invite=Ab3dE9')
      const { peekInviteCode, consumeInviteCode } = await import('../src/shareLink.js')

      expect(peekInviteCode()).toBe('Ab3dE9')
      expect(peekInviteCode()).toBe('Ab3dE9') // still there
      expect(consumeInviteCode()).toBe('Ab3dE9') // now actually spent
      expect(peekInviteCode()).toBeNull()
    })

    it('resolves to null with no invite param at all', async () => {
      window.history.pushState(null, '', '/')
      const { peekInviteCode } = await import('../src/shareLink.js')

      expect(peekInviteCode()).toBeNull()
    })
  })
})
