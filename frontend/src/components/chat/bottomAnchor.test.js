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

  it('keeps following when the echo of its own placement arrives after a growth larger than the near-bottom threshold', () => {
    const { scroller, anchor } = attached(1000)
    scroller.scrollHeight = 1200
    anchor.follow()
    scroller.scrollHeight = 1600
    anchor.onScroll()
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('stops following once the reader scrolls away, and resumes when they come back near the bottom', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop = 800
    anchor.onScroll()
    scroller.scrollHeight = 2400
    anchor.follow()
    expect(scroller.scrollTop).toBe(800)

    scroller.scrollTop = bottomOf(scroller) - 40
    anchor.onScroll()
    scroller.scrollHeight = 2800
    anchor.follow()
    expect(scroller.scrollTop).toBe(bottomOf(scroller))
  })

  it('releases on a wheel gesture that leaves the bottom', () => {
    const { scroller, anchor } = attached(2000)
    scroller.scrollTop = 500
    anchor.handleEvent()
    scroller.scrollHeight = 2400
    anchor.follow()
    expect(scroller.scrollTop).toBe(500)
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
})
