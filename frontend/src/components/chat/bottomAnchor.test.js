import { describe, expect, it } from 'vitest'
import { BottomAnchor } from './bottomAnchor.js'

function fakeScroller(scrollHeight, clientHeight = 400) {
  return { scrollTop: 0, scrollHeight, clientHeight, addEventListener() {}, removeEventListener() {} }
}

function attached(scrollHeight) {
  const scroller = fakeScroller(scrollHeight)
  const anchor = new BottomAnchor()
  anchor.attach(scroller, {})
  return { scroller, anchor }
}

function bottomOf(scroller) {
  return scroller.scrollHeight - scroller.clientHeight
}

describe('BottomAnchor', () => {
  it('is at the bottom after every growth, however dense the growth is', () => {
    const { scroller, anchor } = attached(1000)
    for (let i = 0; i < 300; i++) {
      scroller.scrollHeight += 24
      anchor.follow()
      expect(scroller.scrollTop).toBe(bottomOf(scroller))
    }
  })

  it('keeps following when a scroll event arrives after a growth taller than the threshold and the reader moved nothing', () => {
    const { scroller, anchor } = attached(1000)
    scroller.scrollHeight += 600

    anchor.onScroll()

    expect(anchor.stuck).toBe(true)
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('stops following once the reader scrolls away, and keeps the position they left', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop = bottomOf(scroller) - 400
    anchor.onScroll()
    expect(anchor.stuck).toBe(false)

    const left = scroller.scrollTop
    scroller.scrollHeight += 600
    anchor.follow()
    expect(scroller.scrollTop).toBe(left)
  })

  it('ignores a reader nudge smaller than the threshold, and follows the next growth', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop -= 40
    anchor.onScroll()

    expect(anchor.stuck).toBe(true)
    scroller.scrollHeight += 600
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('follows again once the reader comes back near the bottom', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop = 500
    anchor.onScroll()

    scroller.scrollTop = bottomOf(scroller) - 40
    anchor.onScroll()
    expect(anchor.stuck).toBe(true)

    scroller.scrollHeight += 600
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('keeps following when the content shrinks and the clamped position echoes back', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollHeight -= 500
    anchor.follow()
    scroller.scrollTop = bottomOf(scroller)

    anchor.onScroll()

    expect(anchor.stuck).toBe(true)
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('follows when the scroller itself shrinks around the same content', () => {
    const { scroller, anchor } = attached(2000)
    scroller.clientHeight = 200
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('re-sticks and jumps to the bottom of a new conversation', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop = 100
    anchor.onScroll()
    scroller.scrollHeight = 3000

    anchor.jump()

    expect(scroller.scrollTop).toBe(bottomOf(scroller))
    scroller.scrollHeight = 3100
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('does nothing once detached', () => {
    const { scroller, anchor } = attached(2000)
    anchor.detach()
    scroller.scrollHeight = 5000

    anchor.follow()
    anchor.onScroll()
    anchor.jump()

    expect(scroller.scrollTop).toBe(1600)
  })
})
