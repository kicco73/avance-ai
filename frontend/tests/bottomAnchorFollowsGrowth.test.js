import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { BottomAnchor } from '../src/components/chat/bottomAnchor.js'

let resizeCallback = null
const realResizeObserver = globalThis.ResizeObserver
const realRequestAnimationFrame = globalThis.requestAnimationFrame
const realCancelAnimationFrame = globalThis.cancelAnimationFrame

class CapturingResizeObserver {
  constructor(callback) {
    resizeCallback = callback
  }
  observe() {}
  disconnect() {
    resizeCallback = null
  }
}

const clock = {
  now: 0,
  pending: new Map(),
  nextId: 1,
  request(callback) {
    const id = this.nextId++
    this.pending.set(id, callback)
    return id
  },
  cancel(id) {
    this.pending.delete(id)
  },
  tick(ms) {
    this.now += ms
    const due = [...this.pending]
    this.pending.clear()
    due.forEach(([, callback]) => callback(this.now))
  },
  run() {
    while (this.pending.size) this.tick(50)
  }
}

function fakeScroller() {
  return {
    clientHeight: 400,
    scrollHeight: 400,
    scrollTop: 0,
    smoothScrolls: [],
    addEventListener() {},
    removeEventListener() {},
    scrollTo(options) {
      this.smoothScrolls.push(options)
    },
    grow(px) {
      this.scrollHeight += px
      resizeCallback()
    }
  }
}

describe('BottomAnchor', () => {
  beforeEach(() => {
    globalThis.ResizeObserver = CapturingResizeObserver
    globalThis.requestAnimationFrame = (callback) => clock.request(callback)
    globalThis.cancelAnimationFrame = (id) => clock.cancel(id)
    clock.now = 0
    clock.pending.clear()
  })

  afterEach(() => {
    globalThis.ResizeObserver = realResizeObserver
    globalThis.requestAnimationFrame = realRequestAnimationFrame
    globalThis.cancelAnimationFrame = realCancelAnimationFrame
  })

  it('animates to the bottom when a message grows taller after it was rendered', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(300)
    clock.tick(0)
    clock.tick(16)
    const partway = el.scrollTop
    clock.run()

    expect(partway).toBeGreaterThan(0)
    expect(partway).toBeLessThan(300)
    expect(el.scrollTop).toBe(300)
  })

  it('drives the scroll itself rather than handing iOS a native smooth scroll', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(300)
    clock.run()

    expect(el.smoothScrolls).toEqual([])
  })

  it('keeps following while the message keeps growing during the animation', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(300)
    clock.tick(0)
    clock.tick(16)
    anchor.onScroll()
    el.grow(200)
    clock.run()

    expect(el.scrollTop).toBe(500)
  })

  it('leaves the view alone once the reader has scrolled up', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.scrollHeight = 1200
    el.scrollTop = 400
    anchor.onScroll()
    el.grow(300)
    clock.run()

    expect(el.scrollTop).toBe(400)
  })

  it('stops following the moment the reader takes the scroll', () => {
    const el = fakeScroller()
    const anchor = new BottomAnchor()
    anchor.attach(el, {})

    el.grow(600)
    clock.tick(0)
    clock.tick(16)
    anchor.handleEvent()
    el.scrollTop = 100
    clock.run()

    expect(el.scrollTop).toBe(100)
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
    clock.run()

    expect(el.scrollTop).toBe(1100)
  })
})
