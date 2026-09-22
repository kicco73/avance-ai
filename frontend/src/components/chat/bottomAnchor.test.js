import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BottomAnchor } from './bottomAnchor.js'

function fakeFrames() {
  let nextHandle = 0
  const queue = new Map()
  const requestAnimationFrame = vi.fn((cb) => {
    nextHandle += 1
    queue.set(nextHandle, cb)
    return nextHandle
  })
  const cancelAnimationFrame = vi.fn((handle) => queue.delete(handle))
  function runNextFrame(now) {
    const [[handle, cb]] = queue
    queue.delete(handle)
    cb(now)
  }
  return { requestAnimationFrame, cancelAnimationFrame, runNextFrame, pending: () => queue.size }
}

function fakeScroller(scrollHeight, clientHeight = 400) {
  return { scrollTop: 0, scrollHeight, clientHeight, addEventListener() {}, removeEventListener() {} }
}

describe('BottomAnchor', () => {
  let frames

  beforeEach(() => {
    frames = fakeFrames()
    vi.stubGlobal('requestAnimationFrame', frames.requestAnimationFrame)
    vi.stubGlobal('cancelAnimationFrame', frames.cancelAnimationFrame)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('re-aims a follow already in flight at a growth that arrives before it settles, instead of finishing at the stale target', () => {
    const scroller = fakeScroller(1000)
    const anchor = new BottomAnchor()
    anchor.attach(scroller, {})

    scroller.scrollHeight = 1200
    anchor.follow()
    expect(frames.pending()).toBe(1)
    frames.runNextFrame(0)
    frames.runNextFrame(100)
    expect(anchor.settling).toBe(true)
    expect(scroller.scrollTop).toBeLessThan(scroller.scrollHeight - scroller.clientHeight)

    scroller.scrollHeight = 3000
    anchor.follow()

    expect(frames.pending()).toBe(1)

    frames.runNextFrame(150)
    frames.runNextFrame(150 + 250)

    expect(anchor.settling).toBe(false)
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - scroller.clientHeight)
  })

  it('starts a fresh follow normally when nothing was in flight', () => {
    const scroller = fakeScroller(1000)
    const anchor = new BottomAnchor()
    anchor.attach(scroller, {})

    scroller.scrollHeight = 1800
    anchor.follow()
    frames.runNextFrame(0)
    frames.runNextFrame(250)

    expect(anchor.settling).toBe(false)
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - scroller.clientHeight)
  })
})
