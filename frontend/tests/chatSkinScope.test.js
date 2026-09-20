import { describe, it, expect } from 'vitest'
import { scopeSkinToChat } from '../src/chatSkinScope.js'

describe('scopeSkinToChat hoists what must stay document-level out of @scope', () => {
  it('keeps an @import whole, semicolon included, ahead of the scoped block', () => {
    const applied = scopeSkinToChat(
      "@import url('https://fonts.googleapis.com/css2?family=Foo');\n.chat-header { color: red; }"
    )

    expect(applied).toContain("@import url('https://fonts.googleapis.com/css2?family=Foo');")
    expect(applied.indexOf('@import')).toBeLessThan(applied.indexOf('@scope'))
    expect(applied.slice(applied.indexOf('@scope'))).not.toContain('@import')
    expect(applied.slice(applied.indexOf('@scope'))).toContain('.chat-header')
  })

  it('hoists an @import a comment introduces', () => {
    const applied = scopeSkinToChat("/* fonts */\n@import url('a.css');\n.chat-header { color: red; }")

    expect(applied.indexOf('@import')).toBeLessThan(applied.indexOf('@scope'))
  })

  it('puts @import before the other document-level at-rules whatever order they were written in', () => {
    const applied = scopeSkinToChat(
      "@font-face { font-family: Foo; src: url(foo.woff2); }\n@import url('a.css');\n.chat-header { color: red; }"
    )

    expect(applied.indexOf('@import')).toBeLessThan(applied.indexOf('@font-face'))
    expect(applied.indexOf('@font-face')).toBeLessThan(applied.indexOf('@scope'))
  })

  it('does not end a rule on the url(...) of an @namespace', () => {
    const applied = scopeSkinToChat("@namespace svg url(http://www.w3.org/2000/svg);\n.chat-header { color: red; }")

    expect(applied).toContain('@namespace svg url(http://www.w3.org/2000/svg);')
    expect(applied.slice(applied.indexOf('@scope'))).not.toContain(';\n.chat-header')
  })

  it('scopes a rule whose declaration carries a url(...)', () => {
    const applied = scopeSkinToChat('.chat-body { background: url(bg.png) no-repeat; }\n.chat-header { color: red; }')

    const scoped = applied.slice(applied.indexOf('@scope'))
    expect(scoped).toContain(':scope.chat-body')
    expect(scoped).toContain(':scope.chat-header')
  })
})

describe('scopeSkinToChat keeps :hover for devices that can hover', () => {
  it('moves a :hover rule under @media (hover: hover)', () => {
    const applied = scopeSkinToChat('.action-btn:hover:not(:disabled) { background: red; }')

    const media = applied.indexOf('@media (hover: hover)')
    expect(media).toBeGreaterThan(applied.indexOf('@scope'))
    expect(applied.slice(media)).toContain(':scope.action-btn:hover:not(:disabled) {')
  })

  it('leaves the non-hover selectors of a shared list outside the media query', () => {
    const applied = scopeSkinToChat('.action-btn:focus, .action-btn:hover { background: red; }')

    const media = applied.indexOf('@media (hover: hover)')
    expect(applied.slice(0, media)).toContain('.action-btn:focus')
    expect(applied.slice(0, media)).not.toContain(':hover')
    expect(applied.slice(media)).toContain('.action-btn:hover')
    expect(applied.slice(media)).not.toContain(':focus')
  })

  it('gates a :hover rule written inside a state block', () => {
    const applied = scopeSkinToChat('@media (max-width: 640px) { .state-crisis .action-btn:hover { color: red; } }')

    expect(applied).toContain('@media (hover: hover)')
    expect(applied.indexOf('@media (hover: hover)')).toBeGreaterThan(applied.indexOf('@media (max-width: 640px)'))
  })
})
