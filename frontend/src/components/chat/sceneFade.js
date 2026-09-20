export class SceneFade {
  constructor() {
    this.element = null
  }

  attach(element) {
    this.element = element
  }

  detach() {
    this.element = null
  }

  animations() {
    return this.element?.getAnimations?.() ?? []
  }

  replay() {
    for (const animation of this.animations()) {
      animation.currentTime = 0
      animation.play()
    }
  }
}
