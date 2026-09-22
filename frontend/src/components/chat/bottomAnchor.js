const NEAR_BOTTOM_THRESHOLD_PX = 80

export class BottomAnchor {
  constructor() {
    this.scroller = null
    this.observer = null
    this.stuck = true
    this.placedAt = null
  }

  attach(scroller, content) {
    this.scroller = scroller
    this.observer = new ResizeObserver(() => this.follow())
    this.observer.observe(content)
    this.observer.observe(scroller)
    scroller.addEventListener('wheel', this, { passive: true })
    scroller.addEventListener('touchmove', this, { passive: true })
    scroller.addEventListener('keydown', this)
    this.jump()
  }

  detach() {
    this.observer?.disconnect()
    this.observer = null
    const scroller = this.scroller
    if (scroller) {
      scroller.removeEventListener('wheel', this)
      scroller.removeEventListener('touchmove', this)
      scroller.removeEventListener('keydown', this)
    }
    this.scroller = null
    this.placedAt = null
  }

  handleEvent() {
    this.release()
  }

  get bottom() {
    return this.scroller.scrollHeight - this.scroller.clientHeight
  }

  get distanceFromBottom() {
    return this.bottom - this.scroller.scrollTop
  }

  jump() {
    if (!this.scroller) return
    this.stuck = true
    this.place()
  }

  follow() {
    if (!this.scroller || !this.stuck) return
    this.place()
  }

  place() {
    this.scroller.scrollTop = this.bottom
    this.placedAt = this.scroller.scrollTop
  }

  onScroll() {
    if (!this.scroller) return
    if (this.scroller.scrollTop === this.placedAt) {
      this.placedAt = null
      return
    }
    this.stuck = this.distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
  }

  release() {
    if (!this.scroller) return
    this.stuck = this.distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
  }
}
