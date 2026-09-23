class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= ResizeObserverStub

Element.prototype.scrollTo ??= function scrollTo(options) {
  this.scrollTop = options?.top ?? 0
}

const dialogPrototype = globalThis.HTMLDialogElement?.prototype

dialogPrototype.showModal ??= function showModal() {
  this.open = true
}

dialogPrototype.close ??= function close() {
  this.open = false
  this.dispatchEvent(new Event('close'))
}
