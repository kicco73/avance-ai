import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { BottomAnchor } from '../src/components/chat/bottomAnchor.js'

let resizeCallback = null
const RealResizeObserver = globalThis.ResizeObserver

class CapturingResizeObserver {
  constructor(callback) {
    resizeCallback = callback
  }
  observe() {}
  disconnect() {
    resizeCallback = null
  }
}

function fakeScroller() {
  return {
    clientHeight: 400,
    scrollHeight: 400,
    scrollTop: 0,
    scrolls: [],
    addEventListener() {},
    removeEventListener() {},
    scrollTo(options) {
      this.scrolls.push(options)
    },
    grow(px) {
      this.scrollHeight += px
      resizeCallback()
    }
  }
}

describe('BottomAnchor', () => {
  beforeEach(() => { globalThis.ResizeObserver = CapturingResizeObserver })
  afterEach(() => { globalThis.ResizeObserver = RealResizeObserver })

  it('animates to the bottom when a message grows taller after it was rendered', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(300)

    expect(el.scrolls).toEqual([{ top: 300, behavior: 'smooth' }])
  })

  it('keeps following while the message keeps growing during the animation', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(300)
    anchor.onScroll()
    el.grow(200)

    expect(el.scrolls.at(-1)).toEqual({ top: 500, behavior: 'smooth' })
  })

  it('leaves the view alone once the reader has scrolled up', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.scrollHeight = 1200
    el.scrollTop = 400
    anchor.onScroll()
    el.grow(300)

    expect(el.scrolls).toEqual([])
  })

  it('goes back to following after the reader returns to the bottom', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.scrollHeight = 1200
    el.scrollTop = 400
    anchor.onScroll()
    el.scrollTop = 800
    anchor.onScroll()
    el.grow(300)

    expect(el.scrolls).toEqual([{ top: 1100, behavior: 'smooth' }])
  })
})
