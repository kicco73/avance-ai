// Regression coverage for a real bug: index.css's own url(...) references
// must resolve to an *absolute* API URL, not a same-origin-relative one.
// The backend used to rewrite them itself into a bare "/api/..." path,
// which only happened to work in production (nginx proxies frontend and
// API onto one origin there) — in dev, frontend (:5173) and backend
// (:8000) are different origins with no proxy between them, so every
// background-image etc. silently 404'd while the CSS text itself (fetched
// through the real, absolute VITE_API_URL) loaded fine. Resolution moved
// entirely client-side (see cssAssetUrls.js's own docstring) — this
// verifies it actually produces an absolute, cross-origin-correct URL.
import { describe, expect, it } from 'vitest'
import { resolveCssAssetUrls } from '../src/cssAssetUrls.js'

describe('resolveCssAssetUrls', () => {
  it('rewrites a relative url(...) to the real, absolute API origin — not a same-origin-relative path', () => {
    const result = resolveCssAssetUrls('.x { background: url(bg.png); }', 'proj')
    expect(result).toBe('.x { background: url(http://localhost:8000/api/projects/proj/files/bg.png/content); }')
  })

  it('carries a given sessionId onto the rewritten URL, for pinned-revision consistency with the stylesheet itself', () => {
    const result = resolveCssAssetUrls('.x { background: url(bg.png); }', 'proj', 42)
    expect(result).toContain('session_id=42')
  })

  it('reduces a path with directories to its bare basename — the archive namespace is flat', () => {
    const result = resolveCssAssetUrls(".x { background: url('assets/bg.png'); }", 'proj')
    expect(result).toContain('/files/bg.png/content')
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
    expect(result).toContain('/files/a.png/content')
    expect(result).toContain("'http://localhost:8000/api/projects/proj/files/b.png/content'")
    expect(result).toContain('"http://localhost:8000/api/projects/proj/files/c.png/content"')
  })
})
