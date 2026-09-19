class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= ResizeObserverStub

Element.prototype.scrollTo ??= function scrollTo(options) {
  this.scrollTop = options?.top ?? 0
}
