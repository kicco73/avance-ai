const NEAR_BOTTOM_THRESHOLD_PX = 80

export class BottomAnchor {
  constructor() {
    this.scroller = null
    this.observer = null
    this.stuck = true
    this.placedAt = 0
  }

  attach(scroller, content) {
    this.scroller = scroller
    this.observer = new ResizeObserver(() => this.follow())
    this.observer.observe(content)
    this.observer.observe(scroller)
    this.jump()
  }

  detach() {
    this.observer?.disconnect()
    this.observer = null
    this.scroller = null
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
    if (this.stuck) this.keepFollowing()
    else this.resumeIfBackAtBottom()
  }

  keepFollowing() {
    this.stuck = this.scroller.scrollTop > this.placedAt - NEAR_BOTTOM_THRESHOLD_PX
  }

  resumeIfBackAtBottom() {
    this.stuck = this.distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
    this.placedAt = this.scroller.scrollTop
  }
}
