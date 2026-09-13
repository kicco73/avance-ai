import { beforeAll, describe, expect, it, vi } from 'vitest'

let resolveCssAssetUrls

beforeAll(async () => {
  vi.stubEnv('VITE_API_URL', 'http://localhost:8000/api')
  vi.resetModules()
  ;({ resolveCssAssetUrls } = await import('../src/cssAssetUrls.js'))
})

describe('resolveCssAssetUrls', () => {
  it('rewrites a relative url(...) to the real, absolute API origin — not a same-origin-relative path', () => {
    const result = resolveCssAssetUrls('.x { background: url(bg.png); }', 'proj')
    expect(result).toBe('.x { background: url(http://localhost:8000/api/core/projects/proj/files/aspect%2Fbg.png/content); }')
  })

  it('carries a given sessionId onto the rewritten URL, for pinned-revision consistency with the stylesheet itself', () => {
    const result = resolveCssAssetUrls('.x { background: url(bg.png); }', 'proj', 42)
    expect(result).toContain('session_id=42')
  })

  it('reduces a path with directories to its bare basename — the archive namespace is flat', () => {
    const result = resolveCssAssetUrls(".x { background: url('assets/bg.png'); }", 'proj')
    expect(result).toContain('/files/aspect%2Fbg.png/content')
  })

  it('leaves an absolute http(s)/data: URL untouched', () => {
    expect(resolveCssAssetUrls('.x { background: url(https://example.com/bg.png); }', 'proj'))
      .toBe('.x { background: url(https://example.com/bg.png); }')
    expect(resolveCssAssetUrls('.x { background: url(data:image/png;base64,AAAA); }', 'proj'))
      .toBe('.x { background: url(data:image/png;base64,AAAA); }')
  })

  it('rewrites every url(...) in a multi-rule stylesheet, quoted or not', () => {
    const css = ".a { background: url(a.png); } .b { background: url('b.png'); } .c { background: url(\"c.png\"); }"
    const result = resolveCssAssetUrls(css, 'proj')
    expect(result).toContain('/files/aspect%2Fa.png/content')
    expect(result).toContain("'http://localhost:8000/api/core/projects/proj/files/aspect%2Fb.png/content'")
    expect(result).toContain('"http://localhost:8000/api/core/projects/proj/files/aspect%2Fc.png/content"')
  })
})
