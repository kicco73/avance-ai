const NEAR_BOTTOM_THRESHOLD_PX = 80
const FOLLOW_DURATION_MS = 250

export class BottomAnchor {
  constructor() {
    this.scroller = null
    this.observer = null
    this.stuck = true
    this.settling = false
    this.frame = null
    this.from = 0
    this.startedAt = null
  }

  attach(scroller, content) {
    this.scroller = scroller
    this.observer = new ResizeObserver(() => this.follow())
    this.observer.observe(content)
    scroller.addEventListener('wheel', this, { passive: true })
    scroller.addEventListener('touchmove', this, { passive: true })
    scroller.addEventListener('keydown', this)
    this.jump()
  }

  detach() {
    this.stopSettling()
    this.observer?.disconnect()
    this.observer = null
    const scroller = this.scroller
    if (scroller) {
      scroller.removeEventListener('wheel', this)
      scroller.removeEventListener('touchmove', this)
      scroller.removeEventListener('keydown', this)
    }
    this.scroller = null
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
    this.stopSettling()
    this.stuck = true
    this.scroller.scrollTop = this.bottom
  }

  follow() {
    if (!this.scroller || !this.stuck) return
    if (this.distanceFromBottom < 1) {
      this.stopSettling()
      return
    }
    this.from = this.scroller.scrollTop
    this.startedAt = null
    if (this.settling) return
    this.settling = true
    this.frame = requestAnimationFrame((now) => this.step(now))
  }

  step(now) {
    this.frame = null
    if (!this.scroller || !this.settling) return
    this.startedAt ??= now
    const progress = Math.min(1, (now - this.startedAt) / FOLLOW_DURATION_MS)
    const eased = 1 - (1 - progress) ** 3
    this.scroller.scrollTop = this.from + (this.bottom - this.from) * eased
    if (progress === 1) {
      this.settling = false
      return
    }
    this.frame = requestAnimationFrame((next) => this.step(next))
  }

  stopSettling() {
    this.settling = false
    if (this.frame !== null) cancelAnimationFrame(this.frame)
    this.frame = null
  }

  onScroll() {
    if (!this.scroller) return
    if (this.settling) return
    this.stuck = this.distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
  }

  release() {
    if (!this.scroller) return
    this.stopSettling()
    this.stuck = this.distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
  }
}
